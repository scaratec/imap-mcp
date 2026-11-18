Feature: tool_surface_info exposes the 3.0.0 contract version and breaking-change history

  ADR 0044 and ADR 0052 define tool_surface_info as an authenticated
  meta-tool (reason="authenticated") returning tool_set_version,
  package_version, protocol_revision, and breaking_changes_since so
  clients can pin 3.0.0 before issuing mailbox operations.

  Covered error layers (per BDD Guidelines §4.5):
    - Contract block shape and reason="authenticated"           : 1
    - SemVer 3.0.0 agreement with Initialize serverInfo         : 1
    - breaking_changes_since documents 2.0.0 and 3.0.0 cuts     : 1
    - Reachable with zero account grants                        : 1
    - Rejects unexpected arguments with JSON-RPC -32602         : 1
    Total enumerated                                            : 5   covered by this feature: 5

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

  Scenario: tool_surface_info returns the complete contract block with reason authenticated
    When invoice-agent calls tool_surface_info
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field reason equals "authenticated"
    And the response field tool_set_version equals "3.0.0"
    And the response field package_version equals "3.0.0"
    And the response field protocol_revision is a non-empty string
    And the response field breaking_changes_since is a non-empty array

  Scenario: tool_surface_info agrees with Initialize serverInfo metadata
    When invoice-agent calls tool_surface_info
    Then the response field tool_set_version matches the serverInfo tool_set_version
    And the response field package_version matches the serverInfo package_version

  Scenario: breaking_changes_since records both the 2.0.0 and 3.0.0 major breaks
    When invoice-agent calls tool_surface_info
    Then breaking_changes_since contains an entry with version "2.0.0" mentioning "ADR 0028"
    And breaking_changes_since contains an entry with version "3.0.0" mentioning "ADR 0040"

  Scenario: tool_surface_info succeeds for a caller whose policy grants zero accounts
    When invoice-agent calls tool_surface_info
    Then the response decision is ALLOW
    And the response field result equals "OK"

  Scenario: tool_surface_info rejects undeclared arguments with JSON-RPC -32602
    When invoice-agent calls tool_surface_info with the arguments {"unexpected": true}
    Then the server responds with JSON-RPC error code -32602
