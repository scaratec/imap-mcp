Feature: create_draft surfaces the IMAP server's APPEND-rejection reason

  When the IMAP server rejects an APPEND, the caller must learn the
  reason class AND get the verbatim server text so the difference
  between "token rejected", "mailbox not selectable", "over quota",
  and "syntax error" is visible from the response itself — no
  server-log diving for trivial cases.

  Reported bug (2026-05-16, claude-agent on gupta@scaratec.com / [Gmail]/Drafts):
    Four `create_draft` calls with four different RFC822 variants all
    returned `append_failed` with no further detail. The OAuth token
    refresh that immediately preceded each call succeeded, reads on the
    same account worked, the oauth_scope was `https://mail.google.com/`,
    and the policy granted draft_append on the folder — yet the response
    gave the operator no signal about which layer actually rejected the
    APPEND. The server-side NO reason that Gmail sent was lost between
    aioimaplib and the tool response.

  Tool response contract (uses the ADR 0027 unified envelope):
    - On success:
        decision = "ALLOW", result = "OK"
        error    = (absent)
    - When the IMAP server returns a tagged NO or BAD response:
        decision     = "ALLOW", result = "ERROR"
        error.type   = "append_rejected"
        error.detail = the verbatim reason text the server sent after
                       the NO or BAD response token, response codes
                       like "[ALERT]" or "[OVERQUOTA]" preserved as-is,
                       bounded to 256 characters.
    - When the APPEND does not produce a tagged response within the
      configured `append_timeout`:
        decision     = "ALLOW", result = "ERROR"
        error.type   = "append_timeout"
        error.detail = "" (empty: no server text to surface)
    - When the connection is lost after the APPEND command was sent but
      before a tagged response was received:
        decision     = "ALLOW", result = "ERROR"
        error.type   = "append_failed"
        error.detail = "" (empty: no server text to surface)

  Persistence-Validierung (BDD Guidelines §13.2 Pruefung 1):
    Every error scenario asserts via a second channel (list_messages on
    the same Drafts folder) that the draft did NOT land — the response
    must not lie about ERROR, mirroring draft_consistency.feature.

  Covered error layers (BDD Guidelines §4.5):
    Antwortverarbeitung:
      - tagged NO with response code + free text  : 1
      - tagged BAD with free text                 : 1
    Externe Kommunikation:
      - APPEND timeout                            : 1
      - connection lost after APPEND, no response : 1
    Happy path (regression baseline):
      - successful APPEND keeps imap_response null: 1
    Total enumerated                              : 5   covered: 5

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "Drafts"
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"
    And policy "draft-policy" grants folder:
      | folder | mode      | default | rules | draft_append |
      | Drafts | blacklist | FULL    | []    | true         |

  # -------------------------------------------------------------------
  # Happy path: pins the contract that imap_response is null on success,
  # so a future regression that always fills the field (e.g. with stdout
  # noise) is caught.
  # -------------------------------------------------------------------
  Scenario: successful APPEND omits the error block entirely
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

    # Persistenz-Validierung: the draft really is on the server.
    When draft-agent calls list_messages with account "gupta-scaratec", folder "Drafts", scope "all"
    Then the response field matched_total equals 1

  # -------------------------------------------------------------------
  # The core bug: when the server returns a tagged NO with reason text,
  # the caller must receive that text verbatim in `imap_response` and a
  # distinct error_type so it can tell server-rejection from timeout.
  # The NO text named in the Given is what the proxy actually emits on
  # the wire — the assertion is therefore wire-honest.
  # -------------------------------------------------------------------
  Scenario: tagged NO with response code is surfaced verbatim and classified as append_rejected
    Given the IMAP server for "gupta-scaratec" responds to the next APPEND with NO response text "[OVERQUOTA] Mailbox is full (0.005 + 0.000 / 0.001 GB)"
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.test
      To: counterparty@example.com
      Subject: Rejected draft — over quota
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body the server will reject.
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "append_rejected"
    And the response field error.detail equals "[OVERQUOTA] Mailbox is full (0.005 + 0.000 / 0.001 GB)"

    # Persistenz-Validierung: nothing landed on the server.
    When draft-agent calls list_messages with account "gupta-scaratec", folder "Drafts", scope "all"
    Then the response field matched_total equals 0

  # -------------------------------------------------------------------
  # Same surface contract, BAD (protocol-level) instead of NO (logical-
  # level). Both must surface the text and both must classify the same
  # way — the agent does not get to act differently on NO vs BAD; what
  # matters is the operator-readable reason.
  # -------------------------------------------------------------------
  Scenario: tagged BAD with free text is surfaced verbatim and classified as append_rejected
    Given the IMAP server for "gupta-scaratec" responds to the next APPEND with BAD response text "Command APPEND argument 1 invalid"
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.test
      To: counterparty@example.com
      Subject: Rejected draft — bad syntax
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body the server will reject with BAD.
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "append_rejected"
    And the response field error.detail equals "Command APPEND argument 1 invalid"

    # Persistenz-Validierung: nothing landed on the server.
    When draft-agent calls list_messages with account "gupta-scaratec", folder "Drafts", scope "all"
    Then the response field matched_total equals 0

  # -------------------------------------------------------------------
  # Catch-all path: the server accepted the APPEND command but then the
  # connection was closed before any tagged response was sent. The
  # implementation has no server text to surface, but the agent must
  # still see a result distinct from "timeout" and "rejected" so the
  # operator can recognise the connectivity-failure class.
  # -------------------------------------------------------------------
  Scenario: connection closed mid-APPEND with no server response is classified as append_failed
    Given the IMAP server for "gupta-scaratec" closes the connection after the next APPEND command without responding
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.test
      To: counterparty@example.com
      Subject: Draft that lost its connection
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body the server will never finish acknowledging.
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "append_failed"
    And the response field error.detail equals ""

    # Persistenz-Validierung: nothing landed on the server.
    When draft-agent calls list_messages with account "gupta-scaratec", folder "Drafts", scope "all"
    Then the response field matched_total equals 0

  # -------------------------------------------------------------------
  # The timeout path needs its own error_type so the operator can tell
  # "the server took too long" from "the server actively said no". The
  # imap_response stays null because there is no server text to surface.
  # -------------------------------------------------------------------
  Scenario: APPEND timeout is classified as append_timeout with empty detail
    Given the IMAP server for "gupta-scaratec" delays the next APPEND response by 45 seconds
    And the server append_timeout is configured to 2 seconds
    When draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@gupta-scaratec.test
      To: counterparty@example.com
      Subject: Timed-out draft
      MIME-Version: 1.0
      Content-Type: text/plain; charset=US-ASCII

      Body the server will hang on.
      """
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "append_timeout"
    And the response field error.detail equals ""

    # Persistenz-Validierung: nothing landed on the server.
    When draft-agent calls list_messages with account "gupta-scaratec", folder "Drafts", scope "all"
    Then the response field matched_total equals 0
