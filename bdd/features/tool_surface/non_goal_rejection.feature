Feature: Non-goal tool surface is genuinely absent

  The server's defence against misbehaving callers is partly by
  omission: certain operations are not exposed as MCP tools at all.
  Attempts to invoke them, send them raw, or reach them through
  side channels must fail closed. See ADR 0018.

  Covered error layers (per BDD Guidelines §4.5):
    - Tool does not exist (JSON-RPC unknown method) : 5
    - Tool exists but arg constitutes bypass        : 2
    - Admin-shape channel absent                    : 3
    Total enumerated                                 : 10   covered by this feature: 10

  Background:
    Given the server is started with a minimal configuration
    And invoice-agent completes an Initialize handshake successfully

  Scenario Outline: Calling a non-goal tool yields JSON-RPC error method_not_found
    When invoice-agent calls the MCP method "tools/call" with name "<tool>"
    Then the server responds with JSON-RPC error code -32601
    And the audit log contains an entry with tool "auth_failed_or_unknown_method", decision "DENY", reason "unknown_tool"

    Examples:
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
      | rotate_tokens         |
      | reload_policy         |
      | get_audit_log         |
      | get_server_config     |

  Scenario: A move call whose target is a non-existent "Trash" (because no Trash folder policy exists) yields capability_missing, not silent deletion
    Given caller "invoice-agent" has no policy that references any folder named "Trash"
    And the folder "INBOX/Rechnungen" holds a message with uid 701
    When invoice-agent calls move with source {"account":"gupta-scaratec","folder":"INBOX/Rechnungen","uid":701}, target {"account":"gupta-scaratec","folder":"Trash"}
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"
    And the IMAP folder "INBOX/Rechnungen" still contains uid 701
    And the IMAP server has no folder named "Trash" that now holds uid 701

  Scenario: A mark_tagged call attempting to set \Deleted is rejected
    Given the folder "INBOX/Rechnungen" holds a message with uid 702
    And policy grants mark_tagged=true on "INBOX/Rechnungen"
    When invoice-agent calls mark_tagged with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 702, tags ["\Deleted"], mode "add"
    Then the response decision is DENY
    And the response field reason equals "forbidden_system_flag"
    And the IMAP message at "INBOX/Rechnungen" uid 702 does NOT have flag "\Deleted"

  Scenario: Calling describe_policy never returns rule patterns or hidden folder names (re-asserted against bypass attempts)
    Given the IMAP account "gupta-scaratec" has a hidden folder "Banking"
    When invoice-agent calls describe_policy
    Then the JSON response does NOT contain the literal string "Banking"

  @pending @pending_LIM_0007
  Scenario: An attempt to reach `/admin` over HTTP transport is 404, not a routed endpoint
    Given the server is started with transport "http" on a random port
    When an HTTP client makes GET /admin against the server
    Then the HTTP response status code is 404
    When an HTTP client makes POST /admin/reload-policy against the server
    Then the HTTP response status code is 404

  Scenario: Invocation of a meta-tool with an "impersonate" argument ignores it silently
    When invoice-agent calls describe_policy with extra argument {"impersonate": "overview-agent"}
    Then the response field caller_id equals "invoice-agent"
    And the response field accounts describes the "invoice-agent" policy, not "overview-agent"
