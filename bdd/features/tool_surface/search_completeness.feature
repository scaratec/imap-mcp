@pending
Feature: A search tells the caller when matches lie outside the applied window, and accepts calendar dates

  ADR 0026 §5 makes a 7-day window the default for search and
  list_messages and reports it as applied_scope. The default is right
  for "what came in recently" and was introduced for performance
  (LIM-0011). It is wrong for "show me all invoices from Nordlicht",
  and nothing in the response tells the caller that older matches
  exist: applied_scope says which window was used, not what it hid.
  Time windows can only be expressed relative to now (newer_than,
  older_than), so "the third quarter of 2026" has to be converted into
  day counts by the caller, a step agents get wrong.

  ADR 0036 (amends 0024 and 0026 §5).

  Rules used for the numbers below:
    - The server date is 2026-11-02; the recent window therefore starts
      2026-10-26 (ADR 0026 §5).
    - outside_scope_total counts messages the same criteria match before
      the window, before policy filtering, like matched_total
      (ADR 0017 transparency: counts are disclosed, content is not).
    - since is inclusive, before is exclusive; both are calendar dates
      in IMAP SEARCH SINCE / BEFORE semantics (RFC 3501 §6.4.4).

  What is at stake. An agent asked for "all invoices from Nordlicht"
  reports one invoice when there are six, and the owner has no reason
  to doubt it.

  Independent-channel rule. The seeded folder below is the ground truth;
  every expected uid set is derivable from the table and the rules
  above. Validation failures are additionally witnessed by the IMAP
  command log showing that no SEARCH reached the server.

  Not specified here: searching several folders in one call. Invoices
  filed under Rechnungseingänge need a second call; whether agents make
  it is measured in usability/features/03_invoice_search.feature.

  Covered error layers (per BDD Guidelines §4.5):
    - Default window hides older matches, count reported         : 2
    - scope "all" reports no outside count                        : 1
    - Calendar window since + before                              : 1
    - Calendar window since only                                  : 1
    - Malformed calendar date                                     : 1
    - since not before before                                     : 1
    - Calendar and duration bounds mixed                          : 1
    Total enumerated                                              : 8   covered by this feature: 8

  Background:
    Given the server date is pinned to "2026-11-02"
    And the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "search-agent" using policy "search-policy"
    And policy "search-policy" grants account "gupta-scaratec"
    And policy "search-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                              | subject                    | date                 |
      | 901 | rechnung@nordlicht.example.org    | Ihre Rechnung 2026-10      | 2026-10-30T07:00:00Z |
      | 902 | rechnung@nordlicht.example.org    | Ihre Rechnung 2026-09      | 2026-09-29T07:00:00Z |
      | 903 | rechnung@nordlicht.example.org    | Ihre Rechnung 2026-08      | 2026-08-28T07:00:00Z |
      | 904 | rechnung@nordlicht.example.org    | Ihre Rechnung 2026-07      | 2026-07-30T07:00:00Z |
      | 905 | buchhaltung@nordlicht.example.org | Zahlungserinnerung 2026-06 | 2026-06-15T07:00:00Z |
      | 906 | rechnung@nordlicht.example.org    | Ihre Rechnung 2026-06      | 2026-06-29T07:00:00Z |
      | 907 | info@nordlicht-reisen.example.org | Ihre Reisebuchung          | 2026-10-31T07:00:00Z |
      | 908 | mara.lindqvist@example.org        | Angebot                    | 2026-10-29T07:00:00Z |

  # --- Main path: the default window says what it hid ------------------

  Scenario Outline: The default window reports how many matches lie before it
    When search-agent calls search with account "gupta-scaratec", folder "INBOX", criteria <criteria>
    Then the response decision is ALLOW
    And the response uids equals <in_window>
    And the response field applied_scope equals "recent_7d"
    And the response field outside_scope_total equals <outside>

    Examples:
      | criteria                                    | in_window | outside |
      | {"from_domain": "nordlicht.example.org"}     | [901]     | 5       |
      | {"subject_contains": "Rechnung"}             | [901]     | 4       |

  Scenario: scope "all" returns every match and reports no outside count
    When search-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "nordlicht.example.org"}, scope "all"
    Then the response uids equals [901, 902, 903, 904, 905, 906]
    And the response field applied_scope equals "all_time"
    And the response does not contain any field named "outside_scope_total"

  # --- Calendar windows ------------------------------------------------

  Scenario Outline: A calendar window selects exactly the messages inside it
    When search-agent calls search with account "gupta-scaratec", folder "INBOX", criteria <criteria>
    Then the response decision is ALLOW
    And the response uids equals <uids>
    And the response field applied_scope equals "explicit_window"

    Examples:
      | criteria                                                                          | uids            |
      | {"from_domain": "nordlicht.example.org", "since": "2026-07-01", "before": "2026-10-01"} | [902, 903, 904] |
      | {"from_domain": "nordlicht.example.org", "since": "2026-10-01"}                    | [901]           |

  # --- Error paths: input validation -----------------------------------

  Scenario Outline: An invalid calendar window is refused before any IMAP command is sent
    When search-agent calls search with account "gupta-scaratec", folder "INBOX", criteria <criteria>
    Then the tool call fails with an invalid-arguments error
    And the IMAP command log for "gupta-scaratec" contains no SEARCH command

    Examples:
      | criteria                                                  | why                         |
      | {"since": "01.07.2026"}                                    | not an ISO calendar date    |
      | {"since": "2026-10-01", "before": "2026-07-01"}            | since is not before before  |
      | {"since": "2026-07-01", "newer_than": "30d"}               | calendar and duration mixed |
