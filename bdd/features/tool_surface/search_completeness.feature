Feature: Static completeness indicators and ISO calendar bounds in search and list_messages

  ADR 0041 and ADR 0043 replace the leaky pre-policy outside_scope_total
  counter with static completeness values derived strictly from folder
  policy (may_hide) and applied_scope:
    - "complete"                  (explicit_window or all_time, may_hide=false)
    - "policy_may_hide"           (explicit_window or all_time, may_hide=true)
    - "window_limited"            (recent_7d, may_hide=false)
    - "window_and_policy_limited" (recent_7d, may_hide=true)
  Calendar bounds since (inclusive) and before (exclusive) evaluate UTC
  INTERNALDATE and reject malformed or mixed inputs with JSON-RPC -32602.

  Covered error layers (per BDD Guidelines §4.5):
    - Four static completeness states (2 scope x 2 may_hide)        : 4
    - Calendar windows (since + before, since only, exact midnight) : 3
    - Invalid calendar inputs (-32602 before any IMAP SEARCH)       : 4
    - Authorized non-existent folder -> folder_absent (search/list) : 2
    Total enumerated                                                : 13  covered by this feature: 13

  Background:
    Given the server clock is pinned to "2026-11-02T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the server is configured with caller "search-agent" using policy "search-policy"
    And policy "search-policy" grants account "gupta-scaratec"
    And policy "search-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from_address                      | to_address            | subject                    | date                 |
      | 901 | rechnung@nordlicht.example.org    | me@gupta-scaratec.com | Ihre Rechnung 2026-10      | 2026-10-30T07:00:00Z |
      | 902 | rechnung@nordlicht.example.org    | me@gupta-scaratec.com | Ihre Rechnung 2026-09      | 2026-09-29T07:00:00Z |
      | 903 | rechnung@nordlicht.example.org    | me@gupta-scaratec.com | Ihre Rechnung 2026-08      | 2026-08-28T07:00:00Z |
      | 904 | rechnung@nordlicht.example.org    | me@gupta-scaratec.com | Ihre Rechnung 2026-07      | 2026-07-30T07:00:00Z |
      | 905 | buchhaltung@nordlicht.example.org | me@gupta-scaratec.com | Zahlungserinnerung 2026-06 | 2026-06-15T07:00:00Z |
      | 906 | rechnung@nordlicht.example.org    | me@gupta-scaratec.com | Ihre Rechnung 2026-06      | 2026-06-29T07:00:00Z |
      | 907 | info@nordlicht-reisen.example.org | me@gupta-scaratec.com | Ihre Reisebuchung          | 2026-10-31T07:00:00Z |
      | 908 | mara.lindqvist@example.org        | me@gupta-scaratec.com | Angebot                    | 2026-10-29T07:00:00Z |

  Scenario Outline: Static completeness reflects folder may_hide and scope without outside_scope_total
    Given policy "search-policy" grants folder on account "gupta-scaratec":
      | folder | mode   | default   | rules   |
      | INBOX  | <mode> | <default> | <rules> |
    When search-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "nordlicht.example.org"}, scope "<scope>"
    Then the response decision is ALLOW
    And the response field uids equals in exact order <expected_uids>
    And the response field applied_scope equals "<applied_scope>"
    And the response field completeness equals "<completeness>"
    And the response does not contain any field named "outside_scope_total"

    Examples:
      | mode      | default  | rules                                            | scope  | expected_uids                  | applied_scope | completeness              |
      | blacklist | ENVELOPE | []                                               | recent | [901]                          | recent_7d     | window_limited            |
      | blacklist | ENVELOPE | []                                               | all    | [901, 902, 903, 904, 905, 906] | all_time      | complete                  |
      | whitelist | NONE     | [{from_domain=nordlicht.example.org -> ENVELOPE}]| recent | [901]                          | recent_7d     | window_and_policy_limited |
      | whitelist | NONE     | [{from_domain=nordlicht.example.org -> ENVELOPE}]| all    | [901, 902, 903, 904, 905, 906] | all_time      | policy_may_hide           |

  Scenario Outline: ISO calendar windows select the exact messages inside [since, before)
    When search-agent calls search with account "gupta-scaratec", folder "INBOX", criteria <criteria>
    Then the response decision is ALLOW
    And the response field uids equals in exact order <uids>
    And the response field applied_scope equals "explicit_window"
    And the response field completeness equals "complete"

    Examples:
      | criteria                                                                                | uids            |
      | {"from_domain": "nordlicht.example.org", "since": "2026-07-01", "before": "2026-10-01"} | [902, 903, 904] |
      | {"from_domain": "nordlicht.example.org", "since": "2026-10-01"}                         | [901]           |

  Scenario: Exact UTC midnight boundary includes since date and excludes before date
    Given the folder "INBOX" holds messages:
      | uid | from_address                   | to_address            | subject          | date                 |
      | 951 | rechnung@nordlicht.example.org | me@gupta-scaratec.com | Inclusive Start  | 2026-10-01T00:00:00Z |
      | 952 | rechnung@nordlicht.example.org | me@gupta-scaratec.com | Exclusive Cutoff | 2026-10-02T00:00:00Z |
    When search-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "nordlicht.example.org", "since": "2026-10-01", "before": "2026-10-02"}
    Then the response field uids equals in exact order [951]

  Scenario Outline: Invalid calendar window (<why>) is rejected with JSON-RPC -32602 before any IMAP SEARCH
    When search-agent calls search with account "gupta-scaratec", folder "INBOX", criteria <criteria>
    Then the server responds with JSON-RPC error code -32602
    And the IMAP command log for "gupta-scaratec" contains no SEARCH command

    Examples:
      | criteria                                         | why                          |
      | {"since": "01.07.2026"}                          | non-ISO calendar format      |
      | {"since": "2026-02-30"}                          | impossible calendar day      |
      | {"since": "2026-10-01", "before": "2026-07-01"}  | since is not before before   |
      | {"since": "2026-07-01", "newer_than": "30d"}     | calendar and duration mixed  |

  Scenario Outline: Calling <tool> on an authorized folder that does not exist reports folder_absent
    Given policy "search-policy" grants folder on account "gupta-scaratec":
      | folder                      | mode      | default  | rules |
      | INBOX/Rechnungen-Tippfehler | blacklist | ENVELOPE | []    |
    When search-agent calls <tool> with account "gupta-scaratec", folder "INBOX/Rechnungen-Tippfehler", criteria {"from_domain": "nordlicht.example.org"}
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "folder_absent"

    Examples:
      | tool          |
      | search        |
      | list_messages |
