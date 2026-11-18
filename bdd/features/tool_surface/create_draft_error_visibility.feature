@test_hooks
Feature: create_draft classifies upstream APPEND rejections, connection drops, and timeouts in the normalized envelope

  ADR 0044 and ADR 0045 require create_draft to report upstream APPEND
  failures cleanly in the normalized ALLOW + ERROR envelope:
    - Tagged NO or BAD -> error.type="append_rejected" with sanitized
      status token/detail (<= 256 chars, no caller input echoed).
    - Connection closed after APPEND before tagged reply ->
      error.type="append_failed".
    - Tagged reply delayed beyond append_timeout ->
      error.type="append_timeout".
  Every scenario verifies actual mailbox state directly via IMAP.

  Covered error layers (per BDD Guidelines §4.5):
    - Successful APPEND omits error block, stores 1 draft on IMAP  : 1
    - Tagged NO [OVERQUOTA] -> append_rejected, 0 drafts on IMAP   : 1
    - Tagged BAD -> append_rejected, 0 drafts on IMAP              : 1
    - Connection closed after forwarded APPEND -> append_failed    : 1
    - Delayed APPEND response -> append_timeout                    : 1
    Total enumerated                                               : 5   covered: 5

  Background:
    Given the server clock is pinned to "2026-05-16T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules | draft_append |
      | Drafts | blacklist | FULL    | []    | true         |

  Scenario: Successful APPEND omits the error block and stores the draft on IMAP
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.test
      To: counterparty@example.com
      Subject: Visibility happy path
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body that the IMAP server will accept.
      """
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response does not contain any field named "error"
    And the IMAP folder "Drafts" contains exactly one message with subject "Visibility happy path"

  Scenario: Tagged NO with [OVERQUOTA] is classified as append_rejected and stores nothing on IMAP
    Given the IMAP proxy for "gupta-scaratec" rejects the next APPEND on "Drafts" once with "NO [OVERQUOTA] Mailbox is full"
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.test
      To: counterparty@example.com
      Subject: Rejected draft over quota
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body the server will reject.
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "append_rejected"
    And the response field error.detail contains "[OVERQUOTA]"
    And the IMAP folder "Drafts" contains no message with subject "Rejected draft over quota"

  Scenario: Tagged BAD is classified as append_rejected and stores nothing on IMAP
    Given the IMAP proxy for "gupta-scaratec" rejects the next APPEND on "Drafts" once with "BAD Command APPEND argument invalid"
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.test
      To: counterparty@example.com
      Subject: Rejected draft bad syntax
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body the server will reject with BAD.
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "append_rejected"
    And the response field error.detail is a non-empty string of at most 256 characters
    And the IMAP folder "Drafts" contains no message with subject "Rejected draft bad syntax"

  Scenario: Connection dropped after forwarding APPEND before tagged response returns append_failed
    Given the IMAP proxy for "gupta-scaratec" forwards the next APPEND on "Drafts" and closes the connection before returning the tagged response
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.test
      To: counterparty@example.com
      Subject: Draft that lost its connection
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body forwarded before disconnect.
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "append_failed"
    And the IMAP folder "Drafts" contains exactly one message with subject "Draft that lost its connection"

  Scenario: APPEND response delayed beyond append_timeout returns append_timeout
    Given the server append_timeout is configured to 1 second
    And the IMAP proxy for "gupta-scaratec" forwards the next APPEND on "Drafts" and delays its response by 3 seconds
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.test
      To: counterparty@example.com
      Subject: Timed-out draft
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body whose ACK arrives late.
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "append_timeout"
    When 3 seconds elapse for the delayed upstream APPEND to finish
    Then the IMAP folder "Drafts" contains exactly one message with subject "Timed-out draft"
