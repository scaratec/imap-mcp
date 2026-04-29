Feature: Pluggable secret-store backends

  Secrets flow through a narrow SecretStore interface with three V1
  backends: file_dir (plaintext files, confidentiality from the
  surrounding system), env_var (orchestrator-managed, read-only),
  and gpg_file (per-file GPG decryption). The server performs no
  cryptography of its own. See ADR 0011.

  The observable test surface is caller authentication
  (shared_token lookup) and the OAuth bootstrap script (put/delete
  attempts against a read-only backend).

  Covered error layers (per BDD Guidelines §4.5):
    - file_dir happy path            : 1
    - file_dir: key missing           : 1
    - env_var happy path              : 1
    - env_var: key unset              : 1
    - env_var: put() on read-only     : 1
    - gpg_file: decryption succeeds   : 1
    - gpg_file: wrong passphrase      : 1
    Total enumerated                  : 7   covered by this feature: 7

  Background:
    Given the server is configured with caller:
      | caller_id     | auth_type    | token_secret_ref                     |
      | invoice-agent | shared_token | secret://callers/invoice-agent/token |

  Scenario: file_dir — handshake succeeds when the secret file matches the bearer
    Given secret_store configuration is:
      """
      backend: file_dir
      path: $SCRATCH/secrets
      """
    And the file "$SCRATCH/secrets/callers/invoice-agent/token" contains the exact bytes "correct-horse-battery"
    And the server is started with transport "http" on a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake succeeds
    And a subsequent get_caller_identity returns caller_id "invoice-agent"

  Scenario: file_dir — handshake fails when the secret file is absent
    Given secret_store configuration is:
      """
      backend: file_dir
      path: $SCRATCH/secrets
      """
    And no file exists at "$SCRATCH/secrets/callers/invoice-agent/token"
    And the server is started with transport "http" on a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake fails with error "auth_failed"
    And the audit log contains an entry with tool "auth_failed", decision "DENY", reason "auth_failed"
    And the audit record does NOT contain the literal string "correct-horse-battery"

  Scenario: env_var — handshake succeeds when the matching env var is set
    Given secret_store configuration is:
      """
      backend: env_var
      """
    And the server process environment includes:
      | name                                            | value                |
      | IMAP_MCP_SECRET__CALLERS__INVOICE_AGENT__TOKEN  | correct-horse-battery|
    And the server is started with transport "http" on a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake succeeds

  Scenario: env_var — handshake fails when the env var is unset
    Given secret_store configuration is:
      """
      backend: env_var
      """
    And the server process environment does NOT include any variable starting with "IMAP_MCP_SECRET__CALLERS__INVOICE_AGENT__"
    And the server is started with transport "http" on a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake fails with error "auth_failed"

  Scenario: env_var — oauth_bootstrap against a read-only backend is rejected at startup
    Given secret_store configuration is:
      """
      backend: env_var
      """
    And account "gmail-archive" is configured with provider "google" and oauth_scope "https://mail.google.com/"
    When the operator runs `imap-mcp-oauth-bootstrap --account gmail-archive`
    Then the bootstrap refuses to start
    And the startup error indicates "env_var backend is read-only; bootstrap requires a writable secret store"
    And the server process environment still has no variable for "accounts/gmail-archive/refresh_token"

  Scenario: gpg_file — handshake succeeds when the encrypted file can be decrypted
    Given a GPG keypair exists with fingerprint "ABCDEF0123456789ABCDEF0123456789ABCDEF01" and a pinentry that returns passphrase "test-passphrase"
    And secret_store configuration is:
      """
      backend: gpg_file
      path: $SCRATCH/secrets
      recipient: ABCDEF0123456789ABCDEF0123456789ABCDEF01
      """
    And the file "$SCRATCH/secrets/callers/invoice-agent/token.gpg" was produced by `gpg --encrypt --recipient ABCDEF0123456789ABCDEF0123456789ABCDEF01` over the exact bytes "correct-horse-battery"
    And the server is started with transport "http" on a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake succeeds
    And the audit record does NOT contain the literal string "correct-horse-battery"

  Scenario: gpg_file — decryption failure on wrong passphrase is reported without leaking bytes
    Given a GPG keypair exists with fingerprint "ABCDEF0123456789ABCDEF0123456789ABCDEF01" and a pinentry that returns passphrase "wrong-passphrase"
    And secret_store configuration is:
      """
      backend: gpg_file
      path: $SCRATCH/secrets
      recipient: ABCDEF0123456789ABCDEF0123456789ABCDEF01
      """
    And the file "$SCRATCH/secrets/callers/invoice-agent/token.gpg" exists and was produced by the same recipient
    And the server is started with transport "http" on a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake fails with error "auth_failed"
    And the audit log contains an entry with tool "auth_failed", decision "DENY", reason "secret_decryption_failed"
    And the audit record does NOT contain the literal string "correct-horse-battery"
    And the audit record does NOT contain any line from the gpg subprocess' stderr
