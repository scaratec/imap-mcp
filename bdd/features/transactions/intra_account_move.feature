Feature: Intra-account move via native MOVE or safe UID-scoped UIDPLUS fallback

  When source and target folder belong to the same standard IMAP account,
  move requires source move_out=true, target accept_incoming=true,
  source message visibility >= ENVELOPE, and target visibility <= source
  visibility (ADR 0045). On the wire (ADR 0046 §3):
    1. Servers advertising MOVE execute RFC 6851 UID MOVE.
    2. Servers lacking MOVE but advertising UIDPLUS execute UID COPY +
       UID STORE +FLAGS.SILENT (\Deleted) + UID EXPUNGE <uid> (never an
       unqualified EXPUNGE that would destroy unrelated \Deleted mail).
    3. Servers advertising neither MOVE nor UIDPLUS return ALLOW + ERROR
       with error.type="unsupported_imap_capability".

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path native UID MOVE (no WAL entry, tx_id=null)         : 1
    - Safe UID COPY + UID STORE + UID EXPUNGE fallback spares peers : 1
    - Neither MOVE nor UIDPLUS -> unsupported_imap_capability       : 1
    - Non-existent UID -> ALLOW + ERROR not_found                   : 1
    - Authorized missing target folder -> ALLOW + ERROR folder_absent : 1
    - Same source and target folder -> JSON-RPC -32602              : 1
    - Stale uidvalidity (explicit or mid-session) -> uidvalidity_changed : 2
    Total enumerated                                                : 8   covered by this feature: 8

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path            |
      | INBOX                  |
      | INBOX/Rechnungen       |
      | Archiv/Rechnungen-2026 |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants the following folder policies:
      | folder                 | mode      | default | move_out | accept_incoming | rules                                   |
      | INBOX/Rechnungen       | whitelist | NONE    | true     | false           | [{from_domain=hornbach.de -> ENVELOPE}] |
      | Archiv/Rechnungen-2026 | whitelist | NONE    | false    | true            | []                                      |
    And the WAL is empty

  Scenario: Intra-account move executes native UID MOVE with tx_id=null and zero WAL rows
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject       | date                 |
      | 701 | <m-701@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7823 | 2026-05-06T09:00:00Z |
    And the IMAP folder "Archiv/Rechnungen-2026" is empty
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 701}, target {"account": "gupta-scaratec", "folder": "Archiv/Rechnungen-2026"}
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field tx_id equals null
    And the response field mechanism equals "native_move"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-701@gupta-scaratec.com>" returns zero results
    And a direct IMAP SEARCH on "gupta-scaratec:Archiv/Rechnungen-2026" for message-id "<m-701@gupta-scaratec.com>" returns exactly one result
    And the WAL contains 0 transaction entries

  Scenario: Server without MOVE uses UID EXPUNGE and leaves unrelated \Deleted messages untouched
    Given the IMAP proxy for "gupta-scaratec" strips capability "MOVE" while keeping "UIDPLUS"
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject           | flags      | date                 |
      | 702 | <m-702@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7824     | []         | 2026-05-06T09:00:00Z |
      | 703 | <m-703@gupta-scaratec.com> | other@example.org    | me@gupta-scaratec.com | Fremde Loeschvorm | [\Deleted] | 2026-05-06T09:05:00Z |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 702}, target {"account": "gupta-scaratec", "folder": "Archiv/Rechnungen-2026"}
    Then the response decision is ALLOW
    And the response field mechanism equals "copy_store_uid_expunge"
    And the IMAP command log for "gupta-scaratec" contains in order:
      | command     |
      | UID COPY    |
      | UID STORE   |
      | UID EXPUNGE |
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-702@gupta-scaratec.com>" returns zero results
    And a direct IMAP SEARCH on "gupta-scaratec:Archiv/Rechnungen-2026" for message-id "<m-702@gupta-scaratec.com>" returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-703@gupta-scaratec.com>" returns exactly one result

  Scenario: Server advertising neither MOVE nor UIDPLUS refuses intra-account move safely
    Given the IMAP proxy for "gupta-scaratec" strips capabilities "MOVE" and "UIDPLUS"
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject       | date                 |
      | 709 | <m-709@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7829 | 2026-05-06T09:00:00Z |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 709}, target {"account": "gupta-scaratec", "folder": "Archiv/Rechnungen-2026"}
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "unsupported_imap_capability"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-709@gupta-scaratec.com>" returns exactly one result
    And the IMAP folder "Archiv/Rechnungen-2026" is empty

  Scenario: Moving a non-existent UID returns ALLOW + ERROR not_found and leaves existing mail untouched
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 700 | <m-700@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Bleibt da    | 2026-05-06T09:00:00Z |
    And the folder "INBOX/Rechnungen" contains no message with uid 9999
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 9999}, target {"account": "gupta-scaratec", "folder": "Archiv/Rechnungen-2026"}
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    And the response does not contain any field named "error_type"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-700@gupta-scaratec.com>" returns exactly one result
    And the IMAP folder "Archiv/Rechnungen-2026" is empty

  Scenario: Moving into an authorized folder that does not exist on IMAP returns folder_absent
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject       | date                 |
      | 704 | <m-704@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 704  | 2026-05-06T09:00:00Z |
    And policy "invoice-policy" grants folder:
      | folder                 | mode      | default | accept_incoming | rules |
      | Archiv/Rechnungen-2099 | whitelist | NONE    | true            | []    |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 704}, target {"account": "gupta-scaratec", "folder": "Archiv/Rechnungen-2099"}
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "folder_absent"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-704@gupta-scaratec.com>" returns exactly one result

  Scenario: Moving to the identical account and folder is rejected with JSON-RPC -32602
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | move_out | accept_incoming | rules                                   |
      | INBOX/Rechnungen | whitelist | NONE    | true     | true            | [{from_domain=hornbach.de -> ENVELOPE}] |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 705 | <m-705@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 705 | 2026-05-06T09:00:00Z |
    When invoice-agent calls move with the arguments {"source": {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 705}, "target": {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen"}}
    Then the server responds with JSON-RPC error code -32602
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-705@gupta-scaratec.com>" returns exactly one result

  Scenario: Passing an explicit stale uidvalidity aborts move with uidvalidity_changed
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 706 | <m-706@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 706 | 2026-05-06T09:00:00Z |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 706, "uidvalidity": 99999999}, target {"account": "gupta-scaratec", "folder": "Archiv/Rechnungen-2026"}
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "uidvalidity_changed"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-706@gupta-scaratec.com>" returns exactly one result

  Scenario: Mid-session UIDVALIDITY change between search and move aborts with uidvalidity_changed
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject      | date                 |
      | 707 | <m-707@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 707 | 2026-05-06T09:00:00Z |
    And invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    And the IMAP proxy for "gupta-scaratec" rewrites UIDVALIDITY of "INBOX/Rechnungen" to 888888 on subsequent SELECT commands
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 707}, target {"account": "gupta-scaratec", "folder": "Archiv/Rechnungen-2026"}
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "uidvalidity_changed"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-707@gupta-scaratec.com>" returns exactly one result
