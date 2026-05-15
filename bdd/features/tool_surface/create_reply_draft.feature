Feature: create_reply_draft builds a deterministic top-posted reply

  The create_reply_draft tool reads an original message from
  <source_folder>/<uid>, constructs a text/plain top-posted reply with the
  original quoted line-by-line, derives reply-all recipients from the source
  headers (excluding the account's own identity), and APPENDs the result as
  a draft to <drafts_folder>. The agent provides only the account, source
  folder, source UID, drafts folder, and the reply_text. Subject prefix,
  threading headers (In-Reply-To, References), recipient derivation, the
  attribution line, and quoting are produced by the server, never by the
  agent.

  Tool signature:
    create_reply_draft(account, source_folder, uid, drafts_folder, reply_text)

  Quoting contract (server-produced, agent cannot override):
    1. <reply_text>
    2. blank line
    3. "On YYYY-MM-DD HH:MM, <Display> <<email>> wrote:"
       - date from the source Date header, rendered without timezone
         conversion (clock time of the Date header is preserved verbatim)
       - if Date header is missing, the attribution omits the date prefix:
         "<Display> <<email>> wrote:"
       - if From has no display-name, attribution shows only "<email>"
    4. each line of the source plain body prefixed with "> "
       (lines that already begin with ">" become ">>" etc.; no signature
       stripping, no nested-quote collapsing)
    5. for HTML-only sources, the stripped text body is quoted (per the
       fetch_body HTML fallback contract, see fetch_body_html_fallback.feature)

  Recipient contract (reply-all):
    - To = the source Reply-To header if present, else the source From
    - Cc = (source To union source Cc) minus the account's own identity
           (case-insensitive address compare on the addr-spec)
    - From = the account's own identity
    - Manipulation of To/Cc/Bcc and Subject after creation is the job of
      separate tools and out of scope here.

  Subject contract:
    - Server prepends "Re: " unless the source Subject already begins with
      "Re:" (case-insensitive, ignoring leading whitespace).
    - Locale-specific prefixes (AW:, WG:, Rep:, Fwd:) are NOT recognised
      and DO get a "Re: " prepended.

  Threading contract:
    - In-Reply-To = source Message-ID
    - References  = source References (if any) followed by source Message-ID
    - If the source has no Message-ID header, the call is denied with
      reason=missing_message_id (no draft is created).

  Covered error layers (per BDD Guidelines §4.5):
    Eingabevalidierung:
      - source uid_not_found                      : 1
      - drafts_folder lacks draft_append          : 1
      - source folder visibility below BODY       : 1
      - empty reply_text                          : 1
    Original-Message-Validierung:
      - missing Message-ID                        : 1
      - missing Date (tolerant)                   : 1
      - missing From display-name (tolerant)      : 1
    Reply-Konstruktion:
      - Reply-To preferred over From for To       : 1
      - case-insensitive self dedup in Cc         : 1
    Subject-Prefix:
      - "Re: " prepended / not duplicated         : 4 (Scenario Outline)
    Body / Quoting:
      - Cyrillic/UTF-8 preservation (happy path)  : 1
      - HTML-only source quotes stripped text     : 1
      - already-quoted lines get an extra ">"     : 1
    Persistence (covered inside happy path):
      - draft discoverable via list_messages      : (in #1)
      - In-Reply-To equals source Message-ID      : (in #1)
      - References chain extended                 : (in #1)
    Total enumerated                              : 15   covered: 15

  Background:
    Given the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
      | Drafts      |
    And the account "gupta-scaratec" has identity "agent@scaratec.bg"
    And the server is configured with caller "reply-agent" using policy "reply-policy"
    And policy "reply-policy" grants account "gupta-scaratec"
    And policy "reply-policy" grants the following folder policies:
      | folder | mode      | default | rules | draft_append |
      | INBOX  | blacklist | FULL    | []    | false        |
      | Drafts | blacklist | FULL    | []    | true         |

  # -------------------------------------------------------------------
  # Happy path: real-world Cyrillic example, validated via a second channel
  # (list_messages + fetch_body on the resulting draft).
  # Covers: attribution, top-posting, "Re: " prepend, In-Reply-To,
  # References, reply-all with self-dedup (incl. mixed-case duplicate),
  # draft discoverability, UTF-8 preservation.
  # -------------------------------------------------------------------
  Scenario: reply-all to a Cyrillic message produces a discoverable draft with correct headers and quoted body
    Given the folder "INBOX" holds a message with:
      | uid  | from                          | reply_to                | to_header                                             | cc_header             | subject     | date                            | message_id                          | references_header         |
      | 1001 | "Иван Петров" <ivan@example.com> | ivan.replies@example.com | agent@scaratec.bg, ops@scaratec.bg, AGENT@SCARATEC.BG | partner@third.example | Здравейте   | Fri, 15 May 2026 10:56:00 +0300 | <20260515-1056-ivan@example.com>    | <thread-root@example.com> |
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
    And the response field error_type equals null

    # Persistence-Validierung (Spec-Audit Pruefung 1, §13.2):
    # the draft must be visible in the Drafts folder via a separate read.
    When reply-agent calls list_messages with account "gupta-scaratec", folder "Drafts"
    Then the response field matched_total equals 1
    And the response field messages contains exactly one entry with:
      | field          | value                                                       |
      | from           | agent@scaratec.bg                                           |
      | subject        | Re: Здравейте                                               |
    And the message in folder "Drafts" with subject "Re: Здравейте" has In-Reply-To header equal to "<20260515-1056-ivan@example.com>"
    And the message in folder "Drafts" with subject "Re: Здравейте" has To header equal to "ivan.replies@example.com"
    And the message in folder "Drafts" with subject "Re: Здравейте" has Cc header NOT containing "agent@scaratec.bg"
    And the message in folder "Drafts" with subject "Re: Здравейте" has Cc header NOT containing "AGENT@SCARATEC.BG"

    # Body assertion: top-posted reply, blank line, attribution, quoted body.
    When reply-agent calls fetch_body on the only message in folder "Drafts"
    Then the response text_body equals the following document:
      """
      Благодаря.

      On 2026-05-15 10:56, Иван Петров <ivan@example.com> wrote:
      > Здравейте,
      >
      > Това е примерно съобщение за UTF-8 тест.
      """

  # -------------------------------------------------------------------
  # Subject-Prefix logic: Scenario Outline forces varianz (§2.3) so the
  # rule cannot be hardcoded against a single subject string.
  # -------------------------------------------------------------------
  Scenario Outline: "Re: " is prepended only when not already present (case-insensitive)
    Given the folder "INBOX" holds a message with:
      | uid    | from                | subject     | date                            | message_id                |
      | <uid>  | sender@example.com  | <subject>   | Mon, 04 May 2026 09:00:00 +0000 | <subj-<uid>@example.com>  |
    And the message at uid <uid> has plain text body:
      """
      Original body text.
      """
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid <uid>, drafts_folder "Drafts", reply_text:
      """
      Acknowledged.
      """
    Then the response decision is ALLOW
    And the message in folder "Drafts" with In-Reply-To "<subj-<uid>@example.com>" has subject "<expected_subject>"

    Examples:
      | uid  | subject              | expected_subject       |
      | 2001 | Тестово съобщение     | Re: Тестово съобщение  |
      | 2002 | Re: Тестово съобщение | Re: Тестово съобщение  |
      | 2003 | re: lowercase reply  | re: lowercase reply    |
      | 2004 | AW: deutscher Reply  | Re: AW: deutscher Reply|

  # -------------------------------------------------------------------
  # Reply-To wins over From for the To-field of the draft.
  # -------------------------------------------------------------------
  Scenario: when the source has a Reply-To, the draft's To equals Reply-To, not From
    Given the folder "INBOX" holds a message with:
      | uid  | from                                  | reply_to              | to_header           | subject       | date                            | message_id                |
      | 3001 | "Bot Sender" <noreply@example.com>    | desk@example.com      | agent@scaratec.bg   | Ticket #42    | Tue, 05 May 2026 12:00:00 +0000 | <ticket-42@example.com>   |
    And the message at uid 3001 has plain text body:
      """
      Your ticket has been received.
      """
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 3001, drafts_folder "Drafts", reply_text:
      """
      Thanks.
      """
    Then the response decision is ALLOW
    And the message in folder "Drafts" with subject "Re: Ticket #42" has To header equal to "desk@example.com"
    And the message in folder "Drafts" with subject "Re: Ticket #42" has To header NOT containing "noreply@example.com"

  # -------------------------------------------------------------------
  # Missing Message-ID: hard error per the threading contract.
  # -------------------------------------------------------------------
  Scenario: a source message without a Message-ID header is rejected and produces no draft
    Given the folder "INBOX" holds a message with:
      | uid  | from                | subject              | date                            | message_id |
      | 4001 | sender@example.com  | No Message-ID here   | Wed, 06 May 2026 08:00:00 +0000 |            |
    And the message at uid 4001 has plain text body:
      """
      Body.
      """
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 4001, drafts_folder "Drafts", reply_text:
      """
      Will not be sent.
      """
    Then the response decision is DENY
    And the response field reason equals "missing_message_id"
    When reply-agent calls list_messages with account "gupta-scaratec", folder "Drafts"
    Then the response field matched_total equals 0

  # -------------------------------------------------------------------
  # Missing Date header: tolerant. Attribution omits the date prefix.
  # -------------------------------------------------------------------
  Scenario: a source message without a Date header still yields a draft with a dateless attribution
    Given the folder "INBOX" holds a message with:
      | uid  | from                              | subject       | date | message_id                |
      | 5001 | "Anna" <anna@example.com>         | Quick note    |      | <quick-note@example.com>  |
    And the message at uid 5001 has plain text body:
      """
      Hello.
      """
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 5001, drafts_folder "Drafts", reply_text:
      """
      Hi back.
      """
    Then the response decision is ALLOW
    When reply-agent calls fetch_body on the only message in folder "Drafts"
    Then the response text_body equals the following document:
      """
      Hi back.

      Anna <anna@example.com> wrote:
      > Hello.
      """

  # -------------------------------------------------------------------
  # Missing From display-name: tolerant. Attribution shows only "<email>".
  # -------------------------------------------------------------------
  Scenario: a source From without display-name yields an attribution with only the bare address
    Given the folder "INBOX" holds a message with:
      | uid  | from               | subject  | date                            | message_id              |
      | 5101 | bare@example.com   | Bare     | Thu, 07 May 2026 09:00:00 +0000 | <bare@example.com>      |
    And the message at uid 5101 has plain text body:
      """
      One line.
      """
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 5101, drafts_folder "Drafts", reply_text:
      """
      Reply.
      """
    Then the response decision is ALLOW
    When reply-agent calls fetch_body on the only message in folder "Drafts"
    Then the response text_body equals the following document:
      """
      Reply.

      On 2026-05-07 09:00, <bare@example.com> wrote:
      > One line.
      """

  # -------------------------------------------------------------------
  # HTML-only source: server quotes the stripped text body
  # (per fetch_body HTML-fallback contract, commit dd92826).
  # -------------------------------------------------------------------
  Scenario: HTML-only source is quoted using the stripped text body
    Given the folder "INBOX" holds a message with:
      | uid  | from                                 | subject       | date                            | message_id              |
      | 6001 | "Marketing" <marketing@example.com>  | Newsletter    | Fri, 08 May 2026 11:00:00 +0000 | <news-1@example.com>    |
    And the message at uid 6001 has html body "<p>Sehr geehrte Damen und Herren,</p> <p>willkommen.</p>"
    And the message at uid 6001 has no plain text body
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 6001, drafts_folder "Drafts", reply_text:
      """
      Danke.
      """
    Then the response decision is ALLOW
    When reply-agent calls fetch_body on the only message in folder "Drafts"
    Then the response field text_body contains "Danke."
    And the response field text_body contains "> Sehr geehrte Damen und Herren, willkommen."

  # -------------------------------------------------------------------
  # Already-quoted lines in the source: nesting is preserved by
  # prepending an extra ">".
  # -------------------------------------------------------------------
  Scenario: lines already starting with ">" become ">>" in the quoted block
    Given the folder "INBOX" holds a message with:
      | uid  | from               | subject  | date                            | message_id          |
      | 7001 | b@example.com      | Re: Re:  | Mon, 11 May 2026 10:00:00 +0000 | <nested@example.com>|
    And the message at uid 7001 has plain text body:
      """
      My answer.

      > Earlier quoted line.
      >> Even older line.
      """
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 7001, drafts_folder "Drafts", reply_text:
      """
      Closing the thread.
      """
    Then the response decision is ALLOW
    When reply-agent calls fetch_body on the only message in folder "Drafts"
    Then the response text_body equals the following document:
      """
      Closing the thread.

      On 2026-05-11 10:00, <b@example.com> wrote:
      > My answer.
      >
      >> Earlier quoted line.
      >>> Even older line.
      """

  # -------------------------------------------------------------------
  # Eingabevalidierung: empty reply_text rejected; original untouched.
  # -------------------------------------------------------------------
  Scenario: empty reply_text is rejected with validation_failed and produces no draft
    Given the folder "INBOX" holds a message with:
      | uid  | from               | subject  | date                            | message_id            |
      | 8001 | a@example.com      | Hello    | Tue, 12 May 2026 12:00:00 +0000 | <hello-8001@example>  |
    And the message at uid 8001 has plain text body:
      """
      Hi.
      """
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 8001, drafts_folder "Drafts", reply_text ""
    Then the response decision is DENY
    And the response field reason equals "validation_failed"
    And the response field error_type equals "empty_reply_text"
    When reply-agent calls list_messages with account "gupta-scaratec", folder "Drafts"
    Then the response field matched_total equals 0

  # -------------------------------------------------------------------
  # Eingabevalidierung: non-existent source UID.
  # -------------------------------------------------------------------
  Scenario: non-existent source UID returns ERROR uid_not_found and produces no draft
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 99999, drafts_folder "Drafts", reply_text:
      """
      Anything.
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error_type equals "uid_not_found"
    When reply-agent calls list_messages with account "gupta-scaratec", folder "Drafts"
    Then the response field matched_total equals 0

  # -------------------------------------------------------------------
  # Eingabevalidierung: drafts_folder lacks draft_append capability.
  # -------------------------------------------------------------------
  Scenario: drafts_folder without draft_append is denied with capability_missing
    Given the folder "INBOX" holds a message with:
      | uid  | from               | subject  | date                            | message_id              |
      | 8101 | a@example.com      | Hello    | Wed, 13 May 2026 12:00:00 +0000 | <hello-8101@example>    |
    And the message at uid 8101 has plain text body:
      """
      Hi.
      """
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 8101, drafts_folder "INBOX", reply_text:
      """
      Will not be appended here.
      """
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "draft_append"
    And the IMAP folder "INBOX" does not contain a message with subject "Re: Hello"

  # -------------------------------------------------------------------
  # Eingabevalidierung: source folder visibility below BODY blocks the
  # read needed for quoting; the call is denied before any draft is built.
  # -------------------------------------------------------------------
  Scenario: source folder with visibility below BODY is denied and no draft is created
    Given the IMAP account "gupta-scaratec" also exists with folder "Restricted"
    And policy "reply-policy" grants folder:
      | folder     | mode      | default  | rules | draft_append |
      | Restricted | blacklist | METADATA | []    | false        |
    And the folder "Restricted" holds a message with:
      | uid  | from               | subject     | date                            | message_id            |
      | 8201 | a@example.com      | Confidential| Thu, 14 May 2026 12:00:00 +0000 | <conf-8201@example>   |
    And the message at uid 8201 has plain text body:
      """
      Secret.
      """
    When reply-agent calls create_reply_draft with account "gupta-scaratec", source_folder "Restricted", uid 8201, drafts_folder "Drafts", reply_text:
      """
      Will not be quoted.
      """
    Then the response decision is DENY
    And the response field reason equals "visibility_below_BODY"
    When reply-agent calls list_messages with account "gupta-scaratec", folder "Drafts"
    Then the response field matched_total equals 0
