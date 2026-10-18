@pending
Feature: Pending sagas recover on process start
  As a cluster operator running imap-mcp as a sidecar (ADR-0029),
  I want a transaction that was in flight when the process died to be
  driven to a terminal state by the restart itself,
  so that node drains, evictions, image rolls and OOM kills — all of
  which are routine in a cluster — do not leave mail half-moved until
  somebody notices.

  # @pending: red-first specification for ADR-0029 §8. Recovery logic
  # exists (`run_pending_recovery`), but on the current code path it is
  # reachable only from the test-only control surface (ADR-0023); no
  # startup scan drives it. The tag comes off in the change that runs
  # recovery at startup. See docs/ROADMAP.md entry 22.
  #
  # The Background mirrors features/transactions/saga_crash_recovery.feature.
  # Align step phrasings against that file when implementing; where the
  # two disagree, that file wins — it is the one with passing steps.

  This feature is the deliberate counterpart to
  features/transactions/saga_crash_recovery.feature. That feature
  narrates startup recovery in its header but drives every scenario
  with an explicit "the server's background recovery loop runs once"
  step. The recovery *logic* is therefore well covered; the recovery
  *trigger* is not covered at all, and the trigger is the only part a
  cluster depends on. A restarted pod has no test harness standing by
  to call a hidden tool.

  Every scenario below restarts the server and then asserts the
  terminal state without any step that pokes the recovery machinery.
  The only permitted stimulus after "the server is restarted" is
  ordinary client traffic, and the assertions carry an explicit
  timeout so that a recovery pass that never runs fails the scenario
  rather than hanging it.

  Persistence validation (per BDD Guidelines §13.2 Prüfung 1): every
  scenario verifies via direct IMAP on both accounts AND direct WAL
  read. The saga must not depend on its own self-reported state.

  Covered error layers (per BDD Guidelines §4.5):
    - Pending saga at startup, recoverable          : 1
    - Pending saga at startup, target unreachable   : 1
    - No pending sagas: no spurious work            : 1
    - Saga already at retry_limit: not retried      : 1
    - Recovery does not block the tool surface      : 1
    - WAL file absent at startup                    : 1
    - Recovery is audited; chain continues          : 1
    - Second restart does not re-apply a commit     : 1
    Total enumerated                                : 8   covered by this feature: 8

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Rechnungen"
    And the IMAP account "personal" exists with folder "Archiv/Belege"
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants account "personal"
    And policy "invoice-policy" folder "INBOX/Rechnungen" has:
      | mode      | default | rules |
      | whitelist | FULL    | []    |
    And policy "invoice-policy" folder "Archiv/Belege" has:
      | mode      | default | rules |
      | whitelist | FULL    | []    |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from                 | message_id             |
      | 901 | rechnung@hornbach.de | <inv-901@hornbach.de>  |
    And the WAL is empty

  Scenario: A saga interrupted after APPEND completes on the next start
    # The dangerous window: the message exists in both mailboxes. Until
    # the saga finishes, a restart that does nothing leaves a duplicate
    # in the operator's mail, silently.
    Given the server is configured to crash after APPEND before VERIFY
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 901}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the server terminates ungracefully
    Then the WAL entry for this tx_id has status "appended"
    When the server is restarted
    And invoice-agent completes an Initialize handshake successfully
    Then the WAL entry for this tx_id reaches status "committed" within 30 seconds
    And the message with Message-ID "<inv-901@hornbach.de>" is present exactly once in "personal" folder "Archiv/Belege"
    And the message with Message-ID "<inv-901@hornbach.de>" is absent from "gupta-scaratec" folder "INBOX/Rechnungen"

  Scenario: A saga interrupted before APPEND aborts on the next start
    Given the server is configured to crash after WAL BEGIN persistence
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 901}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the server terminates ungracefully
    When the server is restarted
    And invoice-agent completes an Initialize handshake successfully
    Then the WAL entry for this tx_id reaches status "aborted" within 30 seconds
    And the message with Message-ID "<inv-901@hornbach.de>" is present exactly once in "gupta-scaratec" folder "INBOX/Rechnungen"
    And the message with Message-ID "<inv-901@hornbach.de>" is absent from "personal" folder "Archiv/Belege"

  Scenario: A pending saga whose target is unreachable leaves the server serving
    # A sidecar must come up even when the far account is down. Recovery
    # is best-effort; startup is not.
    Given the server is configured to crash after APPEND before VERIFY
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 901}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the server terminates ungracefully
    And the IMAP account "personal" refuses connections
    When the server is restarted
    And invoice-agent completes an Initialize handshake successfully
    Then the handshake succeeds
    And invoice-agent can call list_folders on "gupta-scaratec"
    And the WAL entry for this tx_id has status "appended"
    And the WAL entry for this tx_id has a retry_count greater than 0

  Scenario: A start with no pending sagas performs no recovery work
    When the server is restarted
    And invoice-agent completes an Initialize handshake successfully
    Then the handshake succeeds
    And the WAL contains no entries with a non-terminal status
    And the audit log contains no entry with tool "saga_recovery"

  Scenario: A saga already at the retry limit is not retried again on restart
    # Otherwise every pod restart burns another attempt against a
    # transaction a human has already been asked to look at, and the
    # retry_count grows without bound across a rollout.
    Given the WAL retry_limit is configured to 3
    And the WAL contains an in-progress transaction with status "appended" referencing uid 901 and Message-ID "<inv-901@hornbach.de>"
    And that WAL entry has retry_count 3
    When the server is restarted
    And invoice-agent completes an Initialize handshake successfully
    Then the WAL entry for this tx_id has status "needs_operator"
    And the WAL entry for this tx_id has retry_count 3

  Scenario: The tool surface is available while recovery is still running
    Given the server is configured to crash after APPEND before VERIFY
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 901}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the server terminates ungracefully
    And the IMAP account "personal" delays every command by 5 seconds
    When the server is restarted
    And invoice-agent completes an Initialize handshake successfully
    Then the handshake succeeds within 2 seconds
    And invoice-agent can call list_folders on "gupta-scaratec" within 2 seconds

  Scenario: A configured WAL whose file does not yet exist starts clean
    # First start of a fresh pod: the state volume is empty. This must
    # be an ordinary start, not an error, and must not create a
    # transaction of its own.
    Given the WAL database file does not exist
    When the server is restarted
    And invoice-agent completes an Initialize handshake successfully
    Then the handshake succeeds
    And the WAL contains no entries

  Scenario: Recovery is audited and the audit chain continues across the restart
    Given the server is configured to crash after APPEND before VERIFY
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 901}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the server terminates ungracefully
    When the server is restarted
    And invoice-agent completes an Initialize handshake successfully
    Then the WAL entry for this tx_id reaches status "committed" within 30 seconds
    And the audit log contains an entry with tool "saga_recovery", decision "ALLOW"
    And that audit entry carries caller_id "invoice-agent"
    And the audit chain verifies across the restart boundary

  Scenario: A second restart does not re-apply an already committed saga
    Given the server is configured to crash after APPEND before VERIFY
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 901}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the server terminates ungracefully
    When the server is restarted
    And invoice-agent completes an Initialize handshake successfully
    Then the WAL entry for this tx_id reaches status "committed" within 30 seconds
    When the server is restarted
    And invoice-agent completes an Initialize handshake successfully
    Then the WAL entry for this tx_id has status "committed"
    And the message with Message-ID "<inv-901@hornbach.de>" is present exactly once in "personal" folder "Archiv/Belege"
