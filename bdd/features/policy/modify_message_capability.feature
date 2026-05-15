Feature: modify_message capability gates attachment tools

  The modify_message capability controls whether a caller may
  rewrite messages in a folder (add, replace, or delete attachments).
  It is orthogonal to read-side visibility and other write caps.

  Covered error layers (per BDD Guidelines §4.5):
    - modify_message=false denies add_attachment       : 1
    - modify_message=true allows add_attachment         : 1
    Total enumerated                                    : 2   covered: 2

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "att-agent" using policy "att-policy"
    And policy "att-policy" grants account "gupta-scaratec"

  Scenario: add_attachment without modify_message is denied
    Given policy "att-policy" grants folder:
      | folder | mode      | default | rules | modify_message |
      | INBOX  | blacklist | FULL    | []    | false          |
    And the folder "INBOX" holds a message with:
      | uid | from               | subject   |
      | 701 | sender@example.com | Original  |
    When att-agent calls add_attachment with account "gupta-scaratec", folder "INBOX", uid 701, filename "test.pdf", mime_type "application/pdf", content "dGVzdA=="
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "modify_message"

  Scenario: add_attachment with modify_message succeeds
    Given policy "att-policy" grants folder:
      | folder | mode      | default | rules | modify_message |
      | INBOX  | blacklist | FULL    | []    | true           |
    And the folder "INBOX" holds a message with:
      | uid | from               | subject   |
      | 711 | sender@example.com | Original  |
    When att-agent calls add_attachment with account "gupta-scaratec", folder "INBOX", uid 711, filename "test.pdf", mime_type "application/pdf", content "dGVzdA=="
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mechanism equals "message_rewrite"
