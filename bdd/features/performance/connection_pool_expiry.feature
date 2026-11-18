Feature: Pooled IMAP connections are closed after idle_ttl and max_age

  ADR 0055 bounds connection lifetimes with idle_ttl (default 120 seconds,
  override IMAP_MCP_POOL_IDLE_TTL) and max_age (default 1800 seconds,
  override IMAP_MCP_POOL_MAX_AGE). Expired connections are closed with
  LOGOUT and replaced on subsequent calls.

  Covered error layers (per BDD Guidelines §4.5):
    - Idle TTL reached -> connection logged out and replaced  : 1
    - Maximum age reached -> connection logged out and replaced: 1
    Total enumerated                                          : 2   covered by this feature: 2

  Background:
    Given the server clock is pinned to "2026-11-02T12:00:00Z"
    And the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "pool-agent" using policy "pool-policy"
    And policy "pool-policy" grants account "scaratec-gmail"
    And policy "pool-policy" grants folder on account "scaratec-gmail":
      | folder | mode      | default | rules |
      | INBOX  | blacklist | FULL    | []    |
    And the folder "scaratec-gmail:INBOX" holds messages:
      | uid | from_address                   | to_address     | subject               | date                 |
      | 501 | rechnung@nordlicht.example.org | me@example.org | Ihre Rechnung 2026-10 | 2026-10-30T07:00:00Z |

  Scenario: A pooled connection idle for longer than idle_ttl is logged out and replaced
    Given the server's connection pool closes connections idle for 1 second
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    Then within 5 seconds the IMAP command log for "scaratec-gmail" ends with a LOGOUT command
    When pool-agent calls fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 501
    Then the response decision is ALLOW
    And the response field subject equals "Ihre Rechnung 2026-10"
    And the mock-gmail server received exactly 2 IMAP connections
    And the IMAP command log for "scaratec-gmail" contains 2 LOGIN or AUTHENTICATE commands

  Scenario: A pooled connection reaching max_age is logged out and replaced even while idle_ttl is far off
    Given the server's connection pool closes connections idle for 600 seconds
    And the server's connection pool closes connections older than 2 seconds
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    Then within 6 seconds the IMAP command log for "scaratec-gmail" ends with a LOGOUT command
    When pool-agent calls fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 501
    Then the response decision is ALLOW
    And the response field subject equals "Ihre Rechnung 2026-10"
    And the mock-gmail server received exactly 2 IMAP connections
    And the IMAP command log for "scaratec-gmail" contains 2 LOGIN or AUTHENTICATE commands
