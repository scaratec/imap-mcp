Feature: On a Gmail account, calendar windows, attachment structure, completeness, and visible counts match Gmail behaviour

  ADR 0040, ADR 0041, ADR 0043.

  Rules used for the numbers below:
    - The server clock is pinned to 2026-11-02T12:00:00Z; the recent_7d
      window covers INTERNALDATE >= 2026-10-26T12:00:00Z.
    - since is inclusive, before is exclusive, evaluated against UTC
      INTERNALDATE (ADR 0041).
    - BODY visibility is required to inspect has_attachment (ADR 0041,
      ADR 0043).
    - Default recent_7d searches report static completeness="window_limited"
      (or "window_and_policy_limited") rather than pre-policy outside counts,
      and list_folders omits raw message_count while folder_stats reports
      authorized visible_count (ADR 0043).

  Fixture fidelity checks (mock self-validation, not SUT error paths): 7
  Covered SUT error layers (per BDD Guidelines §4.5):
    - search with calendar windows (since + before, before only)                   : 2
    - Default recent_7d window reports completeness="window_limited"               : 1
    - has_attachment in search criteria and list_messages at BODY visibility       : 2
    - list_folders omits raw message_count; folder_stats reports visible_count     : 2
    Total SUT enumerated                                                           : 7   covered by this feature: 7 (plus 7 mock-fidelity checks)

  Background:
    Given the server clock is pinned to "2026-11-02T12:00:00Z"
    And the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path      |
      | INBOX            |
      | [Gmail]/All Mail |
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "scaratec-gmail"
    And policy "invoice-policy" grants folder on account "scaratec-gmail":
      | folder | mode      | default | rules |
      | INBOX  | blacklist | BODY    | []    |
    And the folder "scaratec-gmail:INBOX" holds messages:
      | uid | from_address                      | to_address     | subject                    | date                 |
      | 901 | rechnung@nordlicht.example.org    | me@example.org | Ihre Rechnung 2026-10      | 2026-10-30T07:00:00Z |
    And the message at uid 901 on "scaratec-gmail:INBOX" has attachment "rechnung-2026-10.pdf" of type "application/pdf" with content byte "A" repeated 4096 times
    And the folder "scaratec-gmail:INBOX" holds messages:
      | uid | from_address                      | to_address     | subject                    | date                 |
      | 902 | rechnung@nordlicht.example.org    | me@example.org | Ihre Rechnung 2026-09      | 2026-09-29T07:00:00Z |
    And the message at uid 902 on "scaratec-gmail:INBOX" has attachment "rechnung-2026-09.pdf" of type "application/pdf" with content byte "B" repeated 2048 times
    And the folder "scaratec-gmail:INBOX" holds messages:
      | uid | from_address                      | to_address     | subject                    | date                 |
      | 903 | buchhaltung@nordlicht.example.org | me@example.org | Zahlungserinnerung 2026-08 | 2026-08-28T07:00:00Z |
      | 904 | mara.lindqvist@example.org        | me@example.org | Angebot                    | 2026-10-31T07:00:00Z |

  Scenario Outline: The mock answers UID SEARCH <key> on UTC internal date or Date header per RFC 3501
    When a direct IMAP command is issued in "INBOX" on "scaratec-gmail":
      """
      UID SEARCH <key> <day>
      """
    Then the direct IMAP command is answered with status "OK"
    And the direct IMAP SEARCH answer lists exactly the messages <messages>

    Examples:
      | key        | day         | messages   |
      | BEFORE     | 01-Sep-2026 | [903]      |
      | ON         | 29-Sep-2026 | [902]      |
      | SINCE      | 30-Oct-2026 | [901, 904] |
      | SENTBEFORE | 30-Oct-2026 | [902, 903] |
      | SENTON     | 31-Oct-2026 | [904]      |

  Scenario: The mock renders a message with a PDF attachment as multipart/mixed in BODYSTRUCTURE
    Then a direct IMAP FETCH of the BODYSTRUCTURE of message 901 in "scaratec-gmail:INBOX" shows a multipart/mixed body with the parts:
      | part | type            | disposition | filename             |
      | 1    | text/plain      |             |                      |
      | 2    | application/pdf | attachment  | rechnung-2026-10.pdf |

  Scenario: The mock reports the number of messages in a mailbox through STATUS
    Then a direct IMAP STATUS of "INBOX" on "scaratec-gmail" reports MESSAGES 4

  Scenario Outline: A calendar window on a Gmail account selects the exact messages inside it
    When invoice-agent calls search with account "scaratec-gmail", folder "INBOX", criteria <criteria>
    Then the response decision is ALLOW
    And the response field uids equals <uids>
    And the response field applied_scope equals "explicit_window"
    And the response field completeness equals "complete"

    Examples:
      | criteria                                                                                | uids  |
      | {"from_domain": "nordlicht.example.org", "since": "2026-09-01", "before": "2026-10-01"} | [902] |
      | {"from_domain": "nordlicht.example.org", "before": "2026-09-01"}                        | [903] |

  Scenario: Default recent_7d window signals completeness="window_limited" and omits outside_scope_total
    When invoice-agent calls search with account "scaratec-gmail", folder "INBOX", criteria {"from_domain": "nordlicht.example.org"}
    Then the response decision is ALLOW
    And the response field uids equals [901]
    And the response field total_visible equals 1
    And the response field applied_scope equals "recent_7d"
    And the response field completeness equals "window_limited"
    And the response does not contain any field named "outside_scope_total"

  Scenario: has_attachment on a Gmail account at BODY visibility selects the two PDF invoices
    When invoice-agent calls search with account "scaratec-gmail", folder "INBOX", criteria {"has_attachment": true}, scope "all"
    Then the response decision is ALLOW
    And the response field uids equals [901, 902]

  Scenario: list_messages on a Gmail account at BODY visibility projects has_attachment accurately
    When invoice-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    Then the response contains 4 messages
    And the response field messages has exactly one entry matching:
      | field          | value                 |
      | subject        | Ihre Rechnung 2026-10 |
      | has_attachment | true                  |
    And the response field messages has exactly one entry matching:
      | field          | value                 |
      | subject        | Ihre Rechnung 2026-09 |
      | has_attachment | true                  |
    And the response field messages has exactly one entry matching:
      | field          | value                      |
      | subject        | Zahlungserinnerung 2026-08 |
      | has_attachment | false                      |
    And the response field messages has exactly one entry matching:
      | field          | value   |
      | subject        | Angebot |
      | has_attachment | false   |

  Scenario: list_folders on a Gmail account omits raw message_count per ADR 0043
    When invoice-agent calls list_folders with account "scaratec-gmail"
    Then the response decision is ALLOW
    And the response field folders equals ["INBOX"]
    And the JSON response does NOT contain the literal strings:
      | forbidden_string |
      | message_count    |

  Scenario: folder_stats on a Gmail account reports exact visible_count and completeness
    When invoice-agent calls folder_stats with account "scaratec-gmail", folder "INBOX"
    Then the response decision is ALLOW
    And the response field visible_count equals 4
    And the response field completeness equals "complete"
    And the response does not contain any field named "hidden_count"
