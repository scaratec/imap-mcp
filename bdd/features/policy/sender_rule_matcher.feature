Feature: Sender rule matcher grammar

  Sender rules combine one or more predicates from the V1 core grammar
  (from, from_domain, to, to_contains, subject_contains, has_attachment,
  newer_than, older_than, size_gt, size_lt). Predicates in one rule are
  AND-combined; multiple rules are OR-combined across a folder.
  See ADR 0004.

  Covered error layers (per BDD Guidelines §4.5):
    - Predicate match/no-match  : 10 (one per predicate, happy + miss)
    - Combination semantics     : 3  (AND within rule, OR across rules, mix)
    - Grammar rejection         : 4  (subject_regex, header_matches, body_contains, free-form key)
    - Normalization             : 3  (case, trailing dot in domain, NFC in subject)
    Total enumerated            : 20  covered by this feature: 20

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"
    And policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | whitelist | NONE    |

  Scenario Outline: The predicate <predicate> matches the expected message and skips others
    Given policy "inbox-policy" sets folder "INBOX" rules to:
      | match                | grant    |
      | <match_key>=<value>  | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                    | to                         | subject                   | has_attachment | size_bytes | date                |
      | 401 | alice@example.com       | me@gupta-scaratec.com      | Project update            | false          | 1024       | 2026-04-20T09:00:00Z |
      | 402 | bob@hornbach.de         | me@gupta-scaratec.com      | Rechnung 42               | true           | 48000      | 2026-04-15T09:00:00Z |
      | 403 | newsletter@shop.example | friends@gupta-scaratec.com | Grosse Aktion             | false          | 2048       | 2026-01-01T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "365d"}
    Then the response field uids contains exactly <matching_uids>

    Examples:
      | predicate        | match_key         | value                       | matching_uids |
      | from             | from              | bob@hornbach.de             | [402]         |
      | from_domain      | from_domain       | hornbach.de                 | [402]         |
      | to               | to                | me@gupta-scaratec.com       | [401, 402]    |
      | to_contains      | to_contains       | friends                     | [403]         |
      | subject_contains | subject_contains  | rechnung                    | [402]         |
      | has_attachment   | has_attachment    | true                        | [402]         |
      | newer_than       | newer_than        | 30d                         | [401, 402]    |
      | older_than       | older_than        | 30d                         | [403]         |
      | size_gt          | size_gt           | 10000                       | [402]         |
      | size_lt          | size_lt           | 1500                        | [401]         |

  Scenario: Predicates within one rule are AND-combined
    Given policy "inbox-policy" sets folder "INBOX" rules to:
      | match                                              | grant    |
      | from_domain=hornbach.de AND has_attachment=true    | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                | has_attachment |
      | 410 | rechnung@hornbach.de| true           |
      | 411 | info@hornbach.de    | false          |
      | 412 | other@example.com   | true           |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field uids contains exactly [410]

  Scenario: Multiple rules in one folder are OR-combined
    Given policy "inbox-policy" sets folder "INBOX" rules to:
      | match                       | grant    |
      | from_domain=hornbach.de     | ENVELOPE |
      | from_domain=obi.de          | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                 |
      | 420 | rechnung@hornbach.de |
      | 421 | service@obi.de       |
      | 422 | spam@example.net     |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field uids contains exactly [420, 421]

  Scenario: from_domain matches regardless of trailing dot and letter case
    Given policy "inbox-policy" sets folder "INBOX" rules to:
      | match                       | grant    |
      | from_domain=hornbach.de     | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                          |
      | 430 | RECHNUNG@HORNBACH.DE          |
      | 431 | info@hornbach.de.             |
      | 432 | sales@hornbach.de             |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field uids contains exactly [430, 431, 432]

  Scenario: subject_contains matches case-insensitively after NFC normalization
    Given policy "inbox-policy" sets folder "INBOX" rules to:
      | match                                | grant    |
      | subject_contains=rechnung            | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | subject                          |
      | 440 | RECHNUNG 0001                    |
      | 441 | Große Rechnung                   |
      | 442 | Newsletter                       |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field uids contains exactly [440, 441]

  Scenario Outline: The policy loader rejects predicates that are not in the V1 core grammar
    Given the server loads a policy file containing:
      """
      policies:
        inbox-policy:
          accounts:
            gupta-scaratec:
              folders:
                - path: INBOX
                  mode: whitelist
                  default: NONE
                  rules:
                    - match: { <invalid_key>: "<value>" }
                      grant: ENVELOPE
      """
    Then the server refuses to start
    And the startup error indicates the rule predicate "<invalid_key>" as "not in V1 core grammar"

    Examples:
      | invalid_key     | value         |
      | subject_regex   | ^Rechnung.*$  |
      | header_matches  | X-Spam: *     |
      | body_contains   | dringend      |
      | label           | Rechnungen    |
