Feature: Blacklist folder mode

  A folder declared with mode=blacklist has a non-NONE default and
  cap rules that reduce the effective level for specific senders.
  Effective level is min(default, min(cap-of-matching-rules)).
  See ADR 0003.

  Covered error layers (per BDD Guidelines §4.5):
    - Policy validation    : 2 (NONE default mismatch, grant in blacklist)
    - Authorization        : 4 (no rule / one cap / cap to NONE / overlapping caps)
    Total enumerated       : 6  covered by this feature: 6

  Background:
    Given the IMAP account "personal" exists with folder "INBOX"
    And the server is configured with caller "overview-agent" using policy "overview-policy"
    And policy "overview-policy" grants account "personal"

  Scenario: Message without any matching cap rule keeps the folder default
    Given policy "overview-policy" grants folder:
      | folder | mode      | default   | rules |
      | INBOX  | blacklist | ENVELOPE  | []    |
    And the folder "INBOX" holds a message with:
      | uid | from                   | subject             |
      | 301 | friend@example.com     | Birthday plans      |
    When overview-agent calls fetch_envelope with account "personal", folder "INBOX", uid 301
    Then the response decision is ALLOW
    And the response field visibility_applied equals "ENVELOPE"
    And the response includes field from with value "friend@example.com"
    And the response includes field subject with value "Birthday plans"

  Scenario: A cap reduces the effective level for a matching sender
    Given policy "overview-policy" grants folder:
      | folder | mode      | default   | rules                                        |
      | INBOX  | blacklist | ENVELOPE  | [{from_domain=bank.de -> cap METADATA}]      |
    And the folder "INBOX" holds a message with:
      | uid | from                  | subject             |
      | 310 | noreply@bank.de       | Kontoauszug         |
    When overview-agent calls fetch_envelope with account "personal", folder "INBOX", uid 310
    Then the response decision is DENY
    And the response field reason equals "visibility_below_ENVELOPE"
    When overview-agent calls folder_stats with account "personal", folder "INBOX"
    Then the response decision is ALLOW
    And the response field visibility_level equals "ENVELOPE"

  Scenario: A cap of NONE makes a matching message fully invisible (sender_blacklisted)
    Given policy "overview-policy" grants folder:
      | folder | mode      | default   | rules                                     |
      | INBOX  | blacklist | ENVELOPE  | [{from_domain=bank.de -> cap NONE}]       |
    And the folder "INBOX" holds messages:
      | uid | from                  | subject             |
      | 320 | noreply@bank.de       | Kontoauszug         |
      | 321 | friend@example.com    | Hello               |
    When overview-agent calls search with account "personal", folder "INBOX", criteria {}
    Then the response field uids equals [321]
    And the response field matched_total equals 2
    And the response field matched_visible equals 1
    And the response field filtered_out equals 1
    When overview-agent calls fetch_envelope with account "personal", folder "INBOX", uid 320
    Then the response decision is DENY
    And the response field reason equals "sender_blacklisted"

  Scenario: Multiple overlapping caps apply the strictest
    Given policy "overview-policy" grants folder:
      | folder | mode      | default   | rules                                                                    |
      | INBOX  | blacklist | ENVELOPE  | [{from_domain=bank.de -> cap METADATA}, {subject_contains=VERTRAULICH -> cap NONE}] |
    And the folder "INBOX" holds a message with:
      | uid | from                  | subject                        |
      | 330 | noreply@bank.de       | [VERTRAULICH] Neues Konto      |
    When overview-agent calls folder_stats with account "personal", folder "INBOX"
    Then the response field visibility_level equals "ENVELOPE"
    When overview-agent calls fetch_envelope with account "personal", folder "INBOX", uid 330
    Then the response decision is DENY
    And the response field reason equals "sender_blacklisted"

  Scenario: Policy loader rejects a blacklist folder with default NONE
    Given the server loads a policy file containing:
      """
      policies:
        overview-policy:
          accounts:
            personal:
              folders:
                - path: INBOX
                  mode: blacklist
                  default: NONE
                  rules: []
      """
    Then the server refuses to start
    And the startup error indicates the folder "INBOX" as "blacklist mode requires default > NONE"

  Scenario: Policy loader rejects grant rules in a blacklist folder
    Given the server loads a policy file containing:
      """
      policies:
        overview-policy:
          accounts:
            personal:
              folders:
                - path: INBOX
                  mode: blacklist
                  default: ENVELOPE
                  rules:
                    - match: { from_domain: friend.example }
                      grant: FULL
      """
    Then the server refuses to start
    And the startup error indicates the rule as "blacklist mode forbids 'grant'; use 'cap'"

  Scenario: Audit distinguishes sender_blacklisted from sender_not_whitelisted
    Given policy "overview-policy" grants folder:
      | folder | mode      | default   | rules                                     |
      | INBOX  | blacklist | ENVELOPE  | [{from_domain=bank.de -> cap NONE}]       |
    And the folder "INBOX" holds a message with:
      | uid | from                  | subject             |
      | 340 | noreply@bank.de       | Statement           |
    When overview-agent calls fetch_envelope with account "personal", folder "INBOX", uid 340
    Then the audit log contains an entry with:
      | field           | value                |
      | caller_id       | overview-agent       |
      | tool            | fetch_envelope       |
      | decision        | DENY                 |
      | reason          | sender_blacklisted   |
    And the audit entry does not contain the field "from" with any cleartext value
