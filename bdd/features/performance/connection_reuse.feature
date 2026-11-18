Feature: Single-session batch envelope evaluation avoids N+1 IMAP connections

  Operations evaluating per-message rules over N candidate messages fetch
  metadata in a single IMAP session rather than opening N+1 connections
  (LIM 0023, ADR 0055). Default pagination limit is 50 (ADR 0043 §3.2).

  Covered error layers (per BDD Guidelines §4.5):
    - Bounded connection count on list_messages over 20 messages : 1
    - Bounded connection count on search over 20 messages        : 1
    Total enumerated                                             : 2   covered by this feature: 2

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "perf-agent" using policy "perf-policy"
    And policy "perf-policy" grants account "scaratec-gmail"
    And policy "perf-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | whitelist | NONE    |
    And policy "perf-policy" sets folder "INBOX" rules to:
      | match                   | grant    |
      | from_domain=example.com | ENVELOPE |

  Scenario: list_messages across 20 matching messages opens at most 2 IMAP connections
    Given the folder "scaratec-gmail:INBOX" holds 20 messages from "billing@example.com" dated "2026-05-06T09:00:00Z"
    When perf-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {"newer_than": "30d"}, limit 5
    Then the response decision is ALLOW
    And the response contains 5 messages
    And the response field total_visible equals 20
    And the mock-gmail server received between 1 and 2 IMAP connections

  Scenario: search across 20 matching messages opens at most 2 IMAP connections
    Given the folder "scaratec-gmail:INBOX" holds 20 messages from "billing@example.com" dated "2026-05-06T09:00:00Z"
    When perf-agent calls search with account "scaratec-gmail", folder "INBOX", criteria {"newer_than": "30d"}
    Then the response decision is ALLOW
    And the response field total_visible equals 20
    And the response field uids has length 20
    And the mock-gmail server received between 1 and 2 IMAP connections
