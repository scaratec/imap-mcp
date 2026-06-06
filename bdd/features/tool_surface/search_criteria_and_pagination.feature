Feature: Search criteria pre-filtering, scope, and pagination

  The search tool translates MCP criteria into IMAP SEARCH terms before
  fetching UIDs, reducing the working set on large mailboxes. The scope
  argument controls the implicit time window; criteria control the
  per-message filter. Results are paginated via limit/offset.
  See ADR 0004, ADR 0024 (duration grammar), ADR 0026 (scope), LIM-0011.

  Covered error layers (per BDD Guidelines §4.5):
    - IMAP pre-filter per V2 predicate            : 11
    - Duration unit coverage (s/m/h/d/w/y)        : 6
    - Duration schema rejection (bad unit, empty) : 2
    - scope=recent default + explicit window      : 3
    - scope=all suppresses implicit window        : 2
    - applied_scope field always present          : 3
    - Pagination limit / offset / has_more        : 3
    - Pagination beyond result set                : 1
    - Combined criteria + pagination              : 1
    - PDP still filters after pre-filter          : 1
    Total enumerated                              : 33   covered by this feature: 33

  Background:
    Given the server date is pinned to "2026-05-07"
    And the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"
    And policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | whitelist | NONE     |
    And policy "inbox-policy" sets folder "INBOX" rules to:
      | match                    | grant    |
      | from_domain=hornbach.de  | ENVELOPE |
      | from_domain=obi.de       | ENVELOPE |

  # --- IMAP pre-filter: one scenario per V2 predicate ---

  Scenario: from criteria narrows IMAP search to matching sender
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject      | date                 |
      | 101 | rechnung@hornbach.de   | Rechnung A   | 2026-05-06T09:00:00Z |
      | 102 | service@obi.de         | Bestellung   | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"from": "rechnung@hornbach.de"}
    Then the response field uids contains exactly [101]
    And the response field matched_visible equals 1

  Scenario: from_domain criteria narrows IMAP search to matching domain
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject      | date                 |
      | 201 | rechnung@hornbach.de   | Rechnung A   | 2026-05-06T09:00:00Z |
      | 202 | info@hornbach.de       | Info         | 2026-05-06T09:00:00Z |
      | 203 | service@obi.de         | Bestellung   | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "hornbach.de"}
    Then the response field uids contains exactly [201, 202]

  Scenario: to criteria narrows IMAP search to matching recipient
    Given the folder "INBOX" holds messages:
      | uid | from                   | to                          | subject    | date                 |
      | 301 | rechnung@hornbach.de   | me@gupta-scaratec.com       | Rechnung A | 2026-05-06T09:00:00Z |
      | 302 | rechnung@hornbach.de   | office@gupta-scaratec.com   | Rechnung B | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"to": "me@gupta-scaratec.com"}
    Then the response field uids contains exactly [301]

  Scenario: to_contains criteria narrows IMAP search to substring in recipient
    Given the folder "INBOX" holds messages:
      | uid | from                   | to                          | subject    | date                 |
      | 401 | rechnung@hornbach.de   | me@gupta-scaratec.com       | Rechnung A | 2026-05-06T09:00:00Z |
      | 402 | rechnung@hornbach.de   | friends@gupta-scaratec.com  | Rechnung B | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"to_contains": "friends"}
    Then the response field uids contains exactly [402]

  Scenario: subject_contains criteria narrows IMAP search to subject substring
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject          | date                 |
      | 501 | rechnung@hornbach.de   | Rechnung 42      | 2026-05-06T09:00:00Z |
      | 502 | rechnung@hornbach.de   | Newsletter       | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"subject_contains": "Rechnung"}
    Then the response field uids contains exactly [501]

  Scenario: newer_than criteria narrows IMAP search via SINCE
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject      | date                 |
      | 601 | rechnung@hornbach.de   | Neue         | 2026-05-06T09:00:00Z |
      | 602 | rechnung@hornbach.de   | Alte         | 2025-01-15T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "30d"}
    Then the response field uids contains exactly [601]
    And the response field applied_scope equals "explicit_window"

  Scenario: older_than criteria narrows IMAP search via BEFORE
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject      | date                 |
      | 701 | rechnung@hornbach.de   | Neue         | 2026-05-06T09:00:00Z |
      | 702 | rechnung@hornbach.de   | Alte         | 2025-01-15T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"older_than": "30d"}
    Then the response field uids contains exactly [702]
    And the response field applied_scope equals "explicit_window"

  Scenario: size_gt criteria narrows IMAP search via LARGER
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject   | size_bytes | date                 |
      | 801 | rechnung@hornbach.de   | Klein     | 1024       | 2026-05-06T09:00:00Z |
      | 802 | rechnung@hornbach.de   | Gross     | 48000      | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"size_gt": 10000}
    Then the response field uids contains exactly [802]

  Scenario: size_lt criteria narrows IMAP search via SMALLER
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject   | size_bytes | date                 |
      | 901 | rechnung@hornbach.de   | Klein     | 1024       | 2026-05-06T09:00:00Z |
      | 902 | rechnung@hornbach.de   | Gross     | 48000      | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"size_lt": 2000}
    Then the response field uids contains exactly [901]

  Scenario: has_attachment criteria narrows IMAP search heuristically
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject   | has_attachment | date                 |
      | 1001| rechnung@hornbach.de   | Mit Datei | true           | 2026-05-06T09:00:00Z |
      | 1002| rechnung@hornbach.de   | Ohne      | false          | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"has_attachment": true}
    Then the response field uids contains exactly [1001]

  Scenario: flagged criteria narrows IMAP search via FLAGGED
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject     | flagged | date                 |
      | 1011| rechnung@hornbach.de   | Markiert    | true    | 2026-05-06T09:00:00Z |
      | 1012| rechnung@hornbach.de   | Unmarkiert  | false   | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"flagged": true}, scope "all"
    Then the response field uids contains exactly [1011]

  # --- Duration unit coverage: every unit of ADR 0024's grammar reaches the server ---

  Scenario Outline: every duration unit is honoured by newer_than
    Given the folder "INBOX" holds messages:
      | uid     | from                   | subject | date                 |
      | <uid_a> | rechnung@hornbach.de   | InWin   | <date_in_window>     |
      | <uid_b> | rechnung@hornbach.de   | OutWin  | <date_outside>       |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "<duration>"}
    Then the response field uids contains exactly [<uid_a>]
    And the response field applied_scope equals "explicit_window"

    Examples:
      | uid_a | uid_b | duration | date_in_window       | date_outside         |
      | 2001  | 2002  | 60s      | 2026-05-07T11:59:30Z | 2026-05-06T09:00:00Z |
      | 2011  | 2012  | 30m      | 2026-05-07T11:45:00Z | 2026-05-06T09:00:00Z |
      | 2021  | 2022  | 6h       | 2026-05-07T08:00:00Z | 2026-05-05T09:00:00Z |
      | 2031  | 2032  | 7d       | 2026-05-05T09:00:00Z | 2026-04-15T09:00:00Z |
      | 2041  | 2042  | 2w       | 2026-04-25T09:00:00Z | 2026-03-01T09:00:00Z |
      | 2051  | 2052  | 1y       | 2025-09-01T09:00:00Z | 2024-01-15T09:00:00Z |

  # --- Duration schema rejection: malformed strings fail at the JSON-RPC layer, not in the handler ---

  Scenario: malformed duration is rejected with JSON-RPC -32602
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "10x"}
    Then the server responds with JSON-RPC error code -32602
    And the response error message contains "newer_than"

  Scenario: empty duration string is rejected with JSON-RPC -32602
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": ""}
    Then the server responds with JSON-RPC error code -32602
    And the response error message contains "newer_than"

  # --- scope: recent (default) ---

  Scenario: scope defaults to recent and applies the 7-day window
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject   | date                 |
      | 1101| rechnung@hornbach.de   | Heute     | 2026-05-06T09:00:00Z |
      | 1102| rechnung@hornbach.de   | Letzte Wo | 2026-05-02T09:00:00Z |
      | 1103| rechnung@hornbach.de   | Alt       | 2025-01-15T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field uids contains exactly [1101, 1102]
    And the response field matched_visible equals 2
    And the response field applied_scope equals "recent_7d"

  Scenario: scope=recent with an explicit newer_than honours the explicit window, not the 7-day default
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject   | date                 |
      | 1111| rechnung@hornbach.de   | 1 Tag     | 2026-05-06T09:00:00Z |
      | 1112| rechnung@hornbach.de   | 60 Tage   | 2026-03-15T09:00:00Z |
      | 1113| rechnung@hornbach.de   | 2 Jahre   | 2024-05-01T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "90d"}, scope "recent"
    Then the response field uids contains exactly [1111, 1112]
    And the response field applied_scope equals "explicit_window"

  Scenario: scope=recent with a non-time predicate still applies the 7-day window
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject     | date                 |
      | 1121| rechnung@hornbach.de   | Recent      | 2026-05-06T09:00:00Z |
      | 1122| rechnung@hornbach.de   | Old         | 2025-01-15T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "hornbach.de"}
    Then the response field uids contains exactly [1121]
    And the response field applied_scope equals "recent_7d"

  # --- scope: all ---

  Scenario: scope=all with empty criteria returns every visible message in the folder
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject   | date                 |
      | 1131| rechnung@hornbach.de   | Heute     | 2026-05-06T09:00:00Z |
      | 1132| rechnung@hornbach.de   | Letzte Wo | 2026-05-02T09:00:00Z |
      | 1133| rechnung@hornbach.de   | Alt       | 2025-01-15T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids contains exactly [1131, 1132, 1133]
    And the response field matched_visible equals 3
    And the response field applied_scope equals "all_time"

  Scenario: scope=all with flagged predicate returns every starred message regardless of date
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject   | flagged | date                 |
      | 1141| rechnung@hornbach.de   | Stern alt | true    | 2024-09-01T09:00:00Z |
      | 1142| rechnung@hornbach.de   | Stern neu | true    | 2026-05-06T09:00:00Z |
      | 1143| rechnung@hornbach.de   | Kein Ster | false   | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"flagged": true}, scope "all"
    Then the response field uids contains exactly [1141, 1142]
    And the response field applied_scope equals "all_time"

  # --- Pagination ---

  Scenario: limit restricts the number of returned UIDs
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject      | date                 |
      | 1301| rechnung@hornbach.de   | Rechnung 1   | 2026-05-06T09:00:00Z |
      | 1302| rechnung@hornbach.de   | Rechnung 2   | 2026-05-05T09:00:00Z |
      | 1303| rechnung@hornbach.de   | Rechnung 3   | 2026-05-04T09:00:00Z |
      | 1304| rechnung@hornbach.de   | Rechnung 4   | 2026-05-03T09:00:00Z |
      | 1305| rechnung@hornbach.de   | Rechnung 5   | 2026-05-02T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, limit 3
    Then the response field uids has length 3
    And the response field page_limit equals 3
    And the response field has_more equals true

  Scenario: offset skips the first N visible results
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject      | date                 |
      | 1401| rechnung@hornbach.de   | Rechnung 1   | 2026-05-06T09:00:00Z |
      | 1402| rechnung@hornbach.de   | Rechnung 2   | 2026-05-05T09:00:00Z |
      | 1403| rechnung@hornbach.de   | Rechnung 3   | 2026-05-04T09:00:00Z |
      | 1404| rechnung@hornbach.de   | Rechnung 4   | 2026-05-03T09:00:00Z |
      | 1405| rechnung@hornbach.de   | Rechnung 5   | 2026-05-02T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, limit 2, offset 2
    Then the response field uids contains exactly [1403, 1404]
    And the response field page_offset equals 2
    And the response field has_more equals true

  Scenario: Pagination metadata reflects total visible count
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject      | date                 |
      | 1501| rechnung@hornbach.de   | Rechnung 1   | 2026-05-06T09:00:00Z |
      | 1502| rechnung@hornbach.de   | Rechnung 2   | 2026-05-05T09:00:00Z |
      | 1503| rechnung@hornbach.de   | Rechnung 3   | 2026-05-04T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, limit 2
    Then the response field matched_visible equals 3
    And the response field page_limit equals 2
    And the response field page_offset equals 0
    And the response field has_more equals true

  Scenario: Offset beyond result set returns empty UIDs and has_more false
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject      | date                 |
      | 1601| rechnung@hornbach.de   | Rechnung 1   | 2026-05-06T09:00:00Z |
      | 1602| rechnung@hornbach.de   | Rechnung 2   | 2026-05-05T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, limit 50, offset 100
    Then the response field uids equals []
    And the response field has_more equals false
    And the response field matched_visible equals 2

  Scenario: Criteria combined with pagination returns the correct page
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject      | date                 |
      | 1701| rechnung@hornbach.de   | Rechnung 1   | 2026-05-06T09:00:00Z |
      | 1702| rechnung@hornbach.de   | Rechnung 2   | 2026-05-05T09:00:00Z |
      | 1703| rechnung@hornbach.de   | Rechnung 3   | 2026-05-04T09:00:00Z |
      | 1704| service@obi.de         | Bestellung   | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "hornbach.de"}, limit 2, offset 1
    Then the response field uids contains exactly [1702, 1703]
    And the response field matched_visible equals 3
    And the response field has_more equals false

  # --- PDP still applies after pre-filter ---

  Scenario: PDP sender-rule filtering applies after IMAP pre-filter
    Given the folder "INBOX" holds messages:
      | uid | from                   | subject      | date                 |
      | 1801| rechnung@hornbach.de   | Rechnung A   | 2026-05-06T09:00:00Z |
      | 1802| spam@example.net       | Rechnung B   | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"subject_contains": "Rechnung"}
    Then the response field uids contains exactly [1801]
    And the response field matched_visible equals 1
    And the response field filtered_out equals 1
