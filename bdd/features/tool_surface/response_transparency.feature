Feature: Response transparency for policy-filtered data

  Every tool response carries transparency fields so that an LLM
  caller does not construct false conclusions from a silently
  filtered view. The vocabulary is categorical and never exposes
  rule identifiers or the names of hidden objects.
  See ADR 0017.

  Covered error layers (per BDD Guidelines §4.5):
    - Hidden counts (list/search/folder)    : 3
    - Redaction reason codes                 : 6 (categorical vocabulary)
    - describe_policy own-profile disclosure : 1
    - describe_policy no-leak rules          : 1 (no rule patterns, no hidden names)
    - Per-field redacted_fields flags        : 1
    Total enumerated                          : 12   covered by this feature: 12

  Background:
    Given the IMAP account "gupta-scaratec" exists with folders:
      | folder path      |
      | INBOX/Rechnungen |
      | Banking          |
      | Archiv           |
    And the IMAP account "osthues-mail" exists with folder "INBOX"
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec" and folder:
      | folder           | mode      | default | rules                                  |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> ENVELOPE}]|

  Scenario: list_accounts exposes hidden_accounts_count without naming the hidden accounts
    When invoice-agent calls list_accounts
    Then the visible account ids equal ["gupta-scaratec"]
    And the response field hidden_accounts_count equals 1
    And the response does not contain any field named "hidden_accounts"
    And the response does not contain any field naming "osthues-mail"

  Scenario: list_folders exposes hidden_folders_count per account
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response field folders contains exactly ["INBOX/Rechnungen"]
    And the response field hidden_folders_count equals 3
    And the response does not contain any field naming "Banking" or "Archiv"

  Scenario: search exposes matched_total, matched_visible, filtered_out
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from                     | subject       |
      | 601 | rechnung@hornbach.de     | Rechnung A    |
      | 602 | invoice@obi.de           | Rechnung B    |
      | 603 | billing@bauhaus.info     | Rechnung C    |
      | 604 | rechnung@hornbach.de     | Rechnung D    |
    When invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}
    Then the response field matched_total equals 4
    And the response field matched_visible equals 2
    And the response field filtered_out equals 2
    And the response field uids contains exactly [601, 604]

  Scenario Outline: DENY responses carry a categorical reason code without revealing rule details
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                     |
      | 610 | noreply@example.com      |
    When invoice-agent calls <tool_call>
    Then the response decision is DENY
    And the response field reason equals "<reason_code>"
    And the response does not contain any field named "matched_rule"
    And the response does not contain any field named "rule_pattern"

    Examples:
      | tool_call                                                                                                                | reason_code              |
      | fetch_envelope with account "gupta-scaratec", folder "Banking", uid 1                                                    | folder_hidden            |
      | fetch_envelope with account "osthues-mail", folder "INBOX", uid 1                                                        | account_hidden           |
      | fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 610                                         | sender_not_whitelisted   |
      | fetch_body with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 610                                             | sender_not_whitelisted   |
      | mark_seen with account "gupta-scaratec", folder "Banking", uid 1, seen true                                              | folder_hidden            |
      | move with source {"account":"gupta-scaratec","folder":"INBOX/Rechnungen","uid":9999}, target {"account":"gupta-scaratec","folder":"Banking"} | capability_missing       |

  Scenario: Partial-content ALLOW responses flag redacted fields and carry a redaction_reason
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                   | subject      |
      | 620 | rechnung@hornbach.de   | Rechnung 42  |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 620
    Then the response decision is ALLOW
    And the response field visibility_applied equals "ENVELOPE"
    And the response field body equals null
    And the response field attachments equals null
    And the response field redacted_fields contains "body"
    And the response field redacted_fields contains "attachments"
    And the response field redaction_reason equals "visibility_below_BODY"

  Scenario: describe_policy exposes the caller's own profile
    When invoice-agent calls describe_policy
    Then the response field caller_id equals "invoice-agent"
    And the response field tool_set_version matches the regex "^1\.\d+\.\d+$"
    And the response field accounts contains exactly one entry with:
      | field                   | value                     |
      | id                      | gupta-scaratec            |
      | semantics               | imap-standard             |
    And the accounts[0].folders_visible contains exactly one entry with:
      | field                   | value                     |
      | path                    | INBOX/Rechnungen          |
      | mode                    | whitelist                 |
      | default_visibility      | NONE                      |
      | max_visibility          | ENVELOPE                  |
      | sender_rules_count      | 1                         |
    And accounts[0].hidden_folders_count equals 3
    And the response field hidden_accounts_count equals 1

  Scenario: describe_policy never discloses rule patterns, hidden folder names, or other callers
    When invoice-agent calls describe_policy
    Then the JSON response does NOT contain the literal strings:
      | forbidden_string |
      | hornbach.de      |
      | Banking          |
      | Archiv           |
      | osthues-mail     |
      | overview-agent   |
