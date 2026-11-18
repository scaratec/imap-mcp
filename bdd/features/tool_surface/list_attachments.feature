Feature: list_attachments returns attachment metadata at BODY visibility

  ADR 0043, ADR 0044, and ADR 0047 specify that list_attachments returns
  the attachment metadata array (index, filename, mime_type, size_bytes,
  zero payload bytes) when effective message visibility reaches BODY,
  denies with visibility_below_BODY when METADATA <= eff_vis < BODY, and
  returns opaque not_found when the UID is missing or eff_vis < METADATA.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path: message with multiple attachments       : 1
    - Happy path: message with zero attachments           : 1
    - BODY-granted caller succeeds without FULL           : 1
    - Per-message ENVELOPE cap denied (visibility_below_BODY) : 1
    - NONE message or missing UID returns not_found       : 2
    - Unlisted folder denied with folder_hidden           : 1
    Total enumerated                                      : 7   covered by this feature: 7

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path     |
      | INBOX           |
      | INBOX/Documents |
      | Banking         |
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"

  Scenario: list_attachments returns index, filename, mime_type, and size_bytes for every attachment part
    Given policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | BODY    |
    And the folder "INBOX/Documents" holds messages:
      | uid | from_address        | to_address            | subject      | date                 |
      | 901 | sender@test.example | me@gupta-scaratec.com | Two invoices | 2026-05-06T09:00:00Z |
    And the message at uid 901 has attachment "invoice.pdf" of type "application/pdf" with content byte "A" repeated 4096 times
    And the message at uid 901 has attachment "receipt.xlsx" of type "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" with content byte "B" repeated 2048 times
    When doc-agent calls list_attachments with account "gupta-scaratec", folder "INBOX/Documents", uid 901
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field attachments has 2 entries
    And attachment 0 has field "index" equal to 0
    And attachment 0 has field "filename" equal to "invoice.pdf"
    And attachment 0 has field "mime_type" equal to "application/pdf"
    And attachment 0 has field "size_bytes" equal to 4096
    And attachment 1 has field "index" equal to 1
    And attachment 1 has field "filename" equal to "receipt.xlsx"
    And attachment 1 has field "mime_type" equal to "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    And attachment 1 has field "size_bytes" equal to 2048

  Scenario: list_attachments on a message without attachments returns an empty array
    Given policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | BODY    |
    And the folder "INBOX/Documents" holds messages:
      | uid | from_address        | to_address            | subject    | date                 |
      | 902 | sender@test.example | me@gupta-scaratec.com | Plain text | 2026-05-06T09:00:00Z |
    When doc-agent calls list_attachments with account "gupta-scaratec", folder "INBOX/Documents", uid 902
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field attachments has 0 entries

  Scenario: list_attachments at BODY grant succeeds without FULL visibility
    Given policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | BODY    |
    And the folder "INBOX/Documents" holds messages:
      | uid | from_address        | to_address            | subject | date                 |
      | 903 | sender@test.example | me@gupta-scaratec.com | One PDF | 2026-05-06T09:00:00Z |
    And the message at uid 903 has attachment "doc.pdf" of type "application/pdf" with content byte "D" repeated 512 times
    When doc-agent calls list_attachments with account "gupta-scaratec", folder "INBOX/Documents", uid 903
    Then the response decision is ALLOW
    And the response field attachments has 1 entries

  Scenario: Message capped to ENVELOPE in a BODY folder is denied with visibility_below_BODY
    Given policy "doc-policy" grants folder on account "gupta-scaratec":
      | folder          | mode      | default | rules                                   |
      | INBOX/Documents | blacklist | BODY    | [{from_domain=bank.de -> cap ENVELOPE}] |
    And the folder "INBOX/Documents" holds messages:
      | uid | from_address    | to_address            | subject | date                 |
      | 904 | noreply@bank.de | me@gupta-scaratec.com | Capped  | 2026-05-06T09:00:00Z |
    When doc-agent calls list_attachments with account "gupta-scaratec", folder "INBOX/Documents", uid 904
    Then the response decision is DENY
    And the response field reason equals "visibility_below_BODY"

  Scenario Outline: NONE-capped message and non-existent UID both return opaque not_found
    Given policy "doc-policy" grants folder on account "gupta-scaratec":
      | folder          | mode      | default | rules                               |
      | INBOX/Documents | blacklist | BODY    | [{from_domain=bank.de -> cap NONE}] |
    And the folder "INBOX/Documents" holds messages:
      | uid | from_address    | to_address            | subject | date                 |
      | 905 | noreply@bank.de | me@gupta-scaratec.com | Hidden  | 2026-05-06T09:00:00Z |
    And the folder "INBOX/Documents" contains no message with uid 9999
    When doc-agent calls list_attachments with account "gupta-scaratec", folder "INBOX/Documents", uid <target_uid>
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"

    Examples:
      | target_uid |
      | 905        |
      | 9999       |

  Scenario: list_attachments on an unlisted folder is denied with folder_hidden
    When doc-agent calls list_attachments with account "gupta-scaratec", folder "Banking", uid 1
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"
