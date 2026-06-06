Feature: delete_attachment removes a part from an existing message

  The delete_attachment tool identifies an attachment by filename,
  removes it from the MIME structure, and rewrites the message via
  WAL-backed FETCH-APPEND-DELETE.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path: delete named attachment                : 1
    - Attachment not found by filename                   : 1
    - After deletion fetch_attachment no longer lists it  : 1
    Total enumerated                                     : 3   covered: 3

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "att-agent" using policy "att-policy"
    And policy "att-policy" grants account "gupta-scaratec"
    And policy "att-policy" grants folder:
      | folder | mode      | default | rules | modify_message |
      | INBOX  | blacklist | FULL    | []    | true           |

  Scenario: delete attachment removes it from the message
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject      |
      | 881 | sender@example.com | Two attached |
    And the message has attachment "keep.pdf" of type "application/pdf" with size 100 bytes
    And the message has attachment "remove.pdf" of type "application/pdf" with size 50 bytes
    When att-agent calls delete_attachment with account "gupta-scaratec", folder "INBOX", uid 881, filename "remove.pdf"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mechanism equals "message_rewrite"
    When att-agent calls list_attachments with account "gupta-scaratec", folder "INBOX", uid 881
    Then the response field attachments has length 1

  Scenario: delete attachment with non-existent filename returns error
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject     | has_attachment |
      | 891 | sender@example.com | Has one att | true           |
    When att-agent calls delete_attachment with account "gupta-scaratec", folder "INBOX", uid 891, filename "ghost.pdf"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "attachment_not_found"

  Scenario: fetch_attachment after delete confirms removal
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject    |
      | 911 | sender@example.com | Single att |
    And the message has attachment "only.pdf" of type "application/pdf" with size 100 bytes
    When att-agent calls delete_attachment with account "gupta-scaratec", folder "INBOX", uid 911, filename "only.pdf"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    When att-agent calls list_attachments with account "gupta-scaratec", folder "INBOX", uid 911
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field attachments has 0 entries
