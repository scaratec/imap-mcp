Feature: bulk_mark_tagged updates user keywords on matching ENVELOPE-visible messages in one IMAP session

  ADR 0041, ADR 0043, and ADR 0045 require bulk_mark_tagged to hold
  mark_tagged=true, enforce effective message visibility >=
  max(ENVELOPE, req_vis(criteria)), reject all \-prefixed system flags
  with forbidden_flag, and execute keyword updates within a bounded
  number of IMAP connections. Every write scenario verifies keywords via
  direct IMAP inspection rather than reading back through the server.

  Covered error layers (per BDD Guidelines §4.5):
    - mode=add verified directly on IMAP                       : 1
    - mode=remove verified directly on IMAP                    : 1
    - mode=replace preserves \Seen & \Flagged, swaps keywords  : 1
    - Empty match returns tagged_count=0, leaves IMAP untouched: 1
    - Missing mark_tagged capability denied                    : 1
    - System flag rejected with forbidden_flag                 : 1
    - Per-message visibility & criteria floor skips capped mail: 2
    - Proxy-verified bounded connection count across 20 mails  : 1
    Total enumerated                                           : 9   covered by this feature: 9

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"

  Scenario: bulk_mark_tagged with mode=add sets the keyword on all 10 matching messages on IMAP
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | true        |
    And the folder "INBOX" holds 10 messages from "vendor@example.com" dated "2026-05-06T09:00:00Z"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["archive"], mode "add", scope "all"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field tagged_count equals 10
    And the IMAP folder "INBOX" contains 10 messages with keyword "archive"

  Scenario: bulk_mark_tagged with mode=remove strips the keyword from all 4 matching messages on IMAP
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | true        |
    And the folder "INBOX" holds 4 messages from "vendor@example.com" dated "2026-05-06T09:00:00Z" each tagged "archive"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["archive"], mode "remove", scope "all"
    Then the response field tagged_count equals 4
    And the IMAP folder "INBOX" contains 0 messages with keyword "archive"

  Scenario: bulk_mark_tagged with mode=replace overwrites user keywords while keeping \Seen and \Flagged
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | true        |
    And the folder "INBOX" holds 3 messages from "vendor@example.com" dated "2026-05-06T09:00:00Z" each carrying keyword "draft_kw" and flags [\Seen, \Flagged]
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["final_kw"], mode "replace", scope "all"
    Then the response field tagged_count equals 3
    And the IMAP folder "INBOX" contains 3 messages with keyword "final_kw"
    And the IMAP folder "INBOX" contains 0 messages with keyword "draft_kw"
    And the IMAP folder "INBOX" contains 3 messages with flag "\Seen"
    And the IMAP folder "INBOX" contains 3 messages with flag "\Flagged"

  Scenario: bulk_mark_tagged with no matching messages returns tagged_count=0 and writes zero keywords
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | true        |
    And the folder "INBOX" holds 5 messages from "vendor@example.com" dated "2026-05-06T09:00:00Z"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_address": "nobody@nowhere.com"}, tags ["archive"], mode "add", scope "all"
    Then the response decision is ALLOW
    And the response field tagged_count equals 0
    And the IMAP folder "INBOX" contains 0 messages with keyword "archive"

  Scenario: bulk_mark_tagged without mark_tagged capability is denied with capability_missing
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | false       |
    And the folder "INBOX" holds 2 messages from "vendor@example.com" dated "2026-05-06T09:00:00Z"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["archive"], mode "add", scope "all"
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the IMAP folder "INBOX" contains 0 messages with keyword "archive"

  Scenario: bulk_mark_tagged rejects \-prefixed system flags with forbidden_flag
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | true        |
    And the folder "INBOX" holds 2 messages from "vendor@example.com" dated "2026-05-06T09:00:00Z"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["\\Deleted"], mode "add", scope "all"
    Then the response decision is DENY
    And the response field reason equals "forbidden_flag"
    And the IMAP folder "INBOX" contains 0 messages with flag "\Deleted"

  Scenario: bulk_mark_tagged across 20 messages opens at most 2 IMAP connections through the proxy
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | true        |
    And the folder "INBOX" holds 20 messages from "vendor@example.com" dated "2026-05-06T09:00:00Z"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["archive"], mode "add", scope "all"
    Then the response field tagged_count equals 20
    And the IMAP folder "INBOX" contains 20 messages with keyword "archive"
    And the IMAP connections opened for account "gupta-scaratec" number between 1 and 2

  Scenario: bulk_mark_tagged skips METADATA-capped messages below its ENVELOPE floor
    Given policy "inbox-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | mark_tagged | rules                                   |
      | INBOX  | blacklist | FULL    | true        | [{from_domain=bank.de -> cap METADATA}] |
    And the folder "INBOX" holds messages:
      | uid | from_address    | to_address            | subject | date                 |
      | 901 | noreply@bank.de | me@gupta-scaratec.com | Beleg A | 2026-05-06T09:00:00Z |
      | 902 | vendor@obi.de   | me@gupta-scaratec.com | Beleg B | 2026-05-06T09:01:00Z |
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "7d"}, tags ["archive"], mode "add", scope "all"
    Then the response decision is ALLOW
    And the response field tagged_count equals 1
    And the IMAP message at "INBOX" uid 902 has keyword "archive"
    And the IMAP message at "INBOX" uid 901 does NOT have keyword "archive"

  Scenario: bulk_mark_tagged with has_attachment criterion skips ENVELOPE-capped messages below BODY
    Given policy "inbox-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | mark_tagged | rules                                   |
      | INBOX  | blacklist | FULL    | true        | [{from_domain=bank.de -> cap ENVELOPE}] |
    And the folder "INBOX" holds messages:
      | uid | from_address    | to_address            | subject | date                 |
      | 911 | noreply@bank.de | me@gupta-scaratec.com | BankPDF | 2026-05-06T09:00:00Z |
      | 912 | vendor@obi.de   | me@gupta-scaratec.com | ObiPDF  | 2026-05-06T09:01:00Z |
    And the message at uid 911 has attachment "b.pdf" of type "application/pdf" with content byte "B" repeated 128 times
    And the message at uid 912 has attachment "o.pdf" of type "application/pdf" with content byte "O" repeated 128 times
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"has_attachment": true}, tags ["has_pdf"], mode "add", scope "all"
    Then the response decision is ALLOW
    And the response field tagged_count equals 1
    And the IMAP message at "INBOX" uid 912 has keyword "has_pdf"
    And the IMAP message at "INBOX" uid 911 does NOT have keyword "has_pdf"
