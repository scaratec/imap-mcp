Feature: Default scope transparency for list_messages and search

  When no criteria are provided, list_messages and search apply an
  implicit 7-day SINCE filter to prevent unbounded result sets.
  The response must signal this via a "default_scope" field so that
  callers know older messages exist but were excluded.

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"
    And policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |

  Scenario: list_messages with empty criteria signals default_scope
    Given the server date is pinned to "2026-05-12"
    And the folder "INBOX" holds messages:
      | uid | from               | subject | date                 |
      | 701 | sender@test.local  | Recent  | 2026-05-10T09:00:00Z |
      | 702 | sender@test.local  | Old     | 2026-04-01T09:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX"
    Then the response contains 1 messages
    And the response field default_scope equals "newer_than_7d"

  Scenario: search with empty criteria signals default_scope
    Given the server date is pinned to "2026-05-12"
    And the folder "INBOX" holds messages:
      | uid | from               | subject | date                 |
      | 711 | sender@test.local  | Recent  | 2026-05-10T09:00:00Z |
      | 712 | sender@test.local  | Old     | 2026-04-01T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field matched_total equals 1
    And the response field default_scope equals "newer_than_7d"

  Scenario: explicit newer_than suppresses default_scope
    Given the server date is pinned to "2026-05-12"
    And the folder "INBOX" holds messages:
      | uid | from               | subject | date                 |
      | 721 | sender@test.local  | Recent  | 2026-05-10T09:00:00Z |
      | 722 | sender@test.local  | Old     | 2026-04-01T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "90d"}
    Then the response field matched_total equals 2
    And the response does not contain any field named "default_scope"
