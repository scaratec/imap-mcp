Feature: Drafts are revised, discarded, and moved on a localized Gmail account as Gmail really behaves

  ADR 0040, ADR 0042, ADR 0044, ADR 0045.

  On a German Gmail account:
    - System labels travel as unquoted flag atoms (\Trash, \Spam, \Inbox)
      in UID STORE +/-X-GM-LABELS; quoting "\Trash" fails with BAD.
    - [Gmail]/Drafts is "[Gmail]/Entw&APw-rfe" on the wire, carries
      \Draft \Seen and X-GM-LABELS (), and auto-expunges on +FLAGS (\Deleted).
    - MOVE and UIDPLUS are advertised only after authentication.
    - discard_draft verifies authentic caller provenance (X-SC-Provenance)
      and moves every revision to localized [Gmail]/Trash.

  Fixture fidelity checks (mock self-validation, not SUT error paths): 4
  Covered SUT error layers (per BDD Guidelines §4.5):
    - update_draft on localized Drafts keeps one signed revision    : 1
    - discard_draft moves every authentic revision to localized Trash : 1
    - Move INBOX to a system-label folder sends atom (\Trash, \Spam): 2
    - Move of a draft to a user label leaves the drafts folder      : 1
    Total SUT enumerated                                            : 5   covered by this feature: 5 (plus 4 mock-fidelity checks)

  Background:
    Given the server clock is pinned to "2026-11-02T12:00:00Z"
    And the IMAP account "gupta-gmail-de" exists with provider "google" and folders:
      | folder path      |
      | INBOX            |
      | Angebote         |
      | [Gmail]/All Mail |
      | [Gmail]/Drafts   |
      | [Gmail]/Spam     |
      | [Gmail]/Trash    |
    And the mock-gmail server for "gupta-gmail-de" uses localized folder names:
      | canonical        | localized                | flags                  |
      | [Gmail]/All Mail | [Gmail]/Alle Nachrichten | \All \HasNoChildren    |
      | [Gmail]/Drafts   | [Gmail]/Entw&APw-rfe     | \Drafts \HasNoChildren |
      | [Gmail]/Trash    | [Gmail]/Papierkorb       | \Trash \HasNoChildren  |
    And the server is configured with caller "draft-agent" using policy "de-policy"
    And policy "de-policy" grants account "gupta-gmail-de"

  Scenario: The mock refuses a system label in a quoted string with a bare backslash and accepts the atom
    Given a Gmail message on "gupta-gmail-de" has:
      | x_gm_msgid | message_id            | from_address              | to_address     | subject      | date                 |
      | 30001      | <m-30001@example.org> | anfrage@nordlicht.example | me@example.org | Anfrage 2026 | 2026-11-01T09:00:00Z |
    And the Gmail message 30001 on "gupta-gmail-de" carries labels ["INBOX"] with UID 401 in "INBOX" and UID 30001 in "[Gmail]/All Mail"
    When a direct IMAP command is issued in "INBOX" on "gupta-gmail-de":
      """
      UID STORE 401 +X-GM-LABELS ("\Trash")
      """
    Then the direct IMAP command is answered with status "BAD"
    And a direct IMAP SEARCH on "gupta-gmail-de:INBOX" for X-GM-MSGID 30001 returns exactly one result
    When a direct IMAP command is issued in "INBOX" on "gupta-gmail-de":
      """
      UID STORE 401 +X-GM-LABELS (\Trash)
      """
    Then the direct IMAP command is answered with status "OK"
    And a direct IMAP SEARCH on "gupta-gmail-de:INBOX" for X-GM-MSGID 30001 returns zero results
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Papierkorb" for X-GM-MSGID 30001 returns exactly one result

  Scenario: A draft on the mock carries \Draft and no Drafts label, and STORE of \Drafts changes nothing
    Given a Gmail message on "gupta-gmail-de" has:
      | x_gm_msgid | message_id            | from_address            | to_address     | subject        | date                 |
      | 30002      | <d-30002@example.org> | draft-agent@example.org | me@example.org | Entwurf Termin | 2026-11-01T09:00:00Z |
    And the Gmail message 30002 on "gupta-gmail-de" carries labels ["\\Drafts"] with UID 302 in "[Gmail]/Drafts" and UID 30002 in "[Gmail]/All Mail"
    Then a direct IMAP FETCH of uid 302 in "gupta-gmail-de:[Gmail]/Entw&APw-rfe" shows the flag "\Draft"
    And a direct IMAP FETCH of uid 302 in "gupta-gmail-de:[Gmail]/Entw&APw-rfe" shows X-GM-LABELS "()"
    When a direct IMAP command is issued in "[Gmail]/Entw&APw-rfe" on "gupta-gmail-de":
      """
      UID STORE 302 -X-GM-LABELS (\Drafts)
      """
    Then the direct IMAP command is answered with status "OK"
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for X-GM-MSGID 30002 returns exactly one result

  Scenario: A message marked \Deleted leaves the drafts mailbox immediately without EXPUNGE
    Given a Gmail message on "gupta-gmail-de" has:
      | x_gm_msgid | message_id            | from_address            | to_address     | subject        | date                 |
      | 30003      | <d-30003@example.org> | draft-agent@example.org | me@example.org | Entwurf Preise | 2026-11-01T09:00:00Z |
    And the Gmail message 30003 on "gupta-gmail-de" carries labels ["\\Drafts"] with UID 303 in "[Gmail]/Drafts" and UID 30003 in "[Gmail]/All Mail"
    When a direct IMAP command is issued in "[Gmail]/Entw&APw-rfe" on "gupta-gmail-de":
      """
      UID STORE 303 +FLAGS (\Deleted)
      """
    Then the direct IMAP command is answered with status "OK"
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for X-GM-MSGID 30003 returns zero results

  Scenario: The mock advertises MOVE and UIDPLUS only after authentication
    When a fresh direct IMAP connection to "gupta-gmail-de" asks for CAPABILITY before and after LOGIN
    Then the CAPABILITY before LOGIN lists neither "MOVE" nor "UIDPLUS"
    And the CAPABILITY after LOGIN lists both "MOVE" and "UIDPLUS"

  Scenario: update_draft on canonical [Gmail]/Drafts leaves one message in localized wire Drafts
    Given policy "de-policy" grants folder:
      | folder         | mode      | default | rules | draft_append | draft_edit |
      | [Gmail]/Drafts | blacklist | FULL    | []    | true         | true       |
    And draft-agent calls create_draft with account "gupta-gmail-de", folder "[Gmail]/Drafts", rfc822 payload:
      """
      From: draft-agent@example.org
      To: mara.lindqvist@example.org
      Subject: Angebot Fruehjahr
      MIME-Version: 1.0
      Content-Type: text/plain; charset=UTF-8

      Anbei unser Angebot.
      """
    When draft-agent calls update_draft with account "gupta-gmail-de", folder "[Gmail]/Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Angebot Fruehjahr (korrigiert)"}
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field revision equals 2
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for header X-SC-Draft-Id equal to the returned draft_id returns exactly one result
    And the message carrying the returned draft_id in "gupta-gmail-de:[Gmail]/Entw&APw-rfe" has the headers:
      | header         | value                          |
      | X-SC-Draft-Rev | 2                              |
      | Subject        | Angebot Fruehjahr (korrigiert) |
    And the IMAP command log for "gupta-gmail-de" contains no command addressing the mailbox "[Gmail]/Entwürfe"
    And the IMAP command log for "gupta-gmail-de" contains no command addressing the mailbox "[Gmail]/Drafts"

  Scenario: discard_draft on canonical [Gmail]/Drafts moves every authentic revision to localized Trash
    Given policy "de-policy" grants folder:
      | folder         | mode      | default  | rules | draft_append | draft_edit | move_out | accept_incoming |
      | [Gmail]/Drafts | blacklist | FULL     | []    | true         | true       | true     | false           |
      | [Gmail]/Trash  | blacklist | METADATA | []    | false        | false      | false    | true            |
    And draft-agent creates a draft in "gupta-gmail-de:[Gmail]/Drafts" with subject "Liefertermin (alt)" and body "Woche 14"
    And an interrupted revision leaves both authentic revision 1 ("Liefertermin (alt)") and authentic revision 2 ("Liefertermin (aktuell)") for that draft_id in "gupta-gmail-de:[Gmail]/Drafts" with no active WAL entry
    When draft-agent calls discard_draft with account "gupta-gmail-de", folder "[Gmail]/Drafts", the returned draft_id
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field discarded_count equals 2
    And the response field moved_to equals "[Gmail]/Trash"
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for header X-SC-Draft-Id equal to the returned draft_id returns zero results
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Papierkorb" for header X-SC-Draft-Id equal to the returned draft_id returns 2 results
    And the mock-gmail server answered every X-GM-LABELS STORE from the server for "gupta-gmail-de" with status "OK"

  Scenario Outline: Moving from INBOX to <target> sends the system label as an atom and leaves the message in <wire> only
    Given policy "de-policy" grants folder:
      | folder   | mode      | default  | rules | move_out | accept_incoming |
      | INBOX    | blacklist | FULL     | []    | true     | false           |
      | <target> | blacklist | METADATA | []    | false    | true            |
    And a Gmail message on "gupta-gmail-de" has:
      | x_gm_msgid   | message_id           | from_address              | to_address     | subject       | date                 |
      | <x_gm_msgid> | <m-move@example.org> | werbung@nordlicht.example | me@example.org | Sonderangebot | 2026-11-01T09:00:00Z |
    And the Gmail message <x_gm_msgid> on "gupta-gmail-de" carries labels ["INBOX"] with UID 412 in "INBOX" and UID <x_gm_msgid> in "[Gmail]/All Mail"
    When draft-agent calls move with source {"account": "gupta-gmail-de", "folder": "INBOX", "uid": 412}, target {"account": "gupta-gmail-de", "folder": "<target>"}
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mechanism equals "gmail_label_swap"
    And a direct IMAP SEARCH on "gupta-gmail-de:INBOX" for X-GM-MSGID <x_gm_msgid> returns zero results
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Alle Nachrichten" for X-GM-MSGID <x_gm_msgid> returns zero results
    And a direct IMAP SEARCH on "gupta-gmail-de:<wire>" for X-GM-MSGID <x_gm_msgid> returns exactly one result
    And the mock-gmail server answered every X-GM-LABELS STORE from the server for "gupta-gmail-de" with status "OK"
    And the mock-gmail server received the label "<label>" in an X-GM-LABELS STORE "+" from the server for "gupta-gmail-de"

    Examples:
      | target        | wire               | label  | x_gm_msgid |
      | [Gmail]/Trash | [Gmail]/Papierkorb | \Trash | 30010      |
      | [Gmail]/Spam  | [Gmail]/Spam       | \Spam  | 30011      |

  Scenario: Moving a draft to a user label removes it from localized Drafts
    Given policy "de-policy" grants folder:
      | folder         | mode      | default | rules | move_out | accept_incoming |
      | [Gmail]/Drafts | blacklist | FULL    | []    | true     | false           |
      | Angebote       | blacklist | FULL    | []    | false    | true            |
    And a Gmail message on "gupta-gmail-de" has:
      | x_gm_msgid | message_id            | from_address            | to_address     | subject        | date                 |
      | 30020      | <d-30020@example.org> | draft-agent@example.org | me@example.org | Angebot Sommer | 2026-11-01T09:00:00Z |
    And the Gmail message 30020 on "gupta-gmail-de" carries labels ["\\Drafts"] with UID 320 in "[Gmail]/Drafts" and UID 30020 in "[Gmail]/All Mail"
    When draft-agent calls move with source {"account": "gupta-gmail-de", "folder": "[Gmail]/Drafts", "uid": 320}, target {"account": "gupta-gmail-de", "folder": "Angebote"}
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mechanism equals "gmail_label_swap"
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for X-GM-MSGID 30020 returns zero results
    And a direct IMAP SEARCH on "gupta-gmail-de:Angebote" for X-GM-MSGID 30020 returns exactly one result
