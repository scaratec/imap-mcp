Feature: fetch_body exposes attachment metadata at FULL visibility

  When a message has attachments and the caller has FULL visibility,
  fetch_body must populate the attachments array with metadata for
  each attachment. Each entry carries a numeric index (for use with
  fetch_attachment), the filename (informational, may be null), the
  MIME type, and the size in bytes.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path: attachments populated at FULL visibility  : 1
    - No attachments: empty array at FULL visibility        : 1
    Total enumerated                                        : 2   covered: 2

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "att-agent" using policy "att-policy"
    And policy "att-policy" grants account "gupta-scaratec"
    And policy "att-policy" grants folder:
      | folder | mode      | default | rules |
      | INBOX  | blacklist | FULL    | []    |

  Scenario: fetch_body with FULL visibility populates attachments
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject      |
      | 701 | sender@example.com | With invoice |
    And the message has attachment "invoice.pdf" of type "application/pdf" with size 4096 bytes
    And the message has attachment "logo.png" of type "image/png" with size 512 bytes
    When att-agent calls fetch_body with account "gupta-scaratec", folder "INBOX", uid 701
    Then the response decision is ALLOW
    And the response field attachments has 2 entries
    And attachment 0 has field "index" equal to 0
    And attachment 0 has field "filename" equal to "invoice.pdf"
    And attachment 0 has field "mime_type" equal to "application/pdf"
    And attachment 0 has field "size_bytes" equal to 4096
    And attachment 1 has field "index" equal to 1
    And attachment 1 has field "filename" equal to "logo.png"

  Scenario: fetch_body without attachments returns empty array at FULL
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject  |
      | 702 | sender@example.com | No files |
    When att-agent calls fetch_body with account "gupta-scaratec", folder "INBOX", uid 702
    Then the response decision is ALLOW
    And the response field attachments has 0 entries
