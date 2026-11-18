Feature: Write visibility floors, flow monotonicity, Gmail label caps, and draft recipient guardrails

  ADR 0040 and ADR 0045 enforce four structural write and multi-label
  invariants:
    1. Write <= Read: every message-mutating tool requires a minimum
       effective read visibility on the target message (METADATA for
       mark_seen -> opaque not_found when < METADATA per ADR 0044 §2.5;
       ENVELOPE for mark_tagged/move/copy -> DENY visibility_below_ENVELOPE
       when METADATA; BODY for reply).
    2. Flow monotonicity: move, copy, and Gmail label addition are denied
       with relocation_would_escalate whenever eff_target(m) > eff_source(m).
    3. Multi-label composition on Gmail: account-wide blacklist caps hold
       across all folders, [Gmail]/All Mail cannot escalate above specific
       labels (reporting completeness="policy_may_hide", ADR 0043), and
       moving to \Trash/\Spam requires move_out on all active specific labels.
    4. Draft recipient allowlists: draft_recipients blocks unauthorized
       To, Cc, or Bcc addresses on create_draft (structured and rfc822),
       create_reply_draft, and update_draft with recipient_not_allowed.

  Covered error layers (per BDD Guidelines §4.5):
    - Write <= Read per-message floor denials (COUNT -> mark_seen, METADATA -> mark_tagged/move/copy) : 4
    - Flow monotonicity: ENVELOPE -> FULL denied on move, copy, cross-account saga, Gmail label-add   : 4
    - Flow monotonicity: FULL -> FULL and ENVELOPE -> NONE deposit allowed                            : 2
    - Gmail account-wide blacklist cap + All Mail non-escalation + Trash multi-label move_out         : 2
    - Outbound draft_recipients allowlist blocks To/Cc/Bcc exfiltration (structured, rfc822, reply, update) : 5
    Total enumerated                                                                                  : 17  covered here: 17

  Background:
    Given the server clock is pinned to "2026-11-17T08:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path  | special use |
      | INBOX        |             |
      | OffeneAblage |             |
      | Archiv       |             |
      | Drafts       | \Drafts     |
    And the IMAP account "secondary" exists with folders:
      | folder path |
      | INBOX       |
      | Offen       |
    And the server is configured with caller "triage-agent" using policy "triage-policy"
    And policy "triage-policy" grants account "gupta-scaratec"
    And policy "triage-policy" grants account "secondary"

  Scenario: mark_seen is denied with not_found when message visibility is COUNT (below METADATA)
    Given policy "triage-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | mark_seen | rules |
      | INBOX  | blacklist | COUNT   | true      | []    |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address      | subject | flags | date                 |
      | 101 | alice@example.com | me@scaratec.com | Counted | []    | 2026-11-16T09:00:00Z |
    When triage-agent calls mark_seen with account "gupta-scaratec", folder "INBOX", uid 101, seen true
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    And the IMAP message at "INBOX" uid 101 does NOT have flag "\Seen"

  Scenario Outline: <tool> is denied with visibility_below_ENVELOPE when message visibility is METADATA and leaves mail unmutated
    Given policy "triage-policy" grants the following folder policies:
      | account        | folder | mode      | default  | mark_tagged | move_out | copy_out | accept_incoming | rules |
      | gupta-scaratec | INBOX  | blacklist | METADATA | true        | true     | true     | false           | []    |
      | gupta-scaratec | Archiv | whitelist | NONE     | false       | false    | false    | true            | []    |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address      | subject   | flags | date                 |
      | 110 | alice@example.com | me@scaratec.com | Meta-only | []    | 2026-11-16T09:00:00Z |
    When triage-agent calls <call>
    Then the response decision is DENY
    And the response field reason equals "visibility_below_ENVELOPE"
    And the IMAP message at "INBOX" uid 110 does NOT have keyword "$Tagged"
    And the IMAP folder "INBOX" still contains uid 110
    And the IMAP folder "Archiv" is empty

    Examples:
      | tool        | call                                                                                                                                   |
      | mark_tagged | mark_tagged with account "gupta-scaratec", folder "INBOX", uid 110, tags ["$Tagged"], mode "add"                                       |
      | move        | move with source {"account": "gupta-scaratec", "folder": "INBOX", "uid": 110}, target {"account": "gupta-scaratec", "folder": "Archiv"} |
      | copy        | copy with source {"account": "gupta-scaratec", "folder": "INBOX", "uid": 110}, target {"account": "gupta-scaratec", "folder": "Archiv"} |

  Scenario Outline: Relocating an ENVELOPE-visible message into a FULL-visible target via <tool> is denied with relocation_would_escalate
    Given policy "triage-policy" grants the following folder policies:
      | account        | folder       | mode      | default  | move_out | copy_out | accept_incoming | rules |
      | gupta-scaratec | INBOX        | blacklist | ENVELOPE | true     | true     | false           | []    |
      | gupta-scaratec | OffeneAblage | blacklist | FULL     | false    | false    | true            | []    |
      | secondary      | Offen        | blacklist | FULL     | false    | false    | true            | []    |
    And the folder "INBOX" holds messages:
      | uid | message_id             | from_address      | to_address      | subject     | date                 |
      | 201 | <esc-201@scaratec.com> | alice@example.com | me@scaratec.com | Geheimbrief | 2026-11-16T09:00:00Z |
    And the message at uid 201 has plain text body "Streng geheimer Volltext"
    When triage-agent calls <call>
    Then the response decision is DENY
    And the response field reason equals "relocation_would_escalate"
    And the IMAP folder "INBOX" still contains uid 201
    And a direct IMAP SEARCH on "<target_check>" for message-id "<esc-201@scaratec.com>" returns zero results

    Examples:
      | tool            | call                                                                                                                                         | target_check                |
      | intra_move      | move with source {"account": "gupta-scaratec", "folder": "INBOX", "uid": 201}, target {"account": "gupta-scaratec", "folder": "OffeneAblage"} | gupta-scaratec:OffeneAblage |
      | intra_copy      | copy with source {"account": "gupta-scaratec", "folder": "INBOX", "uid": 201}, target {"account": "gupta-scaratec", "folder": "OffeneAblage"} | gupta-scaratec:OffeneAblage |
      | cross_saga_move | move with source {"account": "gupta-scaratec", "folder": "INBOX", "uid": 201}, target {"account": "secondary", "folder": "Offen"}             | secondary:Offen             |

  Scenario Outline: Monotonic transfers succeed when target visibility does not exceed source visibility (<case>)
    Given policy "triage-policy" grants the following folder policies:
      | account        | folder       | mode      | default | move_out | copy_out | accept_incoming | rules                                    |
      | gupta-scaratec | INBOX        | whitelist | NONE    | true     | true     | false           | [{from_domain=hornbach.de -> <src_vis>}] |
      | gupta-scaratec | OffeneAblage | blacklist | FULL    | false    | false    | true            | []                                       |
      | gupta-scaratec | Archiv       | whitelist | NONE    | false    | false    | true            | []                                       |
    And the folder "INBOX" holds messages:
      | uid | message_id           | from_address         | to_address      | subject      | date                 |
      | 210 | <ok-210@hornbach.de> | rechnung@hornbach.de | me@scaratec.com | Rechnung 210 | 2026-11-16T09:00:00Z |
    When triage-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX", "uid": 210}, target {"account": "gupta-scaratec", "folder": "<dst>"}
    Then the response decision is ALLOW
    And the IMAP folder "INBOX" does not contain uid 210
    And a direct IMAP SEARCH on "gupta-scaratec:<dst>" for message-id "<ok-210@hornbach.de>" returns exactly one result

    Examples:
      | case               | src_vis  | dst          |
      | FULL into FULL     | FULL     | OffeneAblage |
      | ENVELOPE into NONE | ENVELOPE | Archiv       |

  Scenario: Gmail mark_tagged cannot add a label whose folder policy would escalate visibility
    Given the IMAP account "g-work" exists with provider "google" and folders:
      | folder path      |
      | INBOX            |
      | OffeneRechnungen |
      | [Gmail]/All Mail |
    And policy "triage-policy" grants account "g-work"
    And policy "triage-policy" grants folder on account "g-work":
      | folder           | mode      | default  | mark_tagged | accept_incoming | rules |
      | INBOX            | blacklist | ENVELOPE | true        | false           | []    |
      | OffeneRechnungen | blacklist | FULL     | false       | true            | []    |
    And a Gmail message on "g-work" has:
      | x_gm_msgid | message_id          | from_address      | to_address  | subject     | date                 |
      | 50001      | <gm-50001@work.org> | alice@example.com | me@work.org | Vertraulich | 2026-11-16T09:00:00Z |
    And the Gmail message 50001 on "g-work" carries labels ["INBOX"] with UID 301 in "INBOX" and UID 50001 in "[Gmail]/All Mail"
    When triage-agent calls mark_tagged with account "g-work", folder "INBOX", uid 301, tags ["OffeneRechnungen"], mode "add"
    Then the response decision is DENY
    And the response field reason equals "relocation_would_escalate"
    And a direct IMAP SEARCH on "g-work:OffeneRechnungen" for X-GM-MSGID 50001 returns zero results

  Scenario: Gmail account-wide blacklist cap overrides INBOX grant and All Mail respects specific-label precedence with positive control
    Given the IMAP account "g-work" exists with provider "google" and folders:
      | folder path      |
      | INBOX            |
      | Banking          |
      | [Gmail]/All Mail |
    And policy "triage-policy" grants account "g-work"
    And policy "triage-policy" grants folder on account "g-work":
      | folder           | mode      | default | rules                                                               |
      | INBOX            | whitelist | NONE    | [{from_domain=hornbach.de -> FULL}, {from_domain=bank.de -> FULL}]  |
      | Banking          | blacklist | FULL    | [{from_domain=bank.de -> cap NONE}]                                 |
      | [Gmail]/All Mail | blacklist | FULL    | []                                                                  |
    And a Gmail message on "g-work" has:
      | x_gm_msgid | message_id          | from_address         | to_address  | subject      | date                 |
      | 50010      | <gm-50010@work.org> | privat@freunde.de    | me@work.org | Privatbrief  | 2026-11-16T09:00:00Z |
    And the Gmail message 50010 on "g-work" carries labels ["INBOX"] with UID 310 in "INBOX" and UID 50010 in "[Gmail]/All Mail"
    And a Gmail message on "g-work" has:
      | x_gm_msgid | message_id          | from_address         | to_address  | subject      | date                 |
      | 50011      | <gm-50011@work.org> | noreply@bank.de      | me@work.org | Kontoauszug  | 2026-11-16T09:01:00Z |
    And the Gmail message 50011 on "g-work" carries labels ["INBOX"] with UID 311 in "INBOX" and UID 50011 in "[Gmail]/All Mail"
    And a Gmail message on "g-work" has:
      | x_gm_msgid | message_id          | from_address         | to_address  | subject      | date                 |
      | 50012      | <gm-50012@work.org> | rechnung@hornbach.de | me@work.org | Baumarkt     | 2026-11-16T09:02:00Z |
    And the Gmail message 50012 on "g-work" carries labels ["INBOX"] with UID 312 in "INBOX" and UID 50012 in "[Gmail]/All Mail"
    When triage-agent calls search with account "g-work", folder "[Gmail]/All Mail", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the response field uids equals [50012]
    And the response field completeness equals "policy_may_hide"
    When triage-agent calls fetch_envelope with account "g-work", folder "[Gmail]/All Mail", uid 50012
    Then the response decision is ALLOW
    And the response field visibility_applied equals "FULL"
    When triage-agent calls fetch_body with account "g-work", folder "INBOX", uid 311
    Then the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    When triage-agent calls fetch_body with account "g-work", folder "[Gmail]/All Mail", uid 50010
    Then the response field result equals "ERROR"
    And the response field error.type equals "not_found"

  Scenario: Moving a Gmail message to [Gmail]/Trash is denied when another label on the message forbids move_out
    Given the IMAP account "g-work" exists with provider "google" and folders:
      | folder path      |
      | INBOX            |
      | Steuerarchiv     |
      | [Gmail]/All Mail |
      | [Gmail]/Trash    |
    And policy "triage-policy" grants account "g-work"
    And policy "triage-policy" grants folder on account "g-work":
      | folder        | mode      | default  | move_out | accept_incoming | rules |
      | INBOX         | blacklist | FULL     | true     | false           | []    |
      | Steuerarchiv  | blacklist | FULL     | false    | true            | []    |
      | [Gmail]/Trash | blacklist | METADATA | false    | true            | []    |
    And a Gmail message on "g-work" has:
      | x_gm_msgid | message_id          | from_address         | to_address  | subject    | date                 |
      | 50020      | <gm-50020@work.org> | rechnung@hornbach.de | me@work.org | Beleg GoBD | 2026-11-16T09:00:00Z |
    And the Gmail message 50020 on "g-work" carries labels ["INBOX", "Steuerarchiv"] with UID 320 in "INBOX", UID 420 in "Steuerarchiv", and UID 50020 in "[Gmail]/All Mail"
    When triage-agent calls move with source {"account": "g-work", "folder": "INBOX", "uid": 320}, target {"account": "g-work", "folder": "[Gmail]/Trash"}
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And a direct IMAP SEARCH on "g-work:INBOX" for X-GM-MSGID 50020 returns exactly one result
    And a direct IMAP SEARCH on "g-work:Steuerarchiv" for X-GM-MSGID 50020 returns exactly one result
    And a direct IMAP SEARCH on "g-work:[Gmail]/Trash" for X-GM-MSGID 50020 returns zero results

  Scenario Outline: draft_recipients allowlist blocks unauthorized recipients in <operation>
    Given the account "gupta-scaratec" has identity "me@scaratec.com"
    And policy "triage-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | draft_append | draft_edit | draft_recipient_domains       | rules |
      | INBOX  | blacklist | FULL    | false        | false      |                               | []    |
      | Drafts | blacklist | FULL    | true         | true       | ["scaratec.com","allianz.de"] | []    |
    And the folder "INBOX" holds messages:
      | uid | message_id             | from_address      | to_address      | subject  | date                 |
      | 501 | <src-501@evil.example> | lure@evil.example | me@scaratec.com | Rueckruf | 2026-11-16T09:00:00Z |
    And the message at uid 501 has plain text body "Bitte antworten Sie."
    And triage-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["service@allianz.de"], "subject": "Erlaubter Entwurf", "body_text": "Anfang"}
      """
    When triage-agent calls <unauthorized_call>
    Then the response decision is DENY
    And the response field reason equals "recipient_not_allowed"
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP message in folder "Drafts" with subject "Erlaubter Entwurf" has no header "Bcc"

    Examples:
      | operation            | unauthorized_call                                                                                                                                                    |
      | create_draft_bcc     | create_draft with account "gupta-scaratec", folder "Drafts", fields {"to": ["service@allianz.de"], "bcc": ["drop@evil.example"], "subject": "Exfil", "body_text": "Leak"} |
      | create_draft_cc      | create_draft with account "gupta-scaratec", folder "Drafts", fields {"to": ["service@allianz.de"], "cc": ["drop@evil.example"], "subject": "Exfil", "body_text": "Leak"}  |
      | create_draft_rfc822  | create_draft with account "gupta-scaratec", folder "Drafts", rfc822 "From: me@scaratec.com\r\nTo: service@allianz.de\r\nBcc: drop@evil.example\r\nSubject: Exfil\r\n\r\nLeak" |
      | create_reply_draft   | create_reply_draft with account "gupta-scaratec", source_folder "INBOX", uid 501, drafts_folder "Drafts", reply_text "Hier die Daten"                                |
      | update_draft_bcc     | update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes {"bcc": ["drop@evil.example"]}                          |
