Feature: update_draft revises a draft in place, as often as the caller likes

  The caller changes individual fields of a draft and never learns that
  the message underneath was replaced. ADR 0031.

  Two rules govern the numbers in this file:
    - A draft starts at revision 1 and every successful update_draft
      produces the next integer.
    - A member absent from "changes" leaves that field untouched; a
      member set to null (or [] for an address list) removes it.

  A repeated call with an unchanged base_revision and identical changes
  is a retry of a lost response, not a second edit: it reports the
  revision that already exists and writes nothing.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path: one field changed, revision advances       : 1
    - IMAP second channel: old revision gone, one message left: 1
    - Three consecutive revisions on one handle              : 1
    - Absent member leaves the field untouched               : 1
    - Explicit null removes the field                        : 1
    - Attachments survive a body change                      : 1
    - Idempotent replay of a lost response                   : 1
    - Replay key is canonical: member order does not matter  : 1
    - Replay key is canonical: an explicit null is part of it: 1
    - Every per-revision header is regenerated, not inherited: 1
    - WAL transaction committed, mechanism reported          : 1
    Total enumerated                                         : 11  covered: 11

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "Drafts"
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder:
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

  Scenario: changing the subject advances the draft to revision 2
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

  Scenario: the previous revision is replaced, not duplicated
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

  Scenario: a draft can be revised repeatedly under the same handle
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

  Scenario: a field that is not mentioned keeps its value
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

  Scenario: an explicit null removes the field from the message
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"cc": null}
      """
    Then the response field revision equals 2
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response field cc has length 0
    And the response field to contains exactly:
      | address                  |
      | counterparty@example.com |
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value |
      | X-SC-Draft-Rev | 2     |
    And the IMAP message in folder "Drafts" with subject "Quotation for the spring order" has no header "Cc"

  Scenario: attachments survive a body change
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

  # ADR 0031 regenerates four headers on every revision. Message-ID is
  # the load-bearing one: ADR 0008 uses it as the saga's idempotency
  # key, so a revision 2 that kept revision 1's Message-ID would make
  # the next saga believe its APPEND had already happened.
  Scenario: every per-revision header is regenerated, not carried over
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
    And the IMAP folder "Drafts" contains exactly 1 message with subject "Regenerated headers"
    And the header "X-SC-Draft-Rev" of the IMAP message in folder "Drafts" with subject "Regenerated headers" is not "1"
    And the header "Message-ID" of the IMAP message in folder "Drafts" with subject "Regenerated headers" is not "<composed-in-2020@example.com>"
    And the header "Date" of the IMAP message in folder "Drafts" with subject "Regenerated headers" is not "Wed, 01 Jan 2020 10:00:00 +0000"
    And the header "Date" of the IMAP message in folder "Drafts" with subject "Regenerated headers" is a timestamp within the last 300 seconds
    # sha256 of {"body_text":"We can deliver in week 13."} — the canonical
    # encoding ADR 0031 spells out: members sorted, no whitespace, UTF-8.
    And the message in folder "Drafts" with subject "Regenerated headers" has X-SC-Draft-Change-Hash header equal to "1f06a0f3ecd072be79fff639e679d4bba51b4b3d49d88bd02f371391027a06f7"
    And the header "Message-ID" of the IMAP message in folder "Drafts" with subject "Regenerated headers" is remembered as "message-id of revision 2"
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 2, changes:
      """
      {"body_text": "We can deliver in week 12."}
      """
    Then the response field revision equals 3
    And the IMAP folder "Drafts" contains exactly 1 message with subject "Regenerated headers"
    And the header "Message-ID" of the IMAP message in folder "Drafts" with subject "Regenerated headers" differs from the remembered "message-id of revision 2"
    And the header "Date" of the IMAP message in folder "Drafts" with subject "Regenerated headers" is a timestamp within the last 300 seconds
    # A different change must produce a different hash, or the replay
    # check below would accept an unrelated retry.
    And the message in folder "Drafts" with subject "Regenerated headers" has X-SC-Draft-Change-Hash header equal to "a0c5cde5048154a551637cbbb868371ae43a9192be33079db5ea206f69ae2aa5"

  Scenario: repeating a call whose response was lost does not edit twice
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
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value |
      | X-SC-Draft-Rev | 2     |

  # ADR 0031 specifies the replay key as sha256 over a *canonical* JSON
  # encoding — members sorted. Two retries that spell the same request
  # in a different member order are therefore the same request.
  Scenario: a retry that spells the same changes in another order is a replay
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
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field revision equals 2
    And the response field idempotent_replay equals true
    And the response field mechanism equals "draft_revision"
    And the response does not contain any field named "tx_id"
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value             |
      | Subject        | Reordered members |
      | X-SC-Draft-Rev | 2                 |

  # ADR 0031: "null members preserved" in the canonical encoding. That
  # cuts both ways, and both halves are asserted here: a retry carrying
  # the null is the same request, and a retry that drops it is not.
  Scenario: an explicit null is part of the request, in the replay key too
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
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field revision equals 2
    And the response field idempotent_replay equals true
    And the response field mechanism equals "draft_revision"
    And the response does not contain any field named "tx_id"
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Null stays null"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "draft_revision_conflict"
    And the response field current_revision equals 2
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value           |
      | Subject        | Null stays null |
      | X-SC-Draft-Rev | 2               |

  Scenario: the revision is a committed WAL transaction
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Transactional"}
      """
    Then the response decision is ALLOW
    And the response field mechanism equals "draft_revision"
    And the response field tx_id is a non-empty string
    When draft-agent calls get_transaction_status with the returned tx_id
    Then the status response field state equals "committed"
