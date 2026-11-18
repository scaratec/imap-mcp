Feature: Single-caller instance boundary and caller authentication

  Every server instance serves strictly one configured caller in
  callers.yaml (ADR 0049). stdio_trusted is permitted only on stdio and
  requires matching IMAP_MCP_CALLER_ID; shared_token verifies bearer
  credentials in constant time and is required on HTTP. Session identity
  is immutable after the Initialize handshake, and every failed
  authentication writes a chained auth_failed audit record.

  Covered error layers (per BDD Guidelines §4.5):
    - Single-caller startup check (0 callers, 2 callers)       : 2
    - Invalid caller auth config (missing auth, stdio on HTTP) : 2
    - stdio_trusted: matching, unknown, unset caller_id        : 3
    - shared_token over HTTP: valid (+ caller_addr) + 3 invalid: 4
    - Session identity immutable on active session             : 1
    Total enumerated                                           : 12  covered by this feature: 12

  Background:
    Given the audit log directory is a fresh $TMPDIR/audit
    And secret_store configuration is:
      """
      backend: file_dir
      path: $SCRATCH/secrets
      acknowledge_plaintext: true
      """
    And the file "$SCRATCH/secrets/callers/invoice-agent/token" contains the exact bytes "correct-horse-battery"

  Scenario: Server refuses to start when callers.yaml defines zero callers
    Given callers.yaml contains an empty callers list
    When the server process is started with transport "stdio"
    Then the server refuses to start
    And the startup error indicates "callers.yaml must define exactly one caller"

  Scenario: Server refuses to start when callers.yaml defines multiple callers
    Given callers.yaml defines two callers "invoice-agent" and "overview-agent"
    When the server process is started with transport "http"
    Then the server refuses to start
    And the startup error indicates "callers.yaml must define exactly one caller"

  Scenario: Server refuses to start when a caller omits its auth block
    Given callers.yaml defines caller "no-auth-agent" without an auth block
    When the server process is started with transport "http"
    Then the server refuses to start
    And the startup error indicates "no-auth-agent" and "auth"

  Scenario: A stdio_trusted caller on HTTP transport is a fatal startup configuration error
    Given the server is configured with single caller:
      | caller_id    | auth_type     | policy         |
      | wrong-config | stdio_trusted | invoice-policy |
    And policy "invoice-policy" grants no accounts
    When the server process is started with transport "http"
    Then the server refuses to start
    And the startup error indicates "stdio_trusted"

  Scenario: stdio_trusted — matching IMAP_MCP_CALLER_ID is accepted
    Given the server is configured with single caller:
      | caller_id      | auth_type     | policy          |
      | overview-agent | stdio_trusted | overview-policy |
    And policy "overview-policy" grants no accounts
    And the server process is started with transport "stdio" and environment IMAP_MCP_CALLER_ID="overview-agent"
    When the MCP client performs an Initialize handshake
    Then the handshake succeeds
    When the MCP client calls get_caller_identity on the established stdio session
    Then the response field caller_id equals "overview-agent"

  Scenario: stdio_trusted — mismatched IMAP_MCP_CALLER_ID is rejected and audited
    Given the server is configured with single caller:
      | caller_id      | auth_type     | policy          |
      | overview-agent | stdio_trusted | overview-policy |
    And policy "overview-policy" grants no accounts
    And the server process is started with transport "stdio" and environment IMAP_MCP_CALLER_ID="ghost-agent"
    When the MCP client performs an Initialize handshake
    Then the handshake fails with error "auth_failed"
    And the audit log contains an entry with:
      | field    | value       |
      | tool     | auth_failed |
      | decision | DENY        |
      | reason   | auth_failed |
    And the most recent audit entry has field "caller_addr" starting with "stdio:pid="

  Scenario: stdio_trusted — unset IMAP_MCP_CALLER_ID is rejected and audited
    Given the server is configured with single caller:
      | caller_id      | auth_type     | policy          |
      | overview-agent | stdio_trusted | overview-policy |
    And policy "overview-policy" grants no accounts
    And the server process is started with transport "stdio" and no IMAP_MCP_CALLER_ID set
    When the MCP client performs an Initialize handshake
    Then the handshake fails with error "auth_failed"
    And the audit log contains an entry with tool "auth_failed", decision "DENY", reason "auth_failed"

  Scenario: shared_token — the correct bearer token is accepted over loopback HTTP and logs http caller_addr
    Given the server is configured with single caller:
      | caller_id     | auth_type    | token_secret_ref                     | policy         |
      | invoice-agent | shared_token | secret://callers/invoice-agent/token | invoice-policy |
    And policy "invoice-policy" grants no accounts
    And the server process is started with transport "http" on 127.0.0.1 and a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake succeeds
    When the MCP client calls get_caller_identity on the established HTTP session
    Then the response field caller_id equals "invoice-agent"
    And the most recent audit entry for tool "get_caller_identity" has field "caller_addr" starting with "http:127.0.0.1:"

  Scenario Outline: shared_token — wrong, truncated, or empty bearer token is rejected with auth_failed
    Given the server is configured with single caller:
      | caller_id     | auth_type    | token_secret_ref                     | policy         |
      | invoice-agent | shared_token | secret://callers/invoice-agent/token | invoice-policy |
    And policy "invoice-policy" grants no accounts
    And the server process is started with transport "http" on 127.0.0.1 and a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "<provided_token>"
    Then the handshake fails with error "auth_failed"
    And the audit log contains an entry with tool "auth_failed", decision "DENY", reason "auth_failed"
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string      |
      | correct-horse-battery |
      | wrong-token-secret    |

    Examples:
      | provided_token        |
      | wrong-token-secret    |
      | correct-horse-batte   |
      |                       |

  Scenario: Session identity is immutable for the duration of an established session
    Given the server is configured with single caller:
      | caller_id     | auth_type    | token_secret_ref                     | policy         |
      | invoice-agent | shared_token | secret://callers/invoice-agent/token | invoice-policy |
    And policy "invoice-policy" grants no accounts
    And the server process is started with transport "http" on 127.0.0.1 and a random port
    And the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    When the MCP client sends a second Initialize request on the same HTTP session claiming caller_id "overview-agent"
    Then the second Initialize request is rejected with error "identity_immutable"
    When the MCP client calls get_caller_identity on the established HTTP session without re-initializing
    Then the response field caller_id equals "invoice-agent"
