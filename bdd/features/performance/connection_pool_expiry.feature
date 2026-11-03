Feature: Pooled IMAP connections are closed after the idle TTL and the maximum age

  ADR 0013 bounds how long a pooled connection lives: one that was not
  used for the idle TTL (default 10 minutes) is closed, and every
  connection is closed once it reaches the maximum age (default 1 hour),
  however busy it was. Servers drop idle sessions on their own schedule
  (Gmail after about 30 minutes) and count open sessions against a
  per-account limit; a pool that kept connections forever would hold
  slots and dead sockets.

  connection_pool.feature covers reuse, separate pools, the NOOP check,
  the size bound and the logout on shutdown. This feature covers the
  two lifetimes. Both are operator settings (README, Environment
  variables): IMAP_MCP_POOL_IDLE_TTL and IMAP_MCP_POOL_MAX_AGE, in
  seconds. The scenarios set them to seconds so the expiry is observable
  within a scenario; the defaults are not changed by them.

  Independent-channel rule. That a connection was closed is read from
  the mock-gmail server's command log (the LOGOUT the server sent), that
  a later call needed a new one from the mock's connection counter and
  the number of authentications in the log.

  Covered error layers (per BDD Guidelines §4.5):
    - Idle TTL reached, connection logged out and replaced        : 1
    - Maximum age reached, connection logged out and replaced     : 1
    Total enumerated                                              : 2   covered by this feature: 2

  Background:
    Given the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "pool-agent" using policy "pool-policy"
    And policy "pool-policy" grants account "scaratec-gmail"
    And policy "pool-policy" grants folder:
      | folder | mode      | default | rules |
      | INBOX  | blacklist | FULL    | []    |
    And the folder "INBOX" on "scaratec-gmail" holds messages:
      | uid | from                           | subject               | date                 |
      | 501 | rechnung@nordlicht.example.org | Ihre Rechnung 2026-10 | 2026-10-30T07:00:00Z |

  Scenario: A pooled connection idle for longer than the idle TTL is logged out and replaced
    Given the server's connection pool closes connections idle for 1 second
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    Then within 5 seconds the IMAP command log for "scaratec-gmail" ends with a LOGOUT command
    When pool-agent calls fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 501
    Then the response decision is ALLOW
    And the response field subject equals "Ihre Rechnung 2026-10"
    And the mock-gmail server received at most 2 IMAP connections
    And the IMAP command log for "scaratec-gmail" contains exactly 2 AUTHENTICATE commands

  Scenario: A pooled connection reaching the maximum age is logged out although the idle TTL is far off
    Given the server's connection pool closes connections idle for 600 seconds
    And the server's connection pool closes connections older than 2 seconds
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    Then within 6 seconds the IMAP command log for "scaratec-gmail" ends with a LOGOUT command
    When pool-agent calls fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 501
    Then the response decision is ALLOW
    And the response field subject equals "Ihre Rechnung 2026-10"
    And the mock-gmail server received at most 2 IMAP connections
    And the IMAP command log for "scaratec-gmail" contains exactly 2 AUTHENTICATE commands
