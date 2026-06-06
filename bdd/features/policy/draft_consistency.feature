Feature: create_draft consistency with subsequent reads

  When a caller successfully writes a draft via create_draft, the same
  caller must be able to discover it via list_messages on the same
  folder, and the response of create_draft must accurately describe
  what happened on the IMAP server. ADR 0002 + ADR 0006.

  Reported bug (2026-05-15, claude-agent on info@scaratec.bg/Drafts):
    1. create_draft returned append_failed even though the message
       did land in the IMAP folder (confirmed via Thunderbird).
    2. A follow-up list_messages on the same Drafts folder returned
       matched_total=0 although the new draft was present.
  Both symptoms break the response-state contract this feature pins
  down.

  Covered error layers (per BDD Guidelines §4.5):
    - round-trip: create_draft then list_messages same folder  : 1
    - IMAP second-channel: draft present after create_draft OK : 1
    Total enumerated                                           : 2   covered: 2

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "Drafts"
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append |
      | Drafts | blacklist | FULL    | []    | true         |

  Scenario: list_messages discovers a freshly created draft
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
    When draft-agent calls list_messages with account "gupta-scaratec", folder "Drafts"
    Then the response decision is ALLOW
    And the response field matched_total equals 1
    And the response field matched_visible equals 1

  Scenario: create_draft success implies the draft is on the IMAP server
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
