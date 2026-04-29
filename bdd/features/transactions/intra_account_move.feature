Feature: Intra-account move via native IMAP MOVE

  When source and target folder are in the same account, the move tool
  issues a single RFC 6851 MOVE command. The response carries no tx_id
  because no saga is involved. See ADR 0006.

  Persistence validation (per BDD Guidelines §13.2 Prüfung 1):
  every scenario verifies the outcome via a second channel — a direct
  IMAP query against the source and target folders, independent of the
  move response.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path intra-account            : 1
    - Server without MOVE extension       : 1 (COPY+STORE fallback)
    - UID does not exist on source        : 1
    - Target folder does not exist        : 1
    - Same source and target folder       : 1
    - Concurrent writes to source UIDV    : 1 (UIDVALIDITY changed)
    Total enumerated                      : 6  covered by this feature: 6

  Background:
    Given the IMAP account "gupta-scaratec" exists with folders:
      | folder path            |
      | INBOX/Rechnungen       |
      | Archiv/Rechnungen-2026 |
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants the following folder capabilities:
      | folder                 | mode      | default | move_out | accept_incoming |
      | INBOX/Rechnungen       | whitelist | NONE    | true     | false           |
      | Archiv/Rechnungen-2026 | whitelist | NONE    | false    | true            |
    And the WAL is empty

  Scenario: Intra-account move of a single message succeeds with no tx_id
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | message_id                     | subject         |
      | 701 | <m-701@gupta-scaratec.com>     | Rechnung 7823   |
    And the folder "Archiv/Rechnungen-2026" is empty
    When invoice-agent calls move with account "gupta-scaratec", source folder "INBOX/Rechnungen" uid 701, target folder "Archiv/Rechnungen-2026"
    Then the response decision is ALLOW
    And the response field tx_id equals null
    And the response field mechanism equals "native_move"
    And a direct IMAP SEARCH on "INBOX/Rechnungen" for message-id "<m-701@gupta-scaratec.com>" returns zero results
    And a direct IMAP SEARCH on "Archiv/Rechnungen-2026" for message-id "<m-701@gupta-scaratec.com>" returns exactly one result
    And the WAL contains no entries for this operation

  Scenario: On an IMAP server without the MOVE extension the server falls back to COPY+STORE+EXPUNGE
    Given the IMAP server for "gupta-scaratec" does not advertise the MOVE capability
    And the folder "INBOX/Rechnungen" holds a message with:
      | uid | message_id                 | subject       |
      | 702 | <m-702@gupta-scaratec.com> | Rechnung 7824 |
    When invoice-agent calls move with account "gupta-scaratec", source folder "INBOX/Rechnungen" uid 702, target folder "Archiv/Rechnungen-2026"
    Then the response decision is ALLOW
    And the response field mechanism equals "copy_store_expunge"
    And the IMAP command log for "gupta-scaratec" contains in order:
      | command        |
      | COPY           |
      | STORE \Deleted |
      | EXPUNGE        |
    And a direct IMAP SEARCH on "INBOX/Rechnungen" for message-id "<m-702@gupta-scaratec.com>" returns zero results
    And a direct IMAP SEARCH on "Archiv/Rechnungen-2026" for message-id "<m-702@gupta-scaratec.com>" returns exactly one result

  Scenario: Moving a non-existent UID yields an ERROR with result uid_not_found and the source folder is unchanged
    Given the folder "INBOX/Rechnungen" contains no message with uid 9999
    When invoice-agent calls move with account "gupta-scaratec", source folder "INBOX/Rechnungen" uid 9999, target folder "Archiv/Rechnungen-2026"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error_type equals "uid_not_found"
    And the folder "INBOX/Rechnungen" is unchanged

  Scenario: Moving to a non-existent target folder yields ERROR target_folder_missing
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | message_id                 |
      | 704 | <m-704@gupta-scaratec.com> |
    And policy "invoice-policy" references folder "Archiv/Rechnungen-2099" with accept_incoming=true
    And the IMAP account "gupta-scaratec" does not contain folder "Archiv/Rechnungen-2099"
    When invoice-agent calls move with account "gupta-scaratec", source folder "INBOX/Rechnungen" uid 704, target folder "Archiv/Rechnungen-2099"
    Then the response field result equals "ERROR"
    And the response field error_type equals "target_folder_missing"
    And a direct IMAP SEARCH on "INBOX/Rechnungen" for message-id "<m-704@gupta-scaratec.com>" returns exactly one result

  Scenario: Moving to the same folder is rejected with error_type same_source_and_target
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | message_id                 |
      | 705 | <m-705@gupta-scaratec.com> |
    When invoice-agent calls move with account "gupta-scaratec", source folder "INBOX/Rechnungen" uid 705, target folder "INBOX/Rechnungen"
    Then the response field result equals "ERROR"
    And the response field error_type equals "same_source_and_target"
    And the folder "INBOX/Rechnungen" still contains uid 705

  Scenario: A UIDVALIDITY change during the call is detected and reported as uid_stale
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | message_id                 |
      | 706 | <m-706@gupta-scaratec.com> |
    And the UIDVALIDITY of "INBOX/Rechnungen" changes between the caller's SEARCH and the server's MOVE
    When invoice-agent calls move with account "gupta-scaratec", source folder "INBOX/Rechnungen" uid 706, target folder "Archiv/Rechnungen-2026"
    Then the response field result equals "ERROR"
    And the response field error_type equals "uid_stale"
    And a direct IMAP SEARCH on "INBOX/Rechnungen" for message-id "<m-706@gupta-scaratec.com>" returns exactly one result
