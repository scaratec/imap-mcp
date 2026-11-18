Feature: Bounded per-account IMAP connection pool with NOOP healthcheck and pool_exhausted timeout

  ADR 0055 pools authenticated IMAP connections per account with
  multi-instance-safe defaults (max_size=2, idle_ttl=120s, max_age=1800s,
  acquire_timeout=30s):
    - Sequential calls reuse an idle connection after verifying liveness
      with NOOP; a dropped socket is evicted and replaced transparently.
    - Every mailbox operation issues SELECT before UID commands.
    - Concurrent calls never exceed max_size; waiting longer than
      acquire_timeout returns ALLOW + ERROR with error.type="pool_exhausted".
    - Clean shutdown closes all pooled connections with LOGOUT.

  Covered error layers (per BDD Guidelines §4.5):
    - Sequential calls reuse 1 connection with NOOP healthcheck   : 1
    - Two accounts maintain strictly separate connection pools    : 1
    - Folder switch on a reused connection issues SELECT first    : 1
    - Dropped pooled connection evicted after failed NOOP         : 1
    - Concurrent calls bounded by default max_size=2 (peak == 2)  : 1
    - Acquire timeout returns ALLOW + ERROR pool_exhausted        : 1
    - Pooled connections send LOGOUT on clean server shutdown     : 1
    Total enumerated                                              : 7   covered by this feature: 7

  Background:
    Given the server clock is pinned to "2026-11-02T12:00:00Z"
    And the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path    |
      | INBOX          |
      | [Gmail]/Drafts |
    And the server is configured with caller "pool-agent" using policy "pool-policy"
    And policy "pool-policy" grants account "scaratec-gmail"
    And policy "pool-policy" grants folder on account "scaratec-gmail":
      | folder         | mode      | default | rules |
      | INBOX          | blacklist | FULL    | []    |
      | [Gmail]/Drafts | blacklist | FULL    | []    |
    And the folder "scaratec-gmail:INBOX" holds messages:
      | uid | from_address                   | to_address     | subject               | date                 |
      | 501 | rechnung@nordlicht.example.org | me@example.org | Ihre Rechnung 2026-10 | 2026-10-30T07:00:00Z |
      | 502 | mara.lindqvist@example.org     | me@example.org | Angebot               | 2026-10-29T07:00:00Z |
    And the folder "scaratec-gmail:[Gmail]/Drafts" holds messages:
      | uid | from_address           | to_address     | subject   | date                 |
      | 601 | pool-agent@example.org | me@example.org | Entwurf A | 2026-10-31T07:00:00Z |

  Scenario: Five sequential calls on one account share one connection and issue NOOP before reuse
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    And pool-agent calls fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 501
    And pool-agent calls fetch_body with account "scaratec-gmail", folder "INBOX", uid 501
    And pool-agent calls search with account "scaratec-gmail", folder "INBOX", criteria {"from_domain": "nordlicht.example.org"}, scope "all"
    And pool-agent calls fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 502
    Then the mock-gmail server received exactly 1 IMAP connection
    And the IMAP command log for "scaratec-gmail" contains 1 LOGIN or AUTHENTICATE command
    And the IMAP command log for "scaratec-gmail" contains at least 4 NOOP commands

  Scenario: Two accounts maintain separate pools of one connection each
    Given the IMAP account "archive-srv" exists with provider "imap-standard" and folders:
      | folder path |
      | Archiv      |
    And the IMAP traffic for "archive-srv" is captured through a proxy
    And policy "pool-policy" grants account "archive-srv"
    And policy "pool-policy" grants folder on account "archive-srv":
      | folder | mode      | default | rules |
      | Archiv | blacklist | FULL    | []    |
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    And pool-agent calls list_messages with account "archive-srv", folder "Archiv", criteria {}, scope "all"
    And pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    And pool-agent calls list_messages with account "archive-srv", folder "Archiv", criteria {}, scope "all"
    Then the mock-gmail server received exactly 1 IMAP connection
    And the IMAP connections opened for account "archive-srv" number exactly 1

  Scenario: A reused connection issues SELECT before working in each switched folder
    When pool-agent calls search with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    And pool-agent calls search with account "scaratec-gmail", folder "[Gmail]/Drafts", criteria {}, scope "all"
    Then the response field uids equals [601]
    When pool-agent calls search with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals [501, 502]
    And the mock-gmail server received exactly 1 IMAP connection
    And the IMAP command log for "scaratec-gmail" contains in order:
      | command                 |
      | SELECT "INBOX"          |
      | SELECT "[Gmail]/Drafts" |
      | SELECT "INBOX"          |

  Scenario: A pooled connection dropped by the server is replaced after a failed NOOP
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    And the mock-gmail server closes every open connection
    And pool-agent calls fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 501
    Then the response decision is ALLOW
    And the response field subject equals "Ihre Rechnung 2026-10"
    And the mock-gmail server received exactly 2 IMAP connections

  Scenario: Concurrent calls on one account reach and never exceed default max_size=2
    Given the mock-gmail server delays each UID FETCH response by 300 milliseconds
    When pool-agent issues 6 concurrent calls of fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 501
    Then all 6 responses have decision ALLOW
    And the mock-gmail peak concurrent connections equalled 2

  Scenario: Exhausting acquire_timeout returns ALLOW + ERROR with error.type=pool_exhausted
    Given the server's connection pool is configured with max_size 1 and acquire_timeout 1 second
    And the mock-gmail server delays the next UID FETCH response by 2500 milliseconds
    When pool-agent issues 2 concurrent calls of fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 501
    Then 1 response has result "OK" and 1 response has result "ERROR" with error.type "pool_exhausted"

  Scenario: Pooled connections send LOGOUT when the server shuts down cleanly
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    And the MCP test client closes the server's stdin immediately after the response
    Then within 5 seconds the server process has exited
    And the IMAP command log for "scaratec-gmail" ends with a LOGOUT command
