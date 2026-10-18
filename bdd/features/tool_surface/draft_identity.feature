Feature: Drafts carry a stable handle that survives revision

  A draft created through this server is addressable by a handle that
  does not change when the underlying message is replaced. The server
  injects the handle into the message itself (ADR 0030):

    X-SC-Draft-Id   an opaque identifier, stable for the life of the draft
    X-SC-Draft-Rev  an integer, 1 on creation, +1 on every revision

  The caller never sees a UID. Resolution is a header search inside the
  one folder the caller named.

  A message in the same folder that does not carry X-SC-Draft-Id was
  written by some other client. It is listed, but it is not editable.

  Covered error layers (per BDD Guidelines §4.5):
    - create_draft returns handle + revision 1              : 1
    - the handle is a uuid4, and the caller cannot choose it: 1
    - IMAP second channel: handle headers really on the wire: 1
    - \Draft flag set on append                             : 1
    - create_reply_draft returns a handle too               : 1
    - get_draft round-trips the composed fields             : 1
    - list_drafts marks foreign drafts as not editable      : 1
    Total enumerated                                        : 7   covered: 7

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "Drafts"
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | true         | true       |

  Scenario: create_draft returns a handle and the first revision
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Quotation for the spring order
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field draft_id is a non-empty string
    And the response field draft_id matches the regex "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    And the response field revision equals 1

  # ADR 0030: "The identifier is generated server-side. A caller cannot
  # choose it." create_draft takes raw RFC-822 bytes, so the payload is
  # the obvious place to try.
  Scenario: a handle the caller put in the payload is discarded
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Handle of my own choosing
      X-SC-Draft-Id: 77777777-7777-4777-8777-777777777777
      X-SC-Draft-Rev: 9
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      The caller named its own draft.
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field draft_id matches the regex "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    And the response field draft_id does not equal "77777777-7777-4777-8777-777777777777"
    And the response field revision equals 1
    And the IMAP message in folder "Drafts" with subject "Handle of my own choosing" carries exactly 1 header "X-SC-Draft-Id"
    And the IMAP message in folder "Drafts" with subject "Handle of my own choosing" carries exactly 1 header "X-SC-Draft-Rev"
    And the header "X-SC-Draft-Id" of the IMAP message in folder "Drafts" with subject "Handle of my own choosing" is not "77777777-7777-4777-8777-777777777777"
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                     |
      | Subject        | Handle of my own choosing |
      | X-SC-Draft-Rev | 1                         |
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", draft_id "77777777-7777-4777-8777-777777777777"
    Then the response field result equals "ERROR"
    And the response field error.type equals "draft_not_found"

  Scenario: the handle is carried by the message, not by the server
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Handle on the wire
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body of the draft.
      """
    Then the response decision is ALLOW
    And the response field revision equals 1
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                |
      | Subject        | Handle on the wire   |
      | X-SC-Draft-Rev | 1                    |

  Scenario: a created draft is flagged as a draft
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Flagged as draft
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body of the draft.
      """
    Then the response decision is ALLOW
    And the IMAP message in folder "Drafts" with subject "Flagged as draft" has flags:
      | flag   |
      | \Draft |
      | \Seen  |

  Scenario: create_reply_draft returns a handle as well
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the account "gupta-scaratec" has identity "draft-agent@gupta-scaratec.com"
    And policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | INBOX  | blacklist | FULL    | []    | false        | false      |
    And the folder "INBOX" holds a message with:
      | uid | from                    | subject         | message_id            |
      | 401 | counterparty@example.com | Your quotation | <src-401@example.com> |
    When draft-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 401, drafts_folder "Drafts", reply_text "Week 14 works for us."
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field draft_id is a non-empty string
    And the response field revision equals 1

  Scenario: get_draft returns the composed fields under the handle
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Cc: office@example.com
      Subject: Round trip
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    Then the response decision is ALLOW
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response decision is ALLOW
    And the response field revision equals 1
    And the response field subject equals "Round trip"
    And the response field body_text equals "We can deliver in week 14."
    And the response field to contains exactly:
      | address                  |
      | counterparty@example.com |
    And the response field cc contains exactly:
      | address            |
      | office@example.com |

  Scenario: a draft written by another client is listed but not editable
    Given the folder "Drafts" holds a verbatim message:
      """
      From: anika@example.com
      To: counterparty@example.com
      Subject: Written in Thunderbird
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Composed outside this server.
      """
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Written by the agent
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Composed by this server.
      """
    Then the response decision is ALLOW
    When draft-agent calls list_drafts with account "gupta-scaratec", folder "Drafts"
    Then the response decision is ALLOW
    And the response field drafts has length 2
    And the response field drafts has exactly one entry matching:
      | field    | value                 |
      | subject  | Written by the agent  |
      | revision | 1                     |
      | editable | true                  |
    And the response field drafts has exactly one entry matching:
      | field    | value                 |
      | subject  | Written in Thunderbird |
      | editable | false                 |
