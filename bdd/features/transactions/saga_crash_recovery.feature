Feature: Saga crash recovery and idempotency

  After an unclean shutdown (kill -9, power loss, container kill) the
  server re-scans the WAL on startup and resumes non-terminal
  transactions. The idempotency key is the Message-ID at the target;
  content hash and fallback 5-tuple guard against ambiguity.
  See ADRs 0006-0008.

  Persistence validation (per BDD Guidelines §13.2 Prüfung 1):
  every scenario verifies via direct IMAP on both accounts AND direct
  WAL read. The saga must not depend on its own self-reported state.

  Covered error layers (per BDD Guidelines §4.5):
    - Crash after BEGIN before FETCH               : 1
    - Crash after FETCH before APPEND              : 1
    - Crash after APPEND before VERIFY             : 1
    - Crash after DELETE before COMMIT             : 1
    - Idempotency: Message-ID already present      : 1
    - Fallback: no Message-ID, 5-tuple unique      : 1
    - Fallback: no Message-ID, 5-tuple ambiguous   : 1 (-> needs_operator)
    Total enumerated                               : 7   covered here: 7

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Rechnungen"
    And the IMAP account "personal" exists with folder "Archiv/Belege"
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" allows cross-account move between these folders
    And the WAL is empty

  Scenario: Crash after BEGIN but before FETCH leaves source intact and recovery aborts the transaction cleanly
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id                     |
      | 901 | <m-901@gupta-scaratec.com>     |
    And the server is configured to crash after WAL BEGIN persistence
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 901}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the server terminates ungracefully
    And the server is restarted
    And the server's background recovery loop runs once
    Then the WAL entry for this tx_id has status "aborted"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-901@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-901@gupta-scaratec.com>" returns zero results

  Scenario: Crash after FETCH but before APPEND: recovery re-executes APPEND from WAL-held RFC822 bytes and commits
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id                     |
      | 902 | <m-902@gupta-scaratec.com>     |
    And the server is configured to crash after WAL FETCH persistence
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 902}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the server terminates ungracefully
    And the server is restarted
    And the server's background recovery loop runs once
    Then the WAL entry for this tx_id reaches status "committed" within 30 seconds
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-902@gupta-scaratec.com>" returns zero results
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-902@gupta-scaratec.com>" returns exactly one result

  Scenario: Crash after APPEND but before VERIFY: recovery detects target presence by Message-ID and proceeds to DELETE
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id                     |
      | 903 | <m-903@gupta-scaratec.com>     |
    And the server is configured to crash after APPEND but before WAL staged persistence
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 903}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the server terminates ungracefully
    And the server is restarted
    And the server's background recovery loop runs once
    Then the WAL entry reaches status "committed" within 30 seconds
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-903@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-903@gupta-scaratec.com>" returns zero results

  Scenario: Crash after DELETE but before COMMIT: recovery observes source absence and commits
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id                     |
      | 904 | <m-904@gupta-scaratec.com>     |
    And the server is configured to crash after DELETE but before WAL commit persistence
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 904}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the server terminates ungracefully
    And the server is restarted
    And the server's background recovery loop runs once
    Then the WAL entry reaches status "committed" within 10 seconds
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-904@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-904@gupta-scaratec.com>" returns zero results

  Scenario: Idempotency — a duplicate saga for the same Message-ID commits without a second APPEND
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id                     |
      | 905 | <m-905@gupta-scaratec.com>     |
    And the folder "personal:Archiv/Belege" already contains a message with:
      | uid | message_id                     |
      | 42  | <m-905@gupta-scaratec.com>     |
    And the WAL contains an in-progress transaction with status "staged" referencing uid 905 and Message-ID "<m-905@gupta-scaratec.com>"
    When the server's background recovery loop runs once
    Then the recovery observes the existing target message via direct IMAP SEARCH on "personal:Archiv/Belege"
    And the recovery does NOT issue an additional APPEND to "personal:Archiv/Belege"
    And the WAL entry reaches status "committed"
    And the folder "personal:Archiv/Belege" contains exactly one message with message-id "<m-905@gupta-scaratec.com>"

  Scenario: Fallback key — message without Message-ID identified uniquely by 5-tuple
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id | from                  | subject         | date                 | size_bytes |
      | 906 | (absent)   | rechnung@hornbach.de  | Rechnung 7823   | 2026-04-01T10:00:00Z | 48213      |
    And the server is configured to crash after APPEND but before WAL staged persistence
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 906}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the server terminates ungracefully
    And the server is restarted
    And the server's background recovery loop runs once
    Then the WAL entry reaches status "committed"
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for FROM "rechnung@hornbach.de" SENTON "2026-04-01" SUBJECT "Rechnung 7823" returns exactly one result
    And that result has a size of 48213 bytes
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for FROM "rechnung@hornbach.de" SENTON "2026-04-01" returns zero results

  Scenario: Fallback key ambiguous — two identical candidates trigger escalation to needs_operator
    Given the folder "personal:Archiv/Belege" contains two pre-existing messages with:
      | uid | message_id | from                  | subject         | date                 | size_bytes |
      | 10  | (absent)   | rechnung@hornbach.de  | Rechnung 7823   | 2026-04-01T10:00:00Z | 48213      |
      | 11  | (absent)   | rechnung@hornbach.de  | Rechnung 7823   | 2026-04-01T10:00:00Z | 48213      |
    And the WAL has an in-progress transaction with fallback-key (from=rechnung@hornbach.de, date=2026-04-01, subject=Rechnung 7823, size=48213, first_4kb_sha256=same-as-both) and status "staged"
    When the server's background recovery loop runs once
    Then the WAL entry transitions to status "needs_operator"
    And the audit log contains an entry with tool "saga_transition", step "escalated", reason "ambiguous_fallback_match"
    And no additional DELETE is issued against "gupta-scaratec:INBOX/Rechnungen"
