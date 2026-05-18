Feature: Attachment discovery without part_id

  When fetch_attachment is called without a part_id, the response
  should list all available attachments with their metadata so
  that the caller can discover part_ids before downloading.

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Documents"
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"
    And policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | FULL    |

  Scenario: fetch_attachment without part_id lists all attachments
    Given the folder "INBOX/Documents" holds a message with:
      | uid | from                | subject      |
      | 901 | sender@test.example | Two invoices |
    And the message has attachment "invoice.pdf" of type "application/pdf" with size 4096 bytes
    And the message has attachment "receipt.xlsx" of type "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" with size 2048 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 901
    Then the response decision is ALLOW
    And the response field attachments has 2 entries
    And attachment 0 has field "index" equal to 0
    And attachment 0 has field "filename" equal to "invoice.pdf"
    And attachment 0 has field "mime_type" equal to "application/pdf"
    And attachment 0 has field "size_bytes" equal to 4096
    And attachment 1 has field "index" equal to 1
    And attachment 1 has field "filename" equal to "receipt.xlsx"
    And attachment 1 has field "size_bytes" equal to 2048

  Scenario: fetch_attachment with part_id returns blob for that part
    Given the folder "INBOX/Documents" holds a message with:
      | uid | from                | subject      |
      | 911 | sender@test.example | Two invoices |
    And the message has attachment "invoice.pdf" of type "application/pdf" with size 4096 bytes
    And the message has attachment "receipt.xlsx" of type "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" with size 2048 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 911, part_id 0
    Then the response decision is ALLOW
    And the response field part_id equals 0
    And the response contains a blob resource with mime type "application/pdf"
