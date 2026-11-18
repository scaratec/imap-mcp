@test_hooks
Feature: Draft revision recovers forward after a crash and cleans up residue

  A draft revision appends revision r+1 with fresh X-SC-Provenance and
  then removes revision r via UID EXPUNGE. Startup recovery drives
  interrupted draft revisions forward without rolling back; reads
  (get_draft, list_drafts) surface the highest authentic revision without
  mutating the mailbox; and persistent delete failures escalate cleanly
  to needs_operator (ADR 0042, ADR 0045, ADR 0046).

  Covered error layers (per BDD Guidelines §4.5):
    - Crash after APPEND before WAL staged: startup recovery removes old rev : 1
    - Crash after DELETE before WAL commit: startup recovery commits         : 1
    - Residue from interrupted revision: read is clean, next edit sweeps it  : 1
    - \Draft and \Seen flags survive a draft revision                        : 1
    - Persistent UID EXPUNGE refusal escalates to needs_operator             : 1
    Total enumerated                                                         : 5   covered: 5

  Background:
    Given the server clock is pinned to "2026-09-18T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | true         | true       |
    And the WAL retry_limit is configured to 2
    And the WAL is empty

  Scenario: Crash after APPEND leaves one authoritative revision 2 after startup recovery
    Given draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Survives a crash
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    And the server is armed with IMAP_MCP_CRASH_AT="post_append"
    When draft-agent invokes update_draft expecting crash on "gupta-scaratec:Drafts" for the returned draft_id, base_revision 1, changes {"subject": "Survived a crash"}
    And the server is restarted without crash hooks
    Then within 10 seconds the WAL entry for the crashed draft revision has status "committed"
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value            |
      | Subject        | Survived a crash |
      | X-SC-Draft-Rev | 2                |
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response decision is ALLOW
    And the response field revision equals 2
    And the response field subject equals "Survived a crash"

  Scenario: Crash after DELETE before WAL commit commits cleanly on startup recovery
    Given draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Deleted then crashed
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    And the server is armed with IMAP_MCP_CRASH_AT="post_delete"
    When draft-agent invokes update_draft expecting crash on "gupta-scaratec:Drafts" for the returned draft_id, base_revision 1, changes {"subject": "Committed on recovery"}
    And the server is restarted without crash hooks
    Then within 10 seconds the WAL entry for the crashed draft revision has status "committed"
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                 |
      | Subject        | Committed on recovery |
      | X-SC-Draft-Rev | 2                     |

  Scenario: Authentic residue revision is ignored by get_draft and swept by the next update_draft
    Given draft-agent creates a draft in "gupta-scaratec:Drafts" with subject "Leftover first revision" and body "We can deliver in week 14."
    And an interrupted revision leaves both authentic revision 1 ("Leftover first revision") and authentic revision 2 ("Authoritative second revision") for that draft_id in "gupta-scaratec:Drafts" with no active WAL entry
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response decision is ALLOW
    And the response field revision equals 2
    And the response field subject equals "Authoritative second revision"
    And the IMAP folder "Drafts" contains exactly 2 messages with header "X-SC-Draft-Id" set
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 2, changes:
      """
      {"subject": "Third revision"}
      """
    Then the response decision is ALLOW
    And the response field revision equals 3
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value          |
      | Subject        | Third revision |
      | X-SC-Draft-Rev | 3              |

  Scenario: The \Draft and \Seen flags set on create_draft survive a draft revision
    Given draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Still a draft
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    Then the IMAP message in folder "Drafts" with subject "Still a draft" has flags:
      | flag   |
      | \Draft |
      | \Seen  |
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"body_text": "We can deliver in week 13."}
      """
    Then the response field revision equals 2
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP message in folder "Drafts" with subject "Still a draft" has flags:
      | flag   |
      | \Draft |
      | \Seen  |

  Scenario: Persistent refusal of source UID EXPUNGE during draft revision escalates to needs_operator
    Given draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Expunge blocked
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    And the IMAP proxy for "gupta-scaratec" rejects every UID EXPUNGE with "NO [NOPERM] Expunge prohibited"
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Expunge blocked v2"}
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "transient_imap_failure"
    When the test recovery pass runs 2 times
    Then the WAL entry for the returned tx_id has status "needs_operator"
    And the IMAP folder "Drafts" contains exactly 2 messages with header "X-SC-Draft-Id" set
    And the audit log contains an entry with tool "saga_transition" and step "escalated"
