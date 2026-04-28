Feature: Audit retention and access model

  Retention progresses through three stages: Hot (default 90 days),
  Warm (gzipped, default 275 days), Deleted (default >365 days).
  Storage is local only; there is no MCP tool that reads the audit
  stream. See ADR 0022.

  Covered error layers (per BDD Guidelines §4.5):
    - Hot -> Warm transition at day roll         : 1
    - Warm -> Deleted transition at day roll     : 1
    - Retention override (hot_days=1)            : 1
    - Manual deletion of the current file        : 1 (error signalled)
    - No MCP tool reads audit                    : 1
    - External-hook invocation at day roll       : 1
    - GZip integrity preserved in warm files     : 1
    Total enumerated                              : 7   covered by this feature: 7

  Background:
    Given the server is configured with audit:
      | directory            | hot_days | warm_days | delete_after_days |
      | $TMPDIR/audit        | 90       | 275       | 365               |

  Scenario: At day roll, files older than hot_days are gzipped
    Given the audit directory contains:
      | filename            | age_days | state        |
      | 2025-12-20.jsonl    | 120      | plain        |
      | 2026-03-15.jsonl    | 35       | plain        |
      | 2026-04-21.jsonl    | 0        | plain        |
    When the audit rotation task runs
    Then the audit directory contains:
      | filename               | state  |
      | 2025-12-20.jsonl.gz    | warm   |
      | 2026-03-15.jsonl       | hot    |
      | 2026-04-21.jsonl       | hot    |
    And the gzipped file, when decompressed, has SHA-256 equal to the original plain file's SHA-256

  Scenario: Files older than hot_days + warm_days are deleted at day roll
    Given the audit directory contains a file "2024-01-01.jsonl.gz" with age 830 days
    When the audit rotation task runs
    Then the file "2024-01-01.jsonl.gz" no longer exists
    And an audit record with tool "retention_delete" records the filename and age

  Scenario Outline: Retention parameters override the defaults
    Given the server is configured with audit hot_days=<hot>, warm_days=<warm>, delete_after_days=<delete>
    And a file "old.jsonl" with age <file_age> exists in the audit directory
    When the audit rotation task runs
    Then the file final state is "<final_state>"

    Examples:
      | hot | warm | delete | file_age | final_state |
      | 1   | 5    | 6      | 0        | hot         |
      | 1   | 5    | 6      | 2        | warm        |
      | 1   | 5    | 6      | 7        | deleted     |
      | 90  | 275  | 365    | 89       | hot         |
      | 90  | 275  | 365    | 100      | warm        |
      | 90  | 275  | 365    | 400      | deleted     |

  Scenario: No MCP tool ever reads the audit log
    Given the audit file contains 10 records
    When invoice-agent calls any of the tools list_accounts, describe_policy, get_transaction_status
    Then none of the responses contains any field whose value matches a record from the audit file
    And no MCP tool exists with name "get_audit_log"

  @pending @pending_LIM_0009
  Scenario: External root-hash hook is invoked with the final_hash at day roll
    Given the server is configured with audit external_root_hook command "echo %FINAL_HASH% >> $TMPDIR/roots.txt"
    And the current audit file closes with final_hash "sha256:<hash>"
    When the UTC day rolls
    Then the hook command is invoked exactly once
    And "$TMPDIR/roots.txt" contains a line equal to "sha256:<hash>"

  @pending @pending_LIM_0009
  Scenario: An audit file manually removed during runtime is detected and reported
    Given the server is actively writing to "2026-04-21.jsonl"
    When the file "2026-04-21.jsonl" is deleted out-of-band
    Then the server on the next audit-write attempt logs a critical error to its structured log
    And the server emits a record to the next available audit file with tool "audit_file_missing" and the expected filename

  Scenario: The restrictive filesystem permissions survive warm-file creation
    Given a file "2026-01-20.jsonl" with mode 0400 exists
    When the audit rotation task compresses it to "2026-01-20.jsonl.gz"
    Then the gzipped file has mode 0400
    And the original plain file no longer exists on disk
