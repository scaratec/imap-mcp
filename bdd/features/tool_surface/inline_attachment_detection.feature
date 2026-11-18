Feature: Inline attachment detection inside nested Apple Mail multipart/alternative structures

  Apple Mail wraps PDF attachments inside multipart/alternative (or nested
  multipart containers) using Content-Disposition: inline; filename="...".
  ADR 0047 §1 classifies such parts uniformly across has_attachment,
  list_attachments, fetch_body, and fetch_attachment.

  Covered error layers (per BDD Guidelines §4.5):
    - Nested multipart/alternative inline PDF detected & ignored inline text/plain : 1
    - Nested multipart/alternative inline PDF delivered byte-exact by fetch_attachment : 1
    - Non-text leaf part with name= parameter and no Content-Disposition classified as attachment : 1
    Total enumerated                                                                : 3   covered by this feature: 3

  Background:
    Given the server clock is pinned to "2026-06-18T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path   |
      | INBOX         |
      | INBOX/Reports |
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"
    And policy "doc-policy" grants folder on account "gupta-scaratec":
      | folder        | mode      | default | rules |
      | INBOX/Reports | blacklist | FULL    | []    |
    And the folder "INBOX/Reports" holds a verbatim message:
      """
      From: sender@test.example
      To: me@gupta-scaratec.com
      Subject: Apple Mail Monthly Report
      Date: Wed, 17 Jun 2026 09:00:00 +0000
      Message-ID: <apple-mail-801@test.example>
      MIME-Version: 1.0
      Content-Type: multipart/alternative; boundary="Apple-Alt-Boundary"

      --Apple-Alt-Boundary
      Content-Type: text/plain; charset=UTF-8

      Please find the monthly report attached inline.

      --Apple-Alt-Boundary
      Content-Type: multipart/mixed; boundary="Apple-Mixed-Inner"

      --Apple-Mixed-Inner
      Content-Type: text/html; charset=UTF-8

      <html><body><p>Please find the monthly report attached inline.</p></body></html>

      --Apple-Mixed-Inner
      Content-Type: application/pdf; name="report.pdf"
      Content-Disposition: inline; filename="report.pdf"
      Content-Transfer-Encoding: base64

      JVBERi0xLjQK

      --Apple-Mixed-Inner--
      --Apple-Alt-Boundary--
      """

  Scenario: Nested inline PDF is detected by search(has_attachment=true) and list_attachments
    When doc-agent calls search with account "gupta-scaratec", folder "INBOX/Reports", criteria {"has_attachment": true}, scope "all"
    Then the response decision is ALLOW
    And the response field total_visible equals 1
    When doc-agent calls list_attachments on the only visible message in "gupta-scaratec:INBOX/Reports"
    Then the response decision is ALLOW
    And the response field attachments has 1 entries
    And attachment 0 has field "index" equal to 0
    And attachment 0 has field "filename" equal to "report.pdf"
    And attachment 0 has field "mime_type" equal to "application/pdf"
    And attachment 0 has field "size_bytes" equal to 9

  Scenario: Nested inline PDF is delivered byte-for-byte by fetch_attachment
    Given the server attachment sink directory is a fresh writable directory
    When doc-agent calls fetch_attachment for part_id 0 on the only visible message in "gupta-scaratec:INBOX/Reports"
    Then the response decision is ALLOW
    And the response field mime_type equals "application/pdf"
    And the response field size_bytes equals 9
    And the response field saved_to matches "^doc-agent/[0-9a-f]{32}_report\.pdf$"
    And the file at saved_path exists with mode 0400 and exact ASCII content "%PDF-1.4\n"

  Scenario: Non-text part carrying name= without Content-Disposition is classified as attachment while inline text/plain without filename is excluded
    Given the folder "INBOX/Reports" is reset to hold a verbatim message:
      """
      From: sender@test.example
      To: me@gupta-scaratec.com
      Subject: Rule C and Inline Text
      Date: Wed, 17 Jun 2026 10:00:00 +0000
      Message-ID: <rule-c-802@test.example>
      MIME-Version: 1.0
      Content-Type: multipart/mixed; boundary="Mix-802"

      --Mix-802
      Content-Type: text/plain; charset=UTF-8
      Content-Disposition: inline

      Inline body note without filename.
      --Mix-802
      Content-Type: application/pdf; name="spec.pdf"
      Content-Transfer-Encoding: base64

      JVBERi0xLjQK
      --Mix-802--
      """
    When doc-agent calls list_attachments on the only visible message in "gupta-scaratec:INBOX/Reports"
    Then the response decision is ALLOW
    And the response field attachments has 1 entries
    And attachment 0 has field "filename" equal to "spec.pdf"
