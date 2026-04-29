Feature: Caller identity and authentication

  Every MCP session is bound to a caller identity resolved at
  connection time. V1 supports two mechanisms: stdio_trusted (the
  orchestrator signals identity via argv or environment) and
  shared_token (bearer token verified with constant-time comparison).
  Identity is immutable for the duration of the session.
  See ADR 0015.

  Covered error layers (per BDD Guidelines §4.5):
    - stdio_trusted : unset caller id      : 1
    - stdio_trusted : unknown caller id    : 1
    - shared_token  : missing token        : 1
    - shared_token  : wrong token          : 1
    - shared_token  : correct token        : 1
    - Immutability  : mid-session claim    : 1
    - Config error  : stdio_trusted over HTTP : 1
    - Timing        : constant-time compare : 1 (indirectly via audit latency variance)
    Total enumerated                        : 8   covered by this feature: 7

  Background:
    Given the server is configured with callers:
      | caller_id      | auth_type      | token_secret_ref                     |
      | invoice-agent  | shared_token   | secret://callers/invoice-agent/token |
    And the secret store contains value "correct-horse-battery" under "callers/invoice-agent/token"

  # The three stdio_trusted scenarios below add their own
  # `overview-agent` caller in a per-scenario Given. Keeping
  # stdio_trusted callers out of the Background lets the HTTP
  # scenarios run against a strictly-shared_token configuration
  # (ADR-0015 forbids stdio_trusted on non-stdio transport).

  Scenario: stdio_trusted — a known caller id in IMAP_MCP_CALLER_ID is accepted
    Given the server is configured with caller:
      | caller_id      | auth_type     |
      | overview-agent | stdio_trusted |
    And the server process is started with transport "stdio" and environment IMAP_MCP_CALLER_ID="overview-agent"
    When the MCP client performs an Initialize handshake
    Then the handshake succeeds
    And a subsequent get_caller_identity returns caller_id "overview-agent"

  Scenario: stdio_trusted — an unknown caller id is rejected at Initialize time
    Given the server process is started with transport "stdio" and environment IMAP_MCP_CALLER_ID="ghost-agent"
    When the MCP client performs an Initialize handshake
    Then the handshake fails with error "unknown_caller_id"
    And the audit log contains an entry with:
      | field       | value            |
      | tool        | auth_failed      |
      | decision    | DENY             |
      | reason      | auth_failed      |
      | caller_addr | stdio:pid=*      |

  Scenario: stdio_trusted — no caller id at all is rejected
    Given the server process is started with transport "stdio" and no IMAP_MCP_CALLER_ID set
    When the MCP client performs an Initialize handshake
    Then the handshake fails with error "no_caller_identity"

  Scenario: shared_token — the correct bearer token is accepted
    Given the server process is started with transport "http" on a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake succeeds
    And a subsequent get_caller_identity returns caller_id "invoice-agent"

  Scenario Outline: shared_token — wrong or missing token is rejected with auth_failed
    Given the server process is started with transport "http" on a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "<provided_token>"
    Then the handshake fails with error "auth_failed"
    And the audit log contains an entry with tool "auth_failed", decision "DENY", reason "auth_failed"

    Examples:
      | provided_token      |
      | wrong-token         |
      | correct-horse-batte |
      |                     |

  Scenario: Identity is immutable for the duration of a session
    Given the server process is started with transport "http" on a random port
    And the MCP client completes an Initialize handshake as "invoice-agent" with the correct token
    When the MCP client sends an Initialize-like message claiming caller_id "overview-agent"
    Then the server responds with error "identity_immutable"
    And a subsequent get_caller_identity still returns caller_id "invoice-agent"

  Scenario: A stdio_trusted caller on HTTP transport is a fatal configuration error detected at startup
    Given the server is configured with caller:
      | caller_id     | auth_type     |
      | wrong-config  | stdio_trusted |
    And the server process is started with transport "http"
    Then the server refuses to start
    And the startup error indicates caller "wrong-config" as "stdio_trusted not permitted on non-stdio transport"
