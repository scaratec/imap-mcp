@test_hooks
Feature: Automatic day-roll audit retention, safe root-hash hook, and out-of-band tampering detection

  ADR 0048 enforces automatic three-stage audit retention whenever past
  UTC day files are sealed (at startup or on the first write after UTC
  midnight) without relying on a test-only tool:
    - Hot:     age_days <= hot_days (default 90) -> plain .jsonl
    - Warm:    hot_days < age_days <= delete_after_days (default 365) -> .jsonl.gz (mode 0400)
    - Deleted: age_days > delete_after_days -> unlinked with a retention_delete record
  External root-hash hooks execute as argv without a shell and under a
  scrubbed environment so secrets never leak to child processes.

  Covered error layers (per BDD Guidelines §4.5):
    - Automatic Hot -> Warm gzip on UTC day roll preserves SHA-256 & 0400 : 1
    - Automatic Warm -> Deleted unlink emits retention_delete with age    : 1
    - Custom retention parameter boundaries (6 age combinations)          : 6
    - No MCP tool reads or leaks audit records                            : 1
    - Safe external_root_hook receives final_hash without secret env vars : 1
    - Out-of-band deletion of active audit file logged and chained        : 1
    Total enumerated                                                      : 11  covered by this feature: 11

  Background:
    Given secret_store configuration is:
      """
      backend: file_dir
      path: $TMPDIR/secrets
      acknowledge_plaintext: true
      """
    And the server is configured with single caller:
      | caller_id     | auth_type     | policy         |
      | invoice-agent | stdio_trusted | invoice-policy |
    And policy "invoice-policy" grants no accounts

  Scenario: Automatic UTC day roll gzips files older than hot_days with mode 0400 and lossless content
    Given the shared fake clock file is set to "2026-04-21T23:59:50Z"
    And the server is configured with audit:
      | directory     | hot_days | warm_days | delete_after_days |
      | $TMPDIR/audit | 90       | 275       | 365               |
    And the audit directory contains historical sealed day files:
      | filename         |
      | 2025-12-20.jsonl |
      | 2026-03-15.jsonl |
    And the SHA-256 digest of plain file "$TMPDIR/audit/2025-12-20.jsonl" is remembered
    And invoice-agent calls list_accounts
    When the shared fake clock file advances to "2026-04-22T00:00:10Z"
    And invoice-agent calls list_accounts
    Then the file "$TMPDIR/audit/2025-12-20.jsonl.gz" exists with mode 0400
    And the file "$TMPDIR/audit/2025-12-20.jsonl" does not exist
    And decompressing "$TMPDIR/audit/2025-12-20.jsonl.gz" yields the remembered SHA-256 digest
    And the file "$TMPDIR/audit/2026-03-15.jsonl" exists as plain JSONL
    And the file "$TMPDIR/audit/2026-04-21.jsonl" exists with mode 0400

  Scenario: Automatic UTC day roll deletes archives older than delete_after_days and records filename and age_days
    Given the shared fake clock file is set to "2026-04-21T23:59:50Z"
    And the server is configured with audit:
      | directory     | hot_days | warm_days | delete_after_days |
      | $TMPDIR/audit | 90       | 275       | 365               |
    And the audit directory contains historical compressed archive "2024-01-12.jsonl.gz"
    And invoice-agent calls list_accounts
    When the shared fake clock file advances to "2026-04-22T00:00:10Z"
    And invoice-agent calls list_accounts
    Then the file "$TMPDIR/audit/2024-01-12.jsonl.gz" does not exist
    And file "2026-04-22.jsonl" contains a record with:
      | field    | value                |
      | tool     | retention_delete     |
      | filename | 2024-01-12.jsonl.gz  |
      | age_days | 831                  |

  Scenario Outline: Custom retention parameters classify aged day files at day roll
    Given the shared fake clock file is set to "2026-04-21T23:59:50Z"
    And the server is configured with audit hot_days=<hot>, warm_days=<warm>, delete_after_days=<delete>
    And the audit directory contains historical sealed file "<hist_file>"
    And invoice-agent calls list_accounts
    When the shared fake clock file advances to "2026-04-22T00:00:10Z"
    And invoice-agent calls list_accounts
    Then the retention state of base date "<base_date>" in "$TMPDIR/audit" is "<final_state>"

    Examples:
      | hot | warm | delete | base_date  | hist_file        | final_state |
      | 2   | 5    | 7      | 2026-04-20 | 2026-04-20.jsonl | hot_sealed  |
      | 1   | 5    | 6      | 2026-04-19 | 2026-04-19.jsonl | warm        |
      | 1   | 5    | 6      | 2026-04-14 | 2026-04-14.jsonl | deleted     |
      | 90  | 275  | 365    | 2026-01-23 | 2026-01-23.jsonl | hot_sealed  |
      | 90  | 275  | 365    | 2026-01-10 | 2026-01-10.jsonl | warm        |
      | 90  | 275  | 365    | 2025-04-01 | 2025-04-01.jsonl | deleted     |

  Scenario: No MCP tool reads or echoes audit log records
    Given the shared fake clock file is set to "2026-04-21T12:00:00Z"
    And the server is configured with audit:
      | directory     | hot_days | warm_days | delete_after_days |
      | $TMPDIR/audit | 90       | 275       | 365               |
    And invoice-agent calls list_accounts 5 times
    When invoice-agent calls list_accounts, describe_policy, and tool_surface_info
    Then none of the tool responses contains any prev_hash value present in "$TMPDIR/audit/2026-04-21.jsonl"
    When invoice-agent calls the MCP method "tools/call" with name "get_audit_log"
    Then the server responds with JSON-RPC error code -32601

  Scenario: External root-hash hook receives the exact eof_day final_hash under a scrubbed environment
    Given the shared fake clock file is set to "2026-04-21T23:59:50Z"
    And an executable root-hook recorder script exists at "$TMPDIR/record_root.py" writing its argv and environment keys to "$TMPDIR/hook_out.json"
    And the server process environment includes:
      | name                         | value              |
      | IMAP_MCP_SECRET__DUMMY__LEAK | top-secret-do-drop |
    And the server is configured with audit directory "$TMPDIR/audit" and external_root_hook ["$TMPDIR/record_root.py", "%FINAL_HASH%"]
    And invoice-agent calls list_accounts
    When the shared fake clock file advances to "2026-04-22T00:00:10Z"
    And invoice-agent calls list_accounts
    Then "$TMPDIR/hook_out.json" records 1 invocation whose argument equals the final_hash field of the eof_day record in "2026-04-21.jsonl"
    And "$TMPDIR/hook_out.json" environment keys do NOT include "IMAP_MCP_SECRET__DUMMY__LEAK"

  Scenario: Out-of-band deletion of the active day file is logged critically and recorded with audit_file_missing
    Given the shared fake clock file is set to "2026-04-21T12:00:00Z"
    And the server is configured with audit:
      | directory     | hot_days | warm_days | delete_after_days |
      | $TMPDIR/audit | 90       | 275       | 365               |
    And invoice-agent calls list_accounts
    When the file "$TMPDIR/audit/2026-04-21.jsonl" is deleted out-of-band
    And invoice-agent calls get_caller_identity
    Then the server stderr contains a critical log line mentioning "2026-04-21.jsonl"
    And file "2026-04-21.jsonl" contains a record with:
      | field            | value               |
      | tool             | audit_file_missing  |
      | reason           | audit_file_missing  |
      | missing_filename | 2026-04-21.jsonl    |
