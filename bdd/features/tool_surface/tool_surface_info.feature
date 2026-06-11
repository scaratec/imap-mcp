Feature: tool_surface_info exposes the contract version a caller can pin

  ADR 0027 introduces tool_surface_info as the explicit, callable
  meta-tool that returns the surface version. Callers (and a human
  at a prompt) can ask "what server am I talking to" without
  inferring from list_tools output, and a 2.x client can refuse to
  proceed against a 3.x server before issuing any business call.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path: returns version + protocol + change log : 1
    - Version is SemVer 2.x.y                              : 1
    - tool_set_version matches serverInfo metadata         : 1
    - breaking_changes_since is non-empty for 2.0          : 1
    - Available without policy grants (meta tool)          : 1
    Total enumerated                                        : 5   covered by this feature: 5

  Background:
    Given the server is started with a minimal caller configuration
    And invoice-agent completes an Initialize handshake successfully

  Scenario: tool_surface_info returns the contract block
    When invoice-agent calls tool_surface_info
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field tool_set_version is a string
    And the response field package_version is a string
    And the response field protocol_revision is a string
    And the response field breaking_changes_since is an array

  Scenario: tool_set_version is SemVer 2.x.y
    When invoice-agent calls tool_surface_info
    Then the response field tool_set_version matches the regex "^2\.\d+\.\d+$"

  Scenario: tool_surface_info agrees with serverInfo metadata
    When invoice-agent calls tool_surface_info
    Then the response field tool_set_version matches the serverInfo tool_set_version
    And the response field package_version matches the serverInfo package_version

  Scenario: breaking_changes_since enumerates the 2.0 hard cut
    When invoice-agent calls tool_surface_info
    Then the breaking_changes_since field has at least one entry
    And one entry has field "version" equal to "2.0.0"
    And one entry has field "summary" matching the regex "ADR 0028"

  Scenario: tool_surface_info is reachable without any policy grant
    Given the server is configured with caller "no-grants-agent" using policy "empty-policy"
    And policy "empty-policy" grants no accounts
    When no-grants-agent calls tool_surface_info
    Then the response decision is ALLOW
    And the response field result equals "OK"
