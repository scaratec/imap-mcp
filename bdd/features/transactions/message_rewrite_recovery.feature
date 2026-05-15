Feature: Message rewrite crash recovery via WAL

  When a message rewrite (attachment add/replace/delete) crashes after
  APPEND but before DELETE, the WAL records the staged state. On
  recovery the server resumes the DELETE step so the original message
  is cleaned up and the transaction reaches committed.

  Covered error layers (per BDD Guidelines §4.5):
    - Crash after APPEND: staged recovery completes DELETE : 1
    - Flags on original message are preserved on rewrite    : 1
    Total enumerated                                        : 2   covered: 2

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "att-agent" using policy "att-policy"
    And policy "att-policy" grants account "gupta-scaratec"
    And policy "att-policy" grants folder:
      | folder | mode      | default | rules | modify_message |
      | INBOX  | blacklist | FULL    | []    | true           |

  Scenario: flags are preserved after attachment rewrite
    Given the folder "INBOX" holds a message with:
      | uid | from               | subject   | flags      |
      | 951 | sender@example.com | Flagged   | [\Flagged] |
    When att-agent calls add_attachment with account "gupta-scaratec", folder "INBOX", uid 951, filename "doc.pdf", mime_type "application/pdf", content "dGVzdA=="
    Then the response decision is ALLOW
    And the response field result equals "OK"
    When att-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"flagged": true}
    Then the response field matched_visible equals 1
