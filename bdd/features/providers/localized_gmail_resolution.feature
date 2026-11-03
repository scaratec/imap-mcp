@pending
Feature: Gmail special-use folders resolve on every path, from the first call on

  ADR 0033.

  A Gmail account in German shows its system folders under localized
  wire names ([Gmail]/Entwürfe, [Gmail]/Papierkorb, [Gmail]/Alle
  Nachrichten). Policies, tool descriptions and list_folders speak the
  canonical names ([Gmail]/Drafts, [Gmail]/Trash, [Gmail]/All Mail).
  ADR 0016 promises an intent-level surface, so the caller must not have
  to know which language the mailbox owner chose.

  What is at stake. Today the canonical name only works after
  list_folders has filled a per-process alias cache, and the Gmail
  label-swap move never consults that cache at all. On a German account
  an agent that discards a draft by moving "[Gmail]/Drafts" to
  "[Gmail]/Trash" gets result ERROR with error_type "uid_not_found" and
  concludes that drafts cannot be deleted. Policies carry every system
  folder twice as a workaround, and two entries for one physical folder
  can disagree. Production evidence: audit log 2026-05-19 to 2026-10-31,
  9 failed moves from [Gmail]/Drafts, 12 failed create_draft calls on
  [Gmail]/Drafts, all on gupta@scaratec.com (German locale).

  Independent-channel rule. Every Then that claims a folder was
  addressed reads the IMAP command log the mock records (it never sees
  the server's alias map) or a direct IMAP SEARCH against the mock. The
  first scenario pins the mock itself: it must reject the English name
  on a localized account, as Gmail does, otherwise every other scenario
  here could pass against a mock that is more forgiving than production
  (Guideline §7.2).

  Covered error layers (per BDD Guidelines §4.5):
    - Mock fidelity: English name refused on localized account     : 1
    - First call in a session uses a canonical special-use path   : 3 (read tools)
    - First call in a session writes to a canonical path          : 1
    - Label-swap move across canonical/localized name forms       : 3
    - Unselectable source folder reported as folder_absent        : 1
    - Cross-account saga resolves All Mail on a localized account : 1
    - Canonical-only policy governs the localized name            : 1
    - Localized-only policy entry governs the canonical name      : 1
    - Conflicting entries for one special-use folder fail closed   : 1
    - Responses echo the canonical name                            : 1
    Total enumerated                                               : 14  covered by this feature: 14

  Background:
    Given the IMAP account "gupta-gmail-de" exists with provider "google" and folders:
      | folder path       |
      | INBOX             |
      | [Gmail]/All Mail  |
      | [Gmail]/Drafts    |
      | [Gmail]/Sent Mail |
      | [Gmail]/Trash     |
    And the mock-gmail server uses localized folder names:
      | canonical         | localized                | flags                  |
      | [Gmail]/All Mail  | [Gmail]/Alle Nachrichten | \All \HasNoChildren    |
      | [Gmail]/Drafts    | [Gmail]/Entw&APw-rfe     | \Drafts \HasNoChildren |
      | [Gmail]/Sent Mail | [Gmail]/Gesendet         | \Sent \HasNoChildren   |
      | [Gmail]/Trash     | [Gmail]/Papierkorb       | \Trash \HasNoChildren  |
    And the server is configured with caller "draft-agent" using policy "de-policy"
    And policy "de-policy" grants account "gupta-gmail-de"

  # --- Mock fidelity ---------------------------------------------------

  Scenario: The localized mock refuses the English name, as Gmail does
    When a direct IMAP SELECT of "[Gmail]/Drafts" is issued against "gupta-gmail-de"
    Then the direct IMAP command is answered with status "NO"
    When a direct IMAP SELECT of "[Gmail]/Entw&APw-rfe" is issued against "gupta-gmail-de"
    Then the direct IMAP command is answered with status "OK"

  # --- Main path: no list_folders before the first call ----------------

  Scenario Outline: A read tool resolves a canonical special-use path as the first call of a session
    Given policy "de-policy" grants folder:
      | folder    | mode      | default  | rules |
      | <folder>  | blacklist | ENVELOPE | []    |
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

  Scenario: create_draft on the canonical Drafts path lands in the localized folder as the first call
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
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for subject "Angebot Fruehjahr" returns exactly one result
    And the IMAP command log for "gupta-gmail-de" contains no command addressing the mailbox "[Gmail]/Drafts"

  # --- Discarding a draft: the label-swap move -------------------------

  Scenario Outline: A draft moves to the trash on a localized account whichever name form the caller uses
    Given policy "de-policy" grants folder:
      | folder         | mode      | default  | rules | move_out | accept_incoming |
      | [Gmail]/Drafts | blacklist | FULL     | []    | true     | false           |
      | [Gmail]/Trash  | blacklist | METADATA | []    | false    | true            |
    And a Gmail message with:
      | x_gm_msgid | message_id               | from                    | subject          |
      | 20001      | <d-20001@example.org>    | draft-agent@example.org | Entwurf Angebot  |
    And the message carries Gmail labels ["\\Drafts"]
    And the message has UID 301 under "[Gmail]/Drafts", UID 20001 under "[Gmail]/All Mail"
    When draft-agent calls move with account "gupta-gmail-de", source folder "<source>" uid 301, target folder "<target>"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mechanism equals "gmail_label_swap"
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for X-GM-MSGID 20001 returns zero results
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Papierkorb" for X-GM-MSGID 20001 returns exactly one result

    Examples:
      | source               | target              |
      | [Gmail]/Drafts       | [Gmail]/Trash       |
      | [Gmail]/Entwürfe     | [Gmail]/Papierkorb  |
      | [Gmail]/Drafts       | [Gmail]/Papierkorb  |

  # --- Error path: the source folder cannot be selected ----------------

  Scenario: A move whose source folder does not exist reports folder_absent, never uid_not_found
    # Today the label-swap branch maps every SELECT failure to
    # uid_not_found, which tells the caller its UID is wrong when in fact
    # the folder name is. ADR 0025 defines folder_absent for this case.
    Given policy "de-policy" grants folder:
      | folder           | mode      | default  | rules | move_out | accept_incoming |
      | [Gmail]/Archiv19 | blacklist | FULL     | []    | true     | false           |
      | [Gmail]/Trash    | blacklist | METADATA | []    | false    | true            |
    When draft-agent calls move with account "gupta-gmail-de", source folder "[Gmail]/Archiv19" uid 301, target folder "[Gmail]/Trash"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error_type equals "folder_absent"
    And the IMAP command log for "gupta-gmail-de" contains no STORE command

  # --- Cross-account saga ----------------------------------------------

  Scenario: A cross-account move from a localized account fetches from the localized All Mail folder
    Given the IMAP account "archive-srv" exists with provider "imap-standard" and folder "Archiv"
    And policy "de-policy" grants account "archive-srv"
    And policy "de-policy" grants folder:
      | folder | mode      | default | rules | move_out | accept_incoming |
      | INBOX  | blacklist | FULL    | []    | true     | false           |
    And policy "de-policy" grants folder on account "archive-srv":
      | folder | mode      | default | rules | accept_incoming |
      | Archiv | blacklist | FULL    | []    | true            |
    And a Gmail message with:
      | x_gm_msgid | message_id             | from                       | subject         |
      | 20002      | <m-20002@example.org>  | rechnung@nordlicht.example.org | Rechnung 4711 |
    And the message has UID 512 under "INBOX", UID 20002 under "[Gmail]/All Mail"
    When draft-agent calls move with source {"account":"gupta-gmail-de","folder":"INBOX","uid":512}, target {"account":"archive-srv","folder":"Archiv"}
    Then the transaction reaches state committed within 60 seconds
    And the IMAP command log for "gupta-gmail-de" contains a SELECT or EXAMINE of the wire mailbox "[Gmail]/Alle Nachrichten"
    And a direct IMAP SEARCH on "archive-srv:Archiv" for message-id "<m-20002@example.org>" returns exactly one result

  # --- Policy addressing: one physical folder, one policy entry --------

  Scenario: A policy written only in canonical names governs the localized name
    Given policy "de-policy" grants folder:
      | folder         | mode      | default | rules | draft_append |
      | [Gmail]/Drafts | blacklist | FULL    | []    | true         |
    When draft-agent calls list_drafts with account "gupta-gmail-de", folder "[Gmail]/Entwürfe"
    Then the response decision is ALLOW
    And the IMAP command log for "gupta-gmail-de" contains a SELECT or EXAMINE of the wire mailbox "[Gmail]/Entw&APw-rfe"

  Scenario: A policy entry under the localized name governs the canonical name
    # Existing deployments list both names; this keeps a policy that only
    # lists the localized name working after the change.
    Given policy "de-policy" grants folder:
      | folder             | mode      | default  | rules |
      | [Gmail]/Papierkorb | blacklist | METADATA | []    |
    When draft-agent calls list_messages with account "gupta-gmail-de", folder "[Gmail]/Trash"
    Then the response decision is ALLOW
    And the IMAP command log for "gupta-gmail-de" contains a SELECT or EXAMINE of the wire mailbox "[Gmail]/Papierkorb"

  Scenario: Two policy entries for the same special-use folder that disagree fail closed
    # ADR 0033 adds the reason code policy_alias_conflict to ADR 0017.
    # Fail closed because either entry could be the one the operator
    # meant; picking the more permissive would be a policy bypass.
    Given policy "de-policy" grants folder:
      | folder             | mode      | default | rules |
      | [Gmail]/Trash      | whitelist | NONE    | []    |
      | [Gmail]/Papierkorb | blacklist | FULL    | []    |
    When draft-agent calls list_messages with account "gupta-gmail-de", folder "[Gmail]/Papierkorb"
    Then the response decision is DENY
    And the response field reason equals "policy_alias_conflict"
    And the audit log contains an entry with:
      | field    | value                 |
      | tool     | list_messages         |
      | decision | DENY                  |
      | reason   | policy_alias_conflict |
    And the IMAP command log for "gupta-gmail-de" contains no SELECT or EXAMINE of the wire mailbox "[Gmail]/Papierkorb"

  Scenario: Responses name a special-use folder by its canonical path
    # ADR 0033 amends ADR 0025. list_folders already answers in
    # canonical names; echoing the same vocabulary everywhere teaches a
    # caller one set of names instead of two.
    Given policy "de-policy" grants folder:
      | folder         | mode      | default | rules | draft_append |
      | [Gmail]/Drafts | blacklist | FULL    | []    | true         |
    When draft-agent calls list_drafts with account "gupta-gmail-de", folder "[Gmail]/Entwürfe"
    Then the response decision is ALLOW
    And the response field folder equals "[Gmail]/Drafts"
