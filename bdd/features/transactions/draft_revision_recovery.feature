Feature: Draft revision recovers forward after a crash

  A draft revision is one message-rewrite saga: APPEND the next
  revision, then delete the previous one. An interruption between the
  two leaves two messages carrying the same X-SC-Draft-Id, ordered by
  X-SC-Draft-Rev. There is no rollback. The highest revision is the
  draft; the lower one is residue (ADR 0030, ADR 0031).

  Residue is removed by the resuming transaction, or by the next
  update_draft on that handle. Reads never delete: get_draft and
  list_drafts report the highest revision and leave the mailbox alone.

  Covered error layers (per BDD Guidelines §4.5):
    - Crash after APPEND, before WAL staged: delete completes  : 1
    - Crash after DELETE, before COMMIT: transaction commits    : 1
    - Residue without a resumable transaction: read is correct,
      next revision cleans up                                   : 1
    - \Draft flag survives a revision                           : 1
    - Delete step refused by a read-only folder                 : 1  not covered
    Total enumerated                                            : 5   covered: 4

  The uncovered path needs a mailbox fixture that accepts APPEND and
  refuses EXPUNGE. Dovecot's ACL plugin is not configured in the BDD
  compose stack, and the behaviour it would prove — caller unblocked,
  transaction escalates to needs_operator after the retry limit — is
  the same escalation already pinned by saga_crash_recovery.feature.

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "Drafts"
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | true         | true       |
    And the WAL is empty

  Scenario: crash after APPEND leaves one draft, not two
    Given draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Survives a crash
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    And the server is configured to crash after APPEND but before WAL staged persistence
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Survived a crash"}
      """
    And the server terminates ungracefully
    And the server is restarted
    And the server's background recovery loop runs once
    Then the WAL entry reaches status "committed" within 30 seconds
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value            |
      | Subject        | Survived a crash |
      | X-SC-Draft-Rev | 2                |
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response decision is ALLOW
    And the response field revision equals 2
    And the response field subject equals "Survived a crash"

  Scenario: crash after DELETE commits the revision on recovery
    Given draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Deleted then crashed
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    And the server is configured to crash after DELETE but before WAL commit persistence
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Committed on recovery"}
      """
    And the server terminates ungracefully
    And the server is restarted
    And the server's background recovery loop runs once
    Then the WAL entry reaches status "committed" within 30 seconds
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                 |
      | Subject        | Committed on recovery |
      | X-SC-Draft-Rev | 2                     |

  Scenario: residue is ignored by reads and swept by the next revision
    Given the folder "Drafts" holds a verbatim message:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Leftover first revision
      X-SC-Draft-Id: 55555555-5555-4555-8555-555555555555
      X-SC-Draft-Rev: 1
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    And the folder "Drafts" holds a verbatim message:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Authoritative second revision
      X-SC-Draft-Id: 55555555-5555-4555-8555-555555555555
      X-SC-Draft-Rev: 2
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 13.
      """
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", draft_id "55555555-5555-4555-8555-555555555555"
    Then the response decision is ALLOW
    And the response field revision equals 2
    And the response field subject equals "Authoritative second revision"
    And the IMAP folder "Drafts" contains exactly 2 messages with header "X-SC-Draft-Id" set
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", draft_id "55555555-5555-4555-8555-555555555555", base_revision 2, changes:
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

  Scenario: the draft stays flagged as a draft across a revision
    Given draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Still a draft
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"body_text": "We can deliver in week 13."}
      """
    Then the response field revision equals 2
    # Only the body changes here, so both revisions would answer to the
    # same subject. The flag check below addresses the message by that
    # subject, so it is worth nothing until the folder is known to hold
    # one revision and only one — the same uniqueness the other
    # revision scenarios assert.
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value         |
      | Subject        | Still a draft |
      | X-SC-Draft-Rev | 2             |
    And the IMAP message in folder "Drafts" with subject "Still a draft" has flags:
      | flag   |
      | \Draft |
      | \Seen  |
