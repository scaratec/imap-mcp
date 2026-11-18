Feature: Atomic configuration reload from Kubernetes projected ConfigMap and Secret volumes

  ADR 0049 §3 and ADR 0051 §3 ensure that configuration and secret loads
  follow Kubernetes `..data` symlink swaps cleanly:
    - Swapping `..data` alone does not alter enforcement until SIGHUP.
    - On SIGHUP, the loader verifies a stable `..data` generation across
      all files, validates cross-file references (caller -> policy ->
      accounts), and swaps state atomically even after old timestamped
      directories have been garbage-collected.
    - Broken YAML or missing referenced policy files reject the reload
      and retain the previous configuration.

  Covered error layers (per BDD Guidelines §4.5):
    - Projection swap without SIGHUP keeps previous policy active   : 1
    - Projection swap followed by SIGHUP activates new policy       : 1
    - Garbage-collecting superseded generation does not break reload: 1
    - Multiple swaps before SIGHUP apply only the newest generation : 1
    - Malformed YAML in new generation preserves previous policy    : 1
    - Switching caller to a newly added policy file succeeds        : 1
    - Removing the caller's referenced policy file rejects reload   : 1
    - Re-projecting the Secret volume rotates bearer token on SIGHUP: 1
    Total enumerated                                                : 8   covered by this feature: 8

  Background:
    Given the server clock is pinned to "2026-08-25T12:00:00Z"
    And the audit log directory is a fresh $TMPDIR/audit
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path      |
      | INBOX            |
      | INBOX/Rechnungen |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address          | to_address            | subject    | date                 |
      | 301 | rechnung@hornbach.de  | me@gupta-scaratec.com | Rechnung 1 | 2026-08-24T09:00:00Z |
      | 302 | marketing@example.com | me@gupta-scaratec.com | Newsletter | 2026-08-24T10:00:00Z |
    And the projected ConfigMap and Secret volumes are initialized for caller "invoice-agent" with bearer token "correct-horse-battery" and policy "invoice-policy" granting whitelist hornbach.de -> ENVELOPE on "gupta-scaratec:INBOX/Rechnungen"
    And the server process is started from the projected volumes with transport "http" on 127.0.0.1 and a random port
    And the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery"

  Scenario: Re-projecting the ConfigMap volume without SIGHUP keeps the old policy active
    When the MCP client calls search on the established HTTP session with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    Then the response field total_visible equals 1
    When the projected ConfigMap is atomically swapped so policy "invoice-policy" grants blacklist default ENVELOPE on "gupta-scaratec:INBOX/Rechnungen"
    And no signal is sent to the server
    And the MCP client calls search on the established HTTP session with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    Then the response field total_visible equals 1

  Scenario: SIGHUP after the ConfigMap swap activates the new policy generation
    When the projected ConfigMap is atomically swapped so policy "invoice-policy" grants blacklist default ENVELOPE on "gupta-scaratec:INBOX/Rechnungen"
    And the server receives SIGHUP
    And the MCP client calls search on the established HTTP session with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    Then the response field total_visible equals 2

  Scenario: SIGHUP reload succeeds after kubelet removes the superseded timestamp directory
    When the projected ConfigMap is atomically swapped so policy "invoice-policy" grants blacklist default ENVELOPE on "gupta-scaratec:INBOX/Rechnungen"
    And the superseded ConfigMap projection directory is deleted
    And the server receives SIGHUP
    Then the audit log contains a record with tool "policy_reload", result "OK", reason "policy_reload_ok"
    When the MCP client calls search on the established HTTP session with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    Then the response field total_visible equals 2

  Scenario: Multiple swaps before a single SIGHUP apply the newest generation
    When the projected ConfigMap is atomically swapped so policy "invoice-policy" grants blacklist default BODY on "gupta-scaratec:INBOX/Rechnungen"
    And the projected ConfigMap is atomically swapped so policy "invoice-policy" grants blacklist default ENVELOPE on "gupta-scaratec:INBOX/Rechnungen"
    And the server receives SIGHUP
    And the MCP client calls fetch_envelope on the established HTTP session with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 302
    Then the response decision is ALLOW
    And the response field visibility_applied equals "ENVELOPE"

  Scenario: A malformed YAML generation is rejected on SIGHUP and the previous policy stays active
    When the projected ConfigMap is atomically swapped with malformed YAML in "invoice-policy.yaml"
    And the server receives SIGHUP
    Then the audit log contains a record with tool "policy_reload", result "ERROR", reason "policy_reload_rejected"
    When the MCP client calls search on the established HTTP session with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    Then the response field total_visible equals 1

  Scenario: Switching callers.yaml to a newly added policy file in the same swap succeeds on SIGHUP
    When the projected ConfigMap is atomically swapped adding policy "audit-policy" (blacklist default ENVELOPE on "gupta-scaratec:INBOX/Rechnungen") and pointing caller "invoice-agent" to "audit-policy"
    And the server receives SIGHUP
    Then the audit log contains a record with tool "policy_reload", result "OK", reason "policy_reload_ok"
    When the MCP client calls search on the established HTTP session with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    Then the response field total_visible equals 2

  Scenario: Removing the policy file referenced by callers.yaml is rejected on SIGHUP and preserves enforcement
    When the projected ConfigMap is atomically swapped removing "invoice-policy.yaml" while caller "invoice-agent" still references "invoice-policy"
    And the server receives SIGHUP
    Then the audit log contains a record with tool "policy_reload", result "ERROR", reason "policy_reload_rejected"
    When the MCP client calls search on the established HTTP session with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    Then the response field total_visible equals 1

  Scenario: Re-projecting the Secret volume rotates the shared_token bearer credential on SIGHUP
    When the projected Secret volume is atomically swapped updating "callers/invoice-agent/token" to "rotated-token-value"
    And the server receives SIGHUP
    Then a new MCP HTTP Initialize handshake with caller_id "invoice-agent" and bearer token "rotated-token-value" succeeds
    And a new MCP HTTP Initialize handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery" fails with error "auth_failed"
