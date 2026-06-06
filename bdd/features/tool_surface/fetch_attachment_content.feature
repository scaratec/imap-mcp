Feature: fetch_attachment returns binary content for a single attachment part

  After ADR 0026, fetch_attachment takes part_id as a required argument
  and returns the bytes of exactly that part as a base64-encoded
  BlobResourceContents block alongside metadata. The old "no part_id =
  list" mode is gone; that responsibility belongs to list_attachments.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path: bytes match content_hash               : 1
    - Authorization: FULL-granted caller succeeds        : 1
    - Authorization: BODY-only caller is denied          : 1
    - Schema: missing part_id is rejected at JSON-RPC    : 1
    - Out-of-range part_id surfaces attachment_not_found : 1
    Total enumerated                                      : 5   covered by this feature: 5

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Documents"
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"
    And policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | FULL    |
    And the folder "INBOX/Documents" holds a message with:
      | uid | from                 | subject          |
      | 601 | sender@test.example  | Quarterly report |
    And the message has attachment "report.pdf" of type "application/pdf" with size 4096 bytes

  Scenario: fetch_attachment returns blob content matching content_hash
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mime_type equals "application/pdf"
    And the response field size_bytes equals 4096
    And the response contains a blob resource with mime type "application/pdf"
    And the blob content decodes to the same sha256 as the response field content_hash

  Scenario: fetch_attachment at BODY grant is denied with visibility_below_FULL
    Given policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | BODY    |
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    Then the response decision is DENY
    And the response field reason equals "visibility_below_FULL"

  Scenario: fetch_attachment without part_id is rejected at JSON-RPC -32602
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601
    Then the server responds with JSON-RPC error code -32602
    And the response error message contains "part_id"

  Scenario: fetch_attachment with out-of-range part_id returns attachment_not_found
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 99
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "attachment_not_found"
