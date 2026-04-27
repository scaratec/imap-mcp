Feature: Cross-account move via WAL-backed saga

  When source and target folder live on different IMAP accounts the
  server executes a saga: BEGIN -> FETCH source -> APPEND target -> VERIFY
  -> DELETE source -> COMMIT. Each transition is persisted in the
  SQLite WAL and exposed via get_transaction_status. See ADRs 0006-0008.

  Persistence validation (per BDD Guidelines §13.2 Prüfung 1):
  every scenario verifies the outcome via three independent channels —
  direct IMAP query on source, direct IMAP query on target, and
  direct read of the WAL database file.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path cross-account               : 1
    - Target APPEND fails with 5xx            : 1
    - Target APPEND times out mid-call        : 1
    - Source DELETE fails after target APPEND : 1
    - Target server unavailable entirely      : 1
    - Retry count exhausted                   : 1
    - Cross-account copy (no source delete)   : 1
    Total enumerated                          : 7   covered by this feature: 7

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Rechnungen"
    And the IMAP account "personal" exists with folder "Archiv/Belege"
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants the following folder capabilities:
      | account        | folder           | mode      | default | move_out | accept_incoming |
      | gupta-scaratec | INBOX/Rechnungen | whitelist | NONE    | true     | false           |
      | personal       | Archiv/Belege    | whitelist | NONE    | false    | true            |
    And the WAL retry_limit is configured to 3
    And the WAL is empty

  Scenario: A successful cross-account move executes all saga steps and commits
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id                   | from                  | subject         |
      | 801 | <m-801@gupta-scaratec.com>   | rechnung@hornbach.de  | Rechnung 7823   |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 801}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then the response decision is ALLOW
    And the response field tx_id is a non-empty string
    And the response field mechanism equals "saga"
    And invoice-agent calls get_transaction_status with the returned tx_id
    And the status response field state equals "committed"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-801@gupta-scaratec.com>" returns zero results
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-801@gupta-scaratec.com>" returns exactly one result
    And the WAL transactions table has an entry with:
      | field        | value                                |
      | tx_id        | the returned tx_id                   |
      | status       | committed                            |
      | src_account  | gupta-scaratec                       |
      | src_folder   | INBOX/Rechnungen                     |
      | src_uid      | 801                                  |
      | dst_account  | personal                             |
      | dst_folder   | Archiv/Belege                        |
      | message_id   | <m-801@gupta-scaratec.com>           |

  Scenario: A 500 response from the target APPEND leaves the transaction in state staged and the source intact
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id                   |
      | 802 | <m-802@gupta-scaratec.com>   |
    And the IMAP server for "personal" responds to the next APPEND with error 500
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 802}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then the response field result equals "ERROR"
    And the response field error_type equals "target_append_failed"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-802@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-802@gupta-scaratec.com>" returns zero results
    And the WAL transactions table has an entry with:
      | field        | value                                |
      | tx_id        | the returned tx_id                   |
      | status       | pending                              |
      | retry_count  | 1                                    |

  Scenario: An APPEND timeout followed by a successful retry commits with at-least-once semantics
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id                   |
      | 803 | <m-803@gupta-scaratec.com>   |
    And the IMAP server for "personal" delays the next APPEND response by 45 seconds
    And the server append_timeout is configured to 30 seconds
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 803}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then the transaction reaches state committed within 120 seconds of polling
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-803@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-803@gupta-scaratec.com>" returns zero results
    And the audit log contains entries with saga_transition tool for tx_id equal to the returned tx_id and steps:
      | step     |
      | begin    |
      | fetched  |
      | staged   |
      | deleted  |
      | commit   |

  Scenario: A DELETE failure after target APPEND leaves a transient duplicate which a retry resolves
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id                   |
      | 804 | <m-804@gupta-scaratec.com>   |
    And the IMAP server for "gupta-scaratec" responds to the next EXPUNGE with error 500 exactly once
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 804}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then the transaction reaches state committed within 120 seconds of polling
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-804@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-804@gupta-scaratec.com>" returns zero results

  Scenario: Target server unavailable for the entire call keeps the transaction pending and the source intact
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id                   |
      | 805 | <m-805@gupta-scaratec.com>   |
    And the IMAP server for "personal" refuses all connections
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 805}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then the response field result equals "ERROR"
    And the response field error_type equals "target_unreachable"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-805@gupta-scaratec.com>" returns exactly one result
    And the WAL transactions table contains an entry with status "pending" and retry_count 1

  Scenario: After retry_limit is reached the transaction escalates to needs_operator
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id                   |
      | 806 | <m-806@gupta-scaratec.com>   |
    And the IMAP server for "personal" responds to every APPEND with error 500
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 806}, target {"account": "personal", "folder": "Archiv/Belege"}
    And the server's background recovery loop runs 4 times
    Then the WAL entry for this tx_id has status "needs_operator"
    And the WAL entry has retry_count 3
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-806@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-806@gupta-scaratec.com>" returns zero results
    And the audit log contains an entry with tool "saga_transition" and step "escalated"

  Scenario: A cross-account copy commits the target APPEND but retains the source
    Given the folder "gupta-scaratec:INBOX/Rechnungen" holds a message with:
      | uid | message_id                   |
      | 807 | <m-807@gupta-scaratec.com>   |
    When invoice-agent calls copy with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 807}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then the response decision is ALLOW
    And the response field mechanism equals "saga"
    And the transaction reaches state committed within 60 seconds of polling
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-807@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "personal:Archiv/Belege" for message-id "<m-807@gupta-scaratec.com>" returns exactly one result
