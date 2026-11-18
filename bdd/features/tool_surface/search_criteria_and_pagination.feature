Feature: Search criteria pre-filtering, exact UTC post-filtering, scope, and deterministic pagination

  ADR 0041 and ADR 0043 specify the search contract:
    - Closed 3.0.0 criteria grammar evaluated on parsed RFC 5322
      addresses, Unicode-NFC subjects, MIME attachment structure (BODY
      floor), \Flagged, provenance="own", sizes, and UTC INTERNALDATE.
    - Sub-day duration windows (such as older_than="60s" on same-day mail)
      combine IMAP UID SEARCH pre-filtering with exact UTC post-filtering.
    - Results are ordered by ascending UID and report total_visible,
      returned_count, page_limit, page_offset, has_more, applied_scope,
      and static completeness (never matched_total or filtered_out).

  Covered error layers (per BDD Guidelines §4.5):
    - 3.0 criteria predicates + proxy wire SEARCH witness    : 12
    - Duration units & exact boundaries on newer_than & older_than : 8
    - Duration schema rejection (bad unit, empty, >36500d)   : 3
    - scope=recent default vs explicit window vs scope=all   : 5
    - Ascending UID pagination (limit, offset, beyond end)   : 5
    - Non-interfering PDP post-filter after IMAP pre-filter  : 1
    Total enumerated                                         : 34  covered by this feature: 34

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"
    And policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | whitelist | NONE    |
    And policy "inbox-policy" sets folder "INBOX" rules to:
      | match                   | grant |
      | from_domain=hornbach.de | BODY  |
      | from_domain=obi.de      | BODY  |

  Scenario Outline: Criterion <name> filters visible messages accurately while issuing IMAP UID SEARCH
    Given the folder "INBOX" holds messages:
      | uid | from_address         | to_address                | cc_header             | subject       | size_bytes | flags      | date                 |
      | 101 | rechnung@hornbach.de | me@gupta-scaratec.com     | legal@ra-buechner.de  | Rechnung 42   | 48000      | [\Flagged] | 2026-05-06T09:00:00Z |
      | 102 | info@hornbach.de     | office@allianz.de         |                       | Lieferschein  | 1024       | []         | 2026-05-06T10:00:00Z |
      | 103 | service@obi.de       | office@allianz.de, x@y.de |                       | Bestellung    | 2048       | []         | 2025-01-15T09:00:00Z |
    And the message at uid 101 has attachment "inv.pdf" of type "application/pdf" with content byte "P" repeated 512 times
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria <criteria>, scope "all"
    Then the response decision is ALLOW
    And the response field uids equals in exact order <expected_uids>
    And the IMAP command log for "gupta-scaratec" contains a UID SEARCH command

    Examples:
      | name                      | criteria                                                     | expected_uids |
      | from_address              | {"from_address": "rechnung@hornbach.de"}                     | [101]         |
      | from_domain               | {"from_domain": "hornbach.de"}                               | [101, 102]    |
      | to_address                | {"to_address": "me@gupta-scaratec.com"}                      | [101]         |
      | to_domain                 | {"to_domain": "allianz.de"}                                  | [102, 103]    |
      | cc_domain                 | {"cc_domain": "ra-buechner.de"}                              | [101]         |
      | recipient_address         | {"recipient_address": "legal@ra-buechner.de"}                | [101]         |
      | all_recipients_in_domains | {"all_recipients_in_domains": ["allianz.de"]}                | [102]         |
      | subject_contains          | {"subject_contains": "Rechnung"}                             | [101]         |
      | size_gt                   | {"size_gt": 10000}                                           | [101]         |
      | size_lt                   | {"size_lt": 1500}                                            | [102]         |
      | has_attachment            | {"has_attachment": true}                                     | [101]         |
      | flagged                   | {"flagged": true}                                            | [101]         |

  Scenario Outline: Every duration unit is honoured by newer_than against pinned UTC clock 2026-05-07T12:00:00Z
    Given the folder "INBOX" holds messages:
      | uid     | from_address         | to_address            | subject | date             |
      | <uid_a> | rechnung@hornbach.de | me@gupta-scaratec.com | InWin   | <date_in_window> |
      | <uid_b> | rechnung@hornbach.de | me@gupta-scaratec.com | OutWin  | <date_outside>   |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "<duration>"}
    Then the response field uids equals in exact order [<uid_a>]
    And the response field applied_scope equals "explicit_window"

    Examples:
      | uid_a | uid_b | duration | date_in_window       | date_outside         |
      | 2001  | 2002  | 60s      | 2026-05-07T11:59:30Z | 2026-05-06T09:00:00Z |
      | 2011  | 2012  | 30m      | 2026-05-07T11:45:00Z | 2026-05-06T09:00:00Z |
      | 2021  | 2022  | 6h       | 2026-05-07T08:00:00Z | 2026-05-05T09:00:00Z |
      | 2031  | 2032  | 7d       | 2026-05-05T09:00:00Z | 2026-04-15T09:00:00Z |
      | 2041  | 2042  | 2w       | 2026-04-25T09:00:00Z | 2026-03-01T09:00:00Z |
      | 2051  | 2052  | 1y       | 2025-09-01T09:00:00Z | 2024-01-15T09:00:00Z |
      | 2071  | 2072  | 60s      | 2026-05-07T11:59:00Z | 2026-05-07T11:58:59Z |

  Scenario: older_than="60s" is strict (> 60s) at exact boundary and retains same-day messages via +1 day BEFORE padding
    Given the folder "INBOX" holds messages:
      | uid  | from_address         | to_address            | subject            | date                 |
      | 2061 | rechnung@hornbach.de | me@gupta-scaratec.com | 61 sec ago (match) | 2026-05-07T11:58:59Z |
      | 2062 | rechnung@hornbach.de | me@gupta-scaratec.com | 60 sec ago (exact) | 2026-05-07T11:59:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"older_than": "60s"}
    Then the response field uids equals in exact order [2061]
    And the response field applied_scope equals "explicit_window"

  Scenario Outline: Invalid or out-of-bounds duration "<bad_duration>" is rejected with JSON-RPC -32602
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "<bad_duration>"}
    Then the server responds with JSON-RPC error code -32602
    And the response error message contains "newer_than"

    Examples:
      | bad_duration |
      | 10x          |
      |              |
      | 999999d      |

  Scenario: Default scope=recent applies the 7-day window
    Given the folder "INBOX" holds messages:
      | uid  | from_address         | to_address            | subject   | date                 |
      | 1101 | rechnung@hornbach.de | me@gupta-scaratec.com | Heute     | 2026-05-06T09:00:00Z |
      | 1102 | rechnung@hornbach.de | me@gupta-scaratec.com | Letzte Wo | 2026-05-02T09:00:00Z |
      | 1103 | rechnung@hornbach.de | me@gupta-scaratec.com | Alt       | 2025-01-15T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field uids equals in exact order [1101, 1102]
    And the response field total_visible equals 2
    And the response field applied_scope equals "recent_7d"
    And the response field completeness equals "window_and_policy_limited"

  Scenario: scope=recent with explicit newer_than honours the explicit window
    Given the folder "INBOX" holds messages:
      | uid  | from_address         | to_address            | subject | date                 |
      | 1111 | rechnung@hornbach.de | me@gupta-scaratec.com | 1 Tag   | 2026-05-06T09:00:00Z |
      | 1112 | rechnung@hornbach.de | me@gupta-scaratec.com | 60 Tage | 2026-03-15T09:00:00Z |
      | 1113 | rechnung@hornbach.de | me@gupta-scaratec.com | 2 Jahre | 2024-05-01T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "90d"}, scope "recent"
    Then the response field uids equals in exact order [1111, 1112]
    And the response field applied_scope equals "explicit_window"
    And the response field completeness equals "policy_may_hide"

  Scenario: scope=recent with a non-time predicate still applies the 7-day window
    Given the folder "INBOX" holds messages:
      | uid  | from_address         | to_address            | subject | date                 |
      | 1121 | rechnung@hornbach.de | me@gupta-scaratec.com | Recent  | 2026-05-06T09:00:00Z |
      | 1122 | rechnung@hornbach.de | me@gupta-scaratec.com | Old     | 2025-01-15T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "hornbach.de"}
    Then the response field uids equals in exact order [1121]
    And the response field applied_scope equals "recent_7d"

  Scenario: scope=all with empty criteria returns every visible message across all dates
    Given the folder "INBOX" holds messages:
      | uid  | from_address         | to_address            | subject   | date                 |
      | 1131 | rechnung@hornbach.de | me@gupta-scaratec.com | Heute     | 2026-05-06T09:00:00Z |
      | 1132 | rechnung@hornbach.de | me@gupta-scaratec.com | Letzte Wo | 2026-05-02T09:00:00Z |
      | 1133 | rechnung@hornbach.de | me@gupta-scaratec.com | Alt       | 2025-01-15T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals in exact order [1131, 1132, 1133]
    And the response field total_visible equals 3
    And the response field applied_scope equals "all_time"
    And the response field completeness equals "policy_may_hide"

  Scenario: scope=all with flagged=true returns every starred visible message regardless of age
    Given the folder "INBOX" holds messages:
      | uid  | from_address         | to_address            | subject    | flags      | date                 |
      | 1141 | rechnung@hornbach.de | me@gupta-scaratec.com | Stern alt  | [\Flagged] | 2024-09-01T09:00:00Z |
      | 1142 | rechnung@hornbach.de | me@gupta-scaratec.com | Stern neu  | [\Flagged] | 2026-05-06T09:00:00Z |
      | 1143 | rechnung@hornbach.de | me@gupta-scaratec.com | Kein Stern | []         | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"flagged": true}, scope "all"
    Then the response field uids equals in exact order [1141, 1142]
    And the response field applied_scope equals "all_time"

  Scenario: limit restricts the number of ascending UIDs returned on page 0
    Given the folder "INBOX" holds messages:
      | uid  | from_address         | to_address            | subject    | date                 |
      | 1301 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 1 | 2026-05-06T09:00:00Z |
      | 1302 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 2 | 2026-05-05T09:00:00Z |
      | 1303 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 3 | 2026-05-04T09:00:00Z |
      | 1304 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 4 | 2026-05-03T09:00:00Z |
      | 1305 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 5 | 2026-05-02T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, limit 3
    Then the response field uids equals in exact order [1301, 1302, 1303]
    And the response field total_visible equals 5
    And the response field returned_count equals 3
    And the response field page_limit equals 3
    And the response field has_more equals true

  Scenario: offset skips the first N ascending visible UIDs
    Given the folder "INBOX" holds messages:
      | uid  | from_address         | to_address            | subject    | date                 |
      | 1401 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 1 | 2026-05-06T09:00:00Z |
      | 1402 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 2 | 2026-05-05T09:00:00Z |
      | 1403 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 3 | 2026-05-04T09:00:00Z |
      | 1404 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 4 | 2026-05-03T09:00:00Z |
      | 1405 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 5 | 2026-05-02T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, limit 2, offset 2
    Then the response field uids equals in exact order [1403, 1404]
    And the response field page_offset equals 2
    And the response field has_more equals true

  Scenario: Pagination metadata reflects total_visible across pages
    Given the folder "INBOX" holds messages:
      | uid  | from_address         | to_address            | subject    | date                 |
      | 1501 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 1 | 2026-05-06T09:00:00Z |
      | 1502 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 2 | 2026-05-05T09:00:00Z |
      | 1503 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 3 | 2026-05-04T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, limit 2
    Then the response field total_visible equals 3
    And the response field returned_count equals 2
    And the response field page_limit equals 2
    And the response field page_offset equals 0
    And the response field has_more equals true

  Scenario: Offset beyond total_visible returns empty uids and has_more=false
    Given the folder "INBOX" holds messages:
      | uid  | from_address         | to_address            | subject    | date                 |
      | 1601 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 1 | 2026-05-06T09:00:00Z |
      | 1602 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 2 | 2026-05-05T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, limit 50, offset 100
    Then the response field uids equals []
    And the response field returned_count equals 0
    And the response field has_more equals false
    And the response field total_visible equals 2

  Scenario: Criteria combined with pagination returns the exact ascending slice
    Given the folder "INBOX" holds messages:
      | uid  | from_address         | to_address            | subject    | date                 |
      | 1701 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 1 | 2026-05-06T09:00:00Z |
      | 1702 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 2 | 2026-05-05T09:00:00Z |
      | 1703 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 3 | 2026-05-04T09:00:00Z |
      | 1704 | service@obi.de       | me@gupta-scaratec.com | Bestellung | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "hornbach.de"}, limit 2, offset 1
    Then the response field uids equals in exact order [1702, 1703]
    And the response field total_visible equals 3
    And the response field has_more equals false

  Scenario: PDP filtering hides non-whitelisted matches without disclosing filtered_out
    Given the folder "INBOX" holds messages:
      | uid  | from_address         | to_address            | subject    | date                 |
      | 1801 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung A | 2026-05-06T09:00:00Z |
      | 1802 | spam@example.net     | me@gupta-scaratec.com | Rechnung B | 2026-05-06T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"subject_contains": "Rechnung"}
    Then the response field uids equals in exact order [1801]
    And the response field total_visible equals 1
    And the response does not contain any field named "filtered_out"
    And the response does not contain any field named "matched_total"
