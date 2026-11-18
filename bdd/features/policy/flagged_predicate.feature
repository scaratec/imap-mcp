Feature: Human-curated flagged predicate and agent read-only protection

  The "flagged" predicate matches messages carrying the IMAP \Flagged
  flag (the blue/yellow star in Gmail and desktop mail clients). To keep
  starred status a trustworthy human-only approval gesture on both INBOX
  and Sent Mail, agents are strictly forbidden from setting or clearing
  \Flagged via mark_tagged or bulk_mark_tagged (ADR 0041, ADR 0045).

  Covered error layers (per BDD Guidelines §4.5):
    - flagged=true grants FULL in whitelist mode (INBOX & Sent Mail) : 2
    - flagged=false matches unflagged messages                       : 1
    - flagged in blacklist mode caps unflagged messages              : 1
    - flagged composes with from_domain via AND                      : 1
    - Agent cannot set, clear, or replace \Flagged via mark_tagged   : 4 (add/remove/replace + bulk)
    Total enumerated                                                 : 9   covered by this feature: 9

  Background:
    Given the server clock is pinned to "2026-11-17T08:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
      | Sent        |
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"

  Scenario: Starred incoming mail in INBOX is readable at FULL while unstarred mail stays invisible
    Given policy "inbox-policy" grants folder:
      | folder | mode      | default | rules                    |
      | INBOX  | whitelist | NONE    | [{flagged=true -> FULL}] |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address      | subject              | flags      | date                 |
      | 501 | alice@example.com | me@scaratec.com | Starred incoming     | [\Flagged] | 2026-11-16T09:00:00Z |
      | 502 | bob@example.com   | me@scaratec.com | Unstarred private    | []         | 2026-11-16T10:00:00Z |
    And the message at uid 501 has plain text body "Freigegebener Inhalt."
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals [501]
    When inbox-agent calls fetch_body with account "gupta-scaratec", folder "INBOX", uid 501
    Then the response decision is ALLOW
    And the response field visibility_applied equals "FULL"
    And the response field text_body equals "Freigegebener Inhalt."
    And the response does not contain any field named "matched_rule_index"

  Scenario: Starred outgoing mail in Sent is readable at FULL without opening Sent to unstarred recipients
    Given policy "inbox-policy" grants folder:
      | folder | mode      | default | rules                    |
      | Sent   | whitelist | NONE    | [{flagged=true -> FULL}] |
    And the folder "Sent" holds messages:
      | uid | from_address    | to_address                    | subject                        | flags      | date                 |
      | 505 | me@scaratec.com | info@ra-buechner.de           | Mandatsanfrage BU              | [\Flagged] | 2026-11-17T06:18:00Z |
      | 506 | me@scaratec.com | lebensversicherung@allianz.de | Widerspruch AL2006             | [\Flagged] | 2026-11-16T15:00:00Z |
      | 507 | me@scaratec.com | sebastian@privat.example      | Drueckjagd                     | []         | 2026-11-16T12:00:00Z |
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "Sent", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the response contains 2 messages
    And message 0 has field "uid" equal to 505
    And message 1 has field "uid" equal to 506
    And the JSON response does NOT contain the literal strings:
      | forbidden_string         |
      | sebastian@privat.example |
      | Drueckjagd               |

  Scenario: flagged=false matches only unflagged messages
    Given policy "inbox-policy" grants folder:
      | folder | mode      | default | rules                        |
      | INBOX  | whitelist | NONE    | [{flagged=false -> ENVELOPE}]|
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address      | subject | flags      | date                 |
      | 511 | alice@example.com | me@scaratec.com | A       | [\Flagged] | 2026-11-16T09:00:00Z |
      | 512 | bob@example.com   | me@scaratec.com | B       | []         | 2026-11-16T09:00:00Z |
      | 513 | carol@example.com | me@scaratec.com | C       | [\Seen]    | 2026-11-16T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals [512, 513]

  Scenario: flagged in blacklist mode caps unflagged messages to NONE
    Given policy "inbox-policy" grants folder:
      | folder | mode      | default | rules                    |
      | INBOX  | blacklist | FULL    | [{flagged=false -> NONE}]|
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address      | subject | flags      | date                 |
      | 521 | alice@example.com | me@scaratec.com | A       | [\Flagged] | 2026-11-16T09:00:00Z |
      | 522 | bob@example.com   | me@scaratec.com | B       | []         | 2026-11-16T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals [521]

  Scenario: flagged composes with from_domain via AND
    Given policy "inbox-policy" grants folder:
      | folder | mode      | default | rules                                          |
      | INBOX  | whitelist | NONE    | [{flagged=true AND from_domain=example.com -> FULL}] |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address      | subject | flags      | date                 |
      | 531 | alice@example.com | me@scaratec.com | A       | [\Flagged] | 2026-11-16T09:00:00Z |
      | 532 | alice@example.com | me@scaratec.com | B       | []         | 2026-11-16T09:00:00Z |
      | 533 | bob@other.com     | me@scaratec.com | C       | [\Flagged] | 2026-11-16T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals [531]

  Scenario Outline: Agent cannot mutate \Flagged via mark_tagged in mode "<mode>"
    Given policy "inbox-policy" grants folder:
      | folder | mode      | default | mark_tagged | rules                                                           |
      | INBOX  | whitelist | NONE    | true        | [{flagged=true -> FULL}, {from_domain=example.com -> ENVELOPE}] |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address      | subject     | flags          | date                 |
      | 541 | alice@example.com | me@scaratec.com | Secret body | <initial_flag> | 2026-11-16T09:00:00Z |
    And the message at uid 541 has plain text body "Vertraulicher Text"
    When inbox-agent calls mark_tagged with account "gupta-scaratec", folder "INBOX", uid 541, tags ["\\Flagged"], mode "<mode>"
    Then the response decision is DENY
    And the response field reason equals "forbidden_flag"
    And the IMAP message at "INBOX" uid 541 <flag_assertion>

    Examples:
      | mode    | initial_flag | flag_assertion              |
      | add     | []           | does NOT have flag "\Flagged"|
      | remove  | [\Flagged]   | has flag "\Flagged"          |
      | replace | [\Flagged]   | has flag "\Flagged"          |

  Scenario: Agent cannot self-escalate via bulk_mark_tagged with \Flagged
    Given policy "inbox-policy" grants folder:
      | folder | mode      | default  | mark_tagged | rules                                                       |
      | INBOX  | whitelist | NONE     | true        | [{flagged=true -> FULL}, {from_domain=example.com -> ENVELOPE}] |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address      | subject     | flags | date                 |
      | 551 | alice@example.com | me@scaratec.com | Secret bulk | []    | 2026-11-16T09:00:00Z |
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["\\Flagged"], mode "add", scope "all"
    Then the response decision is DENY
    And the response field reason equals "forbidden_flag"
    And the IMAP message at "INBOX" uid 551 does NOT have flag "\Flagged"
