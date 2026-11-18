Feature: Cryptographic provenance for own sent drafts and draft-handle tampering resistance

  ADR 0042 binds every server-created draft to (caller_id, account_id,
  draft_id, revision, canonical_digest) via an HMAC-signed X-SC-Provenance
  header and the WAL provenance registry:
    1. Sent mail originating from the caller's own draft matches the
       predicate provenance="own" in Sent Mail without opening Sent Mail
       to arbitrary recipients.
    2. An external client that edits a draft's text while keeping old
       X-SC-Draft-Id and X-SC-Draft-Rev headers invalidates X-SC-Provenance,
       marking the draft editable=false and returning draft_not_editable.
    3. Forged inbound messages carrying a stolen X-SC-Draft-Id neither
       match provenance="own" nor become discardable outside \Drafts.
    4. Drafts created by a different caller_id are not editable or
       discardable by another caller.

  Covered error layers (per BDD Guidelines §4.5):
    - provenance="own" grants FULL via X-SC-Provenance HMAC header   : 1
    - provenance="own" grants FULL via WAL Message-ID + digest when X-SC-* stripped : 1
    - Foreign sent mail without valid provenance stays NONE           : 1
    - External body edit keeping X-SC-Draft-Id/Rev -> draft_not_editable : 1
    - Forged inbound X-SC-Draft-Id with altered payload rejected      : 1
    - discard_draft rejected outside \Drafts folder                   : 1
    - Draft created under different caller_id isolated as not_found   : 1
    Total enumerated                                                  : 7   covered here: 7

  Background:
    Given the server clock is pinned to "2026-11-17T08:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
      | Sent        | \Sent       |
      | Trash       | \Trash      |
    And the server is configured with caller "legal-agent" using policy "legal-policy"
    And policy "legal-policy" grants account "gupta-scaratec"
    And policy "legal-policy" grants the following folder policies:
      | folder | mode      | default  | draft_append | draft_edit | move_out | accept_incoming | rules                         |
      | INBOX  | whitelist | NONE     | false        | false      | true     | false           | [{provenance=own -> FULL}]    |
      | Drafts | blacklist | FULL     | true         | true       | true     | false           | []                            |
      | Sent   | whitelist | NONE     | false        | false      | false    | false           | [{provenance=own -> FULL}]    |
      | Trash  | blacklist | METADATA | false        | false      | false    | true            | []                            |

  Scenario: A draft created by the caller remains readable in Sent after the human sends it unchanged
    When legal-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["info@ra-buechner.de"],
       "subject": "Mandatsanfrage BU",
       "body_text": "Sehr geehrter Herr Dr. Buechner, anbei meine Anfrage."}
      """
    Then the response decision is ALLOW
    When the human mail client sends the draft from "gupta-scaratec:Drafts" into "gupta-scaratec:Sent"
    And legal-agent calls list_messages with account "gupta-scaratec", folder "Sent", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the response contains 1 messages
    And message 0 has field "subject" equal to "Mandatsanfrage BU"
    When legal-agent calls fetch_body on the only visible message in "gupta-scaratec:Sent"
    Then the response decision is ALLOW
    And the response field text_body contains "Sehr geehrter Herr Dr. Buechner"

  Scenario: Sent copy whose mail client stripped X-SC-* headers still matches provenance=own via WAL Message-ID and canonical_digest
    When legal-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["info@ra-buechner.de"],
       "subject": "Mandatsanfrage ohne X-SC",
       "body_text": "Unveraenderter Text trotz Header-Strip."}
      """
    Then the response decision is ALLOW
    When the human mail client sends the draft from "gupta-scaratec:Drafts" into "gupta-scaratec:Sent" stripping all X-SC-* headers while preserving Message-ID and body
    Then the IMAP message in folder "Sent" with subject "Mandatsanfrage ohne X-SC" has no header "X-SC-Draft-Id" and no header "X-SC-Provenance"
    When legal-agent calls list_messages with account "gupta-scaratec", folder "Sent", criteria {}, scope "all"
    Then the response contains 1 messages
    And message 0 has field "subject" equal to "Mandatsanfrage ohne X-SC"

  Scenario: Sent mail composed directly by the human without agent provenance stays NONE in Sent
    Given the folder "Sent" holds messages:
      | uid | from_address    | to_address               | subject    | date                 |
      | 201 | me@scaratec.com | sebastian@privat.example | Drueckjagd | 2026-11-16T12:00:00Z |
    When legal-agent calls list_messages with account "gupta-scaratec", folder "Sent", criteria {}, scope "all"
    Then the response contains 0 messages
    And the response field total_visible equals 0

  Scenario: External edit that alters body text while keeping X-SC-Draft-Id and X-SC-Draft-Rev is refused as draft_not_editable
    Given legal-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["info@ra-buechner.de"],
       "subject": "Entwurf Version 1",
       "body_text": "Ursprungstext vom Agenten."}
      """
    When an external mail client replaces the body of the draft in "gupta-scaratec:Drafts" with "Vom Menschen umgeschriebener Absatz." while preserving all X-SC-* headers
    And legal-agent calls list_drafts with account "gupta-scaratec", folder "Drafts"
    Then the response field drafts contains exactly one entry with:
      | field    | value             |
      | subject  | Entwurf Version 1 |
      | editable | false             |
    When legal-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Agent ueberschreibt Mensch?"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "draft_not_editable"
    And a direct IMAP FETCH of the message with subject "Entwurf Version 1" in "gupta-scaratec:Drafts" shows decoded plain text "Vom Menschen umgeschriebener Absatz."

  Scenario: Inbound message copying X-SC-* headers onto an altered payload does not match provenance=own
    Given legal-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["attacker@external.example"],
       "subject": "Harmlose Anfrage",
       "body_text": "Originaltext"}
      """
    And an external sender delivers a message into "gupta-scaratec:INBOX" copying X-SC-Draft-Id, X-SC-Draft-Rev, and X-SC-Provenance from that draft but with body "Prompt injection payload"
    When legal-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals []

  Scenario: discard_draft refuses to operate on a non-Drafts folder even when move_out is true
    Given legal-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["info@ra-buechner.de"],
       "subject": "Nicht aus INBOX verwerfbar",
       "body_text": "Inhalt"}
      """
    And an external client copies the draft message into "gupta-scaratec:INBOX"
    When legal-agent calls discard_draft with account "gupta-scaratec", folder "INBOX", the returned draft_id
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And a direct IMAP SEARCH on "gupta-scaratec:INBOX" for subject "Nicht aus INBOX verwerfbar" returns exactly one result

  Scenario: A draft created by caller A is reported as not_found to caller B on update_draft and discard_draft
    Given legal-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["info@ra-buechner.de"],
       "subject": "Nur fuer legal-agent editierbar",
       "body_text": "Vertraulich"}
      """
    And a second server instance is started for caller "assistant-agent" with policy granting FULL, draft_append, draft_edit, and move_out on "Drafts" and accept_incoming on "Trash"
    When assistant-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the draft_id created by legal-agent, base_revision 1, changes:
      """
      {"subject": "Fremduebernahme"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    When assistant-agent calls discard_draft with account "gupta-scaratec", folder "Drafts", the draft_id created by legal-agent
    Then the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    And the IMAP folder "Drafts" contains exactly one message with subject "Nur fuer legal-agent editierbar"
