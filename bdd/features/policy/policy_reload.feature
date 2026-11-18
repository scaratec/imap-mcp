@test_hooks
Feature: Policy reload via SIGHUP

  Policy and account configuration live as YAML files. On SIGHUP the
  server re-reads the entire configuration tree, validates cross-file
  references and rule invariants, and swaps in-memory state atomically.
  Parse or validation errors preserve the previous configuration and
  emit a policy_reload_rejected audit record (ADR 0048, ADR 0049, ADR 0050, ADR 0055).

  Covered error layers (per BDD Guidelines §4.5):
    - Happy reload: new rule makes previously not_found UID readable : 1
    - Broken config: YAML parse error preserves previous policy      : 1
    - Broken config: semantic error preserves previous policy        : 1
    - Account removed: proxy-verified pool LOGOUT & account_hidden   : 1
    - Folder policy added at runtime appears in list_folders         : 1
    - OAuth scope change transitions active account to needs_rebootstrap : 1
    - Mid-saga reload: staged tx commits, pre-APPEND tx aborts (saga_aborted) : 2
    Total enumerated                                                 : 8   covered here: 8

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path      |
      | INBOX            |
      | INBOX/Rechnungen |
    And the IMAP account "personal" exists with folders:
      | folder path   |
      | INBOX         |
      | Archiv/Belege |
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants account "personal"
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder           | mode      | default | move_out | accept_incoming | rules                                    |
      | gupta-scaratec | INBOX/Rechnungen | whitelist | NONE    | true     | false           | [{from_domain=hornbach.de -> ENVELOPE}]  |
      | personal       | Archiv/Belege    | whitelist | NONE    | false    | true            | []                                       |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address          | to_address            | subject    | date                 |
      | 301 | <m-301@gupta-scaratec.com> | rechnung@hornbach.de  | me@gupta-scaratec.com | Rechnung 1 | 2026-05-06T09:00:00Z |
      | 302 | <m-302@gupta-scaratec.com> | marketing@example.com | me@gupta-scaratec.com | Promo      | 2026-05-06T09:00:00Z |

  Scenario: A new rule becomes effective after SIGHUP on the next tool call
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 302
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    When the operator updates "INBOX/Rechnungen" rules to:
      | match                   | grant    |
      | from_domain=hornbach.de | ENVELOPE |
      | from_domain=example.com | ENVELOPE |
    And the server receives SIGHUP
    And invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 302
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field visibility_applied equals "ENVELOPE"
    And the audit log contains a record with tool "policy_reload", result "OK", reason "policy_reload_ok"

  Scenario: A YAML parse error preserves the previous policy
    When the operator replaces the policy file with:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          - path: INBOX/Rechnungen
            mode: whitelist
            default: NONE  rules:
      """
    And the server receives SIGHUP
    Then the audit log contains a record with tool "policy_reload", result "ERROR", reason "policy_reload_rejected"
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 301
    Then the response decision is ALLOW
    And the response field visibility_applied equals "ENVELOPE"

  Scenario: A semantic validation error preserves the previous policy
    When the operator replaces the policy file with:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          - path: INBOX/Rechnungen
            mode: whitelist
            default: BODY
            rules: []
      """
    And the server receives SIGHUP
    Then the audit log contains a record with tool "policy_reload", result "ERROR", reason "policy_reload_rejected"
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 301
    Then the response decision is ALLOW
    And the response field visibility_applied equals "ENVELOPE"

  Scenario: Removing an account drains its pooled IMAP connection and denies subsequent calls
    Given the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 301
    Then the IMAP connections opened for account "gupta-scaratec" number exactly 1
    When the operator removes account "gupta-scaratec" from accounts.yaml and policy "invoice-policy"
    And the server receives SIGHUP
    Then within 5 seconds the IMAP command log for "gupta-scaratec" contains a LOGOUT command
    And the audit log contains a record with tool "pool_drain", reason "pool_drain"
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response decision is DENY
    And the response field reason equals "account_hidden"

  Scenario: Adding a folder policy at runtime makes a previously hidden folder visible in list_folders
    Given the IMAP account "gupta-scaratec" also has folder "Banking"
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response field folders equals ["INBOX/Rechnungen"]
    When the operator adds to policy "invoice-policy":
      | account        | folder  | mode      | default | rules                                   |
      | gupta-scaratec | Banking | whitelist | NONE    | [{from_domain=bank.de -> ENVELOPE}]     |
    And the server receives SIGHUP
    And invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response field folders contains "Banking"
    And the response does not contain any field named "hidden_folders_count"

  Scenario: Changing an account's OAuth scope transitions an active account to needs_rebootstrap
    Given the IMAP account "gmail-archive" is configured with provider "google", oauth_scope "https://mail.google.com/", and a valid refresh token
    And policy "invoice-policy" grants account "gmail-archive"
    When invoice-agent calls list_accounts
    Then the account entry for "gmail-archive" has field "state" equal to "active"
    When the operator changes the oauth_scope for "gmail-archive" to "https://mail.google.com/custom-scope"
    And the server receives SIGHUP
    And invoice-agent calls list_accounts
    Then the account entry for "gmail-archive" has field "state" equal to "needs_rebootstrap"
    When invoice-agent calls list_folders with account "gmail-archive"
    Then the response decision is DENY
    And the response field reason equals "needs_rebootstrap"

  Scenario: A SIGHUP during an in-flight saga lets the staged transfer finish while denying new moves
    Given the folder "INBOX/Rechnungen" also holds a message with:
      | uid | message_id                 | from_address        | to_address            | subject    | date                 |
      | 303 | <m-303@gupta-scaratec.com> | service@hornbach.de | me@gupta-scaratec.com | Rechnung 2 | 2026-05-06T10:00:00Z |
    And saga pause hook is armed at step "staged"
    When invoice-agent starts an asynchronous move of uid 301 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege" and waits until paused at "staged"
    And the operator updates folder "INBOX/Rechnungen" in policy "invoice-policy" to:
      | mode      | default | move_out | rules                                   |
      | whitelist | NONE    | false    | [{from_domain=hornbach.de -> ENVELOPE}] |
    And the server receives SIGHUP
    And the paused saga is released and finishes
    Then the WAL entry for the asynchronous move has status "committed"
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-301@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-301@gupta-scaratec.com>" returns zero results
    When invoice-agent calls move with source {"account":"gupta-scaratec","folder":"INBOX/Rechnungen","uid":303}, target {"account":"personal","folder":"Archiv/Belege"}
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-303@gupta-scaratec.com>" returns exactly one result

  Scenario: Revoking move_out via SIGHUP while a saga is still pre-APPEND at fetched aborts cleanly with saga_aborted
    Given saga pause hook is armed at step "fetched"
    When invoice-agent starts an asynchronous move of uid 301 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege" and waits until paused at "fetched"
    And the operator updates folder "INBOX/Rechnungen" in policy "invoice-policy" to:
      | mode      | default | move_out | rules                                   |
      | whitelist | NONE    | false    | [{from_domain=hornbach.de -> ENVELOPE}] |
    And the server receives SIGHUP
    And the paused saga is released and finishes
    Then the asynchronous move response has decision "ALLOW", result "ERROR", and error.type "saga_aborted"
    And the WAL entry for the asynchronous move has status "aborted"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-301@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-301@gupta-scaratec.com>" returns zero results
