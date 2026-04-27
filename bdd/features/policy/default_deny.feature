Feature: Default-deny hierarchical policy

  The Policy Decision Point evaluates every MCP tool call at three
  levels (Account -> Folder -> SenderRule) and refuses access whenever
  no explicit rule authorizes it. See ADR 0001.

  Covered error layers (per BDD Guidelines §4.5):
    - Input validation           : 0  (these are authz failures, not input errors)
    - Authorization hierarchy    : 6  (3 levels x {absent-rule, present-rule})
    - Response processing        : 0
    - Data integrity             : 0
    - Protocol                   : 0
    Total enumerated             : 6     covered by this feature: 6

  Background:
    Given the IMAP account "gupta-scaratec" exists with folders:
      | folder path        |
      | INBOX              |
      | INBOX/Rechnungen   |
      | Banking            |
      | Archiv             |
    And the IMAP account "osthues-mail" exists with folders:
      | folder path        |
      | INBOX              |
    And the server is configured with caller:
      | caller_id     | policy             |
      | invoice-agent | invoice-policy     |
    And policy "invoice-policy" grants account access:
      | account        |
      | gupta-scaratec |
    And policy "invoice-policy" grants folder access:
      | account        | folder            | mode      | default |
      | gupta-scaratec | INBOX/Rechnungen  | whitelist | NONE    |
    And policy "invoice-policy" has sender rules:
      | folder            | match                       | grant |
      | INBOX/Rechnungen  | from_domain=hornbach.de     | FULL  |

  Scenario: An account without AccountPolicy is invisible
    When invoice-agent calls list_accounts
    Then the response field accounts equals ["gupta-scaratec"]
    And the response field hidden_accounts_count equals 1

  Scenario: A folder without FolderPolicy is invisible within a granted account
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response field folders equals ["INBOX/Rechnungen"]
    And the response field hidden_folders_count equals 3

  Scenario: Fetching from a hidden folder is denied with folder_hidden
    Given the folder "Banking" holds a message with:
      | uid | from                 | subject    |
      | 501 | noreply@bank.de      | Kontoauszug|
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "Banking", uid 501
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"

  Scenario: Fetching from an entirely unknown account is denied with account_hidden
    Given the folder "INBOX" of "osthues-mail" holds a message with:
      | uid | from             | subject    |
      | 10  | client@firma.de  | Angebot    |
    When invoice-agent calls fetch_envelope with account "osthues-mail", folder "INBOX", uid 10
    Then the response decision is DENY
    And the response field reason equals "account_hidden"

  Scenario: A sender with no matching whitelist rule is invisible even inside a granted folder
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                     | subject        |
      | 77  | marketing@example.com    | Newsletter     |
    When invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {"from_domain": "example.com"}
    Then the response field matched_total equals 1
    And the response field matched_visible equals 0
    And the response field filtered_out equals 1

  Scenario: A sender with a matching whitelist rule is visible at the granted level
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                     | subject             |
      | 88  | rechnung@hornbach.de     | Rechnung 7823       |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 88
    Then the response decision is ALLOW
    And the response field reason equals "rule_matched"
    And the response field visibility_applied equals "FULL"
    And the response field subject equals "Rechnung 7823"
