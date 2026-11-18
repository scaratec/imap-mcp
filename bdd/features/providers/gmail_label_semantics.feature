Feature: Gmail label semantics, opaque object_id deduplication, and label-filtered disclosure

  Accounts declared with provider: google model Gmail's multi-label
  object semantics (ADR 0040, ADR 0044, ADR 0045):
    - describe_policy reports semantics="gmail-labels".
    - search and list_messages emit opaque per-caller object_id
      ("gm_" + 24 hex chars) for cross-folder correlation without
      exposing canonical_all_mail_uid.
    - Intra-account move adds the target label (mUTF-7 for user labels,
      atom for system labels) BEFORE removing the source label.
    - Upstream STORE rejections return ALLOW + ERROR with
      error.type="transient_imap_failure" and sanitized status detail.
    - list_labels and fetch_envelope.labels include only labels granted
      by the caller's policy, sorted lexicographically (with zero hidden counts).
    - Cross-account sagas fetch RFC822 bytes deterministically from
      [Gmail]/All Mail.

  Covered error layers (per BDD Guidelines §4.5):
    - Semantics flag in describe_policy (gmail-labels vs imap-standard) : 1
    - Multi-label search correlates via object_id, omits canonical UID  : 1
    - Intra-account move as label swap preserves unrelated third label  : 1
    - Label swap adds target label BEFORE removing source label         : 1
    - Upstream STORE NO surfaces transient_imap_failure, keeps source   : 1
    - Move encodes Modified UTF-7 for non-ASCII user labels             : 1
    - list_labels & fetch_envelope.labels filter out ungranted labels (sorted) : 1
    - list_labels denied on non-Google provider                         : 1
    - Cross-account move fetches RFC822 from [Gmail]/All Mail           : 1
    - Unlisted [Gmail]/Trash omitted from list_folders without count    : 1
    Total enumerated                                                    : 10  covered by this feature: 10

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path       |
      | INBOX             |
      | Rechnungen        |
      | Hornbach          |
      | Geheimprojekt     |
      | Rechnungseingänge |
      | [Gmail]/All Mail  |
      | [Gmail]/Drafts    |
      | [Gmail]/Trash     |
    And the IMAP account "archive-srv" exists with provider "imap-standard" and folders:
      | folder path |
      | INBOX       |
      | Archiv      |
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "scaratec-gmail"
    And policy "invoice-policy" grants account "archive-srv"
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder            | mode      | default | move_out | accept_incoming | rules                               |
      | scaratec-gmail | INBOX             | whitelist | NONE    | true     | false           | [{from_domain=hornbach.de -> FULL}] |
      | scaratec-gmail | Rechnungen        | whitelist | NONE    | true     | true            | [{from_domain=hornbach.de -> FULL}] |
      | scaratec-gmail | Hornbach          | whitelist | NONE    | false    | true            | [{from_domain=hornbach.de -> FULL}] |
      | scaratec-gmail | Rechnungseingänge | whitelist | NONE    | true     | true            | [{from_domain=hornbach.de -> FULL}] |
      | archive-srv    | Archiv            | whitelist | NONE    | false    | true            | []                                  |

  Scenario: describe_policy flags a Google account with semantics gmail-labels
    When invoice-agent calls describe_policy
    Then the accounts entry for "scaratec-gmail" contains field "semantics" equal to "gmail-labels"
    And the accounts entry for "archive-srv" contains field "semantics" equal to "imap-standard"

  Scenario: Multi-label message shares one opaque object_id across folders and omits canonical_all_mail_uid
    Given a Gmail message on "scaratec-gmail" has:
      | x_gm_msgid | message_id              | from_address         | to_address          | subject    | date                 |
      | 9001       | <m-9001@mail.gmail.com> | rechnung@hornbach.de | me@scaratec-g.local | Rechnung X | 2026-05-06T09:00:00Z |
    And the Gmail message 9001 carries labels ["INBOX", "Rechnungen", "Hornbach"] with UID 501 in "INBOX", UID 602 in "Rechnungen", UID 703 in "Hornbach", and UID 10001 in "[Gmail]/All Mail"
    When invoice-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    Then the response contains 1 messages
    And message 0 has field "uid" equal to 501
    And message 0 has field "object_id" matching "^gm_[0-9a-f]{24}$"
    And the value of message 0 field "object_id" is remembered as "inbox_obj_id"
    And the JSON response does NOT contain the literal strings:
      | forbidden_string       |
      | canonical_all_mail_uid |
      | 10001                  |
    When invoice-agent calls list_messages with account "scaratec-gmail", folder "Rechnungen", criteria {}, scope "all"
    Then the response contains 1 messages
    And message 0 has field "uid" equal to 602
    And message 0 has field "object_id" equal to the remembered "inbox_obj_id"

  Scenario: Intra-account move on Google swaps labels and leaves a third label untouched
    Given a Gmail message on "scaratec-gmail" has:
      | x_gm_msgid | message_id               | from_address         | to_address          | subject    | date                 |
      | 10002      | <m-10002@mail.gmail.com> | rechnung@hornbach.de | me@scaratec-g.local | Rechnung 2 | 2026-05-06T09:00:00Z |
    And the Gmail message 10002 carries labels ["INBOX", "Rechnungen"] with UID 505 in "INBOX", UID 605 in "Rechnungen", and UID 10002 in "[Gmail]/All Mail"
    When invoice-agent calls move with source {"account": "scaratec-gmail", "folder": "INBOX", "uid": 505}, target {"account": "scaratec-gmail", "folder": "Hornbach"}
    Then the response decision is ALLOW
    And the response field mechanism equals "gmail_label_swap"
    And a direct IMAP SEARCH on "scaratec-gmail:INBOX" for X-GM-MSGID 10002 returns zero results
    And a direct IMAP SEARCH on "scaratec-gmail:Hornbach" for X-GM-MSGID 10002 returns exactly one result
    And a direct IMAP SEARCH on "scaratec-gmail:Rechnungen" for X-GM-MSGID 10002 returns exactly one result
    And a direct IMAP SEARCH on "scaratec-gmail:[Gmail]/All Mail" for X-GM-MSGID 10002 returns exactly one result

  Scenario: Move on Google adds the target label BEFORE removing the source label
    Given a Gmail message on "scaratec-gmail" has:
      | x_gm_msgid | message_id               | from_address         | to_address          | subject    | date                 |
      | 10005      | <m-10005@mail.gmail.com> | rechnung@hornbach.de | me@scaratec-g.local | Rechnung 5 | 2026-05-06T09:00:00Z |
    And the Gmail message 10005 carries labels ["INBOX"] with UID 505 in "INBOX" and UID 10005 in "[Gmail]/All Mail"
    And the mock Gmail server records all X-GM-LABELS STORE operations
    When invoice-agent calls move with source {"account": "scaratec-gmail", "folder": "INBOX", "uid": 505}, target {"account": "scaratec-gmail", "folder": "Hornbach"}
    Then the response decision is ALLOW
    And the response field mechanism equals "gmail_label_swap"
    And the recorded X-GM-LABELS STORE operations on UID 505 are in order:
      | op | label    |
      | +  | Hornbach |
      | -  | \Inbox   |
    And a direct IMAP SEARCH on "scaratec-gmail:INBOX" for X-GM-MSGID 10005 returns zero results
    And a direct IMAP SEARCH on "scaratec-gmail:Hornbach" for X-GM-MSGID 10005 returns exactly one result

  Scenario: Move on Google surfaces upstream STORE NO as transient_imap_failure in the normalized envelope
    Given a Gmail message on "scaratec-gmail" has:
      | x_gm_msgid | message_id               | from_address         | to_address          | subject    | date                 |
      | 10006      | <m-10006@mail.gmail.com> | rechnung@hornbach.de | me@scaratec-g.local | Rechnung 6 | 2026-05-06T09:00:00Z |
    And the Gmail message 10006 carries labels ["INBOX"] with UID 505 in "INBOX" and UID 10006 in "[Gmail]/All Mail"
    And the mock Gmail server is configured to reject the next X-GM-LABELS STORE with status "NO" and text "[OVERQUOTA] Mailbox over quota"
    When invoice-agent calls move with source {"account": "scaratec-gmail", "folder": "INBOX", "uid": 505}, target {"account": "scaratec-gmail", "folder": "Hornbach"}
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "transient_imap_failure"
    And the response field error.detail contains "OVERQUOTA"
    And the response does not contain any field named "error_type"
    And the response does not contain any field named "imap_response"
    And a direct IMAP SEARCH on "scaratec-gmail:INBOX" for X-GM-MSGID 10006 returns exactly one result

  Scenario: Move on Google encodes Modified UTF-7 for non-ASCII user labels
    Given a Gmail message on "scaratec-gmail" has:
      | x_gm_msgid | message_id               | from_address         | to_address          | subject    | date                 |
      | 10007      | <m-10007@mail.gmail.com> | rechnung@hornbach.de | me@scaratec-g.local | Rechnung 7 | 2026-05-06T09:00:00Z |
    And the Gmail message 10007 carries labels ["INBOX"] with UID 505 in "INBOX" and UID 10007 in "[Gmail]/All Mail"
    And the mock Gmail server records all X-GM-LABELS STORE operations
    When invoice-agent calls move with source {"account": "scaratec-gmail", "folder": "INBOX", "uid": 505}, target {"account": "scaratec-gmail", "folder": "Rechnungseingänge"}
    Then the response decision is ALLOW
    And the response field mechanism equals "gmail_label_swap"
    And the recorded X-GM-LABELS STORE operations on UID 505 are in order:
      | op | label                 |
      | +  | Rechnungseing&AOQ-nge |
      | -  | \Inbox                |
    And a direct IMAP SEARCH on "scaratec-gmail:Rechnungseingänge" for X-GM-MSGID 10007 returns exactly one result
    And a direct IMAP SEARCH on "scaratec-gmail:INBOX" for X-GM-MSGID 10007 returns zero results

  Scenario: list_labels and fetch_envelope.labels disclose only policy-granted labels
    Given a Gmail message on "scaratec-gmail" has:
      | x_gm_msgid | message_id               | from_address         | to_address          | subject    | date                 |
      | 10008      | <m-10008@mail.gmail.com> | rechnung@hornbach.de | me@scaratec-g.local | Rechnung 8 | 2026-05-06T09:00:00Z |
    And the Gmail message 10008 carries labels ["INBOX", "Hornbach", "Geheimprojekt"] with UID 508 in "INBOX", UID 708 in "Hornbach", UID 808 in "Geheimprojekt", and UID 10008 in "[Gmail]/All Mail"
    When invoice-agent calls list_labels with account "scaratec-gmail"
    Then the response decision is ALLOW
    And the labels response includes "INBOX", "Rechnungen", and "Hornbach"
    And the JSON response does NOT contain the literal strings:
      | forbidden_string    |
      | Geheimprojekt       |
      | [Gmail]/Trash       |
      | hidden_labels_count |
      | message_count       |
    When invoice-agent calls fetch_envelope with account "scaratec-gmail", folder "INBOX", uid 508
    Then the response decision is ALLOW
    And the response field labels equals ["Hornbach", "INBOX"]
    And the JSON response does NOT contain the literal strings:
      | forbidden_string |
      | Geheimprojekt    |

  Scenario: list_labels on a standard IMAP account is denied with tool_not_applicable_for_provider
    When invoice-agent calls list_labels with account "archive-srv"
    Then the response decision is DENY
    And the response field reason equals "tool_not_applicable_for_provider"

  Scenario: Cross-account move from a Google account fetches RFC822 bytes from [Gmail]/All Mail
    Given a Gmail message on "scaratec-gmail" has:
      | x_gm_msgid | message_id          | from_address         | to_address          | subject    | date                 |
      | 10003      | <m-10003@gmail.com> | rechnung@hornbach.de | me@scaratec-g.local | Rechnung Y | 2026-05-06T09:00:00Z |
    And the Gmail message 10003 carries labels ["Rechnungen"] with UID 510 in "Rechnungen" and UID 10003 in "[Gmail]/All Mail"
    When invoice-agent calls move with source {"account": "scaratec-gmail", "folder": "Rechnungen", "uid": 510}, target {"account": "archive-srv", "folder": "Archiv"}
    Then the response decision is ALLOW
    And the WAL entry for the returned tx_id has status "committed"
    And the mock-gmail command log for "scaratec-gmail" shows an RFC822 UID FETCH in "[Gmail]/All Mail" for UID 10003 and no RFC822 UID FETCH in "Rechnungen"
    And a direct IMAP SEARCH on "archive-srv:Archiv" for message-id "<m-10003@gmail.com>" returns exactly one result
    And a direct IMAP SEARCH on "scaratec-gmail:Rechnungen" for X-GM-MSGID 10003 returns zero results

  Scenario: Unlisted [Gmail]/Trash is omitted from list_folders without disclosing hidden_folders_count
    When invoice-agent calls list_folders with account "scaratec-gmail"
    Then the response decision is ALLOW
    And the response does not contain any field named "hidden_folders_count"
    And the JSON response does NOT contain the literal strings:
      | forbidden_string |
      | [Gmail]/Trash    |
      | Geheimprojekt    |
