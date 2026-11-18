Feature: create_reply_draft builds a deterministic top-posted reply with per-message BODY and MIME isolation

  ADR 0044 and ADR 0045 §3 specify create_reply_draft:
    - Requires draft_append=true on the \Drafts folder AND effective
      visibility >= BODY on the specific source message (returning
      ALLOW+ERROR not_found if < METADATA and DENY visibility_below_BODY
      if METADATA..HEADERS).
    - Quotes ONLY inline text/plain or stripped text/html body parts —
      never attachment parts or attached message/rfc822 sub-messages.
    - Attribution formats the source Date header's local wall-clock time
      as "On YYYY-MM-DD HH:MM, <sender> wrote:" without timezone conversion
      (omitting the date prefix when Date is absent).
    - Returns ALLOW + ERROR missing_message_id when the source lacks
      Message-ID, ALLOW + ERROR empty_reply_text on empty reply text,
      and ALLOW + ERROR not_found on a non-existent source UID.

  Covered error layers (per BDD Guidelines §4.5):
    - Cyrillic UTF-8 reply-all happy path + threading headers       : 1
    - Subject "Re: " prefix deduplication (4 cases)                 : 4
    - Reply-To preferred over From for draft To                     : 1
    - Missing Date & missing From display-name tolerance            : 2
    - HTML-only source & nested ">" quote preservation              : 2
    - MIME attachment parts excluded from reply quote               : 1
    - Missing source Message-ID -> ALLOW + ERROR missing_message_id : 1
    - Empty reply_text -> ALLOW + ERROR empty_reply_text            : 1
    - Non-existent or NONE source UID -> ALLOW + ERROR not_found    : 2
    - Per-message ENVELOPE cap & folder METADATA -> visibility_below_BODY : 2
    - Target folder lacking draft_append -> DENY capability_missing : 1
    Total enumerated                                                : 18  covered: 18

  Background:
    Given the server clock is pinned to "2026-05-16T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the account "gupta-scaratec" has identity "agent@scaratec.bg"
    And the server is configured with caller "reply-agent" using policy "reply-policy"
    And policy "reply-policy" grants account "gupta-scaratec"
    And policy "reply-policy" grants the following folder policies:
      | folder | mode      | default | rules | draft_append |
      | INBOX  | blacklist | FULL    | []    | false        |
      | Drafts | blacklist | FULL    | []    | true         |

  Scenario: Reply-all to a Cyrillic message produces a signed draft with threading headers and quoted body
    Given the folder "INBOX" holds a message with:
      | uid  | from                             | reply_to                 | to_header                                             | cc_header             | subject   | header_date                     | internal_date        | message_id                       | references_header         |
      | 1001 | "Иван Петров" <ivan@example.com> | ivan.replies@example.com | agent@scaratec.bg, ops@scaratec.bg, AGENT@SCARATEC.BG | partner@third.example | Здравейте | Fri, 15 May 2026 10:56:00 +0300 | 2026-05-15T07:56:00Z | <20260515-1056-ivan@example.com> | <thread-root@example.com> |
    And the message at uid 1001 has plain text body:
      """
      Здравейте,

      Това е примерно съобщение за UTF-8 тест.
      """
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 1001, drafts_folder "Drafts", reply_text:
      """
      Благодаря.
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the IMAP folder "Drafts" contains exactly one message with subject "Re: Здравейте"
    And the IMAP message in folder "Drafts" with subject "Re: Здравейте" has header "In-Reply-To" equal to "<20260515-1056-ivan@example.com>"
    And the IMAP message in folder "Drafts" with subject "Re: Здравейте" has header "References" equal to "<thread-root@example.com> <20260515-1056-ivan@example.com>"
    And the IMAP message in folder "Drafts" with subject "Re: Здравейте" has header "To" equal to "ivan.replies@example.com"
    And the IMAP message in folder "Drafts" with subject "Re: Здравейте" has Cc header containing "ops@scaratec.bg" and "partner@third.example"
    And the IMAP message in folder "Drafts" with subject "Re: Здравейте" has Cc header NOT containing "agent@scaratec.bg"
    And the IMAP message in folder "Drafts" with subject "Re: Здравейте" has Cc header NOT containing "AGENT@SCARATEC.BG"
    And the IMAP message in folder "Drafts" with subject "Re: Здравейте" carries 1 header "X-SC-Draft-Id", 1 header "X-SC-Draft-Rev", and 1 header "X-SC-Provenance"
    When reply-agent calls fetch_body on the only visible message in "gupta-scaratec:Drafts"
    Then the response text_body equals the following document:
      """
      Благодаря.

      On 2026-05-15 10:56, Иван Петров <ivan@example.com> wrote:
      > Здравейте,
      >
      > Това е примерно съобщение за UTF-8 тест.
      """

  Scenario Outline: "Re: " is prepended only when not already present (case-insensitive)
    Given the folder "INBOX" holds a message with:
      | uid   | from               | to_header         | subject   | header_date                     | internal_date        | message_id               |
      | <uid> | sender@example.com | agent@scaratec.bg | <subject> | Mon, 04 May 2026 09:00:00 +0000 | 2026-05-04T09:00:00Z | <subj-<uid>@example.com> |
    And the message at uid <uid> has plain text body "Original body text."
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid <uid>, drafts_folder "Drafts", reply_text "Acknowledged."
    Then the response decision is ALLOW
    And the IMAP message in folder "Drafts" with header "In-Reply-To" equal to "<subj-<uid>@example.com>" has decoded subject "<expected_subject>"

    Examples:
      | uid  | subject               | expected_subject        |
      | 2001 | Тестово съобщение     | Re: Тестово съобщение   |
      | 2002 | Re: Тестово съобщение | Re: Тестово съобщение   |
      | 2003 | re: lowercase reply   | re: lowercase reply     |
      | 2004 | AW: deutscher Reply   | Re: AW: deutscher Reply |

  Scenario: Source Reply-To wins over From for the draft To header
    Given the folder "INBOX" holds a message with:
      | uid  | from                               | reply_to         | to_header         | subject    | header_date                     | internal_date        | message_id              |
      | 3001 | "Bot Sender" <noreply@example.com> | desk@example.com | agent@scaratec.bg | Ticket #42 | Tue, 05 May 2026 12:00:00 +0000 | 2026-05-05T12:00:00Z | <ticket-42@example.com> |
    And the message at uid 3001 has plain text body "Your ticket has been received."
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 3001, drafts_folder "Drafts", reply_text "Thanks."
    Then the response decision is ALLOW
    And the IMAP message in folder "Drafts" with subject "Re: Ticket #42" has header "To" equal to "desk@example.com"
    And the IMAP message in folder "Drafts" with subject "Re: Ticket #42" has To header NOT containing "noreply@example.com"

  Scenario: Source message without a Date header produces a dateless attribution line
    Given the folder "INBOX" holds a message with:
      | uid  | from                      | to_header         | subject    | header_date | internal_date        | message_id               |
      | 5001 | "Anna" <anna@example.com> | agent@scaratec.bg | Quick note |             | 2026-05-06T09:00:00Z | <quick-note@example.com> |
    And the message at uid 5001 has plain text body "Hello."
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 5001, drafts_folder "Drafts", reply_text "Hi back."
    Then the response decision is ALLOW
    When reply-agent calls fetch_body on the only visible message in "gupta-scaratec:Drafts"
    Then the response text_body equals the following document:
      """
      Hi back.

      Anna <anna@example.com> wrote:
      > Hello.
      """

  Scenario: Source From without display-name uses bare <email> in attribution
    Given the folder "INBOX" holds a message with:
      | uid  | from             | to_header         | subject | header_date                     | internal_date        | message_id         |
      | 5101 | bare@example.com | agent@scaratec.bg | Bare    | Thu, 07 May 2026 09:00:00 +0000 | 2026-05-07T09:00:00Z | <bare@example.com> |
    And the message at uid 5101 has plain text body "One line."
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 5101, drafts_folder "Drafts", reply_text "Reply."
    Then the response decision is ALLOW
    When reply-agent calls fetch_body on the only visible message in "gupta-scaratec:Drafts"
    Then the response text_body equals the following document:
      """
      Reply.

      On 2026-05-07 09:00, <bare@example.com> wrote:
      > One line.
      """

  Scenario: HTML-only source is quoted using paragraph-preserving stripped text
    Given the folder "INBOX" holds a verbatim message:
      """
      From: "Marketing" <marketing@example.com>
      To: agent@scaratec.bg
      Subject: Newsletter
      Date: Fri, 08 May 2026 11:00:00 +0000
      Message-ID: <news-1@example.com>
      MIME-Version: 1.0
      Content-Type: text/html; charset=UTF-8

      <p>Sehr geehrte Damen und Herren,</p><p>willkommen.</p>
      """
    When reply-agent calls create_reply_draft on the only visible message in "gupta-scaratec:INBOX" into "Drafts" with reply_text "Danke."
    Then the response decision is ALLOW
    When reply-agent calls fetch_body on the only visible message in "gupta-scaratec:Drafts"
    Then the response field text_body contains "Danke."
    And the response field text_body contains "> Sehr geehrte Damen und Herren,"
    And the response field text_body contains "> willkommen."

  Scenario: Already-quoted lines in source gain one extra ">" prefix
    Given the folder "INBOX" holds a message with:
      | uid  | from          | to_header         | subject | header_date                     | internal_date        | message_id           |
      | 7001 | b@example.com | agent@scaratec.bg | Re: Re: | Mon, 11 May 2026 10:00:00 +0000 | 2026-05-11T10:00:00Z | <nested@example.com> |
    And the message at uid 7001 has plain text body:
      """
      My answer.

      > Earlier quoted line.
      >> Even older line.
      """
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 7001, drafts_folder "Drafts", reply_text "Closing the thread."
    Then the response decision is ALLOW
    When reply-agent calls fetch_body on the only visible message in "gupta-scaratec:Drafts"
    Then the response text_body equals the following document:
      """
      Closing the thread.

      On 2026-05-11 10:00, <b@example.com> wrote:
      > My answer.
      >
      >> Earlier quoted line.
      >>> Even older line.
      """

  Scenario: Attachment parts and attached message/rfc822 sub-messages are never quoted into the reply draft
    Given the folder "INBOX" holds a verbatim message:
      """
      From: sender@example.com
      To: agent@scaratec.bg
      Subject: Beleg mit Textanhang
      Date: Tue, 12 May 2026 10:00:00 +0000
      Message-ID: <att-isolation-7501@example.com>
      MIME-Version: 1.0
      Content-Type: multipart/mixed; boundary="mix-7501"

      --mix-7501
      Content-Type: text/plain; charset=UTF-8

      Kurzes Anschreiben im Body.
      --mix-7501
      Content-Type: text/plain; charset=UTF-8; name="geheim.txt"
      Content-Disposition: attachment; filename="geheim.txt"

      ATTACHMENT-SECRET-DO-NOT-QUOTE
      --mix-7501
      Content-Type: message/rfc822
      Content-Disposition: inline

      From: nested@example.com
      Subject: Nested
      Content-Type: text/plain; charset=UTF-8

      NESTED-RFC822-DO-NOT-QUOTE
      --mix-7501--
      """
    When reply-agent calls create_reply_draft on the only visible message in "gupta-scaratec:INBOX" into "Drafts" with reply_text "Erhalten."
    Then the response decision is ALLOW
    And a direct IMAP FETCH of the message with subject "Re: Beleg mit Textanhang" in "gupta-scaratec:Drafts" shows decoded plain text containing "> Kurzes Anschreiben im Body."
    And a direct IMAP FETCH of the message with subject "Re: Beleg mit Textanhang" in "gupta-scaratec:Drafts" shows decoded plain text NOT containing "ATTACHMENT-SECRET-DO-NOT-QUOTE"
    And a direct IMAP FETCH of the message with subject "Re: Beleg mit Textanhang" in "gupta-scaratec:Drafts" shows decoded plain text NOT containing "NESTED-RFC822-DO-NOT-QUOTE"

  Scenario: Source message without a Message-ID header returns ALLOW + ERROR missing_message_id and creates no draft
    Given the folder "INBOX" holds a message without Message-ID:
      | uid  | from_address       | to_address        | subject            | date                 | exact_size_bytes |
      | 4001 | sender@example.com | agent@scaratec.bg | No Message-ID here | 2026-05-06T08:00:00Z | 1024             |
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 4001, drafts_folder "Drafts", reply_text "Will not be built."
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "missing_message_id"
    And the IMAP folder "Drafts" is empty

  Scenario: Empty reply_text returns ALLOW + ERROR empty_reply_text and creates no draft
    Given the folder "INBOX" holds a message with:
      | uid  | from          | to_header         | subject | header_date                     | internal_date        | message_id           |
      | 8001 | a@example.com | agent@scaratec.bg | Hello   | Tue, 12 May 2026 12:00:00 +0000 | 2026-05-12T12:00:00Z | <hello-8001@example> |
    And the message at uid 8001 has plain text body "Hi."
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 8001, drafts_folder "Drafts", reply_text ""
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "empty_reply_text"
    And the IMAP folder "Drafts" is empty

  Scenario Outline: Non-existent UID and NONE-capped source message both return ALLOW + ERROR not_found
    Given policy "reply-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules                               |
      | INBOX  | blacklist | BODY    | [{from_domain=bank.de -> cap NONE}] |
    And the folder "INBOX" holds messages:
      | uid  | message_id          | from_address    | to_address        | subject | date                 |
      | 8050 | <bank-8050@bank.de> | noreply@bank.de | agent@scaratec.bg | Secret  | 2026-05-12T12:00:00Z |
    And the folder "INBOX" contains no message with uid 99999
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid <target_uid>, drafts_folder "Drafts", reply_text "Anything."
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    And the IMAP folder "Drafts" is empty

    Examples:
      | target_uid |
      | 8050       |
      | 99999      |

  Scenario: Per-message ENVELOPE cap inside a BODY folder denies create_reply_draft with visibility_below_BODY
    Given policy "reply-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules                                   |
      | INBOX  | blacklist | BODY    | [{from_domain=bank.de -> cap ENVELOPE}] |
    And the folder "INBOX" holds messages:
      | uid  | message_id          | from_address    | to_address        | subject | date                 |
      | 8150 | <bank-8150@bank.de> | noreply@bank.de | agent@scaratec.bg | Capped  | 2026-05-12T12:00:00Z |
    And the message at uid 8150 has plain text body "Geheimer Kontoauszug"
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 8150, drafts_folder "Drafts", reply_text "Quote attempt"
    Then the response decision is DENY
    And the response field reason equals "visibility_below_BODY"
    And the IMAP folder "Drafts" is empty

  Scenario: Source folder with max_visibility METADATA denies create_reply_draft with visibility_below_BODY
    Given the IMAP account "gupta-scaratec" also has folder "Restricted"
    And policy "reply-policy" grants folder on account "gupta-scaratec":
      | folder     | mode      | default  | rules | draft_append |
      | Restricted | blacklist | METADATA | []    | false        |
    And the folder "Restricted" holds messages:
      | uid  | message_id          | from_address  | to_address        | subject      | date                 |
      | 8201 | <conf-8201@example> | a@example.com | agent@scaratec.bg | Confidential | 2026-05-14T12:00:00Z |
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "Restricted", uid 8201, drafts_folder "Drafts", reply_text "Will not be quoted."
    Then the response decision is DENY
    And the response field reason equals "visibility_below_BODY"
    And the IMAP folder "Drafts" is empty

  Scenario: Target folder lacking draft_append is denied with capability_missing and no legacy missing_capability field
    Given the folder "INBOX" holds messages:
      | uid  | message_id           | from_address  | to_address        | subject | date                 |
      | 8101 | <hello-8101@example> | a@example.com | agent@scaratec.bg | Hello   | 2026-05-13T12:00:00Z |
    And the message at uid 8101 has plain text body "Hi."
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 8101, drafts_folder "INBOX", reply_text "Will not be appended."
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response does not contain any field named "missing_capability"
    And the IMAP folder "INBOX" does not contain a message with subject "Re: Hello"
