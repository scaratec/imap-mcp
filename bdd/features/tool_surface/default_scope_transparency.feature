Feature: applied_scope is always present and symmetric across search, list_messages, and bulk tools

  ADR 0043 §2 defines the closed applied_scope enumeration (recent_7d,
  explicit_window, all_time) and applies the same window selection across
  search, list_messages, bulk_mark_seen, and bulk_mark_tagged.

  Covered error layers (per BDD Guidelines §4.5):
    - applied_scope on list_messages (recent_7d, all_time, explicit_window) : 3
    - applied_scope on search (recent_7d, all_time, explicit_window)        : 3
    - Cross-tool agreement across search, list_messages, bulk_mark_*        : 2
    Total enumerated                                                        : 8   covered by this feature: 8

  Background:
    Given the server clock is pinned to "2026-05-12T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"
    And policy "inbox-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | mark_seen | mark_tagged | rules |
      | INBOX  | blacklist | ENVELOPE | true      | true        | []    |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address            | subject | date                 |
      | 701 | sender@test.local | me@gupta-scaratec.com | Recent  | 2026-05-10T09:00:00Z |
      | 702 | sender@test.local | me@gupta-scaratec.com | Old     | 2026-04-01T09:00:00Z |

  Scenario: list_messages with default scope reports applied_scope=recent_7d
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX"
    Then the response contains 1 messages
    And the response field total_visible equals 1
    And the response field applied_scope equals "recent_7d"

  Scenario: list_messages with scope=all reports applied_scope=all_time
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the response contains 2 messages
    And the response field total_visible equals 2
    And the response field applied_scope equals "all_time"

  Scenario: list_messages with explicit newer_than reports applied_scope=explicit_window
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "90d"}
    Then the response contains 2 messages
    And the response field applied_scope equals "explicit_window"

  Scenario: search with default scope reports applied_scope=recent_7d
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field total_visible equals 1
    And the response field applied_scope equals "recent_7d"

  Scenario: search with scope=all reports applied_scope=all_time
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field total_visible equals 2
    And the response field applied_scope equals "all_time"

  Scenario: search with explicit newer_than reports applied_scope=explicit_window
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "90d"}
    Then the response field total_visible equals 2
    And the response field applied_scope equals "explicit_window"

  Scenario: list_messages and search agree on applied_scope for identical inputs
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "90d"}
    And inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "90d"}
    Then both responses report applied_scope equal to "explicit_window"

  Scenario: bulk_mark_seen and bulk_mark_tagged apply the same default recent_7d window as search and mutate only in-window mail on IMAP
    When inbox-agent calls bulk_mark_seen with account "gupta-scaratec", folder "INBOX", criteria {}, seen true
    Then the response field marked_count equals 1
    And the response field applied_scope equals "recent_7d"
    And the IMAP message at "INBOX" uid 701 has flag "\Seen"
    And the IMAP message at "INBOX" uid 702 does NOT have flag "\Seen"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {}, tags ["$Recent"], mode "add"
    Then the response field tagged_count equals 1
    And the response field applied_scope equals "recent_7d"
    And the IMAP message at "INBOX" uid 701 has keyword "$Recent"
    And the IMAP message at "INBOX" uid 702 does NOT have keyword "$Recent"
