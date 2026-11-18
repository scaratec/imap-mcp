Feature: draft_edit capability and provenance gate draft revision

  Revising a draft appends a new revision and removes the previous one.
  It requires draft_edit=true on the \Drafts folder AND verified caller
  provenance (X-SC-Provenance HMAC + WAL registry, ADR 0042, ADR 0045).
  Drafts write-isolation stays intact: under visibility NONE with
  draft_edit=true, the caller can revise its own provenance-verified
  draft (explicitly exempted from the <METADATA object gate per ADR 0044
  §2.5) without reading back existing draft content.

  Covered error layers (per BDD Guidelines §4.5):
    - Unauthenticated verbatim X-SC-Draft-Id rejected without draft_edit : 1
    - Caller's own draft still requires draft_edit=true                  : 1
    - draft_edit=true revises caller's own draft on IMAP                 : 1
    - draft_edit=true without draft_append revises after reload, blocks create : 1
    - Reading a draft depends on BODY visibility, not draft_edit         : 1
    - Write-only isolation: update_draft modifies IMAP body, get_draft denied (visibility_below_BODY) : 2
    - list_drafts requires METADATA visibility                           : 1
    Total enumerated                                                     : 8   covered: 8

  Background:
    Given the server clock is pinned to "2026-09-18T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"

  Scenario: update_draft without draft_edit is denied with capability_missing before handle lookup
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
    And the response does not contain any field named "missing_capability"
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value               |
      | Subject        | Not yours to change |
      | X-SC-Draft-Rev | 1                   |

  Scenario: The caller that created a draft still needs draft_edit to revise it
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
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value            |
      | Subject        | Mine, but frozen |
      | X-SC-Draft-Rev | 1                |

  Scenario: update_draft with draft_edit revises the draft on the IMAP server
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
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value  |
      | Subject        | Edited |
      | X-SC-Draft-Rev | 2      |

  Scenario: draft_edit without draft_append revises an existing own draft after policy reload but refuses create_draft
    Given policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | true         | true       |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Created before append was revoked
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    When the operator updates folder "Drafts" in policy "draft-policy" to:
      | mode      | default | draft_append | draft_edit |
      | blacklist | FULL    | false        | true       |
    And the server receives SIGHUP
    And draft-agent calls update_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id, base_revision 1, changes:
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
      Subject: A second draft
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Blocked.
      """
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
    And the IMAP folder "Drafts" contains exactly one message with headers:
      | header         | value                               |
      | Subject        | Revised without the right to append |
      | X-SC-Draft-Rev | 2                                   |

  Scenario: Reading an own draft depends on BODY visibility rather than draft_edit
    Given policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | blacklist | FULL    | []    | true         | false      |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Readable but frozen
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      We can deliver in week 14.
      """
    When draft-agent calls get_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field revision equals 1
    And the response field subject equals "Readable but frozen"
    And the response field body_text equals "We can deliver in week 14."
    When draft-agent calls list_drafts with account "gupta-scaratec", folder "Drafts"
    Then the response decision is ALLOW
    And the response field drafts contains exactly one entry with:
      | field    | value               |
      | subject  | Readable but frozen |
      | revision | 1                   |
      | editable | true                |

  Scenario: A write-only drafts folder revises own draft content on IMAP without disclosing body_text
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
    And a direct IMAP FETCH of the message with subject "Write-only" in "gupta-scaratec:Drafts" shows decoded plain text "We can deliver in week 13."

  Scenario: A write-only drafts folder denies get_draft with visibility_below_BODY for NONE visibility
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

  Scenario: Listing drafts on a folder with maximum visibility NONE is denied with visibility_below_METADATA
    Given policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append | draft_edit |
      | Drafts | whitelist | NONE    | []    | true         | true       |
    When draft-agent calls list_drafts with account "gupta-scaratec", folder "Drafts"
    Then the response decision is DENY
    And the response field reason equals "visibility_below_METADATA"
