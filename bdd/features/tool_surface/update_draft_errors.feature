Feature: update_draft rejects what it cannot safely do

  The failure surface of a draft revision, layer by layer. ADR 0031
  adds four error types to the closed enumeration of ADR 0027:
  draft_not_found, draft_not_editable, draft_revision_conflict and
  draft_ambiguous. Everything that is a malformed call rather than a
  mailbox condition is rejected as an invalid argument, before any IMAP
  connection is opened.

  Covered error layers (per BDD Guidelines §4.5):
    - Argument validation : 5   covered here: 5
        empty changes object, unknown member, base_revision 0,
        CR/LF in a header value (one example per header-valued
        member of changes), malformed address
    - Handle resolution   : 5   covered here: 5
        folder_absent, draft_not_found, draft_not_editable,
        draft_ambiguous, a handle does not reach into a second
        existing folder
    - Concurrency         : 2   covered here: 2
        stale base_revision, base_revision ahead of the draft
    - Policy              : 4   covered here: 1, elsewhere: 3
        here: the draft_edit of the folder the call names decides
        elsewhere, in policy/draft_edit_capability.feature: draft_edit
        absent, draft_append alone, modify_message alone. That file
        enumerates nine layers in total; the three named here are the
        ones that are update_draft denials, the rest are about
        create_draft, get_draft and list_drafts.
    Total enumerated      : 16  covered here: 13, elsewhere: 3

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "Drafts"
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    # Three folders are granted and only "Drafts" is created on the IMAP
    # side; the scenarios below bring the other two into existence where
    # they need them. "Archiv" is granted without draft_edit on purpose —
    # the policy the server reads is fixed at startup, so a scenario
    # cannot add a grant after the first tool call.
    And policy "draft-policy" grants folder:
      | folder    | mode      | default | rules | draft_append | draft_edit |
      | Drafts    | blacklist | FULL    | []    | true         | true       |
      | Entwuerfe | blacklist | FULL    | []    | true         | true       |
      | Archiv    | blacklist | FULL    | []    | true         | false      |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Quotation for the spring order
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """

  Scenario: a changes object with no members is rejected
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {}
      """
    Then the tool call fails with an invalid-arguments error

  Scenario: a member that is not a draft field is rejected
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"reply_to": "someone@example.com"}
      """
    Then the tool call fails with an invalid-arguments error

  Scenario: revision numbers start at 1, so 0 is not a base
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 0, changes:
      """
      {"subject": "Zero is not a revision"}
      """
    Then the tool call fails with an invalid-arguments error

  # ADR 0031 states the CR/LF rule for header values as a class, not for
  # one field: every member of `changes` that ends up on a header line is
  # rejected, never sanitised. One example per such member, so a
  # field-wise implementation cannot pass.
  Scenario Outline: a line break in <field> is rejected rather than sanitised
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      <changes>
      """
    Then the tool call fails with an invalid-arguments error
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                          |
      | Subject        | Quotation for the spring order |
      | X-SC-Draft-Rev | 1                              |
    And the IMAP message in folder "Drafts" with subject "Quotation for the spring order" has no header "Bcc"

    Examples: every member of changes that becomes a header value
      | field       | changes                                                       |
      | subject     | {"subject": "Harmless\r\nBcc: attacker@example.com"}          |
      | in_reply_to | {"in_reply_to": "<a@b.example>\r\nBcc: attacker@example.com"} |
      | to          | {"to": ["<a@b.example>\r\nBcc: attacker@example.com"]}        |
      | cc          | {"cc": ["<a@b.example>\r\nBcc: attacker@example.com"]}        |
      | bcc         | {"bcc": ["<a@b.example>\r\nBcc: attacker@example.com"]}       |

  Scenario: an address that is not RFC 5322 is rejected
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"to": ["not an address"]}
      """
    Then the tool call fails with an invalid-arguments error

  Scenario: a handle that resolves to nothing reports draft_not_found
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", draft_id "11111111-1111-4111-8111-111111111111", base_revision 1, changes:
      """
      {"subject": "Nobody home"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "draft_not_found"
    And the response field reason equals "folder_default_applied"

  Scenario: a message with a handle but no usable revision is not editable
    Given the folder "Drafts" holds a verbatim message:
      """
      From: anika@example.com
      To: counterparty@example.com
      Subject: Handle survived, counter did not
      X-SC-Draft-Id: 22222222-2222-4222-8222-222222222222
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
    And the response field error.type equals "draft_not_editable"
    And the response field reason equals "folder_default_applied"

  # ADR 0030: ambiguity "is an incident": the caller is told, and the
  # transaction is escalated to `needs_operator` so an operator sees it.
  # The caller-facing answer alone does not discharge that promise, so
  # the WAL is read as a second channel.
  Scenario: two messages with the same handle and the same revision are not guessed between
    Given the folder "Drafts" holds a verbatim message:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Twin A
      X-SC-Draft-Id: 33333333-3333-4333-8333-333333333333
      X-SC-Draft-Rev: 2
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      First twin.
      """
    And the folder "Drafts" holds a verbatim message:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Twin B
      X-SC-Draft-Id: 33333333-3333-4333-8333-333333333333
      X-SC-Draft-Rev: 2
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Second twin.
      """
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", draft_id "33333333-3333-4333-8333-333333333333", base_revision 2, changes:
      """
      {"subject": "Which twin?"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "draft_ambiguous"
    And the response field tx_id is a non-empty string
    And the response field reason equals "folder_default_applied"
    And the IMAP folder "Drafts" contains exactly 1 message with subject "Twin A"
    And the IMAP folder "Drafts" contains exactly 1 message with subject "Twin B"
    And the WAL entry reaches status "needs_operator"

  # ADR 0030: "The search runs in exactly one folder, the one in the
  # call. A handle cannot be used to reach into another folder." The
  # folder_absent scenario below cannot prove that, because the folder it
  # names does not exist. This one names a folder that exists, is
  # granted, and holds nothing.
  Scenario: a handle does not resolve into a second, existing folder
    Given the IMAP account "gupta-scaratec" also exists with folder "Entwuerfe"
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Entwuerfe", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Edited through the wrong folder"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "draft_not_found"
    And the response field reason equals "folder_default_applied"
    And the IMAP folder "Entwuerfe" contains exactly 0 messages with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                          |
      | Subject        | Quotation for the spring order |
      | X-SC-Draft-Rev | 1                              |

  # The capability that has to hold is the one on the folder the caller
  # named, not the one on the folder the draft happens to live in. The
  # draft is in Drafts, where draft_edit is granted; the call names
  # Archiv, where it is not.
  Scenario: the capability of the named folder decides, not that of the draft's folder
    Given the IMAP account "gupta-scaratec" also exists with folder "Archiv"
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Archiv", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Edited through a folder without draft_edit"}
      """
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "draft_edit"
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                          |
      | Subject        | Quotation for the spring order |
      | X-SC-Draft-Rev | 1                              |

  Scenario: editing a folder that does not exist reports folder_absent
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Entwuerfe", draft_id "44444444-4444-4444-8444-444444444444", base_revision 1, changes:
      """
      {"subject": "Wrong folder"}
      """
    Then the response field result equals "ERROR"
    And the response field error.type equals "folder_absent"
    And the response field reason equals "folder_default_applied"

  Scenario: a base_revision that has been overtaken is a conflict
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Edit by the first caller"}
      """
    Then the response field revision equals 2
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Edit by the second caller"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "draft_revision_conflict"
    And the response field reason equals "folder_default_applied"
    And the response field current_revision equals 2
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                    |
      | Subject        | Edit by the first caller |
      | X-SC-Draft-Rev | 2                        |

  Scenario: a base_revision ahead of the draft is a conflict too
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 7, changes:
      """
      {"subject": "From the future"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "draft_revision_conflict"
    And the response field reason equals "folder_default_applied"
    And the response field current_revision equals 1
