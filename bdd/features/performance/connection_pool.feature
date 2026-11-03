Feature: Tool calls on one account share pooled IMAP connections

  ADR 0013 (Accepted) decides a per-account pool: a connection is leased
  to one call at a time, returned afterwards, checked with NOOP before
  reuse, and closed after an idle TTL or a maximum age. The server does
  not implement it: every tool call opens a TCP+TLS connection,
  authenticates (XOAUTH2 for Gmail), works, and logs out. Production
  audit data (2026-05 to 2026-11) shows a median of 1.0 s for
  fetch_envelope and 2.4 s for list_messages; an agent task of ten calls
  spends most of its wall time connecting. connection_reuse.feature
  already bounds connections within one call; this feature bounds them
  across calls.

  docs/error_path_analysis.md L13 defers the pool as "no separately
  observable behaviour at the MCP surface". That reason no longer holds:
  the mock-gmail server counts connections and the IMAP proxy logs every
  command, both independent of the server.

  Rules used below (ADR 0013 §Decision):
    - max_size per account 4; one lease per call.
    - Before reuse, a pooled connection is checked with NOOP; a failed
      check closes it and a new connection replaces it.
    - A pooled connection keeps its selected folder; a call needing a
      different folder issues SELECT first.

  Independent-channel rule. Connection counts come from the mock-gmail
  server's own counter, command order from the IMAP command log. Result
  correctness after a folder switch is checked against the seeded uids.

  Idle TTL and max age are covered in connection_pool_expiry.feature
  through short IMAP_MCP_POOL_* settings. Still deferred (see L13): the
  acquire timeout, because its response contract is undecided, and the
  token-refresh drain, because the mock speaks LOGIN only.

  Covered error layers (per BDD Guidelines §4.5):
    - Sequential calls on one account reuse one connection       : 1
    - Two accounts keep separate pools                           : 1
    - Folder switch on a reused connection selects first         : 1
    - Stale pooled connection replaced after a failed NOOP       : 1
    - Concurrent calls bounded by max_size                       : 1
    - Pooled connections logged out on shutdown                  : 1
    Total enumerated                                             : 6   covered by this feature: 6

  Background:
    Given the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path    |
      | INBOX          |
      | [Gmail]/Drafts |
    And the server is configured with caller "pool-agent" using policy "pool-policy"
    And policy "pool-policy" grants account "scaratec-gmail"
    And policy "pool-policy" grants folder:
      | folder         | mode      | default | rules |
      | INBOX          | blacklist | FULL    | []    |
      | [Gmail]/Drafts | blacklist | FULL    | []    |
    And the folder "INBOX" on "scaratec-gmail" holds messages:
      | uid | from                           | subject               | date                 |
      | 501 | rechnung@nordlicht.example.org | Ihre Rechnung 2026-10 | 2026-10-30T07:00:00Z |
      | 502 | mara.lindqvist@example.org     | Angebot               | 2026-10-29T07:00:00Z |
    And the folder "[Gmail]/Drafts" on "scaratec-gmail" holds messages:
      | uid | from                    | subject     | date                 |
      | 601 | pool-agent@example.org  | Entwurf A   | 2026-10-31T07:00:00Z |

  # --- Main path -------------------------------------------------------

  Scenario: Five sequential calls on one account use a single IMAP connection
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    And pool-agent calls fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 501
    And pool-agent calls fetch_body with account "scaratec-gmail", folder "INBOX", uid 501
    And pool-agent calls search with account "scaratec-gmail", folder "INBOX", criteria {"from_domain": "nordlicht.example.org"}, scope "all"
    And pool-agent calls fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 502
    Then the mock-gmail server received at most 1 IMAP connections
    And the IMAP command log for "scaratec-gmail" contains exactly 1 AUTHENTICATE command

  Scenario: Two accounts keep separate pools of one connection each
    Given the IMAP account "archive-srv" exists with provider "imap-standard" and folder "Archiv"
    And policy "pool-policy" grants account "archive-srv"
    And policy "pool-policy" grants folder on account "archive-srv":
      | folder | mode      | default | rules |
      | Archiv | blacklist | FULL    | []    |
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    And pool-agent calls list_messages with account "archive-srv", folder "Archiv", criteria {}, scope "all"
    And pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    And pool-agent calls list_messages with account "archive-srv", folder "Archiv", criteria {}, scope "all"
    Then the mock-gmail server received at most 1 IMAP connections
    And the IMAP connections opened for account "archive-srv" number exactly 1

  Scenario: A reused connection selects the folder a call needs before working in it
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    And pool-agent calls list_messages with account "scaratec-gmail", folder "[Gmail]/Drafts", criteria {}, scope "all"
    Then the response uids equals [601]
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    Then the response uids equals [501, 502]
    And the mock-gmail server received at most 1 IMAP connections
    And the IMAP command log for "scaratec-gmail" contains in order:
      | command                 |
      | SELECT "INBOX"          |
      | SELECT "[Gmail]/Drafts" |
      | SELECT "INBOX"          |

  # --- Error paths -----------------------------------------------------

  Scenario: A pooled connection the server dropped is replaced after a failed NOOP
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    And the mock-gmail server closes every open connection
    And pool-agent calls fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 501
    Then the response decision is ALLOW
    And the response field subject equals "Ihre Rechnung 2026-10"
    And the mock-gmail server received at most 2 IMAP connections

  Scenario: Concurrent calls on one account never exceed the pool size
    When pool-agent issues 6 concurrent calls of fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 501
    Then all 6 responses have decision ALLOW
    And the mock-gmail server never held more than 4 IMAP connections at the same time

  Scenario: Pooled connections are logged out when the server shuts down
    When pool-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    And the MCP test client closes the server's stdin immediately after the response
    Then within 5 seconds the server process has exited
    And the IMAP command log for "scaratec-gmail" ends with a LOGOUT command
