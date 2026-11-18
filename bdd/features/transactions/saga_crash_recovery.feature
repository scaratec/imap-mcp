@test_hooks
Feature: Saga crash recovery and full canonical content-hash verification

  After a hard crash (SIGKILL), recovery resumes non-terminal WAL
  transactions and verifies full canonical_sha256 across the entire
  destination message before ever deleting the source message (ADR 0046).
  A spoofed Message-ID or a message matching only in its first 4 KiB
  never causes silent source deletion.

  Covered error layers (per BDD Guidelines §4.5):
    - Crash after BEGIN before FETCH -> aborted, source intact         : 1
    - Crash after FETCH before APPEND -> appends from WAL & commits    : 1
    - Crash after APPEND before staged -> verifies canonical_sha256    : 1
    - Crash after DELETE before COMMIT -> observes source gone, commits: 1
    - Pre-existing identical Message-ID + canonical_sha256 -> no dup   : 1
    - Message-ID collision with byte difference past 4 KiB -> safe     : 1
    - Fallback without Message-ID: exact size + canonical_sha256 match : 1
    - Fallback without Message-ID: two ambiguous candidates -> needs_operator : 1
    Total enumerated                                                   : 8   covered here: 8

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
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the IMAP traffic for "personal" is captured through a proxy
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants account "personal"
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder           | mode      | default | move_out | accept_incoming | rules                                   |
      | gupta-scaratec | INBOX/Rechnungen | whitelist | NONE    | true     | false           | [{from_domain=hornbach.de -> ENVELOPE}] |
      | personal       | Archiv/Belege    | whitelist | NONE    | false    | true            | []                                      |
    And the WAL is empty

  Scenario: Crash after BEGIN before FETCH aborts cleanly on restart with source intact
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 901 | <m-901@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 901 | 2026-05-06T09:00:00Z |
    And the server is armed with IMAP_MCP_CRASH_AT="post_begin"
    When invoice-agent invokes move expecting crash for uid 901 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And the server is restarted without crash hooks
    Then within 10 seconds the WAL entry for the crashed move has status "aborted"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-901@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-901@gupta-scaratec.com>" returns zero results

  Scenario: Crash after FETCH before APPEND replays APPEND from staged_rfc822 and scrubs WAL bytes on commit
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 902 | <m-902@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 902 | 2026-05-06T09:00:00Z |
    And the message at uid 902 has plain text body "Wiederhergestellter Volltext 902"
    And the server is armed with IMAP_MCP_CRASH_AT="post_fetch"
    When invoice-agent invokes move expecting crash for uid 902 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And the server is restarted without crash hooks
    Then within 10 seconds the WAL entry for the crashed move has status "committed"
    And the WAL entry for the crashed move has staged_rfc822 equal to null
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-902@gupta-scaratec.com>" returns zero results
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-902@gupta-scaratec.com>" returns exactly one result

  Scenario: Crash after APPEND before WAL staged verifies canonical_sha256 on restart and deletes source without re-appending
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 903 | <m-903@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 903 | 2026-05-06T09:00:00Z |
    And the server is armed with IMAP_MCP_CRASH_AT="post_append"
    When invoice-agent invokes move expecting crash for uid 903 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And the IMAP command log for "personal" is cleared
    And the server is restarted without crash hooks
    Then within 10 seconds the WAL entry for the crashed move has status "committed"
    And the IMAP command log for "personal" contains no APPEND command
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-903@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-903@gupta-scaratec.com>" returns zero results

  Scenario: Crash after source DELETE before WAL commit observes source absence on restart and commits
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 904 | <m-904@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 904 | 2026-05-06T09:00:00Z |
    And the server is armed with IMAP_MCP_CRASH_AT="post_delete"
    When invoice-agent invokes move expecting crash for uid 904 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And the server is restarted without crash hooks
    Then within 10 seconds the WAL entry for the crashed move has status "committed"
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-904@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-904@gupta-scaratec.com>" returns zero results

  Scenario: Target already holding an identical canonical message skips duplicate APPEND and commits
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 905 | <m-905@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 905 | 2026-05-06T09:00:00Z |
    And the message at uid 905 has plain text body "Exakt identischer Beleginhalt 905"
    And an identical copy of uid 905 from "gupta-scaratec:INBOX/Rechnungen" already exists in "personal:Archiv/Belege"
    And the server is armed with IMAP_MCP_CRASH_AT="post_fetch"
    When invoice-agent invokes move expecting crash for uid 905 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And the IMAP command log for "personal" is cleared
    And the server is restarted without crash hooks
    Then within 10 seconds the WAL entry for the crashed move has status "committed"
    And the IMAP command log for "personal" contains no APPEND command
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-905@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-905@gupta-scaratec.com>" returns zero results

  Scenario: Pre-existing target message with matching Message-ID and matching 4 KiB prefix but altered tail appends authentic copy and keeps both intact
    Given the folder "INBOX/Rechnungen" holds a message at uid 908 with message-id "<m-908@hornbach.de>", from "rechnung@hornbach.de", to "me@gupta-scaratec.com", subject "Rechnung 908", date "2026-05-06T09:00:00Z", and body prefix "A" repeated 5000 times followed by "AUTHENTIC-TAIL-119EUR"
    And the folder "personal:Archiv/Belege" holds a message with message-id "<m-908@hornbach.de>", from "rechnung@hornbach.de", to "me@gupta-scaratec.com", subject "Rechnung 908", date "2026-05-06T09:00:00Z", and body prefix "A" repeated 5000 times followed by "FORGED-TAIL-000000EUR"
    And the server is armed with IMAP_MCP_CRASH_AT="post_fetch"
    When invoice-agent invokes move expecting crash for uid 908 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And the server is restarted without crash hooks
    Then within 10 seconds the WAL entry for the crashed move has status "committed"
    And the folder "personal:Archiv/Belege" contains 2 messages with message-id "<m-908@hornbach.de>"
    And 1 message in "personal:Archiv/Belege" has body ending with "AUTHENTIC-TAIL-119EUR" and 1 has body ending with "FORGED-TAIL-000000EUR"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-908@hornbach.de>" returns zero results

  Scenario: Message without Message-ID recovers uniquely by exact RFC822 size and full canonical_sha256
    Given the folder "INBOX/Rechnungen" holds a message without Message-ID:
      | uid | from_address         | to_address            | subject       | date                 | exact_size_bytes |
      | 906 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7823 | 2026-04-01T10:00:00Z | 4096             |
    And the server is armed with IMAP_MCP_CRASH_AT="post_append"
    When invoice-agent invokes move expecting crash for uid 906 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And the server is restarted without crash hooks
    Then within 10 seconds the WAL entry for the crashed move has status "committed"
    And the folder "personal:Archiv/Belege" contains 1 message from "rechnung@hornbach.de" with subject "Rechnung 7823" and exact RFC822 size 4096 bytes
    And the folder "gupta-scaratec:INBOX/Rechnungen" contains 0 messages with subject "Rechnung 7823"

  Scenario: Ambiguous target candidates without Message-ID escalate to needs_operator without deleting source
    Given the folder "INBOX/Rechnungen" holds a message without Message-ID:
      | uid | from_address         | to_address            | subject       | date                 | exact_size_bytes |
      | 907 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung Amb  | 2026-04-01T10:00:00Z | 4096             |
    And two identical copies of uid 907 from "gupta-scaratec:INBOX/Rechnungen" already exist in "personal:Archiv/Belege"
    And the server is armed with IMAP_MCP_CRASH_AT="post_fetch"
    When invoice-agent invokes move expecting crash for uid 907 from "gupta-scaratec:INBOX/Rechnungen" to "personal:Archiv/Belege"
    And the server is restarted without crash hooks
    Then within 10 seconds the WAL entry for the crashed move has status "needs_operator"
    And the audit log contains an entry with tool "saga_transition", step "escalated", reason "ambiguous_target_match"
    And the folder "gupta-scaratec:INBOX/Rechnungen" contains 1 message with subject "Rechnung Amb"
