Feature: Inline attachment detection for Apple Mail MIME structures

  Apple Mail wraps PDF attachments inside multipart/alternative
  with Content-Disposition: inline instead of attachment.  The
  server must detect these as attachments and make them
  downloadable via fetch_attachment.

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Reports"
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"
    And policy "doc-policy" folder defaults for "INBOX/Reports" are:
      | mode      | default |
      | blacklist | FULL    |

  Scenario: fetch_attachment finds inline PDF
    Given the server attachment sink directory is a fresh writable directory
    And the folder "INBOX/Reports" holds a message with:
      | uid | from                | subject        |
      | 801 | sender@test.example | Monthly report |
    And the message has inline attachment "report.pdf" of type "application/pdf" with size 4096 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Reports", uid 801, part_id 0
    Then the response decision is ALLOW
    And the response field mime_type equals "application/pdf"
    And the response field saved_to matches the regex "^report_[0-9a-f]{8}\.pdf$"
    And the file named saved_to exists in the sink directory

  Scenario: inline PDF is discoverable via list_attachments
    Given the folder "INBOX/Reports" holds a message with:
      | uid | from                | subject        |
      | 811 | sender@test.example | Monthly report |
    And the message has inline attachment "report.pdf" of type "application/pdf" with size 4096 bytes
    When doc-agent calls list_attachments with account "gupta-scaratec", folder "INBOX/Reports", uid 811
    Then the response decision is ALLOW
    And the response field attachments has 1 entries
    And attachment 0 has field "index" equal to 0
    And attachment 0 has field "filename" equal to "report.pdf"
