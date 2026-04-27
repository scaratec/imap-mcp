@pending @pending_LIM_0003
Feature: OAuth2 bootstrap and token lifecycle

  The server authenticates against IMAP providers via XOAUTH2. A
  bootstrap script obtains a refresh token interactively; the server
  redeems it for access tokens as needed. Per-account scope
  minimization acts as a second authorization layer beneath policy.
  Token cache strategy is per-account (memory_only or persist_all).
  See ADR 0009 and ADR 0010.

  Covered error layers (per BDD Guidelines §4.5):
    - Bootstrap happy path                  : 1
    - Bootstrap user denies consent         : 1
    - Scope minimization blocks write op    : 1
    - Token refresh: happy path             : 1
    - Token refresh: invalid_grant          : 1  (-> needs_rebootstrap)
    - Token cache: memory_only              : 1
    - Token cache: persist_all              : 1
    - PKCE verifier mismatch                : 1
    Total enumerated                        : 8   covered by this feature: 8

  Background:
    Given a mock OAuth2 provider "google-mock" listens on https://localhost:mock-oauth2
    And the secret store "file_dir" is configured at path $TMPDIR/secrets
    And the server is configured with account:
      | id             | provider     | host                 | oauth_scope                   | token_cache  |
      | gmail-archive  | google-mock  | imap.localhost-mock  | https://mail.google.com/      | persist_all  |
      | gmail-ronly    | google-mock  | imap.localhost-mock  | gmail.readonly                | memory_only  |

  Scenario: Bootstrap happy path — user consents and the refresh token lands in the secret store
    Given the mock OAuth2 provider is primed to consent on the next authorization request
    When the operator runs `imap-mcp-oauth-bootstrap --account gmail-archive`
    And the bootstrap opens the provider consent URL and the browser returns authorization code "authcode-xyz"
    Then the bootstrap reports success
    And the secret store contains a non-empty value under key "accounts/gmail-archive/refresh_token"
    And the audit log contains an entry with tool "oauth_bootstrap", decision "ALLOW", result "OK"

  Scenario: Bootstrap aborted — user denies consent leaves the secret store unchanged
    Given the mock OAuth2 provider is primed to deny on the next authorization request
    When the operator runs `imap-mcp-oauth-bootstrap --account gmail-archive`
    And the browser returns error "access_denied"
    Then the bootstrap reports failure with reason "user_denied"
    And the secret store has no value under key "accounts/gmail-archive/refresh_token"

  Scenario: Scope minimization — a readonly-scope account refuses a write tool regardless of policy
    Given the secret store contains a valid refresh token for "gmail-ronly"
    And policy "ronly-policy" grants capability "mark_seen" on folder "INBOX"
    And caller "reader-agent" uses policy "ronly-policy"
    And the folder "INBOX" on "gmail-ronly" holds a message with uid 1001
    When reader-agent calls mark_seen with account "gmail-ronly", folder "INBOX", uid 1001, seen true
    Then the response decision is DENY
    And the response field reason equals "oauth_scope_insufficient"
    And the response field required_scope contains "https://mail.google.com/"
    And the response field granted_scope equals "gmail.readonly"
    And the IMAP command STORE \Seen was NEVER issued against the provider

  Scenario: Token refresh happy path — access token is renewed at 80% of lifetime in the background
    Given the secret store contains a valid refresh token for "gmail-archive"
    And the mock OAuth2 provider returns access tokens with lifetime 60 seconds
    When the server starts and opens an IMAP connection to "gmail-archive"
    And the server runs for 50 seconds
    Then the mock OAuth2 token endpoint records at least 2 token-exchange requests
    And the currently open IMAP connection has never failed authentication
    And the audit log contains at least 1 entry with tool "token_refresh", result "OK", account "gmail-archive"

  Scenario: Refresh-token revoked — invalid_grant moves the account to needs_rebootstrap and halts connections
    Given the secret store contains a refresh token for "gmail-archive"
    And the mock OAuth2 provider responds to the next token-exchange request with error "invalid_grant"
    When the server attempts to open an IMAP connection to "gmail-archive"
    Then the connection attempt fails
    And the account state for "gmail-archive" is "needs_rebootstrap"
    And the audit log contains an entry with tool "token_refresh", decision "DENY", reason "invalid_grant"
    And no further connection attempts to "gmail-archive" occur until the operator reruns the bootstrap

  Scenario: Token cache "memory_only" does NOT persist the access token on disk
    Given the secret store contains a valid refresh token for "gmail-ronly"
    When the server starts and completes one token exchange for "gmail-ronly"
    Then the secret store contains a value under key "accounts/gmail-ronly/refresh_token"
    And the secret store does NOT contain a value under key "accounts/gmail-ronly/access_token"

  Scenario: Token cache "persist_all" stores BOTH refresh and access tokens
    Given the secret store contains a valid refresh token for "gmail-archive"
    When the server starts and completes one token exchange for "gmail-archive"
    Then the secret store contains a value under key "accounts/gmail-archive/refresh_token"
    And the secret store contains a value under key "accounts/gmail-archive/access_token"
    When the server is restarted
    Then the server reuses the persisted access token without issuing a new token-exchange request
    And the mock OAuth2 token endpoint records only the prior token-exchange count

  Scenario: PKCE verifier mismatch — bootstrap fails and no secret is written
    Given the mock OAuth2 provider is primed to tamper with the code_challenge
    When the operator runs `imap-mcp-oauth-bootstrap --account gmail-archive`
    Then the bootstrap reports failure with reason "pkce_verification_failed"
    And the secret store has no value under key "accounts/gmail-archive/refresh_token"
