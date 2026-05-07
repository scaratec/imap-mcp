Feature: Gmail label semantics

  Accounts declared with provider: google get explicit
  label-semantics. Messages may appear under multiple "folders" (labels
  in IMAP projection); the server exposes canonical_all_mail_uid for
  deduplication, a Gmail-only list_labels tool, and implements
  intra-account move as a label swap. Cross-account sagas fetch
  deterministically from [Gmail]/All Mail. See ADR 0019.

  Covered error layers (per BDD Guidelines §4.5):
    - Semantics flag surfacing in describe_policy   : 1
    - Duplicate message in search via multi-label   : 1
    - Intra-account move as label swap              : 1
    - list_labels only for google accounts          : 1
    - Cross-account fetch sources from All Mail     : 1
    - System folders ([Gmail]/*) policy-addressable : 1
    - Non-google account does NOT receive google-only tools: 1
    Total enumerated                                 : 7   covered by this feature: 7

  Background:
    Given the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path          |
      | INBOX                |
      | Rechnungen           |
      | Hornbach             |
      | [Gmail]/All Mail     |
      | [Gmail]/Drafts       |
      | [Gmail]/Trash        |
    And the IMAP account "archive-srv" exists with provider "imap-standard" and folder "Archiv"
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account access:
      | account        |
      | scaratec-gmail |
      | archive-srv    |
    And policy "invoice-policy" grants folder:
      | account        | folder        | mode      | default | move_out | accept_incoming | rules                                        |
      | scaratec-gmail | INBOX         | whitelist | NONE    | true     | false           | [{from_domain=hornbach.de -> FULL}]          |
      | scaratec-gmail | Rechnungen    | whitelist | NONE    | true     | true            | [{from_domain=hornbach.de -> FULL}]          |
      | scaratec-gmail | Hornbach      | whitelist | NONE    | false    | true            | [{from_domain=hornbach.de -> FULL}]          |
      | archive-srv    | Archiv        | whitelist | NONE    | false    | true            | []                                           |

  Scenario: describe_policy flags a Google account with semantics gmail-labels
    When invoice-agent calls describe_policy
    Then the accounts entry for "scaratec-gmail" contains field semantics with value "gmail-labels"
    And the accounts entry for "archive-srv" contains field semantics with value "imap-standard"

  Scenario: A message with multiple labels appears once per label in search but carries a stable canonical_all_mail_uid
    Given the Gmail account has a single message with:
      | x_gm_msgid  | message_id                     | from                  | subject        |
      | 9001        | <m-9001@mail.gmail.com>        | rechnung@hornbach.de  | Rechnung X     |
    And the message carries Gmail labels ["INBOX", "Rechnungen", "Hornbach"]
    And the message has UID 501 under "INBOX", UID 602 under "Rechnungen", UID 703 under "Hornbach", UID 10001 under "[Gmail]/All Mail"
    When invoice-agent calls search with account "scaratec-gmail", folder "INBOX", criteria {}
    Then the response uids equals [501]
    And each result entry contains a field "canonical_all_mail_uid" equal to 10001
    When invoice-agent calls search with account "scaratec-gmail", folder "Rechnungen", criteria {}
    Then the response uids equals [602]
    And each result entry contains a field "canonical_all_mail_uid" equal to 10001
    # The client can deduplicate across these two searches by canonical_all_mail_uid.

  Scenario: Intra-account move on a Google account is implemented as a label swap
    Given a Gmail message with canonical_all_mail_uid 10002 carries labels ["INBOX", "Rechnungen"]
    When invoice-agent calls move with account "scaratec-gmail", source folder "INBOX" uid 505, target folder "Hornbach"
    Then the response decision is ALLOW
    And the response field mechanism equals "gmail_label_swap"
    And a direct IMAP SEARCH on "scaratec-gmail:INBOX" for X-GM-MSGID 10002 returns zero results
    And a direct IMAP SEARCH on "scaratec-gmail:Hornbach" for X-GM-MSGID 10002 returns exactly one result
    And a direct IMAP SEARCH on "scaratec-gmail:[Gmail]/All Mail" for X-GM-MSGID 10002 returns exactly one result
    And the same message still appears under "scaratec-gmail:Rechnungen" (the second original label was not touched)

  Scenario: list_labels is available only for Google accounts
    When invoice-agent calls list_labels with account "scaratec-gmail"
    Then the labels response includes at least:
      | label      |
      | INBOX      |
      | Rechnungen |
      | Hornbach   |
    When invoice-agent calls list_labels with account "archive-srv"
    Then the response decision is DENY
    And the response field reason equals "tool_not_applicable_for_provider"

  Scenario: Cross-account move from a Google account fetches deterministically from [Gmail]/All Mail
    Given a Gmail message with:
      | x_gm_msgid | message_id             | from                  | subject      |
      | 10003      | <m-10003@gmail.com>    | rechnung@hornbach.de  | Rechnung Y   |
    And the message has UID 510 under "Rechnungen", UID 10003 under "[Gmail]/All Mail"
    When invoice-agent calls move with source {"account":"scaratec-gmail","folder":"Rechnungen","uid":510}, target {"account":"archive-srv","folder":"Archiv"}
    Then the saga's FETCH step retrieves RFC822 bytes from "scaratec-gmail:[Gmail]/All Mail" uid 10003, not from "scaratec-gmail:Rechnungen" uid 510
    And the transaction reaches state committed within 60 seconds
    And a direct IMAP SEARCH on "archive-srv:Archiv" for message-id "<m-10003@gmail.com>" returns exactly one result
    And a direct IMAP SEARCH on "scaratec-gmail:Rechnungen" for X-GM-MSGID 10003 returns zero results

  Scenario: [Gmail]/Trash is an ordinary policy-addressable folder — default-deny still applies
    Given policy "invoice-policy" does NOT include folder "[Gmail]/Trash"
    And a Gmail message exists with canonical_all_mail_uid 10004 carrying labels ["[Gmail]/Trash"]
    When invoice-agent calls list_folders with account "scaratec-gmail"
    Then the response folders does NOT include "[Gmail]/Trash"
    And the response hidden_folders_count is at least 1
