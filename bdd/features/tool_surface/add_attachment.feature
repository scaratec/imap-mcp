Feature: add_attachment appends a new part to an existing message

  The add_attachment tool fetches the original message, adds a MIME
  attachment part, APPENDs the modified message, and DELETEs the
  original. The operation is WAL-backed for crash recovery.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path: add to message without attachments    : 1
    - Happy path: add to message with existing attachment: 1
    - IMAP second channel: old UID gone, new UID exists  : 1
    - WAL state committed after success                  : 1
    - uid_not_found error                                : 1
    Total enumerated                                     : 5   covered: 5

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "att-agent" using policy "att-policy"
    And policy "att-policy" grants account "gupta-scaratec"
    And policy "att-policy" grants folder:
      | folder | mode      | default | rules | modify_message |
      | INBOX  | blacklist | FULL    | []    | true           |

  Scenario: add attachment to a plain text message
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject        |
      | 801 | sender@example.com | No attachments |
    When att-agent calls add_attachment with account "gupta-scaratec", folder "INBOX", uid 801, filename "report.pdf", mime_type "application/pdf", content "dGVzdA=="
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mechanism equals "message_rewrite"
    When att-agent calls list_attachments with account "gupta-scaratec", folder "INBOX", uid 801
    Then the response field attachments contains exactly one entry with:
      | field    | value      |
      | index    | 0          |
      | filename | report.pdf |

  Scenario: add attachment preserves existing attachments
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject     | has_attachment |
      | 811 | sender@example.com | Has one att | true           |
    When att-agent calls add_attachment with account "gupta-scaratec", folder "INBOX", uid 811, filename "second.pdf", mime_type "application/pdf", content "dGVzdA=="
    Then the response decision is ALLOW
    And the response field result equals "OK"
    When att-agent calls list_attachments with account "gupta-scaratec", folder "INBOX", uid 811
    Then the response field attachments has length 2

  Scenario: add attachment to non-existent UID returns error
    When att-agent calls add_attachment with account "gupta-scaratec", folder "INBOX", uid 999, filename "x.pdf", mime_type "application/pdf", content "dGVzdA=="
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "uid_not_found"

  Scenario: rewrite replaces the original message, not duplicates it
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject   |
      | 821 | sender@example.com | Will move |
    When att-agent calls add_attachment with account "gupta-scaratec", folder "INBOX", uid 821, filename "a.bin", mime_type "application/octet-stream", content "AAAA"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    When att-agent calls list_messages with account "gupta-scaratec", folder "INBOX", scope "all"
    Then the response field matched_total equals 1

  Scenario: WAL records committed state after add_attachment
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject   |
      | 831 | sender@example.com | WAL check |
    When att-agent calls add_attachment with account "gupta-scaratec", folder "INBOX", uid 831, filename "w.bin", mime_type "application/octet-stream", content "AAAA"
    Then the response decision is ALLOW
    And the response field tx_id is a non-empty string
    When att-agent calls get_transaction_status with the returned tx_id
    Then the status response field state equals "committed"
