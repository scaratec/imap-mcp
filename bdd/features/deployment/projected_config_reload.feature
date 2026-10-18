@pending
Feature: Configuration reload from a projected ConfigMap volume
  As a cluster operator running imap-mcp as a sidecar (ADR-0029),
  I want the server to pick up a changed policy after the kubelet has
  atomically re-projected the ConfigMap volume and I have signalled the
  process,
  so that a policy change reaches the running server without a rebuild
  and without the server ever reading a half-written file.

  # @pending: red-first specification for ADR-0029 §5. SIGHUP reload
  # itself exists and is covered by features/policy/policy_reload.feature;
  # what is unproven is that it survives the *symlink indirection* a
  # kubelet uses. The harness needs a step that builds the
  # `..&lt;timestamp&gt;/` + `..data` + per-key-symlink layout and swaps it
  # the way the kubelet does. The tag comes off with that step.
  # See docs/ROADMAP.md entry 24.

  Kubernetes does not write into a projected volume in place. It
  materialises the new content in a fresh timestamped directory next to
  the old one, points the `..data` symlink at it with a single atomic
  `rename(2)`, and then removes the old directory. Every user-visible
  entry in the mount root is a symlink through `..data`:

      /config/
        ..2026_08_25_09_00_00.418273651/   <- previous content
        ..2026_08_25_11_30_00.771049238/   <- new content
        ..data -> ..2026_08_25_11_30_00.771049238
        accounts.yaml -> ..data/accounts.yaml
        callers.yaml  -> ..data/callers.yaml
        policies      -> ..data/policies

  Two properties follow, and both are load-bearing for ADR-0029:

  1. A reader that opens `/config/accounts.yaml` fresh on every load
     always sees a complete, self-consistent generation. There is no
     torn read to defend against, so the server needs no locking or
     retry around config loading.
  2. A reader that resolved the path once and held on to the *realpath*
     — or held an open directory handle — is pinned to a generation the
     kubelet will delete. Its next read fails with ENOENT rather than
     returning stale data. This is the failure this feature exists to
     rule out.

  The kubelet sends no signal on re-projection. The pod's process
  therefore keeps serving the old configuration until something sends
  SIGHUP. ADR-0029 §5 makes that the operator's responsibility and
  names rolling the pod on a ConfigMap-content hash as the recommended
  way to discharge it. The first scenario below is the one an operator
  most needs to see: editing the ConfigMap alone changes nothing.

  Covered error layers (per BDD Guidelines §4.5):
    - Swap without signal: old policy still in force  : 1
    - Swap with SIGHUP: new policy takes effect       : 1
    - Stale generation removed after swap             : 1
    - Repeated swaps between two SIGHUPs              : 1
    - Malformed content delivered by swap             : 1
    - Policy file added by swap                       : 1
    - Policy file removed by swap                     : 1
    - Secret volume re-projected: token rotation      : 1
    Total enumerated                                  : 8   covered by this feature: 8

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Rechnungen"
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" folder "INBOX/Rechnungen" has:
      | mode      | default | rules |
      | whitelist | NONE    | [{from_domain=hornbach.de -> ENVELOPE}] |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from                  |
      | 301 | rechnung@hornbach.de  |
      | 302 | marketing@example.com |
    And the config directory is projected as a Kubernetes ConfigMap volume
    And the server is started with transport "http" on a random port

  Scenario: Re-projecting the volume alone does not change enforcement
    # The pitfall ADR-0029 §5 exists to prevent: an operator edits the
    # ConfigMap, sees `kubectl get configmap` show the new content, and
    # concludes the change is live. It is not.
    Given invoice-agent sees 1 message in "INBOX/Rechnungen"
    When the projected ConfigMap is atomically swapped, replacing policy "invoice-policy" with:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          folders:
            - path: INBOX/Rechnungen
              mode: whitelist
              default: ENVELOPE
              rules: []
      """
    And no signal is sent to the server
    Then invoice-agent still sees 1 message in "INBOX/Rechnungen"
    And the file "/config/policies/invoice-policy.yaml" resolves to the new generation

  Scenario: SIGHUP after the swap puts the new policy in force
    When the projected ConfigMap is atomically swapped, replacing policy "invoice-policy" with:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          folders:
            - path: INBOX/Rechnungen
              mode: whitelist
              default: ENVELOPE
              rules: []
      """
    And the server receives SIGHUP
    Then invoice-agent sees 2 messages in "INBOX/Rechnungen"

  Scenario: Reload succeeds after the superseded generation has been removed
    # The regression this rules out: a server that cached the realpath
    # of the config directory at startup reads through a path that no
    # longer exists once the kubelet garbage-collects the old
    # generation, and the reload fails with ENOENT instead of applying.
    When the projected ConfigMap is atomically swapped, replacing policy "invoice-policy" with:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          folders:
            - path: INBOX/Rechnungen
              mode: whitelist
              default: ENVELOPE
              rules: []
      """
    And the superseded projection generation is removed
    And the server receives SIGHUP
    Then the reload succeeds
    And invoice-agent sees 2 messages in "INBOX/Rechnungen"

  Scenario: Only the newest generation is applied when several swaps precede one signal
    # The kubelet syncs on its own schedule; two ConfigMap edits inside
    # one sync period collapse into one visible generation, and an
    # operator who signals once must get the newest, not an intermediate.
    When the projected ConfigMap is atomically swapped, replacing policy "invoice-policy" with:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          folders:
            - path: INBOX/Rechnungen
              mode: whitelist
              default: BODY
              rules: []
      """
    And the projected ConfigMap is atomically swapped, replacing policy "invoice-policy" with:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          folders:
            - path: INBOX/Rechnungen
              mode: whitelist
              default: ENVELOPE
              rules: []
      """
    And the server receives SIGHUP
    Then invoice-agent sees 2 messages in "INBOX/Rechnungen"
    And invoice-agent's visibility for uid 302 in "INBOX/Rechnungen" is "ENVELOPE"

  Scenario: A malformed generation is rejected and the last known good policy stays in force
    When the projected ConfigMap is atomically swapped, replacing policy "invoice-policy" with:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          folders:
            - path: INBOX/Rechnungen
              mode: whitelist
              default: ENVELOPE  rules:   # <-- missing newline; invalid YAML
      """
    And the server receives SIGHUP
    Then the reload fails
    And invoice-agent still sees 1 message in "INBOX/Rechnungen"
    And the server is still serving requests

  Scenario: A policy file added by the swap becomes available after the signal
    Given the server is configured with caller "audit-agent" using policy "audit-policy"
    When the projected ConfigMap is atomically swapped, adding policy "audit-policy" with:
      """
      name: audit-policy
      accounts:
        gupta-scaratec:
          folders:
            - path: INBOX/Rechnungen
              mode: whitelist
              default: ENVELOPE
              rules: []
      """
    And the server receives SIGHUP
    Then the reload succeeds
    And audit-agent sees 2 messages in "INBOX/Rechnungen"

  Scenario: A policy file removed by the swap is rejected while a caller still references it
    # A ConfigMap key removed by mistake must not silently leave a
    # caller with no policy — under ADR-0001 that would be
    # indistinguishable from default-deny and would hide the operator
    # error until someone noticed missing mail.
    When the projected ConfigMap is atomically swapped, removing policy "invoice-policy"
    And the server receives SIGHUP
    Then the reload fails
    And the reload error names caller "invoice-agent" and the missing policy "invoice-policy"
    And invoice-agent still sees 1 message in "INBOX/Rechnungen"

  Scenario: A rotated caller token in the projected Secret volume takes effect after the signal
    # Secret volumes are re-projected by exactly the same mechanism.
    # The scenario is here rather than in caller_authentication.feature
    # because it is the projection, not the auth check, under test.
    Given the secret directory is projected as a Kubernetes Secret volume
    And invoice-agent authenticates successfully with bearer token "correct-horse-battery"
    When the projected Secret is atomically swapped, setting "callers/invoice-agent/token" to "rotated-token-value"
    And the server receives SIGHUP
    Then a handshake with caller_id "invoice-agent" and bearer token "rotated-token-value" succeeds
    And a handshake with caller_id "invoice-agent" and bearer token "correct-horse-battery" fails with error "auth_failed"
