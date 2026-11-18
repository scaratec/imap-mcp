@test_hooks
Feature: Cross-account move and copy via content-verified WAL saga and caller-bound status

  Cross-account move and copy execute a SQLite WAL saga:
  BEGIN -> FETCH -> APPEND -> VERIFY (full canonical_sha256) -> DELETE
  (move only) -> COMMIT, scrubbing staged_rfc822 to NULL upon staging/commit.
  get_transaction_status is strictly read-only and bound to the owning
  caller_id (ADR 0045, ADR 0046).

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path cross-account move scrubs staged_rfc822 on commit     : 1
    - Target APPEND rejected -> append_rejected, source intact         : 1
    - Target APPEND delayed past append_timeout -> deduplicated commit : 1
    - Source EXPUNGE rejected once -> recovery deletes source cleanly  : 1
    - Target server unreachable -> transient_imap_failure, pending     : 1
    - Retry limit exhausted -> needs_operator                          : 1
    - Cross-account copy retains source and commits target             : 1
    - get_transaction_status is read-only and returns not_found to foreign caller : 2
    Total enumerated                                                   : 9   covered by this feature: 9

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
      | account        | folder           | mode      | default | move_out | copy_out | accept_incoming | rules                                   |
      | gupta-scaratec | INBOX/Rechnungen | whitelist | NONE    | true     | true     | false           | [{from_domain=hornbach.de -> ENVELOPE}] |
      | personal       | Archiv/Belege    | whitelist | NONE    | false    | false    | true            | []                                      |
    And the WAL retry_limit is configured to 3
    And the WAL is empty

  Scenario: Cross-account move commits all saga steps and scrubs staged_rfc822 in wal.db
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject       | date                 |
      | 801 | <m-801@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7823 | 2026-05-06T09:00:00Z |
    And the message at uid 801 has plain text body "Rechnungstext 801"
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 801}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field tx_id is a non-empty string
    And the response field mechanism equals "saga"
    When invoice-agent calls get_transaction_status with the returned tx_id
    Then the status response field state equals "committed"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-801@gupta-scaratec.com>" returns zero results
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-801@gupta-scaratec.com>" returns exactly one result
    And the WAL transactions table has an entry with:
      | field         | value                      |
      | tx_id         | the returned tx_id         |
      | caller_id     | invoice-agent              |
      | status        | committed                  |
      | src_account   | gupta-scaratec             |
      | src_folder    | INBOX/Rechnungen           |
      | dst_account   | personal                   |
      | dst_folder    | Archiv/Belege              |
      | message_id    | <m-801@gupta-scaratec.com> |
      | staged_rfc822 | (null)                     |

  Scenario: Target APPEND rejected with NO surfaces append_rejected and keeps source intact
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 802 | <m-802@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 802 | 2026-05-06T09:00:00Z |
    And the IMAP proxy for "personal" rejects the next APPEND on "Archiv/Belege" once with "NO [OVERQUOTA] Mailbox full"
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 802}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "append_rejected"
    And the response does not contain any field named "error_type"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-802@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-802@gupta-scaratec.com>" returns zero results
    And the WAL transactions table has an entry with:
      | field       | value              |
      | tx_id       | the returned tx_id |
      | status      | fetched            |
      | retry_count | 1                  |

  Scenario: Delayed target APPEND that lands after append_timeout is deduplicated by canonical_sha256 on recovery without duplicating
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 803 | <m-803@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 803 | 2026-05-06T09:00:00Z |
    And the server append_timeout is configured to 1 second
    And the IMAP proxy for "personal" forwards the next APPEND on "Archiv/Belege" and delays its response by 2 seconds
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 803}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "append_timeout"
    When 3 seconds elapse for the delayed upstream APPEND to finish
    And the test recovery pass runs 1 time
    Then the WAL entry for the returned tx_id has status "committed"
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-803@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-803@gupta-scaratec.com>" returns zero results

  Scenario: Source UID EXPUNGE failure after target APPEND leaves a staged transaction that commits on recovery
    Given the IMAP proxy for "gupta-scaratec" strips capability "MOVE" while keeping "UIDPLUS"
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 804 | <m-804@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 804 | 2026-05-06T09:00:00Z |
    And the IMAP proxy for "gupta-scaratec" rejects the next UID EXPUNGE once with "NO Temporary lock"
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 804}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then the response field result equals "ERROR"
    And the WAL entry for the returned tx_id has status "staged"
    When the test recovery pass runs 1 time
    Then the WAL entry for the returned tx_id has status "committed"
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-804@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-804@gupta-scaratec.com>" returns zero results

  Scenario: Target server unreachable keeps source intact and returns transient_imap_failure
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 805 | <m-805@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 805 | 2026-05-06T09:00:00Z |
    And the IMAP proxy for "personal" refuses all incoming TCP connections
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 805}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "transient_imap_failure"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-805@gupta-scaratec.com>" returns exactly one result
    And the WAL entry for the returned tx_id has status "fetched" and retry_count 1

  Scenario: Exhausting retry_limit escalates the transaction to needs_operator with source intact
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 806 | <m-806@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 806 | 2026-05-06T09:00:00Z |
    And the IMAP proxy for "personal" rejects every APPEND on "Archiv/Belege" with "NO [OVERQUOTA] Mailbox full"
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 806}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the test recovery pass runs 3 times
    Then the WAL entry for the returned tx_id has status "needs_operator"
    And the WAL entry for the returned tx_id has retry_count 3
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-806@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-806@gupta-scaratec.com>" returns zero results
    And the audit log contains an entry with tool "saga_transition" and step "escalated"

  Scenario: Cross-account copy appends to target and retains the source message
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 807 | <m-807@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 807 | 2026-05-06T09:00:00Z |
    When invoice-agent calls copy with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 807}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mechanism equals "saga"
    And the WAL entry for the returned tx_id has status "committed"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-807@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-807@gupta-scaratec.com>" returns exactly one result

  Scenario: get_transaction_status is strictly read-only and never triggers IMAP recovery commands
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 808 | <m-808@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 808 | 2026-05-06T09:00:00Z |
    And the IMAP proxy for "personal" rejects the next APPEND on "Archiv/Belege" once with "NO Temporary error"
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 808}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the IMAP command log for "personal" is cleared
    And invoice-agent calls get_transaction_status with the returned tx_id
    Then the response decision is ALLOW
    And the status response field state equals "fetched"
    And the IMAP command log for "personal" contains 0 commands

  Scenario: get_transaction_status returns opaque not_found for a foreign caller's tx_id and an unknown tx_id
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 809 | <m-809@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 809 | 2026-05-06T09:00:00Z |
    And invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 809}, target {"account": "personal", "folder": "Archiv/Belege"}
    And a second server instance is started sharing wal.db for caller "other-agent" with an empty policy
    When other-agent calls get_transaction_status with the tx_id created by invoice-agent
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    When invoice-agent calls get_transaction_status with tx_id "00000000-0000-4000-8000-000000000000"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
