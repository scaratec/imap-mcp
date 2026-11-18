Feature: Drafts carry a server-generated UUIDv4 handle and HMAC-signed provenance

  ADR 0042, ADR 0043, and ADR 0044 bind every server-created draft via:
    X-SC-Draft-Id:   <uuid4>
    X-SC-Draft-Rev:  <positive integer>
    X-SC-Provenance: v1:<64 lowercase hex HMAC-SHA256>
  Caller-supplied X-SC-* headers are stripped before injection. Foreign
  drafts in the folder appear in list_drafts with editable=false, and
  list_drafts at METADATA visibility redacts subject to null.

  Covered error layers (per BDD Guidelines §4.5):
    - create_draft returns UUIDv4 draft_id + revision 1            : 1
    - Caller-supplied X-SC-* headers stripped; unknown -> not_found: 1
    - Wire verification of X-SC-Draft-Id, X-SC-Draft-Rev, X-SC-Provenance : 1
    - \Draft and \Seen flags set on append                         : 1
    - create_reply_draft injects handle + provenance on the wire   : 1
    - get_draft round-trips composed fields under the handle       : 1
    - list_drafts marks foreign drafts editable=false              : 1
    - list_drafts at METADATA visibility redacts subject to null   : 1
    Total enumerated                                               : 8   covered: 8

  Background:
    Given the server clock is pinned to "2026-09-18T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | true         | true       |

  Scenario: create_draft returns a UUIDv4 handle and revision 1
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
    And the response field draft_id matches "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    And the response field revision equals 1

  Scenario: Caller-supplied X-SC-* headers in rfc822 are stripped and replaced; querying the smuggled ID returns not_found
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Handle of my own choosing
      X-SC-Draft-Id: 77777777-7777-4777-8777-777777777777
      X-SC-Draft-Rev: 9
      X-SC-Provenance: v1:0000000000000000000000000000000000000000000000000000000000000000
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      The caller named its own draft.
      """
    Then the response decision is ALLOW
    And the response field draft_id matches "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    And the response field draft_id does not equal "77777777-7777-4777-8777-777777777777"
    And the response field revision equals 1
    And the IMAP message in folder "Drafts" with subject "Handle of my own choosing" carries 1 header "X-SC-Draft-Id", 1 header "X-SC-Draft-Rev", and 1 header "X-SC-Provenance"
    And the header "X-SC-Draft-Id" of the IMAP message in folder "Drafts" with subject "Handle of my own choosing" is not "77777777-7777-4777-8777-777777777777"
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", draft_id "77777777-7777-4777-8777-777777777777"
    Then the response field result equals "ERROR"
    And the response field error.type equals "not_found"

  Scenario: X-SC-Draft-Id, X-SC-Draft-Rev, and X-SC-Provenance are stored on the IMAP message
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
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value              |
      | Subject        | Handle on the wire |
      | X-SC-Draft-Rev | 1                  |
    And the header "X-SC-Provenance" of the IMAP message in folder "Drafts" with subject "Handle on the wire" matches "^v1:[0-9a-f]{64}$"

  Scenario: A created draft carries \Draft and \Seen flags on IMAP
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

  Scenario: create_reply_draft injects handle and signed X-SC-Provenance on the IMAP message
    Given the account "gupta-scaratec" has identity "draft-agent@gupta-scaratec.com"
    And policy "draft-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules | draft_append | draft_edit |
      | INBOX  | blacklist | FULL    | []    | false        | false      |
    And the folder "INBOX" holds messages:
      | uid | message_id            | from_address             | to_address                     | subject        | date                 |
      | 401 | <src-401@example.com> | counterparty@example.com | draft-agent@gupta-scaratec.com | Your quotation | 2026-09-17T09:00:00Z |
    And the message at uid 401 has plain text body "Please confirm week 14."
    When draft-agent calls create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 401, drafts_folder "Drafts", reply_text "Week 14 works for us."
    Then the response decision is ALLOW
    And the response field draft_id matches "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    And the response field revision equals 1
    And the IMAP message in folder "Drafts" with subject "Re: Your quotation" carries 1 header "X-SC-Draft-Id", 1 header "X-SC-Draft-Rev", and 1 header "X-SC-Provenance"

  Scenario: get_draft round-trips the composed fields under the handle
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

  Scenario: A draft written by an external mail client is listed with editable=false
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
      | field    | value                |
      | subject  | Written by the agent |
      | revision | 1                    |
      | editable | true                 |
    And the response field drafts has exactly one entry matching:
      | field    | value                  |
      | subject  | Written in Thunderbird |
      | editable | false                  |

  Scenario: list_drafts at METADATA visibility redacts subject to null
    Given policy "draft-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | rules | draft_append | draft_edit |
      | Drafts | blacklist | METADATA | []    | true         | true       |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["counterparty@example.com"], "subject": "Confidential Draft Subject", "body_text": "Secret"}
      """
    When draft-agent calls list_drafts with account "gupta-scaratec", folder "Drafts"
    Then the response decision is ALLOW
    And the response field drafts has length 1
    And draft entry 0 has field "subject" equal to null
    And draft entry 0 has field "editable" equal to true
    And the JSON response does NOT contain the literal strings:
      | forbidden_string           |
      | Confidential Draft Subject |
