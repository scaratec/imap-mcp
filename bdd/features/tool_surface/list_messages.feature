Feature: list_messages projects per-message fields in ascending UID order under non-interference

  list_messages combines IMAP UID SEARCH with batch metadata fetching in
  one call (LIM 0023, ADR 0043). Entries are ordered by ascending UID,
  each entry is projected strictly to that message's effective visibility
  level (METADATA -> null envelope/attachment fields; ENVELOPE -> from,
  subject, null has_attachment; BODY -> boolean has_attachment), and
  responses emit total_visible, returned_count, has_more, applied_scope,
  and completeness without matched_total or filtered_out.

  Covered error layers (per BDD Guidelines §4.5):
    - Envelope projection in ascending UID order          : 1
    - Default 7-day window (recent_7d + window_limited)   : 1
    - Criteria narrow visible result set                  : 1
    - Pagination via limit and offset in ascending order  : 1
    - Non-whitelisted messages omitted without leak       : 1
    - Mixed per-message visibility redacts per entry      : 1
    - Empty folder returns 0 messages and total_visible=0 : 1
    Total enumerated                                      : 7   covered by this feature: 7

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"

  Scenario: list_messages returns envelope fields in ascending UID order
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address            | subject      | date                 |
      | 101 | alice@example.com | me@gupta-scaratec.com | Project plan | 2026-05-06T09:00:00Z |
      | 102 | bob@example.com   | me@gupta-scaratec.com | Invoice 42   | 2026-05-06T10:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX"
    Then the response contains 2 messages
    And message 0 has field "uid" equal to 101
    And message 0 has field "from" equal to "alice@example.com"
    And message 0 has field "subject" equal to "Project plan"
    And message 0 has field "date" starting with "2026-05-06"
    And message 0 has field "has_attachment" equal to null
    And message 1 has field "uid" equal to 102
    And message 1 has field "from" equal to "bob@example.com"
    And message 1 has field "subject" equal to "Invoice 42"
    And the response field applied_scope equals "recent_7d"
    And the response field completeness equals "window_limited"

  Scenario: Empty criteria default to the 7-day window
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address            | subject | date                 |
      | 201 | alice@example.com | me@gupta-scaratec.com | Recent  | 2026-05-06T09:00:00Z |
      | 202 | bob@example.com   | me@gupta-scaratec.com | Old     | 2025-01-15T09:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX"
    Then the response contains 1 messages
    And message 0 has field "subject" equal to "Recent"

  Scenario: Criteria narrow the visible result set
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from_address         | to_address            | subject    | date                 |
      | 301 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 1 | 2026-05-06T09:00:00Z |
      | 302 | newsletter@shop.com  | me@gupta-scaratec.com | Newsletter | 2026-05-06T09:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "hornbach.de"}
    Then the response contains 1 messages
    And message 0 has field "from" equal to "rechnung@hornbach.de"

  Scenario: Pagination via limit and offset slices ascending UIDs deterministically
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from_address  | to_address            | subject | date                 |
      | 401 | a@example.com | me@gupta-scaratec.com | Mail A  | 2026-05-06T09:00:00Z |
      | 402 | b@example.com | me@gupta-scaratec.com | Mail B  | 2026-05-05T09:00:00Z |
      | 403 | c@example.com | me@gupta-scaratec.com | Mail C  | 2026-05-04T09:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, limit 2, offset 1
    Then the response contains 2 messages
    And the response field total_visible equals 3
    And the response field returned_count equals 2
    And the response field has_more equals false
    And message 0 has field "uid" equal to 402
    And message 0 has field "from" equal to "b@example.com"
    And message 1 has field "uid" equal to 403

  Scenario: PDP hides non-whitelisted senders without exposing filtered_out
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | whitelist | NONE    |
    And policy "inbox-policy" sets folder "INBOX" rules to:
      | match                   | grant    |
      | from_domain=hornbach.de | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from_address         | to_address            | subject  | date                 |
      | 501 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung | 2026-05-06T09:00:00Z |
      | 502 | spam@example.net     | me@gupta-scaratec.com | Spam     | 2026-05-06T09:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "365d"}
    Then the response contains 1 messages
    And message 0 has field "from" equal to "rechnung@hornbach.de"
    And the response field total_visible equals 1
    And the response field applied_scope equals "explicit_window"
    And the response field completeness equals "policy_may_hide"
    And the response does not contain any field named "filtered_out"
    And the response does not contain any field named "matched_total"

  Scenario: Mixed per-message visibility projects METADATA, ENVELOPE, and BODY entries individually
    Given policy "inbox-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules                                                                             |
      | INBOX  | blacklist | BODY    | [{from_domain=bank.de -> cap METADATA}, {from_domain=shop.com -> cap ENVELOPE}]   |
    And the folder "INBOX" holds messages:
      | uid | from_address         | to_address            | subject     | date                 |
      | 601 | noreply@bank.de      | me@gupta-scaratec.com | Kontoauszug | 2026-05-06T09:00:00Z |
      | 602 | info@shop.com        | me@gupta-scaratec.com | Paket       | 2026-05-06T09:01:00Z |
      | 603 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung    | 2026-05-06T09:02:00Z |
    And the message at uid 601 has attachment "bank.pdf" of type "application/pdf" with content byte "B" repeated 128 times
    And the message at uid 602 has attachment "shop.pdf" of type "application/pdf" with content byte "S" repeated 128 times
    And the message at uid 603 has attachment "horn.pdf" of type "application/pdf" with content byte "H" repeated 128 times
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response contains 3 messages
    And message 0 has field "uid" equal to 601
    And message 0 has field "from" equal to null
    And message 0 has field "subject" equal to null
    And message 0 has field "has_attachment" equal to null
    And message 1 has field "uid" equal to 602
    And message 1 has field "from" equal to "info@shop.com"
    And message 1 has field "has_attachment" equal to null
    And message 2 has field "uid" equal to 603
    And message 2 has field "from" equal to "rechnung@hornbach.de"
    And message 2 has field "has_attachment" equal to true

  Scenario: Empty folder returns zero messages and total_visible=0
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX"
    Then the response contains 0 messages
    And the response field total_visible equals 0
    And the response field returned_count equals 0
    And the response field has_more equals false
