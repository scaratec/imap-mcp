Feature: applied_scope is always present and symmetric between list_messages and search

  ADR 0026 §5 defines the scope argument and the applied_scope response
  field. The field is always present (closed enumeration: recent_7d,
  explicit_window, all_time) so a caller never has to infer the window
  from input shape. This feature pins that contract for both
  list_messages and search, and pins the cross-tool symmetry: the same
  inputs yield the same applied_scope on both tools.

  Covered error layers (per BDD Guidelines §4.5):
    - applied_scope present on every list_messages call : 3 (one per enum value)
    - applied_scope present on every search call        : 3 (one per enum value)
    - list_messages and search agree on applied_scope   : 1
    Total enumerated                                     : 7   covered by this feature: 7

  Background:
    Given the server date is pinned to "2026-05-12"
    And the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"
    And policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from               | subject | date                 |
      | 701 | sender@test.local  | Recent  | 2026-05-10T09:00:00Z |
      | 702 | sender@test.local  | Old     | 2026-04-01T09:00:00Z |

  Scenario: list_messages with default scope reports applied_scope=recent_7d
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX"
    Then the response contains 1 messages
    And the response field applied_scope equals "recent_7d"

  Scenario: list_messages with scope=all reports applied_scope=all_time
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the response contains 2 messages
    And the response field applied_scope equals "all_time"

  Scenario: list_messages with explicit newer_than reports applied_scope=explicit_window
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "90d"}
    Then the response contains 2 messages
    And the response field applied_scope equals "explicit_window"

  Scenario: search with default scope reports applied_scope=recent_7d
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field matched_total equals 1
    And the response field applied_scope equals "recent_7d"

  Scenario: search with scope=all reports applied_scope=all_time
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field matched_total equals 2
    And the response field applied_scope equals "all_time"

  Scenario: search with explicit newer_than reports applied_scope=explicit_window
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "90d"}
    Then the response field matched_total equals 2
    And the response field applied_scope equals "explicit_window"

  Scenario: list_messages and search agree on applied_scope for identical inputs
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "90d"}
    And inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "90d"}
    Then both responses report applied_scope equal to "explicit_window"
