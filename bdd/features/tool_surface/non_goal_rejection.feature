Feature: Non-goal, retired, system-flag, and parameter-smuggling paths fail closed

  ADR 0045, ADR 0048, and ADR 0052 defend the mediation boundary by
  omission and strict input rejection:
    - Destructive, structural, admin, bypass, retired 2.x rewrite,
      deferred bulk_move (LIM 0011), and production-disabled _test_run_*
      tools return JSON-RPC -32601 and log reason="unknown_tool".
    - Every \-prefixed IMAP system flag (\Deleted, \Flagged, \Seen,
      \Answered, \Draft, \Recent) is rejected in mark_tagged with
      reason="forbidden_flag".
    - Undeclared extra arguments (such as {"impersonate": ...}) are
      rejected with JSON-RPC -32602 rather than ignored.
    - HTTP /admin endpoints return 404.

  Covered error layers (per BDD Guidelines §4.5):
    - Non-goal, retired, deferred, and test tools (-32601)  : 24
    - Move to unlisted Trash folder denied as folder_hidden : 1
    - All 6 \-system flags rejected as forbidden_flag       : 6
    - describe_policy never leaks hidden folder names       : 1
    - HTTP /admin and /admin/reload-policy return 404       : 2
    - Extra argument smuggling rejected with -32602         : 1
    Total enumerated                                        : 35  covered by this feature: 35

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the audit log directory is a fresh $TMPDIR/audit
    And secret_store configuration is:
      """
      backend: file_dir
      path: $TMPDIR/secrets
      acknowledge_plaintext: true
      """
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path      |
      | INBOX            |
      | INBOX/Rechnungen |
      | Banking          |
    And the server is configured with single caller:
      | caller_id     | auth_type     | policy         |
      | invoice-agent | stdio_trusted | invoice-policy |
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants folder on account "gupta-scaratec":
      | folder           | mode      | default  | mark_tagged | move_out | rules |
      | INBOX/Rechnungen | blacklist | ENVELOPE | true        | true     | []    |

  Scenario Outline: Calling non-goal, retired, or unenabled test tool <tool> returns JSON-RPC -32601
    Given the server process is started without IMAP_MCP_TEST_MODE on transport "stdio" with IMAP_MCP_CALLER_ID="invoice-agent"
    And invoice-agent completes an Initialize handshake successfully
    When invoice-agent calls the MCP method "tools/call" with name "<tool>"
    Then the server responds with JSON-RPC error code -32601
    And the audit log contains an entry with decision "DENY", reason "unknown_tool"

    Examples:
      | tool                     |
      | delete                    |
      | hard_delete               |
      | delete_draft              |
      | expunge                   |
      | store_deleted_flag        |
      | add_attachment            |
      | replace_attachment        |
      | delete_attachment         |
      | raw_imap_command          |
      | fetch_raw_rfc822          |
      | impersonate               |
      | bypass_redaction          |
      | subscribe_to_new_mail     |
      | search_across_accounts    |
      | create_folder             |
      | rename_folder             |
      | rotate_tokens             |
      | reload_policy             |
      | get_audit_log             |
      | get_server_config         |
      | initiate_oauth            |
      | bulk_move                 |
      | _test_run_recovery        |
      | _test_run_audit_rotation  |

  Scenario: Moving to an unlisted Trash folder is denied with folder_hidden and leaves source intact
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject    | date                 |
      | 701 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 1 | 2026-05-06T09:00:00Z |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 701}, target {"account": "gupta-scaratec", "folder": "Trash"}
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"
    And the IMAP folder "INBOX/Rechnungen" still contains uid 701

  Scenario Outline: mark_tagged rejects IMAP system flag <sys_flag> with forbidden_flag
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject    | flags | date                 |
      | 702 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 2 | []    | 2026-05-06T09:00:00Z |
    When invoice-agent calls mark_tagged with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 702, tags ["<sys_flag>"], mode "add"
    Then the response decision is DENY
    And the response field reason equals "forbidden_flag"
    And the IMAP message at "INBOX/Rechnungen" uid 702 does NOT have flag "<raw_flag>"

    Examples:
      | sys_flag   | raw_flag  |
      | \\Deleted  | \Deleted  |
      | \\Flagged  | \Flagged  |
      | \\Seen     | \Seen     |
      | \\Answered | \Answered |
      | \\Draft    | \Draft    |
      | \\Recent   | \Recent   |

  Scenario: describe_policy omits hidden folder names on granted accounts
    When invoice-agent calls describe_policy
    Then the response decision is ALLOW
    And the JSON response does NOT contain the literal strings:
      | forbidden_string |
      | Banking          |

  Scenario Outline: HTTP admin-shaped path <method> <path> returns 404
    Given the file "$TMPDIR/secrets/callers/invoice-agent/token" contains the exact bytes "tok-secret"
    And the server is configured with single caller:
      | caller_id     | auth_type    | token_secret_ref                     | policy         |
      | invoice-agent | shared_token | secret://callers/invoice-agent/token | invoice-policy |
    And the server process is started with transport "http" on 127.0.0.1 and a random port
    When an HTTP client makes <method> <path> against the server
    Then the HTTP response status code is 404

    Examples:
      | method | path                 |
      | GET    | /admin               |
      | POST   | /admin/reload-policy |

  Scenario: Passing an undeclared impersonate argument to describe_policy is rejected with JSON-RPC -32602
    When invoice-agent calls describe_policy with the arguments {"impersonate": "overview-agent"}
    Then the server responds with JSON-RPC error code -32602
    And the audit log contains an entry with decision "DENY", reason "invalid_arguments"
