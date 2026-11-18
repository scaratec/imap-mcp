Feature: fetch_body derives paragraph-preserving plain text from HTML-only mail and prefers text/plain

  When a message contains only a text/html part, fetch_body derives
  text_body by converting HTML block elements (<p>, <br>, <div>, <li>)
  into line breaks and stripping tags (ADR 0045 §3, ADR 0052). When both
  text/plain and text/html are present, text_body equals the text/plain
  part verbatim without concatenating HTML content.

  Covered error layers (per BDD Guidelines §4.5):
    - HTML-only message yields paragraph-preserving stripped text_body : 1
    - multipart/alternative returns exact text/plain without HTML leak : 1
    Total enumerated                                                   : 2   covered: 2

  Background:
    Given the server clock is pinned to "2026-05-15T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "body-agent" using policy "body-policy"
    And policy "body-policy" grants account "gupta-scaratec"
    And policy "body-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules |
      | INBOX  | blacklist | FULL    | []    |

  Scenario: HTML-only message converts block tags into paragraph breaks in text_body
    Given the folder "INBOX" holds a verbatim message:
      """
      From: sender@example.com
      To: me@gupta-scaratec.com
      Subject: HTML draft
      Date: Fri, 15 May 2026 09:00:00 +0000
      Message-ID: <html-only-901@example.com>
      MIME-Version: 1.0
      Content-Type: text/html; charset=UTF-8

      <html><body><p>Sehr geehrte Damen und Herren,</p><p>anbei die Unterlagen.</p></body></html>
      """
    When body-agent calls fetch_body on the only visible message in "gupta-scaratec:INBOX"
    Then the response decision is ALLOW
    And the response field text_body contains "Sehr geehrte Damen und Herren,"
    And the response field text_body contains "anbei die Unterlagen."
    And the response field text_body does NOT contain "Herren,anbei"
    And the response field text_body does NOT contain "<p>"

  Scenario: multipart/alternative returns text/plain verbatim without concatenating text/html
    Given the folder "INBOX" holds messages:
      | uid | from_address       | to_address            | subject      | date                 |
      | 902 | sender@example.com | me@gupta-scaratec.com | Both formats | 2026-05-15T09:00:00Z |
    And the message at uid 902 has plain text body "Plaintext wins"
    And the message at uid 902 has html body "<p>HTML loses</p>"
    When body-agent calls fetch_body with account "gupta-scaratec", folder "INBOX", uid 902
    Then the response decision is ALLOW
    And the response field text_body equals "Plaintext wins"
    And the response field text_body does NOT contain "HTML loses"
