Feature: Pluggable secret-store backends

  Secrets resolve through three SecretStore backends (ADR 0050 §1):
    - file_dir: atomic 0600 files; requires acknowledge_plaintext: true
    - env_var: read-only environment lookup; rejects bootstrap and persist_all
    - gpg_file: per-file GPG encryption/decryption with optional passphrase

  Covered error layers (per BDD Guidelines §4.5):
    - file_dir without acknowledge_plaintext: true refused at startup : 1
    - file_dir with acknowledge_plaintext: true happy path            : 1
    - file_dir missing secret file -> auth_failed without leak        : 1
    - env_var happy path                                              : 1
    - env_var unset variable -> auth_failed                           : 1
    - env_var rejects oauth_bootstrap as read-only                    : 1
    - env_var rejects persist_all token_cache at startup              : 1
    - gpg_file decryption succeeds with correct passphrase            : 1
    - gpg_file wrong passphrase on caller token -> auth_failed        : 1
    - gpg_file wrong passphrase on account password -> secret_unavailable : 1
    Total enumerated                                                  : 10  covered by this feature: 10

  Background:
    Given the audit log directory is a fresh $TMPDIR/audit
    And the server is configured with single caller:
      | caller_id     | auth_type    | token_secret_ref                     | policy         |
      | invoice-agent | shared_token | secret://callers/invoice-agent/token | invoice-policy |
    And policy "invoice-policy" grants no accounts

  Scenario: file_dir — omitting acknowledge_plaintext: true refuses server startup
    Given secret_store configuration is:
      """
      backend: file_dir
      path: $SCRATCH/secrets
      """
    And the file "$SCRATCH/secrets/callers/invoice-agent/token" contains the exact bytes "correct-horse-battery"
    When the server process is started with transport "http"
    Then the server refuses to start
    And the startup error indicates "acknowledge_plaintext"

  Scenario: file_dir — handshake succeeds when acknowledge_plaintext is true and the secret matches
    Given secret_store configuration is:
      """
      backend: file_dir
      path: $SCRATCH/secrets
      acknowledge_plaintext: true
      """
    And the file "$SCRATCH/secrets/callers/invoice-agent/token" contains the exact bytes "correct-horse-battery"
    And the server process is started with transport "http" on 127.0.0.1 and a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake succeeds
    When the MCP client calls get_caller_identity on the established HTTP session
    Then the response field caller_id equals "invoice-agent"

  Scenario: file_dir — handshake fails when the caller token file is absent
    Given secret_store configuration is:
      """
      backend: file_dir
      path: $SCRATCH/secrets
      acknowledge_plaintext: true
      """
    And no file exists at "$SCRATCH/secrets/callers/invoice-agent/token"
    And the server process is started with transport "http" on 127.0.0.1 and a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake fails with error "auth_failed"
    And the audit log contains an entry with tool "auth_failed", decision "DENY", reason "auth_failed"
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string      |
      | correct-horse-battery |

  Scenario: env_var — handshake succeeds when the matching environment variable is set
    Given secret_store configuration is:
      """
      backend: env_var
      """
    And the server process environment includes:
      | name                                           | value                 |
      | IMAP_MCP_SECRET__CALLERS__INVOICE_AGENT__TOKEN | correct-horse-battery |
      | IMAP_MCP_SERVER_HMAC_KEY                       | 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef |
    And the server process is started with transport "http" on 127.0.0.1 and a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake succeeds

  Scenario: env_var — handshake fails when the caller token environment variable is unset
    Given secret_store configuration is:
      """
      backend: env_var
      """
    And the server process environment includes:
      | name                     | value                                                            |
      | IMAP_MCP_SERVER_HMAC_KEY | 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef |
    And the server process environment does NOT include "IMAP_MCP_SECRET__CALLERS__INVOICE_AGENT__TOKEN"
    And the server process is started with transport "http" on 127.0.0.1 and a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake fails with error "auth_failed"

  Scenario: env_var — oauth_bootstrap against a read-only backend exits non-zero before opening a browser
    Given secret_store configuration is:
      """
      backend: env_var
      """
    And account "gmail-archive" is configured with provider "google", oauth_scope "https://mail.google.com/", and token_cache "memory_only"
    When the operator runs `imap-mcp-oauth-bootstrap --account gmail-archive`
    Then the bootstrap exits with a non-zero code and stderr mentioning "env_var backend is read-only"

  Scenario: env_var — pairing persist_all token_cache with read-only env_var refuses server startup
    Given secret_store configuration is:
      """
      backend: env_var
      """
    And account "gmail-archive" is configured with provider "google", oauth_scope "https://mail.google.com/", and token_cache "persist_all"
    When the server process is started with transport "http"
    Then the server refuses to start
    And the startup error indicates "persist_all"

  Scenario: gpg_file — handshake succeeds when encrypted token is decrypted with the configured passphrase
    Given a passphrase-protected GPG keypair is generated in "$SCRATCH/gnupg" with passphrase "test-passphrase-correct"
    And secret_store configuration uses backend "gpg_file" at "$SCRATCH/secrets" with gnupghome "$SCRATCH/gnupg", the generated key fingerprint, and passphrase "test-passphrase-correct"
    And the plaintext "correct-horse-battery" is encrypted with GPG for the generated key into "$SCRATCH/secrets/callers/invoice-agent/token.gpg"
    And the server process is started with transport "http" on 127.0.0.1 and a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake succeeds
    When the MCP client calls get_caller_identity on the established HTTP session
    Then the response field caller_id equals "invoice-agent"
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string        |
      | correct-horse-battery   |
      | test-passphrase-correct |

  Scenario: gpg_file — wrong passphrase fails closed without leaking token, passphrase, or gpg stderr
    Given a passphrase-protected GPG keypair is generated in "$SCRATCH/gnupg" with passphrase "test-passphrase-correct"
    And secret_store configuration uses backend "gpg_file" at "$SCRATCH/secrets" with gnupghome "$SCRATCH/gnupg", the generated key fingerprint, and passphrase "wrong-passphrase-attempt"
    And the plaintext "correct-horse-battery" is encrypted with GPG for the generated key into "$SCRATCH/secrets/callers/invoice-agent/token.gpg"
    And the server process is started with transport "http" on 127.0.0.1 and a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"
    Then the handshake fails with error "auth_failed"
    And the audit log contains an entry with tool "auth_failed", decision "DENY", reason "auth_failed"
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string         |
      | correct-horse-battery    |
      | test-passphrase-correct  |
      | wrong-passphrase-attempt |
      | gpg:                     |

  Scenario: gpg_file — wrong passphrase on an account credential returns ALLOW + ERROR secret_unavailable
    Given a passphrase-protected GPG keypair is generated in "$SCRATCH/gnupg" with passphrase "acct-passphrase-correct"
    And secret_store configuration uses backend "gpg_file" at "$SCRATCH/secrets" with gnupghome "$SCRATCH/gnupg", the generated key fingerprint, and passphrase "acct-passphrase-wrong"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the plaintext "test123" is encrypted with GPG for the generated key into "$SCRATCH/secrets/accounts/gupta-scaratec/password.gpg"
    And the server is configured with single caller:
      | caller_id     | auth_type     | policy         |
      | invoice-agent | stdio_trusted | invoice-policy |
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "secret_unavailable"
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string        |
      | test123                 |
      | acct-passphrase-correct |
      | acct-passphrase-wrong   |
      | gpg:                    |
