Feature: fetch_body returns readable text from HTML-only messages

  When a message contains only a text/html part (no text/plain),
  fetch_body must still return a non-empty text_body with HTML tags
  stripped. When both text/plain and text/html are present,
  text/plain takes precedence.

  Reported bug (2026-05-15, info@scaratec.bg/INBOX/Drafts UID 11):
  Thunderbird draft saved as HTML-only returned text_body="".

  Covered error layers (per BDD Guidelines §4.5):
    - HTML-only message yields stripped text          : 1
    - multipart/alternative prefers text/plain        : 1
    Total enumerated                                  : 2   covered: 2

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "body-agent" using policy "body-policy"
    And policy "body-policy" grants account "gupta-scaratec"
    And policy "body-policy" grants folder:
      | folder | mode      | default | rules |
      | INBOX  | blacklist | FULL    | []    |

  Scenario: HTML-only multipart message returns stripped text in text_body
    Given the folder "INBOX" holds a message with:
      | uid | from                | subject    | has_attachment |
      | 901 | sender@example.com  | HTML draft | true           |
    And the message has html body "<p>Sehr geehrte Damen und Herren,</p>"
    When body-agent calls fetch_body with account "gupta-scaratec", folder "INBOX", uid 901
    Then the response decision is ALLOW
    And the response field text_body contains "Sehr geehrte Damen und Herren,"
    And the JSON response does NOT contain the literal string "<p>"

  Scenario: multipart/alternative prefers text/plain over text/html
    Given the folder "INBOX" holds a message with:
      | uid | from                | subject      |
      | 902 | sender@example.com  | Both formats |
    And the message has plain text body "Plaintext wins"
    And the message has html body "<p>HTML loses</p>"
    When body-agent calls fetch_body with account "gupta-scaratec", folder "INBOX", uid 902
    Then the response decision is ALLOW
    And the response field text_body contains "Plaintext wins"
