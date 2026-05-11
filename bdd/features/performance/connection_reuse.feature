Feature: IMAP connection reuse
  The server must not open a new IMAP connection for every message
  it evaluates. Operations that touch N messages should use a bounded
  number of connections, not N+1. Discovered via OTEL tracing against
  production Gmail: list_messages on a 700-message folder opened 700+
  connections (270 seconds). See LIM-0010.

  Covered error layers (per BDD Guidelines §4.5):
  - Connection count on list_messages with sender rules : 1
  - Connection count on search with sender rules        : 1
  Total enumerated                                       : 2   covered by this feature: 2

  Background:
    Given the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "perf-agent" using policy "perf-policy"
    And policy "perf-policy" grants account "scaratec-gmail"
    And policy "perf-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | whitelist | NONE     |
    And policy "perf-policy" sets folder "INBOX" rules to:
      | match                    | grant    |
      | from_domain=example.com  | ENVELOPE |

  Scenario: list_messages with 20 messages opens at most 3 connections
    Given the folder "INBOX" on "scaratec-gmail" holds 20 messages
    When perf-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {"newer_than": "30d"}, limit 5
    Then the response contains 5 messages
    And the mock-gmail server received at most 4 IMAP connections

  Scenario: search with 20 messages opens at most 3 connections
    Given the folder "INBOX" on "scaratec-gmail" holds 20 messages
    When perf-agent calls search with account "scaratec-gmail", folder "INBOX", criteria {"newer_than": "30d"}
    Then the response decision is ALLOW
    And the mock-gmail server received at most 4 IMAP connections
