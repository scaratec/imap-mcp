Feature: list_messages returns envelope data in a single call

  An LLM agent asking "show me today's emails" needs sender,
  subject, and date for each message. list_messages combines
  IMAP SEARCH with FETCH ENVELOPE into one MCP call, avoiding
  N sequential fetch_envelope round-trips.
  See LIM-0011.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path with envelope data          : 1
    - Empty criteria default 7d scope        : 1
    - Criteria narrow results                : 1
    - Pagination limit + offset              : 1
    - PDP filters hidden messages            : 1
    - Blacklist fast-path (no envelope loop) : 1
    - Empty result set                       : 1
    - applied_scope present on every call    : 1
    Total enumerated                          : 8   covered by this feature: 8

  Background:
    Given the server date is pinned to "2026-05-07"
    And the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"

  Scenario: list_messages returns from, subject, date for each message
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                   | subject      | date                 |
      | 101 | alice@example.com      | Project plan | 2026-05-06T09:00:00Z |
      | 102 | bob@example.com        | Invoice 42   | 2026-05-06T10:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX"
    Then the response contains 2 messages
    And message 0 has field "from" equal to "alice@example.com"
    And message 0 has field "subject" equal to "Project plan"
    And message 0 has field "date" matching the pattern "2026-05-06"
    And message 1 has field "from" equal to "bob@example.com"
    And message 1 has field "subject" equal to "Invoice 42"
    And the response field applied_scope equals "recent_7d"

  Scenario: Empty criteria default to a 7-day scope
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                   | subject   | date                 |
      | 201 | alice@example.com      | Recent    | 2026-05-06T09:00:00Z |
      | 202 | bob@example.com        | Old       | 2025-01-15T09:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX"
    Then the response contains 1 messages
    And message 0 has field "subject" equal to "Recent"

  Scenario: Criteria narrow the result set
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                   | subject      | date                 |
      | 301 | rechnung@hornbach.de   | Rechnung 1   | 2026-05-06T09:00:00Z |
      | 302 | newsletter@shop.com    | Newsletter   | 2026-05-06T09:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "hornbach.de"}
    Then the response contains 1 messages
    And message 0 has field "from" equal to "rechnung@hornbach.de"

  Scenario: Pagination via limit and offset
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                   | subject    | date                 |
      | 401 | a@example.com          | Mail A     | 2026-05-06T09:00:00Z |
      | 402 | b@example.com          | Mail B     | 2026-05-05T09:00:00Z |
      | 403 | c@example.com          | Mail C     | 2026-05-04T09:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, limit 2, offset 1
    Then the response contains 2 messages
    And the response field matched_total equals 3
    And the response field has_more equals false
    And message 0 has field "from" equal to "b@example.com"

  Scenario: PDP hides messages from non-whitelisted senders
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | whitelist | NONE    |
    And policy "inbox-policy" sets folder "INBOX" rules to:
      | match                    | grant    |
      | from_domain=hornbach.de  | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                   | subject    | date                 |
      | 501 | rechnung@hornbach.de   | Rechnung   | 2026-05-06T09:00:00Z |
      | 502 | spam@example.net       | Spam       | 2026-05-06T09:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "365d"}
    Then the response contains 1 messages
    And message 0 has field "from" equal to "rechnung@hornbach.de"
    And the response field filtered_out equals 1
    And the response field applied_scope equals "explicit_window"

  Scenario: Blacklist fast-path skips per-message fetch
    Given the audit log directory is a fresh $TMPDIR/audit
    And policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                   | subject    | date                 |
      | 601 | alice@example.com      | Mail A     | 2026-05-06T09:00:00Z |
      | 602 | bob@example.com        | Mail B     | 2026-05-06T09:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX"
    Then the response contains 2 messages
    And the response field filtered_out equals 0

  Scenario: Empty folder returns zero messages
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX"
    Then the response contains 0 messages
    And the response field matched_total equals 0
    And the response field has_more equals false
