Feature: Gmail special-use folders resolve canonically from the first call on

  ADR 0056 preserves session-start RFC 6154 special-use alias
  resolution on granted Google accounts: callers and policies may use
  canonical ([Gmail]/Drafts) or localized ([Gmail]/Entwürfe) names,
  wire commands always address the localized mUTF-7 mailbox, responses
  always emit canonical paths, identical duplicate policy entries
  collapse cleanly, and discrepant entries fail closed with
  policy_alias_conflict.

  Fixture fidelity checks (mock self-validation, not SUT error paths): 1
  Covered SUT error layers (per BDD Guidelines §4.5):
    - First call in session resolves canonical special-use read path : 3
    - First call in session writes to canonical [Gmail]/Drafts       : 1
    - Label-swap move across canonical and localized name forms      : 3
    - Unselectable source folder reported as error.type=folder_absent: 1
    - Cross-account saga resolves localized All Mail & removes INBOX : 1
    - Canonical-only policy governs localized caller argument        : 1
    - Localized-only policy governs canonical caller argument        : 1
    - Identical duplicate entries collapse cleanly                   : 1
    - Discrepant duplicate entries fail closed (policy_alias_conflict): 2
    - Responses echo canonical folder path                           : 1
    Total SUT enumerated                                             : 15  covered by this feature: 15 (plus 1 mock-fidelity check)

  Background:
    Given the server clock is pinned to "2026-11-02T12:00:00Z"
    And the audit log directory is a fresh $TMPDIR/audit
    And the IMAP account "gupta-gmail-de" exists with provider "google" and folders:
      | folder path       |
      | INBOX             |
      | [Gmail]/All Mail  |
      | [Gmail]/Drafts    |
      | [Gmail]/Sent Mail |
      | [Gmail]/Trash     |
    And the mock-gmail server for "gupta-gmail-de" uses localized folder names:
      | canonical         | localized                | flags                  |
      | [Gmail]/All Mail  | [Gmail]/Alle Nachrichten | \All \HasNoChildren    |
      | [Gmail]/Drafts    | [Gmail]/Entw&APw-rfe     | \Drafts \HasNoChildren |
      | [Gmail]/Sent Mail | [Gmail]/Gesendet         | \Sent \HasNoChildren   |
      | [Gmail]/Trash     | [Gmail]/Papierkorb       | \Trash \HasNoChildren  |
    And the server is configured with caller "draft-agent" using policy "de-policy"
    And policy "de-policy" grants account "gupta-gmail-de"

  Scenario: The localized mock refuses the English name, as Gmail does
    When a direct IMAP SELECT of "[Gmail]/Drafts" is issued against "gupta-gmail-de"
    Then the direct IMAP command is answered with status "NO"
    When a direct IMAP SELECT of "[Gmail]/Entw&APw-rfe" is issued against "gupta-gmail-de"
    Then the direct IMAP command is answered with status "OK"

  Scenario Outline: A read tool resolves a canonical special-use path as the first call of a session
    Given policy "de-policy" grants folder:
      | folder   | mode      | default  | rules |
      | <folder> | blacklist | ENVELOPE | []    |
    And no tool has been called in this server session yet
    When draft-agent calls <tool> with account "gupta-gmail-de", folder "<folder>"
    Then the response decision is ALLOW
    And the response does not contain any field named "error"
    And the IMAP command log for "gupta-gmail-de" contains a SELECT or EXAMINE of the wire mailbox "<wire>"
    And the IMAP command log for "gupta-gmail-de" contains no command addressing the mailbox "<folder>"

    Examples:
      | tool          | folder            | wire                     |
      | search        | [Gmail]/Sent Mail | [Gmail]/Gesendet         |
      | list_messages | [Gmail]/Trash     | [Gmail]/Papierkorb       |
      | folder_stats  | [Gmail]/All Mail  | [Gmail]/Alle Nachrichten |

  Scenario: create_draft on canonical [Gmail]/Drafts lands in localized Drafts as the first call
    Given policy "de-policy" grants folder:
      | folder         | mode      | default | rules | draft_append |
      | [Gmail]/Drafts | blacklist | FULL    | []    | true         |
    And no tool has been called in this server session yet
    When draft-agent calls create_draft with account "gupta-gmail-de", folder "[Gmail]/Drafts", rfc822 payload:
      """
      From: draft-agent@example.org
      To: mara.lindqvist@example.org
      Subject: Angebot Fruehjahr
      MIME-Version: 1.0
      Content-Type: text/plain; charset=UTF-8

      Anbei unser Angebot.
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for header X-SC-Draft-Id equal to the returned draft_id returns exactly one result
    And the IMAP command log for "gupta-gmail-de" contains no command addressing the mailbox "[Gmail]/Drafts"

  Scenario Outline: A draft moves to trash on a localized account whichever name form the caller uses
    Given policy "de-policy" grants folder:
      | folder         | mode      | default  | rules | move_out | accept_incoming |
      | [Gmail]/Drafts | blacklist | FULL     | []    | true     | false           |
      | [Gmail]/Trash  | blacklist | METADATA | []    | false    | true            |
    And a Gmail message on "gupta-gmail-de" has:
      | x_gm_msgid | message_id            | from_address            | to_address     | subject         | date                 |
      | 20001      | <d-20001@example.org> | draft-agent@example.org | me@example.org | Entwurf Angebot | 2026-11-01T09:00:00Z |
    And the Gmail message 20001 on "gupta-gmail-de" carries labels ["\\Drafts"] with UID 301 in "[Gmail]/Drafts" and UID 20001 in "[Gmail]/All Mail"
    When draft-agent calls move with source {"account": "gupta-gmail-de", "folder": "<source>", "uid": 301}, target {"account": "gupta-gmail-de", "folder": "<target>"}
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mechanism equals "gmail_label_swap"
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for X-GM-MSGID 20001 returns zero results
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Papierkorb" for X-GM-MSGID 20001 returns exactly one result

    Examples:
      | source           | target             |
      | [Gmail]/Drafts   | [Gmail]/Trash      |
      | [Gmail]/Entwürfe | [Gmail]/Papierkorb |
      | [Gmail]/Drafts   | [Gmail]/Papierkorb |

  Scenario: A move whose source folder does not exist reports error.type=folder_absent
    Given policy "de-policy" grants folder:
      | folder           | mode      | default  | rules | move_out | accept_incoming |
      | [Gmail]/Archiv19 | blacklist | FULL     | []    | true     | false           |
      | [Gmail]/Trash    | blacklist | METADATA | []    | false    | true            |
    When draft-agent calls move with source {"account": "gupta-gmail-de", "folder": "[Gmail]/Archiv19", "uid": 301}, target {"account": "gupta-gmail-de", "folder": "[Gmail]/Trash"}
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "folder_absent"
    And the response does not contain any field named "error_type"
    And the IMAP command log for "gupta-gmail-de" contains no STORE command

  Scenario: Cross-account move from a localized Google account fetches from localized All Mail and removes INBOX
    Given the IMAP account "archive-srv" exists with provider "imap-standard" and folders:
      | folder path |
      | Archiv      |
    And policy "de-policy" grants account "archive-srv"
    And policy "de-policy" grants folder on account "gupta-gmail-de":
      | folder | mode      | default | rules | move_out | accept_incoming |
      | INBOX  | blacklist | FULL    | []    | true     | false           |
    And policy "de-policy" grants folder on account "archive-srv":
      | folder | mode      | default | rules | accept_incoming |
      | Archiv | blacklist | FULL    | []    | true            |
    And a Gmail message on "gupta-gmail-de" has:
      | x_gm_msgid | message_id            | from_address                   | to_address     | subject       | date                 |
      | 20002      | <m-20002@example.org> | rechnung@nordlicht.example.org | me@example.org | Rechnung 4711 | 2026-11-01T09:00:00Z |
    And the Gmail message 20002 on "gupta-gmail-de" carries labels ["INBOX"] with UID 512 in "INBOX" and UID 20002 in "[Gmail]/All Mail"
    When draft-agent calls move with source {"account": "gupta-gmail-de", "folder": "INBOX", "uid": 512}, target {"account": "archive-srv", "folder": "Archiv"}
    Then the WAL entry for the returned tx_id has status "committed"
    And the IMAP command log for "gupta-gmail-de" contains a SELECT or EXAMINE of the wire mailbox "[Gmail]/Alle Nachrichten"
    And a direct IMAP SEARCH on "archive-srv:Archiv" for message-id "<m-20002@example.org>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-gmail-de:INBOX" for X-GM-MSGID 20002 returns zero results

  Scenario: A policy written only in canonical names governs localized caller input
    Given policy "de-policy" grants folder:
      | folder         | mode      | default | rules | draft_append |
      | [Gmail]/Drafts | blacklist | FULL    | []    | true         |
    When draft-agent calls list_drafts with account "gupta-gmail-de", folder "[Gmail]/Entwürfe"
    Then the response decision is ALLOW
    And the IMAP command log for "gupta-gmail-de" contains a SELECT or EXAMINE of the wire mailbox "[Gmail]/Entw&APw-rfe"

  Scenario: A policy written under the localized name governs canonical caller input
    Given policy "de-policy" grants folder:
      | folder             | mode      | default  | rules |
      | [Gmail]/Papierkorb | blacklist | METADATA | []    |
    When draft-agent calls list_messages with account "gupta-gmail-de", folder "[Gmail]/Trash", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the IMAP command log for "gupta-gmail-de" contains a SELECT or EXAMINE of the wire mailbox "[Gmail]/Papierkorb"

  Scenario: Two identical policy entries for canonical and localized names collapse cleanly
    Given policy "de-policy" grants folder:
      | folder             | mode      | default  | rules |
      | [Gmail]/Trash      | blacklist | METADATA | []    |
      | [Gmail]/Papierkorb | blacklist | METADATA | []    |
    When draft-agent calls list_messages with account "gupta-gmail-de", folder "[Gmail]/Trash", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the response field result equals "OK"

  Scenario: Two disagreeing policy entries for the same special-use folder fail closed with policy_alias_conflict
    Given policy "de-policy" grants folder:
      | folder             | mode      | default | rules |
      | [Gmail]/Trash      | whitelist | NONE    | []    |
      | [Gmail]/Papierkorb | blacklist | FULL    | []    |
    When draft-agent calls list_messages with account "gupta-gmail-de", folder "[Gmail]/Papierkorb", criteria {}, scope "all"
    Then the response decision is DENY
    And the response field reason equals "policy_alias_conflict"
    And the audit log contains an entry with:
      | field    | value                 |
      | tool     | list_messages         |
      | decision | DENY                  |
      | reason   | policy_alias_conflict |
    And the IMAP command log for "gupta-gmail-de" contains no SELECT or EXAMINE of the wire mailbox "[Gmail]/Papierkorb"

  Scenario: Discrepant canonical and localized Sent Mail entries also fail closed on search
    Given policy "de-policy" grants folder:
      | folder            | mode      | default  | rules |
      | [Gmail]/Sent Mail | blacklist | METADATA | []    |
      | [Gmail]/Gesendet  | blacklist | FULL     | []    |
    When draft-agent calls search with account "gupta-gmail-de", folder "[Gmail]/Sent Mail", criteria {}, scope "all"
    Then the response decision is DENY
    And the response field reason equals "policy_alias_conflict"

  Scenario: Responses echo the canonical special-use folder path even when called with localized name
    Given policy "de-policy" grants folder:
      | folder         | mode      | default | rules | draft_append |
      | [Gmail]/Drafts | blacklist | FULL    | []    | true         |
    When draft-agent calls list_drafts with account "gupta-gmail-de", folder "[Gmail]/Entwürfe"
    Then the response decision is ALLOW
    And the response field folder equals "[Gmail]/Drafts"
