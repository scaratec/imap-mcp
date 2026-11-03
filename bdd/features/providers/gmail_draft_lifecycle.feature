Feature: Drafts are revised, discarded and moved on a localized Gmail account as Gmail really behaves

  ADR 0019, 0030, 0031, 0033, 0034.

  Release 2.2.0 passed every scenario of this suite and still failed
  the acceptance test against a real Gmail account in German. On
  [Gmail]/Drafts, update_draft answered rewrite_failed with detail
  source_delete_failed and left two revisions behind, and discard_draft
  answered transient_imap_failure, "label swap failed after 0 of 2
  message(s)". Raw IMAP against the same account showed why:

    - Gmail answers `UID STORE <uid> +X-GM-LABELS ("\Trash")` with
      `BAD Could not parse command`. Inside an IMAP quoted string a
      backslash escapes the next character (RFC 3501 §4.3), so the
      label has to travel as the flag atom `\Trash`. Every label swap
      that names a system label failed the same way, a plain move out
      of INBOX included (it removes `\Inbox`).
    - The drafts folder is `[Gmail]/Entw&APw-rfe` on the wire. The
      delete step of the rewrite selected it under its UTF-8 name,
      which Gmail does not know.
    - A draft carries the flags `\Draft \Seen` and `X-GM-LABELS ()`:
      Drafts is not a label. STORE of `\Drafts` is answered OK and
      changes nothing. Adding `\Trash` takes the message out of every
      other mailbox at once.
    - With Gmail's default auto-expunge, a message marked `\Deleted`
      leaves the selected label mailbox immediately.
    - MOVE and UIDPLUS are advertised only after authentication.

  The mock had been more forgiving than Gmail on each point, which is
  why the suite stayed green (Guideline §7.2). The first scenarios here
  pin the mock to the observed behaviour; the rest specify the tools
  against it.

  What is at stake. An agent that revises a draft leaves the owner with
  two near-identical drafts; an agent that discards one is told it
  cannot, and the draft stays. Both on every German Gmail account.

  Independent-channel rule. Every Then about where a message ended up
  is a direct IMAP SEARCH or FETCH against the mock, under the wire
  names Gmail uses. Every Then about what was sent reads the command
  log or the label-STORE log the mock records with its own parse
  result; neither depends on the server's code.

  Covered error layers (per BDD Guidelines §4.5):
    - Mock fidelity: quoted system label refused, atom accepted     : 1
    - Mock fidelity: a draft is \Draft and carries no Drafts label  : 1
    - Mock fidelity: \Deleted leaves a label mailbox at once        : 1
    - Mock fidelity: MOVE and UIDPLUS only after authentication     : 1
    - update_draft on the localized drafts folder keeps one message : 1
    - discard_draft moves every revision to the localized trash     : 1
    - Move INBOX to a system-label folder, label sent as an atom    : 2 (Trash, Spam)
    - Move of a draft to a user label leaves the drafts folder      : 1
    Total enumerated                                                : 9   covered by this feature: 9

  Background:
    Given the IMAP account "gupta-gmail-de" exists with provider "google" and folders:
      | folder path      |
      | INBOX            |
      | Angebote         |
      | [Gmail]/All Mail |
      | [Gmail]/Drafts   |
      | [Gmail]/Spam     |
      | [Gmail]/Trash    |
    And the mock-gmail server uses localized folder names:
      | canonical        | localized                | flags                  |
      | [Gmail]/All Mail | [Gmail]/Alle Nachrichten | \All \HasNoChildren    |
      | [Gmail]/Drafts   | [Gmail]/Entw&APw-rfe     | \Drafts \HasNoChildren |
      | [Gmail]/Trash    | [Gmail]/Papierkorb       | \Trash \HasNoChildren  |
    And the server is configured with caller "draft-agent" using policy "de-policy"
    And policy "de-policy" grants account "gupta-gmail-de"

  # --- Mock fidelity ---------------------------------------------------

  Scenario: The mock refuses a system label in a quoted string with a bare backslash and accepts the atom
    Given a Gmail message with:
      | x_gm_msgid | message_id            | from                     | subject      |
      | 30001      | <m-30001@example.org> | anfrage@nordlicht.example | Anfrage 2026 |
    And the message carries Gmail labels ["INBOX"]
    And the message has UID 401 under "INBOX", UID 30001 under "[Gmail]/All Mail"
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

  Scenario: A draft on the mock carries the \Draft flag and no Drafts label, and STORE of \Drafts changes nothing
    Given a Gmail message with:
      | x_gm_msgid | message_id            | from                    | subject        |
      | 30002      | <d-30002@example.org> | draft-agent@example.org | Entwurf Termin |
    And the message carries Gmail labels ["\\Drafts"]
    And the message has UID 302 under "[Gmail]/Drafts", UID 30002 under "[Gmail]/All Mail"
    Then a direct IMAP FETCH of uid 302 in "gupta-gmail-de:[Gmail]/Entw&APw-rfe" shows the flag "\Draft"
    And a direct IMAP FETCH of uid 302 in "gupta-gmail-de:[Gmail]/Entw&APw-rfe" shows X-GM-LABELS "()"
    When a direct IMAP command is issued in "[Gmail]/Entw&APw-rfe" on "gupta-gmail-de":
      """
      UID STORE 302 -X-GM-LABELS (\Drafts)
      """
    Then the direct IMAP command is answered with status "OK"
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for X-GM-MSGID 30002 returns exactly one result

  Scenario: A message marked \Deleted leaves the drafts mailbox at once, without EXPUNGE
    Given a Gmail message with:
      | x_gm_msgid | message_id            | from                    | subject        |
      | 30003      | <d-30003@example.org> | draft-agent@example.org | Entwurf Preise |
    And the message carries Gmail labels ["\\Drafts"]
    And the message has UID 303 under "[Gmail]/Drafts", UID 30003 under "[Gmail]/All Mail"
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

  # --- update_draft ----------------------------------------------------

  Scenario: update_draft on the canonical Drafts path leaves exactly one message, the new revision
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

  # --- discard_draft ---------------------------------------------------

  Scenario: discard_draft on the canonical Drafts path moves every revision to the localized trash
    Given policy "de-policy" grants folder:
      | folder         | mode      | default  | rules | move_out | accept_incoming |
      | [Gmail]/Drafts | blacklist | FULL     | []    | true     | false           |
      | [Gmail]/Trash  | blacklist | METADATA | []    | false    | true            |
    And the IMAP folder "[Gmail]/Entw&APw-rfe" on "gupta-gmail-de" holds drafts carrying the handle "6512bd43-d9ca-4e8d-9f3a-1b2c3d4e5f60":
      | uid | revision | subject                |
      | 81  | 1        | Liefertermin (alt)     |
      | 82  | 2        | Liefertermin (aktuell) |
    When draft-agent calls discard_draft with account "gupta-gmail-de", folder "[Gmail]/Drafts", draft_id "6512bd43-d9ca-4e8d-9f3a-1b2c3d4e5f60"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field discarded_count equals 2
    And the response field moved_to equals "[Gmail]/Trash"
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for header X-SC-Draft-Id "6512bd43-d9ca-4e8d-9f3a-1b2c3d4e5f60" returns zero results
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Papierkorb" for header X-SC-Draft-Id "6512bd43-d9ca-4e8d-9f3a-1b2c3d4e5f60" returns exactly 2 results
    And the mock-gmail server answered every X-GM-LABELS STORE from the server for "gupta-gmail-de" with status "OK"

  # --- move to a system label ------------------------------------------

  Scenario Outline: A move from INBOX to <target> sends the system label as an atom and leaves the message in <wire> only
    Given policy "de-policy" grants folder:
      | folder   | mode      | default  | rules | move_out | accept_incoming |
      | INBOX    | blacklist | FULL     | []    | true     | false           |
      | <target> | blacklist | METADATA | []    | false    | true            |
    And a Gmail message with:
      | x_gm_msgid   | message_id              | from                      | subject       |
      | <x_gm_msgid> | <m-move@example.org>    | werbung@nordlicht.example | Sonderangebot |
    And the message carries Gmail labels ["INBOX"]
    And the message has UID 412 under "INBOX", UID <x_gm_msgid> under "[Gmail]/All Mail"
    When draft-agent calls move with account "gupta-gmail-de", source folder "INBOX" uid 412, target folder "<target>"
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

  # --- move a draft to a user label ------------------------------------

  Scenario: A draft moved to a user label leaves the drafts folder, although Drafts is not a label
    Given policy "de-policy" grants folder:
      | folder         | mode      | default | rules | move_out | accept_incoming |
      | [Gmail]/Drafts | blacklist | FULL    | []    | true     | false           |
      | Angebote       | blacklist | FULL    | []    | false    | true            |
    And a Gmail message with:
      | x_gm_msgid | message_id            | from                    | subject          |
      | 30020      | <d-30020@example.org> | draft-agent@example.org | Angebot Sommer   |
    And the message carries Gmail labels ["\\Drafts"]
    And the message has UID 320 under "[Gmail]/Drafts", UID 30020 under "[Gmail]/All Mail"
    When draft-agent calls move with account "gupta-gmail-de", source folder "[Gmail]/Drafts" uid 320, target folder "Angebote"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mechanism equals "gmail_label_swap"
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for X-GM-MSGID 30020 returns zero results
    And a direct IMAP SEARCH on "gupta-gmail-de:Angebote" for X-GM-MSGID 30020 returns exactly one result
