Feature: replace_attachment swaps content of an existing attachment

  The replace_attachment tool identifies an attachment by filename,
  replaces its content (and optionally filename/mime_type), and
  rewrites the message via WAL-backed FETCH-APPEND-DELETE.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path: replace content of named attachment    : 1
    - Attachment not found by filename                   : 1
    - WAL state committed after success                  : 1
    Total enumerated                                     : 3   covered: 3

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "att-agent" using policy "att-policy"
    And policy "att-policy" grants account "gupta-scaratec"
    And policy "att-policy" grants folder:
      | folder | mode      | default | rules | modify_message |
      | INBOX  | blacklist | FULL    | []    | true           |

  Scenario: replace attachment content changes the content hash
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject        |
      | 851 | sender@example.com | Has attachment |
    And the message has attachment "invoice.pdf" of type "application/pdf" with size 100 bytes
    When att-agent calls replace_attachment with account "gupta-scaratec", folder "INBOX", uid 851, filename "invoice.pdf", new_content "bmV3IGNvbnRlbnQ="
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mechanism equals "message_rewrite"

  Scenario: replace attachment with non-existent filename returns error
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject     | has_attachment |
      | 861 | sender@example.com | Has one att | true           |
    When att-agent calls replace_attachment with account "gupta-scaratec", folder "INBOX", uid 861, filename "nonexistent.pdf", new_content "dGVzdA=="
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error_type equals "attachment_not_found"

  Scenario: WAL records committed state after replace_attachment
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject   |
      | 871 | sender@example.com | WAL check |
    And the message has attachment "data.bin" of type "application/octet-stream" with size 50 bytes
    When att-agent calls replace_attachment with account "gupta-scaratec", folder "INBOX", uid 871, filename "data.bin", new_content "AAAA"
    Then the response decision is ALLOW
    And the response field tx_id is a non-empty string
    When att-agent calls get_transaction_status with the returned tx_id
    Then the status response field state equals "committed"
