Feature: IMAP mailbox names are UTF-8 across the MCP surface

  RFC 3501 §5.1.3 mandates Modified UTF-7 on the IMAP wire, whereas MCP
  callers, policies, WAL rows, and audit logs use plain UTF-8 (ADR 0044 §2).
  If an upstream IMAP server emits a malformed mUTF-7 token in LIST, the
  decoder preserves the raw ASCII token without crashing list_folders and
  records reason="mutf7_decode_fallback" in the audit trail.

  Covered error layers (per BDD Guidelines §4.5):
    - Inbound decoding (umlaut, '&-', mixed German names, ASCII)     : 6
    - Inbound decoding of malformed mUTF-7 via proxy LIST injection  : 1
    - Policy matching (UTF-8 policy governs mUTF-7 wire mailbox)     : 1
    - Outbound encoding (MOVE target, APPEND target)                 : 2
    - Audit log records UTF-8 folder paths, never mUTF-7             : 1
    - Round-trip: wire sequences never leak into list_folders        : 1
    Total enumerated                                                 : 12  covered by this feature: 12

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the audit log directory is a fresh $TMPDIR/audit
    And the IMAP account "gupta-scaratec" exists
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"
    And the WAL is empty

  Scenario: Umlaut mailbox is decoded from Modified UTF-7 into UTF-8
    Given the IMAP server for "gupta-scaratec" exposes the following mailboxes on the wire:
      | wire (mUTF-7 bytes)         | utf-8 (intended name)   |
      | INBOX                       | INBOX                   |
      | INBOX/Rechnungseing&AOQ-nge | INBOX/Rechnungseingänge |
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder                  | mode      | default  |
      | gupta-scaratec | INBOX                   | blacklist | ENVELOPE |
      | gupta-scaratec | INBOX/Rechnungseingänge | blacklist | FULL     |
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response contains folder "INBOX/Rechnungseingänge"
    And the response does not contain folder "INBOX/Rechnungseing&AOQ-nge"

  Scenario: Literal ampersand in a mailbox name decodes via the '&-' escape
    Given the IMAP server for "gupta-scaratec" exposes the following mailboxes on the wire:
      | wire (mUTF-7 bytes) | utf-8 (intended name) |
      | INBOX               | INBOX                 |
      | Tom &- Jerry        | Tom & Jerry           |
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder      | mode      | default  |
      | gupta-scaratec | INBOX       | blacklist | ENVELOPE |
      | gupta-scaratec | Tom & Jerry | blacklist | ENVELOPE |
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response contains folder "Tom & Jerry"
    And the response does not contain folder "Tom &- Jerry"

  Scenario Outline: Mixed-script German mailbox names decode end-to-end
    Given the IMAP server for "gupta-scaratec" exposes the following mailboxes on the wire:
      | wire (mUTF-7 bytes) | utf-8 (intended name) |
      | INBOX               | INBOX                 |
      | <wire>              | <utf8>                |
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder | mode      | default  |
      | gupta-scaratec | INBOX  | blacklist | ENVELOPE |
      | gupta-scaratec | <utf8> | blacklist | ENVELOPE |
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response contains folder "<utf8>"
    And the response does not contain folder "<wire>"

    Examples:
      | wire                   | utf8               |
      | M&APw-ll               | Müll               |
      | Gel&APY-schte Elemente | Gelöschte Elemente |
      | Belege/&ANw-bersicht   | Belege/Übersicht   |

  Scenario: Malformed Modified UTF-7 in LIST keeps its raw token and logs mutf7_decode_fallback
    Given the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the IMAP proxy for "gupta-scaratec" injects raw LIST entry "* LIST (\\HasNoChildren) \"/\" \"INBOX/Bug&AOQ\""
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder        | mode      | default  |
      | gupta-scaratec | INBOX         | blacklist | ENVELOPE |
      | gupta-scaratec | INBOX/Bug&AOQ | blacklist | ENVELOPE |
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response decision is ALLOW
    And the response contains folder "INBOX/Bug&AOQ"
    And the audit log contains a record with tool "mailbox_name_decode", result "OK", reason "mutf7_decode_fallback"

  Scenario: Pure-ASCII mailbox names pass through unchanged
    Given the IMAP server for "gupta-scaratec" exposes the following mailboxes on the wire:
      | wire (mUTF-7 bytes) | utf-8 (intended name) |
      | INBOX               | INBOX                 |
      | INBOX/Archive       | INBOX/Archive         |
      | INBOX/Sent          | INBOX/Sent            |
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder        | mode      | default  |
      | gupta-scaratec | INBOX         | blacklist | ENVELOPE |
      | gupta-scaratec | INBOX/Archive | blacklist | ENVELOPE |
      | gupta-scaratec | INBOX/Sent    | blacklist | ENVELOPE |
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response contains folder "INBOX/Archive"
    And the response contains folder "INBOX/Sent"

  Scenario: Policy written with UTF-8 path grants access to a mUTF-7 wire mailbox
    Given the IMAP server for "gupta-scaratec" exposes the following mailboxes on the wire:
      | wire (mUTF-7 bytes)         | utf-8 (intended name)   |
      | INBOX                       | INBOX                   |
      | INBOX/Rechnungseing&AOQ-nge | INBOX/Rechnungseingänge |
    And the folder "INBOX/Rechnungseingänge" holds messages:
      | uid | from_address         | to_address            | subject       | date                 |
      | 801 | rechnung@voelkner.de | me@gupta-scaratec.com | Gutschrift 42 | 2026-05-06T09:00:00Z |
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder                  | mode      | default  |
      | gupta-scaratec | INBOX                   | blacklist | ENVELOPE |
      | gupta-scaratec | INBOX/Rechnungseingänge | blacklist | FULL     |
    When invoice-agent calls list_messages with account "gupta-scaratec", folder "INBOX/Rechnungseingänge", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the response contains 1 messages
    And message 0 has field "subject" equal to "Gutschrift 42"

  Scenario: move to a UTF-8 target folder issues Modified UTF-7 on the wire
    Given the IMAP server for "gupta-scaratec" exposes the following mailboxes on the wire:
      | wire (mUTF-7 bytes)         | utf-8 (intended name)   |
      | INBOX                       | INBOX                   |
      | INBOX/Rechnungseing&AOQ-nge | INBOX/Rechnungseingänge |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the folder "INBOX" holds messages:
      | uid | message_id                   | from_address         | to_address            | subject       | date                 |
      | 811 | <m-91189@gupta-scaratec.com> | rechnung@voelkner.de | me@gupta-scaratec.com | Gutschrift 42 | 2026-05-06T09:00:00Z |
    And the IMAP folder "INBOX/Rechnungseingänge" is empty
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder                  | mode      | default | move_out | accept_incoming |
      | gupta-scaratec | INBOX                   | blacklist | FULL    | true     | false           |
      | gupta-scaratec | INBOX/Rechnungseingänge | blacklist | FULL    | false    | true            |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX", "uid": 811}, target {"account": "gupta-scaratec", "folder": "INBOX/Rechnungseingänge"}
    Then the response decision is ALLOW
    And the IMAP command log for "gupta-scaratec" contains a command whose target mailbox argument is the wire string "INBOX/Rechnungseing&AOQ-nge"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX" for message-id "<m-91189@gupta-scaratec.com>" returns zero results
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungseingänge" for message-id "<m-91189@gupta-scaratec.com>" returns exactly one result

  Scenario: create_draft into a UTF-8 \Drafts folder appends to the mUTF-7 wire mailbox without duplicating
    Given the IMAP server for "gupta-scaratec" exposes the following mailboxes on the wire:
      | wire (mUTF-7 bytes) | utf-8 (intended name) | special_use |
      | INBOX               | INBOX                 |             |
      | Entw&APw-rfe        | Entwürfe              | \Drafts     |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the IMAP folder "Entwürfe" is empty
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder   | mode      | default | draft_append |
      | gupta-scaratec | INBOX    | blacklist | FULL    | false        |
      | gupta-scaratec | Entwürfe | blacklist | FULL    | true         |
    When invoice-agent calls create_draft with account "gupta-scaratec", folder "Entwürfe", rfc822 payload:
      """
      From: gupta@scaratec.com
      To: bestellung@voelkner.de
      Subject: Draft credit note inquiry

      Please clarify.
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the IMAP command log for "gupta-scaratec" contains a command whose target mailbox argument is the wire string "Entw&APw-rfe"
    And the IMAP folder "Entwürfe" contains exactly one message with subject "Draft credit note inquiry"

  Scenario: Audit log records decoded UTF-8 folder paths and never mUTF-7 wire tokens
    Given the IMAP server for "gupta-scaratec" exposes the following mailboxes on the wire:
      | wire (mUTF-7 bytes)         | utf-8 (intended name)   |
      | INBOX                       | INBOX                   |
      | INBOX/Rechnungseing&AOQ-nge | INBOX/Rechnungseingänge |
    And the folder "INBOX" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject       | date                 |
      | 802 | <m-802@gupta-scaratec.com> | rechnung@voelkner.de | me@gupta-scaratec.com | Gutschrift 43 | 2026-05-06T09:00:00Z |
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder                  | mode      | default | move_out | accept_incoming |
      | gupta-scaratec | INBOX                   | blacklist | FULL    | true     | false           |
      | gupta-scaratec | INBOX/Rechnungseingänge | blacklist | FULL    | false    | true            |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX", "uid": 802}, target {"account": "gupta-scaratec", "folder": "INBOX/Rechnungseingänge"}
    Then the audit log contains an outcome record for tool "move" with source folder "INBOX" and target folder "INBOX/Rechnungseingänge"
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string |
      | &AOQ-            |

  Scenario: list_folders never leaks raw Modified UTF-7 sequences
    Given the IMAP server for "gupta-scaratec" exposes the following mailboxes on the wire:
      | wire (mUTF-7 bytes)         | utf-8 (intended name)   |
      | INBOX                       | INBOX                   |
      | INBOX/Rechnungseing&AOQ-nge | INBOX/Rechnungseingänge |
      | M&APw-ll                    | Müll                    |
      | Entw&APw-rfe                | Entwürfe                |
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder                  | mode      | default  |
      | gupta-scaratec | INBOX                   | blacklist | ENVELOPE |
      | gupta-scaratec | INBOX/Rechnungseingänge | blacklist | ENVELOPE |
      | gupta-scaratec | Müll                    | blacklist | ENVELOPE |
      | gupta-scaratec | Entwürfe                | blacklist | ENVELOPE |
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the JSON response does NOT contain the literal strings:
      | forbidden_string |
      | &AOQ-            |
      | &APw-            |
    And the response contains folder "INBOX/Rechnungseingänge"
    And the response contains folder "Müll"
    And the response contains folder "Entwürfe"
