Feature: draft_edit capability gates draft revision

  Revising a draft appends a new revision and expunges the previous
  one. That is a destructive operation on existing content, so it is
  gated by its own capability, draft_edit, defaulting to false
  (ADR 0031).

  It is deliberately not implied by draft_append, which only permits
  depositing new content, and not by modify_message, which governs
  rewriting arbitrary messages in any folder. A policy that grants
  either of those does not thereby grant the right to destroy a draft.

  The drafts-write-isolation pattern of ADR 0005 stays intact:
  visibility NONE plus draft_edit means the caller can revise a draft
  it is not allowed to read, because changes are field-wise and the
  previous content is read inside the server, never returned.

  Covered error layers (per BDD Guidelines §4.5):
    - draft_edit=false denies update_draft                    : 1
    - draft_append alone does not imply draft_edit            : 1
    - modify_message alone does not imply draft_edit          : 1
    - draft_edit=true allows update_draft                     : 1
    - draft_edit alone does not imply draft_append            : 1
    - reading a draft is not gated by draft_edit              : 1
    - write-only isolation: update_draft allowed, get_draft denied : 2
    - list_drafts needs METADATA visibility                   : 1
    Total enumerated                                          : 9   covered: 9

  Three of these are update_draft denials — the first three — and they
  are the ones tool_surface/update_draft_errors.feature counts as its
  policy layer and delegates here.

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "Drafts"
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"

  Scenario: update_draft without draft_edit is denied
    Given policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | true         | false      |
    And the folder "Drafts" holds a verbatim message:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Not yours to change
      X-SC-Draft-Id: 66666666-6666-4666-8666-666666666666
      X-SC-Draft-Rev: 1
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", draft_id "66666666-6666-4666-8666-666666666666", base_revision 1, changes:
      """
      {"subject": "Changed anyway"}
      """
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "draft_edit"
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value               |
      | Subject        | Not yours to change |
      | X-SC-Draft-Rev | 1                   |

  Scenario: the caller that created the draft still needs draft_edit to change it
    Given policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | true         | false      |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Mine, but frozen
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Second thoughts"}
      """
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "draft_edit"

  Scenario: modify_message does not stand in for draft_edit
    Given policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit | modify_message |
      | Drafts | blacklist | FULL    | []    | true         | false      | true           |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Rewriting is not editing
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Second thoughts"}
      """
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "draft_edit"

  Scenario: update_draft with draft_edit succeeds
    Given policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | true         | true       |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Editable
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"subject": "Edited"}
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field revision equals 2
    And the response field mechanism equals "draft_revision"

  # Independence is a claim about both directions. The scenarios above
  # withhold draft_edit while draft_append is granted; this one does the
  # reverse, so an implementation that quietly treats one capability as
  # the other cannot pass both.
  Scenario: draft_edit without draft_append revises but does not create
    Given policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | false        | true       |
    And the folder "Drafts" holds a verbatim message:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Existing but not mine to duplicate
      X-SC-Draft-Id: 55555555-5555-4555-8555-555555555555
      X-SC-Draft-Rev: 1
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", draft_id "55555555-5555-4555-8555-555555555555", base_revision 1, changes:
      """
      {"subject": "Revised without the right to append"}
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field revision equals 2
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: A draft of my own
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Composed where only revision is allowed.
      """
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "draft_append"
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                               |
      | Subject        | Revised without the right to append |
      | X-SC-Draft-Rev | 2                                   |

  # Reading is governed by visibility, not by the write capability.
  # `editable` in the listing is a statement about the message — it
  # carries a handle and a revision, so it *can* be revised (ADR 0030) —
  # not about what this caller is allowed to do with it.
  Scenario: reading a draft does not depend on draft_edit
    Given policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | true         | false      |
    And the folder "Drafts" holds a verbatim message:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Readable but frozen
      X-SC-Draft-Id: 88888888-8888-4888-8888-888888888888
      X-SC-Draft-Rev: 1
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", draft_id "88888888-8888-4888-8888-888888888888"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field revision equals 1
    And the response field subject equals "Readable but frozen"
    And the response field body_text equals "We can deliver in week 14."
    When draft-agent calls list_drafts with account "gupta-scaratec", folder "Drafts"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field drafts contains exactly one entry with:
      | field    | value               |
      | subject  | Readable but frozen |
      | revision | 1                   |
      | editable | true                |

  Scenario: a write-only drafts folder can still be revised
    Given policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | whitelist | NONE    | []    | true         | true       |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Write-only
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    When draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
      """
      {"body_text": "We can deliver in week 13."}
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field revision equals 2
    And the response does not contain any field named "body_text"
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value      |
      | Subject        | Write-only |
      | X-SC-Draft-Rev | 2          |

  Scenario: a write-only drafts folder cannot be read back
    Given policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | whitelist | NONE    | []    | true         | true       |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Write-only
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response decision is DENY
    And the response field reason equals "visibility_below_BODY"

  Scenario: listing drafts needs metadata visibility
    Given policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | whitelist | NONE    | []    | true         | true       |
    When draft-agent calls list_drafts with account "gupta-scaratec", folder "Drafts"
    Then the response decision is DENY
    And the response field reason equals "visibility_below_METADATA"
