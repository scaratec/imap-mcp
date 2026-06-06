Feature: list_attachments returns attachment metadata at BODY visibility

  ADR 0026 splits the old overloaded fetch_attachment into two tools:
  list_attachments returns the metadata array (no bytes) at BODY level,
  fetch_attachment returns one part's bytes at FULL level. This feature
  pins the list_attachments contract.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path: message with multiple attachments       : 1
    - Happy path: message with zero attachments           : 1
    - Authorization: BODY-granted caller succeeds         : 1
    - Authorization: ENVELOPE-only caller is denied       : 1
    - Folder authorization: folder_hidden DENY            : 1
    Total enumerated                                       : 5   covered by this feature: 5

  Background:
    Given the IMAP account "gupta-scaratec" exists with folders:
      | folder path       |
      | INBOX/Documents   |
      | Banking           |
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"

  Scenario: list_attachments returns metadata for every attachment part
    Given policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | BODY    |
    And the folder "INBOX/Documents" holds a message with:
      | uid | from                | subject      |
      | 901 | sender@test.example | Two invoices |
    And the message has attachment "invoice.pdf" of type "application/pdf" with size 4096 bytes
    And the message has attachment "receipt.xlsx" of type "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" with size 2048 bytes
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
    And attachment 1 has field "size_bytes" equal to 2048

  Scenario: list_attachments on a message without attachments returns an empty array
    Given policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | BODY    |
    And the folder "INBOX/Documents" holds a message with:
      | uid | from                | subject       |
      | 902 | sender@test.example | Plain text    |
    When doc-agent calls list_attachments with account "gupta-scaratec", folder "INBOX/Documents", uid 902
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field attachments has 0 entries

  Scenario: list_attachments at BODY grant succeeds even without FULL
    Given policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | BODY    |
    And the folder "INBOX/Documents" holds a message with:
      | uid | from                | subject  |
      | 903 | sender@test.example | One PDF  |
    And the message has attachment "doc.pdf" of type "application/pdf" with size 512 bytes
    When doc-agent calls list_attachments with account "gupta-scaratec", folder "INBOX/Documents", uid 903
    Then the response decision is ALLOW
    And the response field attachments has 1 entries

  Scenario: list_attachments at ENVELOPE grant is denied with visibility_below_BODY
    Given policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX/Documents" holds a message with:
      | uid | from                | subject |
      | 904 | sender@test.example | Hidden  |
    When doc-agent calls list_attachments with account "gupta-scaratec", folder "INBOX/Documents", uid 904
    Then the response decision is DENY
    And the response field reason equals "visibility_below_BODY"

  Scenario: list_attachments on an unlisted folder is denied with folder_hidden
    When doc-agent calls list_attachments with account "gupta-scaratec", folder "Banking", uid 1
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"
