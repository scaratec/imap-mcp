Feature: bulk_mark_seen updates \Seen on matching visible messages in one bounded IMAP session

  ADR 0041, ADR 0043, and ADR 0045 require bulk_mark_seen to hold
  mark_seen=true on the folder, evaluate criteria only on messages whose
  effective visibility reaches max(METADATA, req_vis(criteria)), update
  \Seen on the wire in a single IMAP session, and report marked_count as
  the number of matched, sufficiently visible messages to which the flag
  state was applied (including messages that already carried \Seen, ADR 0045 §1).

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path seen=true verified on IMAP via second channel  : 1
    - Happy path seen=false clears \Seen on IMAP                : 1
    - Empty match returns marked_count=0, leaves IMAP unchanged : 1
    - Missing mark_seen capability denied with capability_missing: 1
    - Per-message COUNT cap skipped & excluded from marked_count: 1
    - Bounded IMAP connection count across 20 messages          : 1
    Total enumerated                                            : 6   covered by this feature: 6

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "scaratec-gmail"

  Scenario: bulk_mark_seen sets \Seen on all 10 matching messages on the IMAP server
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_seen |
      | blacklist | ENVELOPE | true      |
    And the folder "scaratec-gmail:INBOX" holds 10 messages from "alerts@example.com" dated "2026-05-06T09:00:00Z"
    When inbox-agent calls bulk_mark_seen with account "scaratec-gmail", folder "INBOX", criteria {"from_domain": "example.com"}, seen true, scope "all"
    Then the response decision is ALLOW
    And the response field marked_count equals 10
    And the folder "scaratec-gmail:INBOX" holds 10 messages with flag "\Seen"

  Scenario: bulk_mark_seen with seen=false clears \Seen on matching messages on the IMAP server
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_seen |
      | blacklist | ENVELOPE | true      |
    And the folder "scaratec-gmail:INBOX" holds 4 messages from "alerts@example.com" dated "2026-05-06T09:00:00Z" pre-flagged "\Seen"
    When inbox-agent calls bulk_mark_seen with account "scaratec-gmail", folder "INBOX", criteria {"from_domain": "example.com"}, seen false, scope "all"
    Then the response decision is ALLOW
    And the response field marked_count equals 4
    And the folder "scaratec-gmail:INBOX" holds 0 messages with flag "\Seen"

  Scenario: bulk_mark_seen with no matching messages returns marked_count=0 and sets no \Seen flags
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_seen |
      | blacklist | ENVELOPE | true      |
    And the folder "scaratec-gmail:INBOX" holds 5 messages from "alerts@example.com" dated "2026-05-06T09:00:00Z"
    When inbox-agent calls bulk_mark_seen with account "scaratec-gmail", folder "INBOX", criteria {"from_address": "nobody@nowhere.com"}, seen true, scope "all"
    Then the response decision is ALLOW
    And the response field marked_count equals 0
    And the folder "scaratec-gmail:INBOX" holds 0 messages with flag "\Seen"

  Scenario: bulk_mark_seen without mark_seen capability is denied with capability_missing
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_seen |
      | blacklist | ENVELOPE | false     |
    And the folder "scaratec-gmail:INBOX" holds 3 messages from "alerts@example.com" dated "2026-05-06T09:00:00Z"
    When inbox-agent calls bulk_mark_seen with account "scaratec-gmail", folder "INBOX", criteria {"from_domain": "example.com"}, seen true, scope "all"
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the folder "scaratec-gmail:INBOX" holds 0 messages with flag "\Seen"

  Scenario: bulk_mark_seen across 20 messages opens at most 2 IMAP connections
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_seen |
      | blacklist | ENVELOPE | true      |
    And the folder "scaratec-gmail:INBOX" holds 20 messages from "alerts@example.com" dated "2026-05-06T09:00:00Z"
    When inbox-agent calls bulk_mark_seen with account "scaratec-gmail", folder "INBOX", criteria {"from_domain": "example.com"}, seen true, scope "all"
    Then the response field marked_count equals 20
    And the folder "scaratec-gmail:INBOX" holds 20 messages with flag "\Seen"
    And the mock-gmail server received between 1 and 2 IMAP connections

  Scenario: bulk_mark_seen skips COUNT-capped messages below METADATA and excludes them from marked_count
    Given policy "inbox-policy" grants folder on account "scaratec-gmail":
      | folder | mode      | default  | mark_seen | rules                                |
      | INBOX  | blacklist | ENVELOPE | true      | [{from_domain=bank.de -> cap COUNT}] |
    And the folder "scaratec-gmail:INBOX" holds messages:
      | uid | from_address      | to_address     | subject     | date                 |
      | 801 | noreply@bank.de   | me@example.org | Kontoauszug | 2026-05-06T09:00:00Z |
      | 802 | alerts@example.org| me@example.org | Alert       | 2026-05-06T09:01:00Z |
    When inbox-agent calls bulk_mark_seen with account "scaratec-gmail", folder "INBOX", criteria {}, seen true, scope "all"
    Then the response decision is ALLOW
    And the response field marked_count equals 1
    And a direct IMAP FETCH of uid 802 in "scaratec-gmail:INBOX" shows the flag "\Seen"
    And a direct IMAP FETCH of uid 801 in "scaratec-gmail:INBOX" does NOT show the flag "\Seen"
