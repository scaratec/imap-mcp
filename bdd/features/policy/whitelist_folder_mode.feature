Feature: Whitelist folder mode

  A folder declared with mode=whitelist has default=NONE and grants
  higher visibility only through explicit sender rules. Effective level
  is max(default, max(grant-of-matching-rules)). See ADR 0003.

  Covered error layers (per BDD Guidelines §4.5):
    - Policy validation    : 3 (default mismatch, rule operator mismatch, unreachable rule)
    - Authorization        : 4 (no rule / one rule / two overlapping / competing grants)
    Total enumerated       : 7  covered by this feature: 7

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Rechnungen"
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"

  Scenario: Message from a whitelisted sender is visible at the granted level
    Given policy "invoice-policy" grants folder:
      | folder            | mode      | default | rules                                         |
      | INBOX/Rechnungen  | whitelist | NONE    | [{from_domain=hornbach.de -> FULL}]           |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from                   | subject             |
      | 201 | rechnung@hornbach.de   | Rechnung A          |
      | 202 | marketing@example.com  | Newsletter          |
    When invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}
    Then the response field uids equals [201]
    And the response field matched_total equals 2
    And the response field matched_visible equals 1
    And the response field filtered_out equals 1

  Scenario: Message from a non-whitelisted sender stays at default NONE
    Given policy "invoice-policy" grants folder:
      | folder            | mode      | default | rules                                         |
      | INBOX/Rechnungen  | whitelist | NONE    | [{from_domain=hornbach.de -> FULL}]           |
    And the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                   | subject             |
      | 210 | noreply@stripe.com     | Payment receipt     |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 210
    Then the response decision is DENY
    And the response field reason equals "sender_not_whitelisted"

  Scenario: Two overlapping whitelist rules grant the maximum level
    Given policy "invoice-policy" grants folder:
      | folder            | mode      | default | rules                                                                               |
      | INBOX/Rechnungen  | whitelist | NONE    | [{from_domain=hornbach.de -> ENVELOPE}, {from=rechnung@hornbach.de -> FULL}]        |
    And the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                   | subject             |
      | 220 | rechnung@hornbach.de   | Rechnung B          |
    When invoice-agent calls fetch_body with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 220
    Then the response decision is ALLOW
    And the response field visibility_applied equals "FULL"
    And the response field matched_rule_index is 1

  Scenario: Policy loader rejects a whitelist folder with non-NONE default
    Given the server loads a policy file containing:
      """
      callers:
        - id: invoice-agent
          policy: invoice-policy
      policies:
        invoice-policy:
          accounts:
            gupta-scaratec:
              folders:
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
      policies:
        invoice-policy:
          accounts:
            gupta-scaratec:
              folders:
                - path: INBOX/Rechnungen
                  mode: whitelist
                  default: NONE
                  rules:
                    - match: { from_domain: bank.de }
                      cap: NONE
      """
    Then the server refuses to start
    And the startup error indicates the rule as "whitelist mode forbids 'cap'; use 'grant'"

  Scenario: Policy loader rejects a whitelist rule that grants NONE (unreachable)
    Given the server loads a policy file containing:
      """
      policies:
        invoice-policy:
          accounts:
            gupta-scaratec:
              folders:
                - path: INBOX/Rechnungen
                  mode: whitelist
                  default: NONE
                  rules:
                    - match: { from_domain: example.com }
                      grant: NONE
      """
    Then the server refuses to start
    And the startup error indicates the rule as "grant: NONE in whitelist is unreachable (equals default)"

  Scenario: Whitelist search does not reveal the presence of non-matching messages beyond the aggregate count
    Given policy "invoice-policy" grants folder:
      | folder            | mode      | default | rules                                         |
      | INBOX/Rechnungen  | whitelist | NONE    | [{from_domain=hornbach.de -> FULL}]           |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from                   | subject             |
      | 231 | rechnung@hornbach.de   | Rechnung C          |
      | 232 | invoice@obi.de         | Rechnung D          |
      | 233 | billing@bauhaus.info   | Rechnung E          |
    When invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}
    Then the response field uids equals [231]
    And the response field filtered_out equals 2
    And the response does not include any field named "hidden_from"
    And the response does not include any field named "hidden_subjects"
