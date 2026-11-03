Feature: A draft's body format is a visible, deliberate choice, equally easy either way

  Whether a draft is plain text or HTML is the mailbox owner's
  preference, not the server's. The server must neither impose a format
  nor make one of them so much harder to produce that the effort
  decides instead of the caller. Today create_draft accepts only a raw
  RFC 822 message: a plain-text draft is five header lines and a body,
  an HTML draft needs a hand-built multipart/alternative tree with
  boundaries, charsets and an RFC 2047 encoded subject. update_draft,
  by contrast, already takes body_text and body_html as fields
  (ADR 0031). ADR 0035 decides the behaviour below. Agents therefore write plain text with manual line breaks
  by default, and rewrite the whole draft when asked for HTML.

  The server's part in a deliberate choice is threefold: say in the tool
  description that the format is a choice and that an unknown
  preference is a reason to ask; report back which format was stored;
  and offer both formats through the same structured fields create and
  update share. Whether an agent actually decides deliberately is
  measured in usability/features/01_draft_format_decision.feature, not
  here.

  Independent-channel rule. The stored format is read by a direct IMAP
  FETCH of the appended message's headers and MIME structure, never
  from the create_draft response it is compared against.

  Covered error layers (per BDD Guidelines §4.5):
    - Tool description names the choice and the reason to ask      : 1
    - Response reports the stored format                           : 3 (plain, html, alternative)
    - Structured fields build a well-formed message                : 3 (text, html, both)
    - Non-ASCII subject and body survive structured composition    : 1
    - Raw rfc822 and structured fields are mutually exclusive      : 1
    - Neither raw rfc822 nor any body field                        : 1
    - create_reply_draft accepts an HTML reply                     : 1
    Total enumerated                                               : 11  covered by this feature: 11

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "Drafts"
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | true         | true       |

  # --- The choice is named where the caller reads it -------------------

  Scenario: The create_draft description presents the format as a choice and asks to clarify an unknown preference
    When draft-agent lists the available tools
    Then the description of tool "create_draft" contains the literal string "plain text or HTML"
    And the description of tool "create_draft" contains the literal string "If the user's preference is unknown, ask"
    And the description of tool "create_draft" does NOT contain the literal string "prefer HTML"
    And the description of tool "create_draft" does NOT contain the literal string "prefer plain text"

  # --- The stored format is reported back ------------------------------

  Scenario: create_draft reports text/plain for a plain UTF-8 message
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

  Scenario: create_draft reports text/html for an HTML-only message
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@example.org
      To: mara.lindqvist@example.org
      Subject: =?UTF-8?Q?R=C3=BCckfrage_Angebot?=
      MIME-Version: 1.0
      Content-Type: text/html; charset=UTF-8
      Content-Transfer-Encoding: 8bit

      <html><body>
      <p>Guten Tag Frau Lindqvist,</p>
      <p>passt Ihnen ein Termin am Dienstag?</p>
      <p>Viele Grüße</p>
      </body></html>
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field format equals "text/html"
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows the top-level content type "text/html"

  Scenario: create_draft reports multipart/alternative for a message with both parts
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@example.org
      To: mara.lindqvist@example.org
      Subject: =?UTF-8?Q?R=C3=BCckfrage_Angebot?=
      MIME-Version: 1.0
      Content-Type: multipart/alternative; boundary="sc-alt-0001"

      --sc-alt-0001
      Content-Type: text/plain; charset=UTF-8
      Content-Transfer-Encoding: 8bit

      Guten Tag Frau Lindqvist,

      passt Ihnen ein Termin am Dienstag?

      Viele Grüße

      --sc-alt-0001
      Content-Type: text/html; charset=UTF-8
      Content-Transfer-Encoding: 8bit

      <html><body>
      <p>Guten Tag Frau Lindqvist,</p>
      <p>passt Ihnen ein Termin am Dienstag?</p>
      <p>Viele Grüße</p>
      </body></html>

      --sc-alt-0001--
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field format equals "multipart/alternative"
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows the top-level content type "multipart/alternative"

  # --- Both formats through the same structured fields (proposal) -----

  Scenario Outline: create_draft composes the message from structured fields
    # ADR 0035: create_draft accepts the field set update_draft uses.
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["mara.lindqvist@example.org"], "subject": "Angebot", <bodies>}
      """
    # A body_html without body_text still gets a text/plain alternative,
    # derived by the same tag stripping fetch_body uses
    # (fetch_body_html_fallback.feature), so text-only clients show it.
    Then the response decision is ALLOW
    And the response field format equals "<format>"
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows the leaf parts in order "<parts>"

    Examples:
      | bodies                                                    | format                | parts                                  |
      | "body_text": "Guten Tag,\n\nanbei das Angebot."            | text/plain            | text/plain;utf-8                       |
      | "body_html": "<p>Guten Tag,</p><p>anbei das Angebot.</p>"  | multipart/alternative | text/plain;utf-8, text/html;utf-8      |
      | "body_text": "Guten Tag", "body_html": "<p>Guten Tag</p>"  | multipart/alternative | text/plain;utf-8, text/html;utf-8      |

  Scenario: Non-ASCII subject and body survive structured composition unchanged
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

  Scenario: Raw rfc822 and structured fields in one call are refused as ambiguous
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 "Subject: A\r\n\r\nB" and fields {"body_text": "C"}
    Then the tool call fails with an invalid-arguments error
    And the IMAP folder "Drafts" contains no message with subject "A"

  Scenario: A call with neither rfc822 nor any body field is refused
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["mara.lindqvist@example.org"], "subject": "Leer"}
      """
    Then the tool call fails with an invalid-arguments error
    And the IMAP folder "Drafts" contains no message with subject "Leer"

  Scenario: create_reply_draft accepts an HTML reply alongside the quoted text
    # ADR 0035: reply_html is optional; the quoted original is rendered
    # as a blockquote in the HTML part.
    Given the IMAP account "gupta-scaratec" also has folder "INBOX"
    And the account "gupta-scaratec" has identity "draft-agent@example.org"
    And policy "draft-policy" grants folder:
      | folder | mode      | default | rules |
      | INBOX  | blacklist | FULL    | []    |
    And the folder "INBOX" holds messages:
      | uid | from                       | subject       | date                 |
      | 801 | mara.lindqvist@example.org | Angebot Q1    | 2026-10-28T09:00:00Z |
    When draft-agent calls create_reply_draft with account "gupta-scaratec", source folder "INBOX", uid 801, drafts folder "Drafts", reply_html "<p>Danke, passt.</p>"
    Then the response decision is ALLOW
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows the top-level content type "multipart/alternative"
    And a direct IMAP FETCH of the newest message in "gupta-scaratec:Drafts" shows a text/html part whose decoded text contains "Danke, passt."
