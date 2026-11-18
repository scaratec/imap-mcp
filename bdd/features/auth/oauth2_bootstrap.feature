@test_hooks
Feature: OAuth2 PKCE bootstrap, XOAUTH2 token lifecycle, and durable needs_rebootstrap

  ADR 0050 specifies interactive OAuth2 Authorization Code + PKCE
  bootstrap with cryptographic state verification, XOAUTH2 IMAP
  authentication against real provider/mock endpoints, proactive refresh
  at 80% lifetime with pool drain, single reactive retry on stale cached
  tokens, durable needs_rebootstrap persistence in wal.db across restarts
  and SIGHUP reloads, and startup rejection of narrow OAuth scopes paired
  with write capabilities.

  Covered error layers (per BDD Guidelines §4.5):
    - Bootstrap happy path writes refresh token & clears needs_rebootstrap : 1
    - Bootstrap user denies consent (access_denied)                        : 1
    - Bootstrap rejects OAuth state mismatch before token exchange         : 1
    - Bootstrap fails when token endpoint rejects PKCE code_verifier       : 1
    - Narrow provider OAuth scope + write capability rejected at startup   : 1
    - Proactive 80% token refresh + pool drain                             : 1
    - Single reactive XOAUTH2 refresh retry on first auth rejection        : 1
    - invalid_grant persists needs_rebootstrap across restart & SIGHUP     : 1
    - Token cache strategies: memory_only vs persist_all                   : 2
    Total enumerated                                                       : 10  covered by this feature: 10

  Background:
    Given the audit log directory is a fresh $TMPDIR/audit
    And the mock OAuth2 server is reset and healthy
    And secret_store configuration is:
      """
      backend: file_dir
      path: $TMPDIR/secrets
      acknowledge_plaintext: true
      """

  Scenario: Bootstrap happy path verifies state and PKCE and writes refresh_token to SecretStore
    Given account "gmail-archive" is configured for OAuth2 against the mock OAuth2 server and mock-gmail with scope "https://mail.google.com/" and token_cache "persist_all"
    And the server is configured with caller "archive-agent" using policy "archive-policy"
    And policy "archive-policy" grants account "gmail-archive"
    When the operator runs `imap-mcp-oauth-bootstrap --account gmail-archive` and completes the browser consent callback
    Then the bootstrap exits with code 0
    And the secret store file "accounts/gmail-archive/refresh_token" exists with mode 0600 and non-empty content
    And the audit log contains an entry with tool "oauth_bootstrap", decision "ALLOW", result "OK", reason "oauth_bootstrap_ok"

  Scenario: Bootstrap aborted when user denies consent maps access_denied to user_denied and leaves SecretStore untouched
    Given account "gmail-archive" is configured for OAuth2 against the mock OAuth2 server and mock-gmail with scope "https://mail.google.com/" and token_cache "persist_all"
    And the server is configured with caller "archive-agent" using policy "archive-policy"
    And policy "archive-policy" grants account "gmail-archive"
    When the operator runs `imap-mcp-oauth-bootstrap --account gmail-archive` and the callback returns error "access_denied"
    Then the bootstrap exits with a non-zero code and stderr mentioning "user_denied"
    And no file exists in the secret store at "accounts/gmail-archive/refresh_token"
    And the mock OAuth2 token endpoint recorded 0 token requests

  Scenario: Bootstrap rejects a callback with mismatched state parameter before contacting the token endpoint
    Given account "gmail-archive" is configured for OAuth2 against the mock OAuth2 server and mock-gmail with scope "https://mail.google.com/" and token_cache "persist_all"
    And the server is configured with caller "archive-agent" using policy "archive-policy"
    And policy "archive-policy" grants account "gmail-archive"
    When the operator runs `imap-mcp-oauth-bootstrap --account gmail-archive` and the callback returns a tampered state value
    Then the bootstrap exits with a non-zero code and stderr mentioning "state_mismatch"
    And no file exists in the secret store at "accounts/gmail-archive/refresh_token"
    And the mock OAuth2 token endpoint recorded 0 token requests

  Scenario: Bootstrap fails cleanly when the OAuth2 provider rejects PKCE verification
    Given account "gmail-archive" is configured for OAuth2 against the mock OAuth2 server and mock-gmail with scope "https://mail.google.com/" and token_cache "persist_all"
    And the server is configured with caller "archive-agent" using policy "archive-policy"
    And policy "archive-policy" grants account "gmail-archive"
    And the mock OAuth2 server is primed to reject the next authorization_code exchange with OAuth error "invalid_grant" and description "PKCE verification failed"
    When the operator runs `imap-mcp-oauth-bootstrap --account gmail-archive` and completes the browser consent callback
    Then the bootstrap exits with a non-zero code and stderr mentioning "PKCE"
    And no file exists in the secret store at "accounts/gmail-archive/refresh_token"

  Scenario: Startup rejects a narrow read-only OAuth scope paired with folder write capabilities
    Given account "custom-oauth" is configured with provider "imap-standard", auth "oauth2", oauth_scope "imap.readonly", and token_cache "memory_only"
    And the server is configured with caller "writer-agent" using policy "writer-policy"
    And policy "writer-policy" grants account "custom-oauth"
    And policy "writer-policy" grants folder on account "custom-oauth":
      | folder | mode      | default  | mark_seen | rules |
      | INBOX  | blacklist | ENVELOPE | true      | []    |
    When the server process is started with transport "stdio"
    Then the server refuses to start
    And the startup error indicates "oauth_scope"

  Scenario: Proactive token refresh at 80% of lifetime renews the XOAUTH2 token and drains pooled connections
    Given the shared fake clock file is set to "2026-11-17T10:00:00Z"
    And account "gmail-archive" is configured for OAuth2 against the mock OAuth2 server and mock-gmail with scope "https://mail.google.com/" and token_cache "memory_only"
    And the secret store contains a valid refresh token for "gmail-archive"
    And the mock OAuth2 server issues access tokens with expires_in 100 seconds
    And the server is configured with caller "archive-agent" using policy "archive-policy"
    And policy "archive-policy" grants account "gmail-archive"
    And policy "archive-policy" grants folder on account "gmail-archive":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    When archive-agent calls list_folders with account "gmail-archive"
    Then the response decision is ALLOW
    And the mock OAuth2 token endpoint recorded 1 token request
    And the IMAP command log for "gmail-archive" contains 1 AUTHENTICATE XOAUTH2 command
    When the shared fake clock file advances to "2026-11-17T10:01:25Z"
    And archive-agent calls list_folders with account "gmail-archive"
    Then the response decision is ALLOW
    And the mock OAuth2 token endpoint recorded 2 token requests
    And the audit log contains an entry with tool "token_refresh", result "OK", reason "token_refresh_ok"
    And the audit log contains an entry with tool "pool_drain", reason "pool_drain"

  Scenario: Reactive single retry refreshes token once when XOAUTH2 authentication fails with a cached token
    Given account "gmail-archive" is configured for OAuth2 against the mock OAuth2 server and mock-gmail with scope "https://mail.google.com/" and token_cache "memory_only"
    And the secret store contains a valid refresh token for "gmail-archive"
    And the server is configured with caller "archive-agent" using policy "archive-policy"
    And policy "archive-policy" grants account "gmail-archive"
    And policy "archive-policy" grants folder on account "gmail-archive":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    And archive-agent calls list_folders with account "gmail-archive"
    And the mock-gmail server revokes the currently cached XOAUTH2 access token once and closes every open IMAP connection
    When archive-agent calls list_folders with account "gmail-archive"
    Then the response decision is ALLOW
    And the mock OAuth2 token endpoint recorded 2 token requests
    And the IMAP command log for "gmail-archive" contains 3 AUTHENTICATE XOAUTH2 commands

  Scenario: invalid_grant persists needs_rebootstrap in wal.db across SIGHUP and process restart until re-bootstrapped
    Given account "gmail-archive" is configured for OAuth2 against the mock OAuth2 server and mock-gmail with scope "https://mail.google.com/" and token_cache "memory_only"
    And the secret store contains a refresh token for "gmail-archive"
    And the mock OAuth2 server is primed to reject the next refresh_token exchange with OAuth error "invalid_grant"
    And the server is configured with caller "archive-agent" using policy "archive-policy"
    And policy "archive-policy" grants account "gmail-archive"
    And policy "archive-policy" grants folder on account "gmail-archive":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    When archive-agent calls list_folders with account "gmail-archive"
    Then the response decision is DENY
    And the response field reason equals "needs_rebootstrap"
    And the audit log contains an entry with tool "token_refresh", decision "DENY", reason "token_refresh_failed"
    When the server receives SIGHUP
    And the server is restarted
    And archive-agent calls list_accounts
    Then the account entry for "gmail-archive" has field "state" equal to "needs_rebootstrap"
    When archive-agent calls list_folders with account "gmail-archive"
    Then the response decision is DENY
    And the response field reason equals "needs_rebootstrap"
    And the mock OAuth2 token endpoint recorded 1 token request
    And the mock-gmail server received 0 IMAP connections
    When the operator runs `imap-mcp-oauth-bootstrap --account gmail-archive` and completes the browser consent callback
    And the server receives SIGHUP
    And archive-agent calls list_folders with account "gmail-archive"
    Then the response decision is ALLOW

  Scenario: Token cache memory_only never writes access_token to disk
    Given account "gmail-archive" is configured for OAuth2 against the mock OAuth2 server and mock-gmail with scope "https://mail.google.com/" and token_cache "memory_only"
    And the secret store contains a valid refresh token for "gmail-archive"
    And the server is configured with caller "archive-agent" using policy "archive-policy"
    And policy "archive-policy" grants account "gmail-archive"
    And policy "archive-policy" grants folder on account "gmail-archive":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    When archive-agent calls list_folders with account "gmail-archive"
    Then the response decision is ALLOW
    And the secret store file "accounts/gmail-archive/refresh_token" exists
    And no file exists in the secret store at "accounts/gmail-archive/access_token"

  Scenario: Token cache persist_all persists access_token and reuses it across a server restart
    Given account "gmail-archive" is configured for OAuth2 against the mock OAuth2 server and mock-gmail with scope "https://mail.google.com/" and token_cache "persist_all"
    And the secret store contains a valid refresh token for "gmail-archive"
    And the mock OAuth2 server issues access tokens with expires_in 3600 seconds
    And the server is configured with caller "archive-agent" using policy "archive-policy"
    And policy "archive-policy" grants account "gmail-archive"
    And policy "archive-policy" grants folder on account "gmail-archive":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    When archive-agent calls list_folders with account "gmail-archive"
    Then the response decision is ALLOW
    And the secret store file "accounts/gmail-archive/access_token" exists with mode 0600
    And the mock OAuth2 token endpoint recorded 1 token request
    When the server is restarted
    And archive-agent calls list_folders with account "gmail-archive"
    Then the response decision is ALLOW
    And the mock OAuth2 token endpoint recorded 1 token request
