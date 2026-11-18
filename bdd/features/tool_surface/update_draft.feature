Feature: update_draft revises an authentic own draft field-wise and attachment-wise under CAS locking

  ADR 0042, ADR 0045, and ADR 0046 specify update_draft:
    - Revises an authentic caller-owned draft in a \Drafts folder,
      advancing revision by +1, regenerating Message-ID, Date, and
      X-SC-Provenance, and scrubbing staged_rfc822 in wal.db on commit.
    - Supports structured attachment updates (changes.attachments) on
      own drafts while preserving existing attachments when omitted.
    - Treats an identical (base_revision, canonical_changes_hash) retry
      as an idempotent replay, where X-SC-Draft-Change-Hash equals
      lowercase hex of SHA-256(UTF-8(canonical_json(changes))) with keys
      sorted, compact (",", ":") separators, and explicit null preserved
      (ADR 0063 §3).
    - Serializes concurrent revisions across local server instances via
      SQLite leases.

  Covered error layers (per BDD Guidelines §4.5):
    - Subject update advances revision to 2                        : 1
    - Old revision removed on IMAP, exactly 1 message remains      : 1
    - Three consecutive revisions under the same handle            : 1
    - Absent members preserved; explicit null removes header       : 2
    - Existing attachments survive a body edit; changes.attachments replaces/clears : 2
    - Per-revision headers & X-SC-Draft-Change-Hash regenerated    : 1
    - Idempotent replay (exact, reordered keys, explicit null)     : 3
    - Committed WAL row verified via status tool and direct SQLite : 1
    - Concurrent update_draft across 2 instances serializes via CAS: 1
    Total enumerated                                               : 13  covered: 13

  Background:
    Given the server clock is pinned to "2026-09-18T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | true         | true       |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Cc: office@example.com
      Subject: Quotation for the spring order
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """

  Scenario: Changing the subject advances the draft to revision 2
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Quotation for the spring order (revised)"}
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field revision equals 2
    And the response does not contain any field named "error"
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response field subject equals "Quotation for the spring order (revised)"

  Scenario: The previous revision is replaced on IMAP, never duplicated
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Only one of me"}
      """
    Then the response decision is ALLOW
    And the response field revision equals 2
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value          |
      | Subject        | Only one of me |
      | X-SC-Draft-Rev | 2              |
    And the IMAP folder "Drafts" contains no message with subject "Quotation for the spring order"

  Scenario: A draft can be revised repeatedly under the same handle
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "First pass"}
      """
    Then the response field revision equals 2
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 2, changes:
      """
      {"body_text": "We can deliver in week 13."}
      """
    Then the response field revision equals 3
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 3, changes:
      """
      {"subject": "Third pass"}
      """
    Then the response field revision equals 4
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response field revision equals 4
    And the response field subject equals "Third pass"
    And the response field body_text equals "We can deliver in week 13."

  Scenario: Unmentioned fields keep their previous values
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Subject only"}
      """
    Then the response field revision equals 2
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response field subject equals "Subject only"
    And the response field body_text equals "We can deliver in week 14."
    And the response field to contains exactly:
      | address                  |
      | counterparty@example.com |
    And the response field cc contains exactly:
      | address            |
      | office@example.com |

  Scenario: An explicit null removes the field from the message
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"cc": null}
      """
    Then the response field revision equals 2
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response field cc has length 0
    And the IMAP message in folder "Drafts" with subject "Quotation for the spring order" has no header "Cc"

  Scenario: Existing attachments survive a body-only edit
    Given draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Offer with annex
      MIME-Version: 1.0
      Content-Type: multipart/mixed; boundary="sep"

      --sep
      Content-Type: text/plain; charset=US-ASCII

      Annex attached.
      --sep
      Content-Type: application/pdf; name="annex.pdf"
      Content-Disposition: attachment; filename="annex.pdf"
      Content-Transfer-Encoding: base64

      dGVzdA==
      --sep--
      """
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"body_text": "Annex attached, prices corrected."}
      """
    Then the response field revision equals 2
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response field body_text equals "Annex attached, prices corrected."
    And the response field attachments contains exactly one entry with:
      | field     | value           |
      | filename  | annex.pdf       |
      | mime_type | application/pdf |

  Scenario: changes.attachments replaces and clears attachments on the caller's own draft
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"attachments": [{"filename": "preisliste.pdf", "mime_type": "application/pdf", "content_base64": "JVBERi0xLjQK"}]}
      """
    Then the response field revision equals 2
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response field attachments has 1 entries
    And attachment 0 has field "filename" equal to "preisliste.pdf"
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 2, changes:
      """
      {"attachments": []}
      """
    Then the response field revision equals 3
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response field attachments has 0 entries

  Scenario: Per-revision headers and canonical X-SC-Draft-Change-Hash are regenerated on every edit
    Given draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Regenerated headers
      Date: Wed, 01 Jan 2020 10:00:00 +0000
      Message-ID: <composed-in-2020@example.com>
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"body_text": "We can deliver in week 13."}
      """
    Then the response field revision equals 2
    And the header "Message-ID" of the IMAP message in folder "Drafts" with subject "Regenerated headers" is not "<composed-in-2020@example.com>"
    And the message in folder "Drafts" with subject "Regenerated headers" has X-SC-Draft-Change-Hash header equal to "1f06a0f3ecd072be79fff639e679d4bba51b4b3d49d88bd02f371391027a06f7"
    And the header "Message-ID" of the IMAP message in folder "Drafts" with subject "Regenerated headers" is remembered as "message-id of revision 2"
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 2, changes:
      """
      {"body_text": "We can deliver in week 12."}
      """
    Then the response field revision equals 3
    And the header "Message-ID" of the IMAP message in folder "Drafts" with subject "Regenerated headers" differs from the remembered "message-id of revision 2"
    And the message in folder "Drafts" with subject "Regenerated headers" has X-SC-Draft-Change-Hash header equal to "a0c5cde5048154a551637cbbb868371ae43a9192be33079db5ea206f69ae2aa5"

  Scenario: Repeating an identical update_draft call is an idempotent replay that writes nothing
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Sent once, answered never"}
      """
    Then the response field revision equals 2
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Sent once, answered never"}
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field revision equals 2
    And the response field idempotent_replay equals true
    And the response field mechanism equals "draft_revision"
    And the response does not contain any field named "tx_id"
    And the IMAP folder "Drafts" contains exactly 1 message with subject "Sent once, answered never"

  Scenario: Reordered JSON keys in changes produce the same canonical replay hash
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Reordered members", "body_text": "Two members, one order.", "cc": ["office@example.com"]}
      """
    Then the response field revision equals 2
    And the message in folder "Drafts" with subject "Reordered members" has X-SC-Draft-Change-Hash header equal to "c7bd43906dcb5118e3f7c769d0b94b553bc265abb7dacb642243f44ac302bc87"
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"cc": ["office@example.com"], "body_text": "Two members, one order.", "subject": "Reordered members"}
      """
    Then the response field idempotent_replay equals true
    And the response field revision equals 2

  Scenario: Explicit null is preserved in the canonical replay key
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"cc": null, "subject": "Null stays null"}
      """
    Then the response field revision equals 2
    And the message in folder "Drafts" with subject "Null stays null" has X-SC-Draft-Change-Hash header equal to "5db5b73de9e53f12dbbd59fa1c8cc183f8a74765d6b9844e6a753755e260a041"
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Null stays null", "cc": null}
      """
    Then the response field idempotent_replay equals true
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Null stays null"}
      """
    Then the response field result equals "ERROR"
    And the response field error.type equals "draft_revision_conflict"
    And the response field current_revision equals 2

  Scenario: A draft revision commits a WAL transaction and scrubs staged_rfc822
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Transactional"}
      """
    Then the response decision is ALLOW
    And the response field mechanism equals "draft_revision"
    And the response field tx_id is a non-empty string
    And the WAL transactions table has an entry with:
      | field         | value              |
      | tx_id         | the returned tx_id |
      | status        | committed          |
      | staged_rfc822 | (null)             |
    When draft-agent calls get_transaction_status with the returned tx_id
    Then the status response field state equals "committed"

  Scenario: Two concurrent update_draft calls on base_revision 1 serialize via WAL lease so one wins and one gets conflict
    Given a second server instance is started sharing the same config and wal.db for caller "draft-agent"
    When the two "draft-agent" instances concurrently call update_draft on "gupta-scaratec:Drafts" for the returned draft_id with base_revision 1 and distinct subjects "Concurrent A" and "Concurrent B"
    Then 1 concurrent update_draft response has result "OK" with revision 2 and 1 has result "ERROR" with error.type "draft_revision_conflict"
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains 1 message carrying header "X-SC-Draft-Rev" equal to "2"
