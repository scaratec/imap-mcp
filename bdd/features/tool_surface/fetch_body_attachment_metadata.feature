Feature: fetch_body exposes complete attachment metadata at BODY and FULL visibility

  ADR 0043 §4 specifies that fetch_body populates the attachments array
  (index, filename, mime_type, size_bytes) at both BODY and FULL
  visibility, listing ["attachment_bytes"] in redacted_fields at BODY and
  [] at FULL.

  Covered error layers (per BDD Guidelines §4.5):
    - Attachments populated at FULL with redacted_fields=[]               : 1
    - Attachments populated at BODY with redacted_fields=[attachment_bytes]: 1
    - Message without attachments returns empty array                     : 1
    Total enumerated                                                      : 3   covered: 3

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "att-agent" using policy "att-policy"
    And policy "att-policy" grants account "gupta-scaratec"

  Scenario: fetch_body at FULL visibility populates all four metadata fields per attachment
    Given policy "att-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules |
      | INBOX  | blacklist | FULL    | []    |
    And the folder "INBOX" holds messages:
      | uid | from_address       | to_address            | subject      | date                 |
      | 701 | sender@example.com | me@gupta-scaratec.com | With invoice | 2026-05-06T09:00:00Z |
    And the message at uid 701 has attachment "invoice.pdf" of type "application/pdf" with content byte "I" repeated 4096 times
    And the message at uid 701 has attachment "logo.png" of type "image/png" with content byte "L" repeated 512 times
    When att-agent calls fetch_body with account "gupta-scaratec", folder "INBOX", uid 701
    Then the response decision is ALLOW
    And the response field redacted_fields equals []
    And the response field attachments has 2 entries
    And attachment 0 has field "index" equal to 0
    And attachment 0 has field "filename" equal to "invoice.pdf"
    And attachment 0 has field "mime_type" equal to "application/pdf"
    And attachment 0 has field "size_bytes" equal to 4096
    And attachment 1 has field "index" equal to 1
    And attachment 1 has field "filename" equal to "logo.png"
    And attachment 1 has field "mime_type" equal to "image/png"
    And attachment 1 has field "size_bytes" equal to 512

  Scenario: fetch_body at BODY visibility populates attachment metadata while redacting attachment_bytes
    Given policy "att-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules |
      | INBOX  | blacklist | BODY    | []    |
    And the folder "INBOX" holds messages:
      | uid | from_address       | to_address            | subject      | date                 |
      | 702 | sender@example.com | me@gupta-scaratec.com | Body invoice | 2026-05-06T09:00:00Z |
    And the message at uid 702 has attachment "summary.pdf" of type "application/pdf" with content byte "S" repeated 1024 times
    When att-agent calls fetch_body with account "gupta-scaratec", folder "INBOX", uid 702
    Then the response decision is ALLOW
    And the response field visibility_applied equals "BODY"
    And the response field redacted_fields equals ["attachment_bytes"]
    And the response field attachments has 1 entries
    And attachment 0 has field "filename" equal to "summary.pdf"
    And attachment 0 has field "mime_type" equal to "application/pdf"
    And attachment 0 has field "size_bytes" equal to 1024

  Scenario: fetch_body on a message without attachments returns an empty array
    Given policy "att-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules |
      | INBOX  | blacklist | FULL    | []    |
    And the folder "INBOX" holds messages:
      | uid | from_address       | to_address            | subject  | date                 |
      | 703 | sender@example.com | me@gupta-scaratec.com | No files | 2026-05-06T09:00:00Z |
    When att-agent calls fetch_body with account "gupta-scaratec", folder "INBOX", uid 703
    Then the response decision is ALLOW
    And the response field attachments has 0 entries
