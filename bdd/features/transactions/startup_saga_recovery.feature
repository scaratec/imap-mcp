@test_hooks
Feature: Automatic saga recovery on process startup with cross-instance WAL leases

  ADR 0046 §4 and ADR 0051 §3 require the server to scan wal.db on
  startup and drive unleased non-terminal transactions for its caller_id
  to completion automatically — without calling any test-only recovery
  tool. SQLite leases prevent concurrent local instances from executing
  duplicate recovery writes on the same transaction.
  Audit hash-chain rule (ADR 0048): record i carries prev_hash = "sha256:" +
  lowercase hex sha256(raw UTF-8 line i-1 including trailing LF), with
  day-0 genesis "sha256:" + 64 zeros.

  Covered error layers (per BDD Guidelines §4.5):
    - Interrupted post-APPEND saga commits automatically on restart   : 1
    - Interrupted pre-APPEND saga aborts automatically on restart     : 1
    - Unreachable target increments retry_count and leaves server up  : 1
    - Clean startup with empty WAL emits no recovery audit events     : 1
    - Saga already at retry_limit escalates to needs_operator         : 1
    - Missing wal.db file at first boot starts cleanly                : 1
    - Recovery writes chained saga_transition records across restart  : 1
    - Second restart is idempotent on committed sagas                 : 1
    - Concurrent startup of two instances leases saga once, 1 target  : 1
    Total enumerated                                                  : 9   covered by this feature: 9

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the audit log directory is a fresh $TMPDIR/audit
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path      |
      | INBOX            |
      | INBOX/Rechnungen |
    And the IMAP account "personal" exists with folders:
      | folder path   |
      | INBOX         |
      | Archiv/Belege |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the IMAP traffic for "personal" is captured through a proxy
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants account "personal"
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder           | mode      | default | move_out | accept_incoming | rules |
      | gupta-scaratec | INBOX/Rechnungen | blacklist | FULL    | true     | false           | []    |
      | personal       | Archiv/Belege    | blacklist | FULL    | false    | true            | []    |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id            | from_address         | to_address            | subject      | date                 |
      | 901 | <inv-901@hornbach.de> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 901 | 2026-05-06T09:00:00Z |
    And the WAL is empty

  Scenario: A saga interrupted after APPEND completes automatically on server restart
    Given the server is armed with IMAP_MCP_CRASH_AT="post_append"
    When invoice-agent invokes move expecting crash for uid 901 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    Then the WAL entry for the crashed move has status "fetched"
    When the server is restarted without crash hooks
    And invoice-agent calls list_accounts
    Then within 10 seconds the WAL entry for the crashed move has status "committed"
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<inv-901@hornbach.de>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<inv-901@hornbach.de>" returns zero results

  Scenario: A saga interrupted after BEGIN before FETCH aborts automatically on server restart
    Given the server is armed with IMAP_MCP_CRASH_AT="post_begin"
    When invoice-agent invokes move expecting crash for uid 901 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    When the server is restarted without crash hooks
    And invoice-agent calls list_accounts
    Then within 10 seconds the WAL entry for the crashed move has status "aborted"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<inv-901@hornbach.de>" returns exactly one result
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<inv-901@hornbach.de>" returns zero results

  Scenario: An unreachable destination during startup recovery increments retry_count and leaves the server serving
    Given the server is armed with IMAP_MCP_CRASH_AT="post_fetch"
    When invoice-agent invokes move expecting crash for uid 901 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And the IMAP proxy for "personal" refuses all incoming TCP connections
    And the server is restarted without crash hooks
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response decision is ALLOW
    And within 10 seconds the WAL entry for the crashed move has retry_count >= 1
    And the WAL entry for the crashed move has status "fetched"

  Scenario: A clean startup with an empty WAL emits no saga_transition records
    When the server is restarted without crash hooks
    And invoice-agent calls list_accounts
    Then the WAL contains 0 transaction entries
    And the audit log contains 0 records with tool "saga_transition"

  Scenario: A transaction already at retry_limit escalates to needs_operator on startup without new target APPENDs
    Given the WAL retry_limit is configured to 3
    And the server is armed with IMAP_MCP_CRASH_AT="post_fetch"
    When invoice-agent invokes move expecting crash for uid 901 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And the WAL retry_count for the crashed move is set to 3
    And the IMAP command log for "personal" is cleared
    And the server is restarted without crash hooks
    And invoice-agent calls list_accounts
    Then within 10 seconds the WAL entry for the crashed move has status "needs_operator"
    And the WAL entry for the crashed move has retry_count 3
    And the IMAP command log for "personal" contains no APPEND command

  Scenario: A configured WAL path whose database file does not yet exist initializes cleanly on startup
    Given the WAL database file is removed before startup
    When the server is restarted without crash hooks
    And invoice-agent calls list_accounts
    Then the response decision is ALLOW
    And the WAL database file exists with mode 0600 and 0 transaction entries

  Scenario: Startup recovery writes chained saga_transition audit entries across the restart boundary
    Given the server is armed with IMAP_MCP_CRASH_AT="post_append"
    When invoice-agent invokes move expecting crash for uid 901 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And the server is restarted without crash hooks
    And invoice-agent calls list_accounts
    Then within 10 seconds the WAL entry for the crashed move has status "committed"
    And file "2026-05-07.jsonl" contains a record with tool "saga_transition" and step "commit"
    And all records of file "2026-05-07.jsonl" form a valid hash chain with consecutive seq numbers starting at 0

  Scenario: A second restart does not re-apply an already committed saga
    Given the server is armed with IMAP_MCP_CRASH_AT="post_append"
    When invoice-agent invokes move expecting crash for uid 901 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And the server is restarted without crash hooks
    Then within 10 seconds the WAL entry for the crashed move has status "committed"
    When the IMAP command log for "personal" is cleared
    And the server is restarted without crash hooks
    And invoice-agent calls list_accounts
    Then the WAL entry for the crashed move has status "committed"
    And the IMAP command log for "personal" contains no APPEND command
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<inv-901@hornbach.de>" returns exactly one result

  Scenario: Two server instances starting concurrently lease a pending saga once without duplicating target mail
    Given the server is armed with IMAP_MCP_CRASH_AT="post_fetch"
    When invoice-agent invokes move expecting crash for uid 901 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And two server instances start concurrently sharing the same wal.db and config
    Then within 10 seconds the WAL entry for the crashed move has status "committed"
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<inv-901@hornbach.de>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<inv-901@hornbach.de>" returns zero results
