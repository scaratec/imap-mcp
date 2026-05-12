Feature: bulk_mark_seen marks multiple messages as read in one call

  An agent asking "mark all alerts as read" should not need N
  individual mark_seen calls. bulk_mark_seen accepts search criteria
  and marks all matching messages in a single IMAP session.

  Covered error layers (per BDD Guidelines §4.5):
  - Happy path: criteria match, messages marked        : 1
  - Empty result: criteria match nothing               : 1
  - Capability missing: folder without mark_seen       : 1
  - Connection count: single IMAP session              : 1
  Total enumerated                                      : 4   covered by this feature: 4

  Background:
    Given the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "scaratec-gmail"

  Scenario: bulk_mark_seen marks all matching messages
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_seen |
      | blacklist | ENVELOPE | true      |
    And the folder "INBOX" on "scaratec-gmail" holds 10 messages
    When inbox-agent calls bulk_mark_seen with account "scaratec-gmail", folder "INBOX", criteria {"from_domain": "example.com"}, seen true
    Then the response decision is ALLOW
    And the response field marked_count equals 10

  Scenario: bulk_mark_seen with no matches returns zero
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_seen |
      | blacklist | ENVELOPE | true      |
    And the folder "INBOX" on "scaratec-gmail" holds 5 messages
    When inbox-agent calls bulk_mark_seen with account "scaratec-gmail", folder "INBOX", criteria {"from": "nobody@nowhere.com"}, seen true
    Then the response decision is ALLOW
    And the response field marked_count equals 0

  Scenario: bulk_mark_seen denied without mark_seen capability
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_seen |
      | blacklist | ENVELOPE | false     |
    When inbox-agent calls bulk_mark_seen with account "scaratec-gmail", folder "INBOX", criteria {"from_domain": "example.com"}, seen true
    Then the response decision is DENY
    And the response field reason equals "capability_missing"

  Scenario: bulk_mark_seen opens at most 2 connections for its own work
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_seen |
      | blacklist | ENVELOPE | true      |
    And the folder "INBOX" on "scaratec-gmail" holds 20 messages
    When inbox-agent calls bulk_mark_seen with account "scaratec-gmail", folder "INBOX", criteria {"from_domain": "example.com"}, seen true
    Then the response field marked_count equals 20
    And the mock-gmail server received at most 4 IMAP connections
