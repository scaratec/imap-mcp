Feature: Folder-path quoting, canonical Gmail alias resolution, and three-code folder error taxonomy

  ADR 0044 §2 guarantees:
    1. Folder paths containing spaces, dashes, or non-ASCII characters
       round-trip cleanly via mUTF-7 + RFC 3501 quoting.
    2. Canonical Gmail special-use paths ([Gmail]/Drafts) resolve to
       localized wire mailboxes ([Gmail]/Entw&APw-rfe).
    3. Folder failures partition cleanly into:
         - DENY reason="folder_hidden" (identical whether the folder
           exists on IMAP or not)
         - ALLOW + ERROR error.type="folder_absent" (authorized, missing)
         - ALLOW + ERROR error.type="select_failed" (authorized, SELECT NO/BAD)

  Covered error layers (per BDD Guidelines §4.5):
    - Quoting: folder with spaces and dash                  : 1
    - Quoting: folder with non-ASCII umlaut                 : 1
    - Aliasing: canonical [Gmail]/Drafts -> localized wire  : 1
    - folder_hidden DENY for ungranted folder               : 1
    - folder_absent ERROR for missing authorized folder     : 1
    - select_failed ERROR when IMAP SELECT returns NO       : 1
    - folder_hidden identical for existing vs non-existent  : 1
    Total enumerated                                        : 7   covered by this feature: 7

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path                           |
      | INBOX                                 |
      | INBOX/BuHa - privat offene Rechnungen |
      | Posteingang/Übersicht                 |
      | Existing/But/Hidden                   |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"
    And policy "doc-policy" grants folder on account "gupta-scaratec":
      | folder                                | mode      | default | rules |
      | INBOX                                 | blacklist | COUNT   | []    |
      | INBOX/BuHa - privat offene Rechnungen | blacklist | COUNT   | []    |
      | Posteingang/Übersicht                 | blacklist | COUNT   | []    |

  Scenario: folder_stats works on a folder path containing spaces and a dash
    Given the folder "INBOX/BuHa - privat offene Rechnungen" holds messages:
      | uid | from_address      | to_address            | subject | date                 |
      | 101 | a@example.org     | me@gupta-scaratec.com | A       | 2026-05-06T09:00:00Z |
      | 102 | b@example.org     | me@gupta-scaratec.com | B       | 2026-05-06T09:00:00Z |
      | 103 | c@example.org     | me@gupta-scaratec.com | C       | 2026-05-06T09:00:00Z |
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "INBOX/BuHa - privat offene Rechnungen"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field visible_count equals 3

  Scenario: folder_stats works on a folder path containing non-ASCII characters
    Given the folder "Posteingang/Übersicht" holds messages:
      | uid | from_address      | to_address            | subject | date                 |
      | 201 | a@example.org     | me@gupta-scaratec.com | A       | 2026-05-06T09:00:00Z |
      | 202 | b@example.org     | me@gupta-scaratec.com | B       | 2026-05-06T09:00:00Z |
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "Posteingang/Übersicht"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field visible_count equals 2

  Scenario: Callers address localized Gmail system folders by their canonical path
    Given the IMAP account "gupta-scaratec-google" exists with provider "google" and folders:
      | folder path      |
      | INBOX            |
      | [Gmail]/All Mail |
      | [Gmail]/Drafts   |
    And the mock-gmail server for "gupta-scaratec-google" uses localized folder names:
      | canonical        | localized                | flags                  |
      | [Gmail]/All Mail | [Gmail]/Alle Nachrichten | \All \HasNoChildren    |
      | [Gmail]/Drafts   | [Gmail]/Entw&APw-rfe     | \Drafts \HasNoChildren |
    And policy "doc-policy" grants account "gupta-scaratec-google"
    And policy "doc-policy" grants folder on account "gupta-scaratec-google":
      | folder         | mode      | default | rules |
      | [Gmail]/Drafts | blacklist | COUNT   | []    |
    And the folder "gupta-scaratec-google:[Gmail]/Drafts" holds messages:
      | uid | from_address      | to_address     | subject   | date                 |
      | 301 | me@example.org    | b@example.org  | Entwurf 1 | 2026-05-06T09:00:00Z |
    When doc-agent calls folder_stats with account "gupta-scaratec-google", folder "[Gmail]/Drafts"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field visible_count equals 1

  Scenario: folder_hidden DENY for a folder outside the caller's policy
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "Forbidden/Folder"
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"
    And the response does not contain any field named "error"

  Scenario: folder_absent ERROR when the caller is authorized but the folder does not exist on IMAP
    Given policy "doc-policy" grants folder on account "gupta-scaratec":
      | folder                            | mode      | default | rules |
      | INBOX/Typo - this does not exist  | blacklist | COUNT   | []    |
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "INBOX/Typo - this does not exist"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field reason equals "folder_default_applied"
    And the response field error.type equals "folder_absent"

  Scenario: select_failed ERROR when IMAP SELECT or EXAMINE returns NO on an existing authorized folder
    Given the IMAP proxy for "gupta-scaratec" rejects the next SELECT or EXAMINE of "INBOX/BuHa - privat offene Rechnungen" with "NO [INUSE] Mailbox is locked"
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "INBOX/BuHa - privat offene Rechnungen"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "select_failed"
    And the response field error.detail contains "[INUSE]"

  Scenario: folder_hidden returns the exact same response shape whether the ungranted folder exists on IMAP or not
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "Existing/But/Hidden"
    And the JSON response is remembered as "existing_hidden_resp"
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "Made/Up/Name"
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"
    And the JSON response has the exact same keys and values except "folder" as "existing_hidden_resp"
