Feature: create_draft consistency with subsequent reads

  When a caller creates a draft via create_draft on a folder that grants
  read visibility, a subsequent list_messages call discovers that draft
  immediately with total_visible=1, and direct IMAP inspection confirms
  storage on the server (ADR 0043, ADR 0045).

  Covered error layers (per BDD Guidelines §4.5):
    - Round-trip: create_draft then list_messages same folder  : 1
    - IMAP second-channel: draft present after create_draft OK : 1
    Total enumerated                                           : 2   covered: 2

  Background:
    Given the server clock is pinned to "2026-05-15T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append |
      | Drafts | blacklist | FULL    | []    | true         |

  Scenario: list_messages discovers a freshly created draft immediately
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Round-trip draft
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body of the round-trip draft.
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the IMAP folder "Drafts" contains exactly one message with subject "Round-trip draft"
    When draft-agent calls list_messages with account "gupta-scaratec", folder "Drafts", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the response field total_visible equals 1
    And the response field returned_count equals 1
    And the response field completeness equals "complete"

  Scenario: create_draft success stores the exact subject and body on the IMAP server
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.com
      To: counterparty@example.com
      Subject: Persisted draft
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body of the persisted draft.
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response does not contain any field named "error"
    And the IMAP folder "Drafts" contains exactly one message with subject "Persisted draft"
    And a direct IMAP FETCH of the message with subject "Persisted draft" in "gupta-scaratec:Drafts" shows decoded plain text "Body of the persisted draft."
