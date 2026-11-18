Feature: Structured and RFC822 draft formats, attachments on own drafts, header safety, and HTML reply quoting

  ADR 0045 §3 makes plain-text and HTML draft composition equally easy
  while enforcing strict MIME and header safety:
    - create_draft accepts either structured fields (to, cc, bcc,
      subject, body_text, body_html, in_reply_to, attachments) or rfc822,
      never both, and rejects CR/LF in any header-valued input (-32602).
    - Structured attachments compose multipart/mixed drafts directly,
      replacing the retired received-mail attachment rewrite tools.
    - create_reply_draft supports reply_html with a sanitized blockquote
      of the original message's inline body.

  Covered error layers (per BDD Guidelines §4.5):
    - Tool description names format choice and asking rule         : 1
    - rfc822 format reporting (text/plain, text/html, alternative) : 3
    - Structured body composition (text, html, both)               : 3
    - Structured attachments compose multipart/mixed on own draft  : 1
    - Non-ASCII subject and HTML body survive with ASCII headers   : 1
    - CR/LF in structured create_draft header fields -> -32602     : 3
    - Mutual exclusion (rfc822 + fields) and missing body -> -32602: 2
    - create_reply_draft with reply_html quotes sanitized blockquote: 1
    Total enumerated                                               : 15  covered by this feature: 15

  Background:
    Given the server clock is pinned to "2026-10-31T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the account "gupta-scaratec" has identity "draft-agent@example.org"
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules | draft_append | draft_edit |
      | INBOX  | blacklist | FULL    | []    | false        | false      |
      | Drafts | blacklist | FULL    | []    | true         | true       |

  Scenario: The create_draft description presents plain text or HTML as a choice and asks when unknown
    When draft-agent calls the MCP list_tools method
    Then the description of tool "create_draft" contains the literal string "plain text or HTML"
    And the description of tool "create_draft" contains the literal string "If the user's preference is unknown, ask"
    And the description of tool "create_draft" does NOT contain the literal string "prefer HTML"
    And the description of tool "create_draft" does NOT contain the literal string "prefer plain text"

  Scenario: create_draft reports text/plain for a plain UTF-8 rfc822 message
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@example.org
      To: mara.lindqvist@example.org
      Subject: =?UTF-8?Q?R=C3=BCckfrage_Angebot?=
      MIME-Version: 1.0
      Content-Type: text/plain; charset=UTF-8
      Content-Transfer-Encoding: 8bit

      Guten Tag Frau Lindqvist,

      passt Ihnen ein Termin am Dienstag?

      Viele Grüße
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field format equals "text/plain"
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows the top-level content type "text/plain"

  Scenario: create_draft reports text/html for an HTML-only rfc822 message
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@example.org
      To: mara.lindqvist@example.org
      Subject: =?UTF-8?Q?R=C3=BCckfrage_Angebot?=
      MIME-Version: 1.0
      Content-Type: text/html; charset=UTF-8
      Content-Transfer-Encoding: 8bit

      <html><body><p>Guten Tag Frau Lindqvist,</p></body></html>
      """
    Then the response decision is ALLOW
    And the response field format equals "text/html"
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows the top-level content type "text/html"

  Scenario: create_draft reports multipart/alternative for an rfc822 message with both parts
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@example.org
      To: mara.lindqvist@example.org
      Subject: =?UTF-8?Q?R=C3=BCckfrage_Angebot?=
      MIME-Version: 1.0
      Content-Type: multipart/alternative; boundary="sc-alt-0001"

      --sc-alt-0001
      Content-Type: text/plain; charset=UTF-8

      Guten Tag Frau Lindqvist
      --sc-alt-0001
      Content-Type: text/html; charset=UTF-8

      <html><body><p>Guten Tag Frau Lindqvist</p></body></html>
      --sc-alt-0001--
      """
    Then the response decision is ALLOW
    And the response field format equals "multipart/alternative"
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows the top-level content type "multipart/alternative"

  Scenario Outline: create_draft composes structured body fields into <format>
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["mara.lindqvist@example.org"], "subject": "Angebot", <bodies>}
      """
    Then the response decision is ALLOW
    And the response field format equals "<format>"
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows the leaf parts in order "<parts>"

    Examples:
      | bodies                                                    | format                | parts                             |
      | "body_text": "Guten Tag,\n\nanbei das Angebot."           | text/plain            | text/plain;utf-8                  |
      | "body_html": "<p>Guten Tag,</p><p>anbei das Angebot.</p>" | multipart/alternative | text/plain;utf-8, text/html;utf-8 |
      | "body_text": "Guten Tag", "body_html": "<p>Guten Tag</p>" | multipart/alternative | text/plain;utf-8, text/html;utf-8 |

  Scenario: create_draft composes structured attachments on a new draft
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["mara.lindqvist@example.org"],
       "subject": "Angebot mit PDF",
       "body_text": "Anbei das PDF.",
       "attachments": [{"filename": "angebot.pdf", "mime_type": "application/pdf", "content_base64": "JVBERi0xLjQK"}]}
      """
    Then the response decision is ALLOW
    And the response field format equals "multipart/mixed"
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response field attachments has 1 entries
    And attachment 0 has field "filename" equal to "angebot.pdf"
    And attachment 0 has field "size_bytes" equal to 9

  Scenario: Non-ASCII subject and HTML body survive structured composition with pure-ASCII headers
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["jonas.hartmann@example.org"],
       "subject": "Rückfrage zur Kündigungsfrist",
       "body_html": "<p>Grüße aus Köln</p>"}
      """
    Then the response decision is ALLOW
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows the decoded subject "Rückfrage zur Kündigungsfrist"
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows a text/html part whose decoded text contains "Grüße aus Köln"
    And the raw header block of the newest message in "gupta-scaratec:Drafts" contains only ASCII characters

  Scenario Outline: CR/LF header injection in structured create_draft <field> is rejected with JSON-RPC -32602
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      <payload>
      """
    Then the server responds with JSON-RPC error code -32602
    And the IMAP folder "Drafts" is empty

    Examples:
      | field      | payload                                                                                                                                     |
      | subject    | {"to": ["mara@example.org"], "subject": "Hi\r\nBcc: evil@example.com", "body_text": "Body"}                                                 |
      | to         | {"to": ["mara@example.org\r\nBcc: evil@example.com"], "subject": "Hi", "body_text": "Body"}                                                 |
      | attachment | {"to": ["mara@example.org"], "subject": "Hi", "body_text": "Body", "attachments": [{"filename": "a\r\n.pdf", "mime_type": "application/pdf", "content_base64": "dGVzdA=="}]} |

  Scenario: Passing both rfc822 and structured fields in one call is rejected with JSON-RPC -32602
    When draft-agent calls create_draft with the arguments {"account": "gupta-scaratec", "folder": "Drafts", "rfc822": "Subject: A\r\n\r\nB", "body_text": "C"}
    Then the server responds with JSON-RPC error code -32602
    And the IMAP folder "Drafts" contains no message with subject "A"

  Scenario: Calling create_draft with neither rfc822 nor any body field is rejected with JSON-RPC -32602
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["mara.lindqvist@example.org"], "subject": "Leer"}
      """
    Then the server responds with JSON-RPC error code -32602
    And the IMAP folder "Drafts" contains no message with subject "Leer"

  Scenario: create_reply_draft with reply_html renders a sanitized blockquote and signed provenance
    Given the folder "INBOX" holds messages:
      | uid | message_id               | from_address               | to_address              | subject    | date                 |
      | 801 | <oferta-801@example.org> | mara.lindqvist@example.org | draft-agent@example.org | Angebot Q1 | 2026-10-28T09:00:00Z |
    And the message at uid 801 has html body "<p>Bitte pruefen.</p><script>alert('xss')</script>"
    When draft-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 801, drafts_folder "Drafts", reply_html "<p>Danke, passt.</p>"
    Then the response decision is ALLOW
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows the top-level content type "multipart/alternative"
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows a text/html part whose decoded text contains "Danke, passt."
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows a text/html part whose decoded text contains "<blockquote"
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows a text/html part whose decoded text contains "Bitte pruefen."
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows a text/html part whose decoded text does NOT contain "<script>"
