Feature: Policy reload via SIGHUP

  Policy and account configuration live as YAML files in a Git
  repository. On SIGHUP the server re-parses the entire tree in a
  temporary space, validates it, and swaps the in-memory state
  atomically. Parse or validation failure preserves the previous
  state. See ADR 0014.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy reload: new rule becomes effective          : 1
    - Broken config: parse error preserves old policy   : 1
    - Broken config: semantic error preserves old policy: 1
    - Account removed: pool drained, tool call denies   : 1
    - Account added at runtime (new folder)             : 1
    - OAuth scope change: needs_rebootstrap transition  : 1
    - Mid-saga reload: saga keeps its original policy   : 1
    Total enumerated                                     : 7   covered here: 7

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Rechnungen"
    And the IMAP account "personal" exists with folder "Archiv/Belege"
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" folder "INBOX/Rechnungen" has:
      | mode      | default | rules                                |
      | whitelist | NONE    | [{from_domain=hornbach.de -> ENVELOPE}] |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from                    |
      | 301 | rechnung@hornbach.de    |
      | 302 | marketing@example.com   |

  Scenario: A new rule becomes effective after SIGHUP on the next tool call
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 302
    Then the response decision is DENY
    And the response field reason equals "sender_not_whitelisted"
    When the operator updates "INBOX/Rechnungen" rules to:
      | match                       | grant    |
      | from_domain=hornbach.de     | ENVELOPE |
      | from_domain=example.com     | ENVELOPE |
    And the server receives SIGHUP
    And invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 302
    Then the response decision is ALLOW
    And the response field visibility_applied equals "ENVELOPE"
    And the audit log contains a record with tool "policy_reload", result "OK"

  Scenario: A YAML parse error preserves the previous policy
    Given the current policy file content is:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          folders:
            - path: INBOX/Rechnungen
              mode: whitelist
              default: NONE
              rules:
                - match: { from_domain: hornbach.de }
                  grant: ENVELOPE
      """
    When the operator replaces the policy file with:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          folders:
            - path: INBOX/Rechnungen
              mode: whitelist
              default: NONE  rules:       # <-- missing newline; invalid YAML
      """
    And the server receives SIGHUP
    Then the audit log contains a record with tool "policy_reload", result "ERROR", reason "parse_error"
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 301
    Then the response decision is ALLOW
    And the response field visibility_applied equals "ENVELOPE"

  Scenario: A semantic validation error preserves the previous policy
    When the operator replaces the policy file to contain a whitelist folder with a non-NONE default:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          folders:
            - path: INBOX/Rechnungen
              mode: whitelist
              default: BODY           # illegal for whitelist
              rules: []
      """
    And the server receives SIGHUP
    Then the audit log contains a record with tool "policy_reload", result "ERROR", reason "validation_error"
    And the audit record field detail contains "whitelist mode requires default=NONE"
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 301
    Then the response decision is ALLOW
    And the response field visibility_applied equals "ENVELOPE"

  Scenario: Removing an account drains its connection pool and denies subsequent calls
    Given invoice-agent has made one successful fetch_envelope against "gupta-scaratec" in this session
    And the server has 1 open IMAP connection in the pool for account "gupta-scaratec"
    When the operator removes account "gupta-scaratec" from accounts.yaml
    And the server receives SIGHUP
    Then the number of open IMAP connections for "gupta-scaratec" becomes 0 within 5 seconds
    And the audit log contains a record with tool "pool_drain", account "gupta-scaratec", reason "account_removed"
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response decision is DENY
    And the response field reason equals "account_hidden"

  Scenario: Adding a folder policy at runtime makes a previously hidden folder visible
    Given the IMAP account "gupta-scaratec" also has folder "Banking"
    And policy "invoice-policy" does NOT grant folder "Banking"
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response field folders equals ["INBOX/Rechnungen"]
    When the operator adds to policy "invoice-policy":
      | account        | folder  | mode      | default | rules                              |
      | gupta-scaratec | Banking | whitelist | NONE    | [{from_domain=bank.de -> ENVELOPE}]|
    And the server receives SIGHUP
    And invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response field folders contains "Banking"
    And the response field hidden_folders_count decreases by 1 compared to the previous call

  Scenario: Changing an account's OAuth scope moves it to needs_rebootstrap
    Given account "gmail-archive" is configured with oauth_scope "https://www.googleapis.com/auth/gmail.readonly"
    And the account's state is "healthy"
    When the operator changes the scope for "gmail-archive" to "https://mail.google.com/"
    And the server receives SIGHUP
    Then the account state for "gmail-archive" transitions to "needs_rebootstrap"
    And the audit log contains a record with tool "policy_reload", detail containing "oauth_scope changed; rebootstrap required"
    And new IMAP connection attempts to "gmail-archive" are refused with reason "needs_rebootstrap"

  Scenario: A SIGHUP during an in-flight saga applies only from the next transaction
    Given a cross-account move for uid 301 is in progress, currently at WAL step "fetched"
    When the operator changes policy "invoice-policy" to remove the move_out capability from "INBOX/Rechnungen"
    And the server receives SIGHUP
    Then the in-flight transaction completes under the original capabilities and reaches state "committed"
    When invoice-agent starts a new move with source {"account":"gupta-scaratec","folder":"INBOX/Rechnungen","uid":302}, target {"account":"personal","folder":"Archiv/Belege"}
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "move_out"
