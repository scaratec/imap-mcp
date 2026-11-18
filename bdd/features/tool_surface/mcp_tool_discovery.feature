Feature: MCP tool discovery for Tool Surface 3.0.0

  A connected client enumerates the server's tools via the standard MCP
  tools/list call without test mode enabled. Surface 3.0.0 exposes
  exactly 27 tools partitioned into 13 read, 10 write, and 4 meta tools
  (ADR 0052). Received-mail attachment-rewrite tools (add_attachment,
  replace_attachment, delete_attachment) and test-only tools
  (_test_run_*) are absent. Each tool publishes machine-readable
  x-mcp-imap / annotations metadata naming its category,
  minimum_visibility, and required_capabilities.

  Covered error layers (per BDD Guidelines §4.5):
    - Exact 27-tool presence in production mode              : 1
    - Non-goal, retired 2.x, and _test_run_* tool absence    : 1
    - Tool metadata on tools/list (read, write, meta)        : 3
    - Version advertisement in serverInfo + tool_surface_info: 2
    Total enumerated                                         : 7   covered by this feature: 7

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
    And the server process is started without IMAP_MCP_TEST_MODE on transport "stdio" with IMAP_MCP_CALLER_ID="invoice-agent"
    And invoice-agent completes an Initialize handshake successfully

  Scenario: The 3.0.0 tool set consists of these exact 27 tools
    When invoice-agent calls the MCP list_tools method
    Then the returned tool names equal exactly:
      | tool                   |
      | list_accounts          |
      | list_folders           |
      | list_labels            |
      | folder_stats           |
      | search                 |
      | list_messages          |
      | fetch_envelope         |
      | fetch_headers          |
      | fetch_body             |
      | list_attachments       |
      | fetch_attachment       |
      | list_drafts            |
      | get_draft              |
      | mark_seen              |
      | bulk_mark_seen         |
      | mark_tagged            |
      | bulk_mark_tagged       |
      | move                   |
      | copy                   |
      | create_draft           |
      | create_reply_draft     |
      | update_draft           |
      | discard_draft          |
      | describe_policy        |
      | get_transaction_status |
      | get_caller_identity    |
      | tool_surface_info      |

  Scenario: Non-goal, retired 2.x rewrite, and test-only tool names are absent from tools/list
    When invoice-agent calls the MCP list_tools method
    Then the returned tool names do NOT contain any of:
      | tool                      |
      | delete                    |
      | expunge                   |
      | add_attachment            |
      | replace_attachment        |
      | delete_attachment         |
      | raw_imap_command          |
      | fetch_raw_rfc822          |
      | impersonate               |
      | subscribe_to_new_mail     |
      | search_across_accounts    |
      | create_folder             |
      | rename_folder             |
      | delete_folder             |
      | rotate_tokens             |
      | reload_policy             |
      | get_audit_log             |
      | initiate_oauth            |
      | bulk_move                 |
      | _test_run_recovery        |
      | _test_run_audit_rotation  |

  Scenario: Read tools advertise their exact minimum_visibility in tools/list metadata
    When invoice-agent calls the MCP list_tools method
    Then the tools/list metadata for each read tool has "minimum_visibility" equal to:
      | tool             | minimum_visibility |
      | list_accounts    | (none)             |
      | list_folders     | COUNT              |
      | list_labels      | COUNT              |
      | folder_stats     | COUNT              |
      | search           | METADATA           |
      | list_messages    | METADATA           |
      | fetch_envelope   | ENVELOPE           |
      | fetch_headers    | HEADERS            |
      | fetch_body       | BODY               |
      | list_attachments | BODY               |
      | fetch_attachment | FULL               |
      | list_drafts      | METADATA           |
      | get_draft        | BODY               |

  Scenario: Write tools advertise their required_capabilities list and minimum_visibility floor in tools/list metadata
    When invoice-agent calls the MCP list_tools method
    Then the tools/list metadata for each write tool has "required_capabilities" and "minimum_visibility" equal to:
      | tool               | required_capabilities           | minimum_visibility |
      | mark_seen          | ["mark_seen"]                   | METADATA           |
      | bulk_mark_seen     | ["mark_seen"]                   | METADATA           |
      | mark_tagged        | ["mark_tagged"]                 | ENVELOPE           |
      | bulk_mark_tagged   | ["mark_tagged"]                 | ENVELOPE           |
      | move               | ["move_out", "accept_incoming"] | ENVELOPE           |
      | copy               | ["copy_out", "accept_incoming"] | ENVELOPE           |
      | create_draft       | ["draft_append"]                | (none)             |
      | create_reply_draft | ["draft_append"]                | BODY               |
      | update_draft       | ["draft_edit"]                  | (none)             |
      | discard_draft      | ["move_out", "accept_incoming"] | (none)             |

  Scenario: Meta tools advertise category "meta" with neither minimum_visibility nor required_capabilities
    When invoice-agent calls the MCP list_tools method
    Then each of these tools in tools/list has category "meta" and omits "minimum_visibility" and "required_capabilities":
      | tool                   |
      | describe_policy        |
      | get_transaction_status |
      | get_caller_identity    |
      | tool_surface_info      |

  Scenario: serverInfo advertises package version 3.0.0 and tool_set_version 3.0.0
    Then the server info version equals "3.0.0"
    And the server info metadata contains "tool_set_version" equal to "3.0.0"

  Scenario: tool_surface_info returns version 3.0.0 matching serverInfo
    When invoice-agent calls tool_surface_info
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field reason equals "authenticated"
    And the response field tool_set_version equals "3.0.0"
    And the response field package_version equals "3.0.0"
    And the response field tool_set_version matches the serverInfo tool_set_version
