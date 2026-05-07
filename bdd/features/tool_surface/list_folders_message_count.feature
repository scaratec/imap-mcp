Feature: list_folders includes per-folder message counts

  When an LLM agent explores a mailbox it needs to know the size of
  each folder before issuing a search. list_folders now returns a
  message_count for every visible folder so that the agent can decide
  whether to narrow its search criteria.
  See ADR 0016.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path with populated folders   : 1
    - Empty folder returns count 0        : 1
    - Hidden folders omit counts          : 1
    - Multiple accounts, independent cnts : 1
    Total enumerated                       : 4   covered by this feature: 4

  Background:
    Given the IMAP account "gupta-scaratec" exists with folders:
      | folder path      |
      | INBOX/Rechnungen |
      | Banking          |
      | Archiv           |
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec" and folder:
      | folder           | mode      | default  | rules                                   |
      | INBOX/Rechnungen | whitelist | NONE     | [{from_domain=hornbach.de -> ENVELOPE}]  |

  Scenario: Visible folders carry their message count
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from                   | subject      |
      | 101 | rechnung@hornbach.de   | Rechnung A   |
      | 102 | rechnung@hornbach.de   | Rechnung B   |
      | 103 | info@hornbach.de       | Rechnung C   |
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the folder entry "INBOX/Rechnungen" has message_count 3

  Scenario: An empty visible folder reports message_count 0
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the folder entry "INBOX/Rechnungen" has message_count 0

  Scenario: Hidden folders do not appear in the response and their counts are not disclosed
    Given the folder "Banking" holds messages:
      | uid | from                   | subject     |
      | 201 | bank@sparkasse.de      | Kontoauszug |
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response does not contain any field naming "Banking"
    And the response field hidden_folders_count equals 3

  Scenario: Counts are independent across accounts
    Given the IMAP account "personal" exists with folder "INBOX"
    And policy "invoice-policy" grants account "personal" and folder:
      | folder | mode      | default | rules                                   |
      | INBOX  | whitelist | NONE    | [{from_domain=example.com -> ENVELOPE}] |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from                   | subject      |
      | 301 | rechnung@hornbach.de   | Rechnung X   |
    And the folder "personal:INBOX" holds messages:
      | uid | from                   | subject      |
      | 401 | friend@example.com     | Hallo        |
      | 402 | friend@example.com     | Hallo 2      |
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the folder entry "INBOX/Rechnungen" has message_count 1
    When invoice-agent calls list_folders with account "personal"
    Then the folder entry "INBOX" has message_count 2
