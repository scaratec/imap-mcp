@test_hooks
Feature: Audit trail shared across concurrent local server instances and pre-effect fail-closed writes

  Multiple local stdio server instances configured with the same audit
  directory coordinate through flock on .audit.lock, continue a single
  daily SHA-256 hash chain from disk, seal past UTC days idempotently
  with eof_day (mode 0400), and fsync a pre-effect intent record before
  executing any write side-effect so that an unwritable audit trail
  prevents IMAP mutations completely (ADR 0048).

  Hash-chain rule: record i carries prev_hash = "sha256:" + sha256(raw
  UTF-8 line i-1 including trailing LF), with day-0 genesis
  "sha256:" + 64 zeros and day rollover chaining onto final_hash of the
  previous day's eof_day trailer.

  Covered error layers (per BDD Guidelines §4.5):
    - Day rolled by instance B while instance A stays running      : 1
    - Starting further instances leaves sealed day file untouched  : 1
    - Interleaved writes of two instances form one hash chain      : 1
    - Simultaneous burst writes of two instances form one chain    : 1
    - Restart within the same day continues the day's chain        : 1
    - OAuth token refresh failure recorded in the shared chain     : 1
    - Unwritable audit aborts read call with explicit error        : 1
    - Unwritable audit aborts write call BEFORE any IMAP mutation  : 1
    Total enumerated                                               : 8   covered by this feature: 8

  Background:
    Given the audit log directory is a fresh $TMPDIR/audit
    And the shared fake clock file is set to "2026-10-15T21:00:00Z"
    And secret_store configuration is:
      """
      backend: file_dir
      path: $TMPDIR/secrets
      acknowledge_plaintext: true
      """
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the server is configured with single caller:
      | caller_id     | auth_type     | policy         |
      | invoice-agent | stdio_trusted | invoice-policy |
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder | mode      | default  | draft_append |
      | gupta-scaratec | INBOX  | blacklist | ENVELOPE | false        |
      | gupta-scaratec | Drafts | blacklist | FULL     | true         |

  Scenario: A running instance keeps auditing after another instance has sealed the previous day
    Given server instance "A" is running
    And instance "A" calls list_accounts
    When the shared fake clock file advances to "2026-10-16T03:59:00Z"
    And server instance "B" starts and calls list_accounts
    And instance "A" calls get_caller_identity
    Then the call by instance "A" succeeds
    And file "2026-10-16.jsonl" contains a record with tool "get_caller_identity"
    And file "2026-10-15.jsonl" ends with a record of tool "eof_day" carrying field final_hash
    And file "2026-10-16.jsonl" begins with a record whose prev_hash equals that final_hash
    And the audit file "2026-10-15.jsonl" has mode 0400

  Scenario: Starting further instances leaves an already sealed day file byte-for-byte untouched
    Given server instance "A" is running
    And instance "A" calls list_accounts
    And the shared fake clock file advances to "2026-10-16T08:00:00Z"
    And instance "A" calls list_accounts
    And the exact bytes of audit file "2026-10-15.jsonl" are remembered
    When server instance "B" starts and calls list_accounts
    And server instance "C" starts and calls list_accounts
    Then the audit file "2026-10-15.jsonl" equals the remembered bytes
    And file "2026-10-15.jsonl" contains exactly 1 record with tool "eof_day"

  Scenario: Interleaved calls of two running instances form one continuous hash chain
    Given server instance "A" is running
    And server instance "B" is running
    When instance "A" calls list_accounts
    And instance "B" calls list_accounts
    And instance "A" calls list_accounts
    And instance "B" calls list_accounts
    Then file "2026-10-15.jsonl" contains 4 records with tool "list_accounts"
    And all records of file "2026-10-15.jsonl" form a valid hash chain with consecutive seq numbers starting at 0

  Scenario: Simultaneous burst calls of two running instances form one continuous hash chain
    Given server instance "A" is running
    And server instance "B" is running
    When instances "A" and "B" each call list_accounts 15 times concurrently
    Then file "2026-10-15.jsonl" contains 30 records with tool "list_accounts"
    And all records of file "2026-10-15.jsonl" form a valid hash chain with consecutive seq numbers starting at 0

  Scenario: An instance started after another exits within the same day continues the day's chain
    Given server instance "A" is running
    And instance "A" calls list_accounts
    And instance "A" calls list_accounts
    When server instance "A" stops cleanly
    And server instance "B" starts and calls list_accounts
    Then all records of file "2026-10-15.jsonl" form a valid hash chain with consecutive seq numbers starting at 0

  Scenario: An OAuth token refresh event is chained into the active UTC day file
    Given the mock OAuth2 server is reset and healthy
    And account "gmail-archive" is configured for OAuth2 against the mock OAuth2 server and mock-gmail with scope "https://mail.google.com/" and token_cache "memory_only"
    And the secret store contains a refresh token for "gmail-archive"
    And the mock OAuth2 server is primed to reject the next refresh_token exchange with OAuth error "invalid_grant"
    And policy "invoice-policy" grants account "gmail-archive"
    And policy "invoice-policy" grants folder on account "gmail-archive":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    When server instance "A" starts and calls list_folders with account "gmail-archive"
    And instance "A" calls list_accounts
    Then file "2026-10-15.jsonl" contains 1 record with tool "token_refresh"
    And all records of file "2026-10-15.jsonl" form a valid hash chain with consecutive seq numbers starting at 0

  Scenario: An unwritable audit trail fails read calls closed with an explicit error
    Given the test runner is not executing as root
    And server instance "A" is running
    And instance "A" calls list_accounts
    And the audit directory and current day file are made read-only
    When instance "A" calls get_caller_identity
    Then the call by instance "A" fails with an error containing "audit trail not writable"

  Scenario: An unwritable audit trail aborts create_draft at the intent stage before any IMAP APPEND occurs
    Given the test runner is not executing as root
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And server instance "A" is running
    And instance "A" calls list_accounts
    And the audit directory and current day file are made read-only
    When instance "A" calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["client@example.org"],
       "subject": "Darf ohne Audit nicht landen",
       "body_text": "Kein ungeloggter Seiteneffekt."}
      """
    Then the call by instance "A" fails with an error containing "audit trail not writable"
    And the IMAP command log for "gupta-scaratec" contains no APPEND command
    And the IMAP folder "Drafts" contains no message with subject "Darf ohne Audit nicht landen"
