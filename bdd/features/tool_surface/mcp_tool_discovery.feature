Feature: MCP tool discovery

  A connected client enumerates the server's tools via the standard
  MCP list_tools handshake. V1 exposes exactly nineteen tools
  partitioned into ten read, five write, and three meta. The
  advertised shape matches ADR 0016.

  Covered error layers (per BDD Guidelines §4.5):
    - Tool presence           : 1 (exact set)
    - Tool absence            : 1 (no extras, especially no non-goals)
    - Tool metadata           : 3 (read: minimum-level, write: capability, meta: none)
    - Version advertisement   : 1
    - Package version in serverInfo : 1
    Total enumerated          : 7   covered by this feature: 7

  Background:
    Given the server is started with a minimal caller configuration
    And invoice-agent completes an Initialize handshake successfully

  Scenario: The V1 tool set consists of exactly these 22 tools
    When invoice-agent calls the MCP list_tools method
    Then the returned tool names equal exactly:
      | tool                      |
      | list_accounts             |
      | list_folders              |
      | folder_stats              |
      | search                    |
      | list_messages             |
      | fetch_envelope            |
      | fetch_headers             |
      | fetch_body                |
      | fetch_attachment          |
      | mark_seen                 |
      | bulk_mark_seen            |
      | mark_tagged               |
      | move                      |
      | copy                      |
      | create_draft              |
      | add_attachment            |
      | replace_attachment        |
      | delete_attachment         |
      | describe_policy           |
      | get_transaction_status    |
      | get_caller_identity       |
      | list_labels               |

  Scenario: Non-goal tool names are never advertised
    When invoice-agent calls the MCP list_tools method
    Then the returned tool names do NOT contain any of:
      | tool                  |
      | delete                |
      | expunge               |
      | raw_imap_command      |
      | fetch_raw_rfc822      |
      | impersonate           |
      | subscribe_to_new_mail |
      | search_across_accounts|
      | create_folder         |
      | rename_folder         |
      | delete_folder         |
      | rotate_tokens         |
      | reload_policy         |
      | get_audit_log         |

  Scenario: Read tools advertise their minimum visibility level in metadata
    When invoice-agent calls the MCP list_tools method
    Then each read tool's metadata contains "minimum_visibility" matching:
      | tool               | minimum_visibility |
      | list_accounts      | (n/a)              |
      | list_folders       | COUNT              |
      | folder_stats       | COUNT              |
      | search             | METADATA           |
      | fetch_envelope     | ENVELOPE           |
      | fetch_headers      | HEADERS            |
      | fetch_body         | BODY               |
      | fetch_attachment   | FULL               |

  Scenario: Write tools advertise their required capability in metadata
    When invoice-agent calls the MCP list_tools method
    Then each write tool's metadata contains "required_capability" matching:
      | tool          | required_capability |
      | mark_seen          | mark_seen           |
      | mark_tagged        | mark_tagged         |
      | move               | move_out            |
      | copy               | accept_incoming     |
      | create_draft       | draft_append        |
      | add_attachment     | modify_message      |
      | replace_attachment | modify_message      |
      | delete_attachment  | modify_message      |

  Scenario: Meta tools advertise no visibility requirement
    When invoice-agent calls the MCP list_tools method
    Then each of these tools has no "minimum_visibility" and no "required_capability" metadata:
      | tool                     |
      | describe_policy          |
      | get_transaction_status   |
      | get_caller_identity      |

  Scenario: Server advertises a tool_set_version that follows semantic versioning
    When invoice-agent calls the MCP list_tools method
    Then the server metadata contains "tool_set_version" matching the regex "^\d+\.\d+\.\d+$"
    And the major version equals 1

  Scenario: serverInfo.version matches the installed package version
    Then the server info version matches the installed sc-imap-mcp package version
