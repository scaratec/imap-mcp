Feature: Audit trail shared by several local server instances

  An MCP client such as OpenCode starts one stdio server per session or
  sub-agent, and every one of them is configured with the same audit
  directory. The instances therefore share one audit trail: the day
  files, the day roll that seals a file with its `eof_day` record, and
  the hash chain. Whichever instance writes first on a new UTC day
  closes the previous day; every other instance continues the chain
  from what is on disk, not from what it remembers. A sealed file is
  never written to again. See ADR 0021, ADR 0022 and ADR 0032.

  Regression for the incident of 2026-10-16: a long-running instance
  kept targeting the previous day's file after another instance had
  sealed it at start-up, and every tool call failed with
  "[Errno 13] Permission denied" until the process was restarted.

  The audit clock is shared by all instances of a scenario and can be
  moved while they run (test hook `IMAP_MCP_FAKE_NOW_FILE`, ADR 0023).

  Covered error layers (per BDD Guidelines §4.5):
    - Day rolled by another instance while a writer is running : 1
    - Start-up of further instances on an already sealed day   : 1
    - Interleaved writes of concurrent instances               : 1
    - Simultaneous writes of concurrent instances              : 1
    - Restart within the same day                              : 1
    - Records written outside the audit writer (token refresh) : 1
    - Audit trail not writable                                 : 1
    Total enumerated                                           : 7   covered by this feature: 7

  Background:
    Given the audit log directory is a fresh $TMPDIR/audit
    And the audit clock reads "2026-10-15T21:00:00Z"

  Scenario: A running instance keeps auditing after another instance has sealed the previous day
    Given the server is started with a minimal configuration
    And server instance "A" is running
    And instance "A" has called list_accounts
    When the audit clock advances to "2026-10-16T03:59:00Z"
    And server instance "B" starts
    And instance "A" calls get_caller_identity
    Then the call by instance "A" succeeds
    And file "2026-10-16.jsonl" contains a record with tool "get_caller_identity" written by instance "A"
    And file "2026-10-15.jsonl" ends with a record of tool "eof_day" carrying field final_hash
    And file "2026-10-16.jsonl" begins with a record whose prev_hash equals that final_hash
    And the audit file "2026-10-15.jsonl" has mode 0400

  Scenario: Starting further instances leaves an already sealed day file untouched
    Given the server is started with a minimal configuration
    And server instance "A" is running
    And instance "A" has called list_accounts
    And the audit clock advances to "2026-10-16T08:00:00Z"
    And instance "A" has called list_accounts
    And the content of audit file "2026-10-15.jsonl" is remembered
    When server instance "B" starts
    And server instance "C" starts
    And instance "B" calls list_accounts
    Then the audit file "2026-10-15.jsonl" is byte-for-byte unchanged
    And file "2026-10-15.jsonl" contains exactly 1 record with tool "eof_day"

  Scenario: Interleaved calls of two running instances form one hash chain
    Given the server is started with a minimal configuration
    And server instance "A" is running
    And server instance "B" is running
    When instance "A" calls list_accounts
    And instance "B" calls list_accounts
    And instance "A" calls list_accounts
    And instance "B" calls list_accounts
    Then file "2026-10-15.jsonl" contains exactly 4 records with tool "list_accounts"
    And the records of file "2026-10-15.jsonl" form one hash chain with consecutive seq numbers starting at 0

  Scenario: Simultaneous calls of two running instances form one hash chain
    Given the server is started with a minimal configuration
    And server instance "A" is running
    And server instance "B" is running
    When instances "A" and "B" each call list_accounts 15 times at the same time
    Then file "2026-10-15.jsonl" contains exactly 30 records with tool "list_accounts"
    And the records of file "2026-10-15.jsonl" form one hash chain with consecutive seq numbers starting at 0

  Scenario: An instance restarted within the same day continues the day's chain
    Given the server is started with a minimal configuration
    And server instance "A" is running
    And instance "A" has called list_accounts
    And instance "A" has called list_accounts
    When server instance "A" stops
    And server instance "B" starts
    And instance "B" calls list_accounts
    Then the call by instance "B" succeeds
    And the records of file "2026-10-15.jsonl" form one hash chain with consecutive seq numbers starting at 0

  Scenario: A token refresh is recorded in the hash chain of the day it belongs to
    Given the secret store "file_dir" is configured at path $TMPDIR/secrets
    And the server is configured with account:
      | id            | provider    | host                | oauth_scope              | token_cache |
      | gmail-archive | google-mock | imap.localhost-mock | https://mail.google.com/ | memory_only |
    And the secret store contains a refresh token for "gmail-archive"
    And the mock OAuth2 provider responds to the next token-exchange request with error "invalid_grant"
    When the server attempts to open an IMAP connection to "gmail-archive"
    And invoice-agent calls list_accounts
    Then file "2026-10-15.jsonl" contains exactly 1 record with tool "token_refresh"
    And the records of file "2026-10-15.jsonl" form one hash chain with consecutive seq numbers starting at 0

  Scenario: An audit trail that cannot be written is reported as such
    Given the server is started with a minimal configuration
    And server instance "A" is running
    And instance "A" has called list_accounts
    And the audit trail has been made unwritable
    When instance "A" calls get_caller_identity
    Then the call by instance "A" fails with an error containing "audit trail not writable"
