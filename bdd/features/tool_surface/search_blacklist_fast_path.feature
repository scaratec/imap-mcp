Feature: Search fast-path on rule-free blacklist folders avoids per-message IMAP FETCH

  On a standard IMAP account where a blacklist folder has default >=
  METADATA, zero cap rules, and search criteria push down completely to
  IMAP UID SEARCH (such as empty criteria with scope="all"), the PDP
  decision is predetermined for every candidate UID (LIM 0023, ADR 0043).
  The server returns paginated ascending UIDs directly from UID SEARCH
  without issuing any per-message UID FETCH on the wire.

  Covered error layers (per BDD Guidelines §4.5):
    - Rule-free blacklist search issues UID SEARCH and 0 UID FETCH : 1
    - Fast-path preserves ascending UID order under offset + limit : 1
    - Blacklist with cap rules issues UID FETCH and filters        : 1
    Total enumerated                                               : 3   covered by this feature: 3

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"

  Scenario: Rule-free blacklist search with scope=all returns UIDs with zero wire UID FETCH commands
    Given policy "inbox-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address            | subject | date                 |
      | 101 | alice@example.com | me@gupta-scaratec.com | Mail A  | 2026-05-06T09:00:00Z |
      | 102 | bob@example.com   | me@gupta-scaratec.com | Mail B  | 2026-05-06T09:01:00Z |
      | 103 | carol@example.com | me@gupta-scaratec.com | Mail C  | 2026-05-06T09:02:00Z |
      | 104 | dave@example.com  | me@gupta-scaratec.com | Mail D  | 2026-05-06T09:03:00Z |
      | 105 | eve@example.com   | me@gupta-scaratec.com | Mail E  | 2026-05-06T09:04:00Z |
    And the IMAP command log for "gupta-scaratec" is cleared
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all", limit 3
    Then the response decision is ALLOW
    And the response field uids equals in exact order [101, 102, 103]
    And the response field total_visible equals 5
    And the response field returned_count equals 3
    And the response field has_more equals true
    And the response field completeness equals "complete"
    And the IMAP command log for "gupta-scaratec" contains a UID SEARCH command
    And the IMAP command log for "gupta-scaratec" contains no UID FETCH command

  Scenario: Rule-free fast-path preserves exact ascending UID slice under offset and limit
    Given policy "inbox-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address            | subject | date                 |
      | 201 | alice@example.com | me@gupta-scaratec.com | Mail A  | 2026-05-06T09:00:00Z |
      | 202 | bob@example.com   | me@gupta-scaratec.com | Mail B  | 2026-05-05T09:00:00Z |
      | 203 | carol@example.com | me@gupta-scaratec.com | Mail C  | 2026-05-04T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all", limit 2, offset 1
    Then the response field uids equals in exact order [202, 203]
    And the response field has_more equals false
    And the response field total_visible equals 3

  Scenario: Blacklist folder with a cap rule fetches metadata and suppresses capped messages
    Given policy "inbox-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | rules                                   |
      | INBOX  | blacklist | ENVELOPE | [{from_domain=spammer.net -> cap NONE}] |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address            | subject | date                 |
      | 301 | alice@example.com | me@gupta-scaratec.com | Legit   | 2026-05-06T09:00:00Z |
      | 302 | spam@spammer.net  | me@gupta-scaratec.com | Buy now | 2026-05-06T09:01:00Z |
    And the IMAP command log for "gupta-scaratec" is cleared
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "365d"}
    Then the response field uids equals in exact order [301]
    And the response field total_visible equals 1
    And the response field completeness equals "policy_may_hide"
    And the response field applied_scope equals "explicit_window"
    And the IMAP command log for "gupta-scaratec" contains a UID FETCH command
