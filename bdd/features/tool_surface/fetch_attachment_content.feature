Feature: fetch_attachment returns binary content via MCP EmbeddedResource

  When a caller with FULL visibility calls fetch_attachment, the
  response must include the actual attachment bytes as a base64-
  encoded BlobResourceContents block alongside the metadata JSON.
  This enables callers to save, process, or forward attachment
  content without a second retrieval step.

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Documents"
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"
    And policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | FULL    |
    And the folder "INBOX/Documents" holds a message with:
      | uid | from                 | subject         |
      | 601 | sender@test.example  | Quarterly report |
    And the message has attachment "report.pdf" of type "application/pdf" with size 4096 bytes

  Scenario: fetch_attachment returns blob content alongside metadata
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    Then the response decision is ALLOW
    And the response field mime_type equals "application/pdf"
    And the response field size_bytes equals 4096
    And the response contains a blob resource with mime type "application/pdf"
    And the blob content decodes to the same sha256 as the response field content_hash

  Scenario: fetch_attachment without part_id lists attachments for discovery
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601
    Then the response decision is ALLOW
    And the response field attachments has 1 entries
    And attachment 0 has field "index" equal to 0
    And attachment 0 has field "filename" equal to "report.pdf"
