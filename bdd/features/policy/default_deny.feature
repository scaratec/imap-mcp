Feature: Default-deny hierarchical policy

  The Policy Decision Point evaluates every MCP tool call across three
  levels (AccountPolicy -> FolderPolicy -> MessageRule) and refuses
  access whenever no explicit grant authorizes it. Objects at visibility
  NONE are non-interfering: they neither appear in listings nor alter
  any caller-visible count or error shape (ADR 0040, ADR 0043, ADR 0044).

  Covered error layers (per BDD Guidelines §4.5):
    - Unlisted account omitted from list_accounts, no hidden count : 1
    - Unlisted folder omitted from list_folders, no hidden count   : 1
    - Read on unlisted folder denied with folder_hidden            : 1
    - Read on unlisted account denied with account_hidden          : 1
    - Non-matching whitelist message invisible in search           : 1
    - Matching whitelist message visible at granted level          : 1
    Total enumerated                                               : 6   covered by this feature: 6

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path      |
      | INBOX            |
      | INBOX/Rechnungen |
      | Banking          |
      | Archiv           |
    And the IMAP account "osthues-mail" exists with folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules                                    |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> FULL}]      |

  Scenario: An account without AccountPolicy is omitted without disclosing a hidden count
    When invoice-agent calls list_accounts
    Then the response decision is ALLOW
    And the visible account ids equal ["gupta-scaratec"]
    And the response does not contain any field named "hidden_accounts_count"
    And the JSON response does NOT contain the literal strings:
      | forbidden_string |
      | osthues-mail     |

  Scenario: A folder without FolderPolicy is omitted without disclosing a hidden count
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response decision is ALLOW
    And the response field folders equals ["INBOX/Rechnungen"]
    And the response does not contain any field named "hidden_folders_count"
    And the JSON response does NOT contain the literal strings:
      | forbidden_string |
      | Banking          |
      | Archiv           |

  Scenario: Fetching from a hidden folder is denied with folder_hidden before mailbox selection
    Given the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the folder "Banking" holds a message with:
      | uid | from_address    | to_address            | subject     | date                 |
      | 501 | noreply@bank.de | me@gupta-scaratec.com | Kontoauszug | 2026-05-06T09:00:00Z |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "Banking", uid 501
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"
    And the IMAP command log for "gupta-scaratec" contains no SELECT command

  Scenario: Fetching from an unlisted account is denied with account_hidden
    Given the folder "INBOX" of "osthues-mail" holds a message with:
      | uid | from_address    | to_address          | subject | date                 |
      | 10  | client@firma.de | info@osthues.local  | Angebot | 2026-05-06T09:00:00Z |
    When invoice-agent calls fetch_envelope with account "osthues-mail", folder "INBOX", uid 10
    Then the response decision is DENY
    And the response field reason equals "account_hidden"

  Scenario: A sender with no matching whitelist rule is completely invisible in search
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address          | to_address            | subject    | date                 |
      | 77  | marketing@example.com | me@gupta-scaratec.com | Newsletter | 2026-05-06T09:00:00Z |
    When invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {"from_domain": "example.com"}, scope "all"
    Then the response decision is ALLOW
    And the response field uids equals []
    And the response field total_visible equals 0
    And the response field returned_count equals 0
    And the response field completeness equals "policy_may_hide"
    And the response does not contain any field named "matched_total"
    And the response does not contain any field named "filtered_out"

  Scenario: A sender with a matching whitelist rule is visible at the granted level
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject       | date                 |
      | 88  | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7823 | 2026-05-06T09:00:00Z |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 88
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field reason equals "rule_matched"
    And the response field visibility_applied equals "FULL"
    And the response field subject equals "Rechnung 7823"
