Feature: update_draft rejects invalid inputs, missing/tampered handles, non-Drafts folders, and stale revisions

  ADR 0042, ADR 0044, ADR 0045, and ADR 0063 specify the failure surface of
  update_draft:
    - Malformed inputs (empty changes, unknown members, base_revision 0,
      CR/LF in header fields, invalid RFC 5322 addresses) fail with
      JSON-RPC -32602 before opening any IMAP connection.
    - Unknown handles, foreign handles, and unsigned messages carrying an
      X-SC-Draft-Id return ALLOW + ERROR not_found.
    - The caller's own WAL-registered draft whose X-SC-Provenance no longer
      verifies (stripped or externally edited) returns ALLOW + ERROR
      draft_not_editable.
    - Duplicate messages sharing (draft_id, revision) return ALLOW + ERROR
      draft_ambiguous and record needs_operator in wal.db.
    - Non-\Drafts folders or folders without draft_edit return DENY
      capability_missing (with no legacy missing_capability field).

  Covered error layers (per BDD Guidelines §4.5):
    - Input validation (-32602 with 0 IMAP connections) : 9
    - Handle resolution (unknown not_found, foreign unsigned not_found, tampered own draft_not_editable, draft_ambiguous, folder_absent, wrong folder) : 6
    - Concurrency (stale base_revision, future base_revision) : 2
    - Policy (folder without draft_edit / non-\Drafts)  : 1
    Total enumerated                                    : 18  covered here: 18

  Background:
    Given the server clock is pinned to "2026-09-18T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder on account "gupta-scaratec":
      | folder    | mode      | default | rules | draft_append | draft_edit |
      | Drafts    | blacklist | FULL    | []    | true         | true       |
      | Entwuerfe | blacklist | FULL    | []    | true         | true       |
      | Archiv    | blacklist | FULL    | []    | false        | false      |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Quotation for the spring order
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """

  Scenario Outline: Malformed update_draft input (<case>) fails with JSON-RPC -32602 and zero new IMAP commands
    Given the IMAP command log for "gupta-scaratec" is cleared
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision <base_rev>, changes:
      """
      <changes>
      """
    Then the server responds with JSON-RPC error code -32602
    And the IMAP command log for "gupta-scaratec" contains 0 commands
    And the IMAP message in folder "Drafts" with subject "Quotation for the spring order" has no header "Bcc"

    Examples:
      | case                  | base_rev | changes                                                       |
      | empty changes         | 1        | {}                                                            |
      | unknown member        | 1        | {"reply_to": "someone@example.com"}                           |
      | base_revision zero    | 0        | {"subject": "Zero"}                                           |
      | crlf in subject       | 1        | {"subject": "Harmless\r\nBcc: attacker@example.com"}          |
      | crlf in in_reply_to   | 1        | {"in_reply_to": "<a@b.example>\r\nBcc: attacker@example.com"} |
      | crlf in to            | 1        | {"to": ["<a@b.example>\r\nBcc: attacker@example.com"]}        |
      | crlf in cc            | 1        | {"cc": ["<a@b.example>\r\nBcc: attacker@example.com"]}        |
      | crlf in bcc           | 1        | {"bcc": ["<a@b.example>\r\nBcc: attacker@example.com"]}       |
      | invalid rfc5322 email | 1        | {"to": ["not an address"]}                                    |

  Scenario: Unknown draft_id returns ALLOW + ERROR not_found
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", draft_id "11111111-1111-4111-8111-111111111111", base_revision 1, changes:
      """
      {"subject": "Nobody home"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    And the response field reason equals "folder_default_applied"

  Scenario: Unsigned foreign message carrying X-SC-Draft-Id returns opaque not_found without existence leak
    Given the folder "Drafts" holds a verbatim message:
      """
      From: anika@example.com
      To: counterparty@example.com
      Subject: Foreign unsigned handle
      X-SC-Draft-Id: 22222222-2222-4222-8222-222222222222
      X-SC-Draft-Rev: 1
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Re-composed by a foreign client.
      """
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", draft_id "22222222-2222-4222-8222-222222222222", base_revision 1, changes:
      """
      {"subject": "Try me"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    And the response field reason equals "folder_default_applied"
    And the IMAP folder "Drafts" contains 1 message with subject "Foreign unsigned handle"

  Scenario: Caller's own draft whose X-SC-Provenance header was stripped externally returns draft_not_editable
    Given an external mail client strips header "X-SC-Provenance" from the draft in "gupta-scaratec:Drafts" while keeping X-SC-Draft-Id and X-SC-Draft-Rev
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Try editing stripped own draft"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "draft_not_editable"
    And the IMAP folder "Drafts" contains 1 message with subject "Quotation for the spring order"

  Scenario: Two authentic messages carrying the same draft_id and revision return draft_ambiguous and record needs_operator
    Given an external IMAP client duplicates the draft message in "gupta-scaratec:Drafts" so two copies share the returned draft_id and revision 1
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Which twin?"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "draft_ambiguous"
    And the response field tx_id is a non-empty string
    And the WAL entry for the returned tx_id has status "needs_operator"
    And the IMAP folder "Drafts" contains 2 messages with subject "Quotation for the spring order"

  Scenario: A handle does not resolve into a second existing \Drafts folder
    Given the IMAP account "gupta-scaratec" also has folder "Entwuerfe" with special-use "\Drafts"
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Entwuerfe", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Edited through the wrong folder"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    And the IMAP folder "Entwuerfe" contains 0 messages with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains 1 message with header "X-SC-Draft-Id" set

  Scenario: Calling update_draft on a folder without draft_edit is denied with capability_missing
    Given the IMAP account "gupta-scaratec" also has folder "Archiv"
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Archiv", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Edited through a folder without draft_edit"}
      """
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response does not contain any field named "missing_capability"
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                          |
      | Subject        | Quotation for the spring order |
      | X-SC-Draft-Rev | 1                              |

  Scenario: Calling update_draft on an authorized folder absent on IMAP returns folder_absent
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Entwuerfe", draft_id "44444444-4444-4444-8444-444444444444", base_revision 1, changes:
      """
      {"subject": "Missing folder"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "folder_absent"

  Scenario: An overtaken base_revision returns draft_revision_conflict with current_revision=2
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Edit by the first call"}
      """
    Then the response field revision equals 2
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Edit by the second call"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "draft_revision_conflict"
    And the response field current_revision equals 2
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                  |
      | Subject        | Edit by the first call |
      | X-SC-Draft-Rev | 2                      |

  Scenario: A base_revision ahead of the draft returns draft_revision_conflict with current_revision=1
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 7, changes:
      """
      {"subject": "From the future"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "draft_revision_conflict"
    And the response field current_revision equals 1
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                          |
      | Subject        | Quotation for the spring order |
      | X-SC-Draft-Rev | 1                              |
