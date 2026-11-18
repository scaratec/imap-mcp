Feature: Whitelist folder mode

  A folder declared with mode=whitelist has default=NONE and grants
  higher visibility only through explicit rules. Effective single-folder
  level is max(NONE, max(grant-of-matching-rules)). Non-matching messages
  remain at NONE, returning opaque not_found on direct UID access and
  never leaking rule indices or hidden counts (ADR 0040, ADR 0043, ADR 0044).

  Covered error layers (per BDD Guidelines §4.5):
    - Policy validation : 4 (YAML syntax error, non-NONE default, cap rule in whitelist, grant NONE)
    - Authorization     : 5 (matching grant, non-matching -> not_found, overlapping grants in both rule orders, search non-interference)
    Total enumerated    : 9   covered by this feature: 9

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path      |
      | INBOX            |
      | INBOX/Rechnungen |
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"

  Scenario: Message from a whitelisted sender is visible at the granted level
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules                               |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> FULL}] |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address          | to_address            | subject    | date                 |
      | 201 | rechnung@hornbach.de  | me@gupta-scaratec.com | Rechnung A | 2026-05-06T09:00:00Z |
      | 202 | marketing@example.com | me@gupta-scaratec.com | Newsletter | 2026-05-06T09:00:00Z |
    When invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the response field uids equals [201]
    And the response field total_visible equals 1
    And the response field returned_count equals 1
    And the response field completeness equals "policy_may_hide"
    And the response does not contain any field named "matched_total"
    And the response does not contain any field named "filtered_out"

  Scenario: Message from a non-whitelisted sender returns opaque not_found on direct fetch
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules                               |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> FULL}] |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address       | to_address            | subject         | date                 |
      | 210 | noreply@stripe.com | me@gupta-scaratec.com | Payment receipt | 2026-05-06T09:00:00Z |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 210
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    And the audit log contains an entry with:
      | field               | value                 |
      | caller_id           | invoice-agent         |
      | tool                | fetch_envelope        |
      | result              | ERROR                 |
      | audit_detail_reason | message_not_granted   |

  Scenario Outline: Two overlapping whitelist rules grant the maximum level in order <order> and omit matched_rule_index
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules   |
      | INBOX/Rechnungen | whitelist | NONE    | <rules> |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject    | date                 |
      | 220 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung B | 2026-05-06T09:00:00Z |
    And the message at uid 220 has plain text body "Rechnungstext B"
    When invoice-agent calls fetch_body with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 220
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field visibility_applied equals "FULL"
    And the response field text_body equals "Rechnungstext B"
    And the response does not contain any field named "matched_rule_index"

    Examples:
      | order          | rules                                                                                |
      | FULL first     | [{from_address=rechnung@hornbach.de -> FULL}, {from_domain=hornbach.de -> ENVELOPE}] |
      | ENVELOPE first | [{from_domain=hornbach.de -> ENVELOPE}, {from_address=rechnung@hornbach.de -> FULL}] |

  Scenario: Policy loader rejects malformed YAML syntax at startup
    Given the server loads a policy file containing:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          - path: INBOX/Rechnungen
            mode: whitelist  default: NONE
      """
    Then the server refuses to start
    And the startup error indicates "YAML"

  Scenario: Policy loader rejects a whitelist folder with non-NONE default
    Given the server loads a policy file containing:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          - path: INBOX/Rechnungen
            mode: whitelist
            default: ENVELOPE
            rules: []
      """
    Then the server refuses to start
    And the startup error indicates policy "invoice-policy" folder "INBOX/Rechnungen" as "whitelist mode requires default=NONE"

  Scenario: Policy loader rejects cap rules in a whitelist folder
    Given the server loads a policy file containing:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          - path: INBOX/Rechnungen
            mode: whitelist
            default: NONE
            rules:
              - match: { from_domain: bank.de }
                cap: NONE
      """
    Then the server refuses to start
    And the startup error indicates the rule as "whitelist mode forbids 'cap'; use 'grant'"

  Scenario: Policy loader rejects a whitelist rule that grants NONE
    Given the server loads a policy file containing:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          - path: INBOX/Rechnungen
            mode: whitelist
            default: NONE
            rules:
              - match: { from_domain: example.com }
                grant: NONE
      """
    Then the server refuses to start
    And the startup error indicates the rule as "grant: NONE in whitelist is unreachable (equals default)"

  Scenario: Whitelist search exposes neither hidden message counts nor hidden sender details
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules                               |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> FULL}] |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject    | date                 |
      | 231 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung C | 2026-05-06T09:00:00Z |
      | 232 | invoice@obi.de       | me@gupta-scaratec.com | Rechnung D | 2026-05-06T09:00:00Z |
      | 233 | billing@bauhaus.info | me@gupta-scaratec.com | Rechnung E | 2026-05-06T09:00:00Z |
    When invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    Then the response field uids equals [231]
    And the response field total_visible equals 1
    And the JSON response does NOT contain the literal strings:
      | forbidden_string |
      | obi.de           |
      | bauhaus.info     |
      | Rechnung D       |
      | Rechnung E       |
    And the audit log contains an entry with tool "search", decision "ALLOW", suppressed_by_policy true
