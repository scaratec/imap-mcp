Feature: Non-interference for NONE objects and field-level predicate gating

  ADR 0043 and ADR 0044 require two strict confidentiality properties:
    1. Non-interference for NONE: adding, altering, or deleting messages
       whose effective visibility is NONE must not change any response
       field, count, error class, or side-effect of any tool.
    2. Field-level predicate gating: search and bulk criteria may inspect
       an attribute only on messages whose effective visibility reaches
       that predicate's minimum level (METADATA for time/size/flagged,
       ENVELOPE for addresses/subject, BODY for has_attachment).

  Covered error layers (per BDD Guidelines §4.5):
    - Two-world non-interference across search, list_messages, folder_stats, bulk_mark : 5
    - Indistinguishable not_found + zero mutation on NONE vs missing UID               : 3
    - Folder-level predicate floor denial (METADATA + subject, ENVELOPE + attachment)  : 2
    - Per-message predicate floor filter (mixed METADATA/ENVELOPE + FULL in one folder): 2
    Total enumerated                                                                   : 12  covered here: 12

  Background:
    Given the server clock is pinned to "2026-11-17T08:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Sent        | \Sent       |
      | Archiv      |             |
    And the server is configured with caller "triage-agent" using policy "triage-policy"
    And policy "triage-policy" grants account "gupta-scaratec"

  Scenario Outline: Adding NONE messages in Sent Mail leaves <tool> output byte-for-byte identical after positive control
    Given policy "triage-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | mark_seen | mark_tagged | rules                             |
      | Sent   | whitelist | NONE    | true      | true        | [{to_domain=moelders.de -> FULL}] |
    And the folder "Sent" holds messages:
      | uid | from_address    | to_address          | subject             | date                 |
      | 101 | me@scaratec.com | verkauf@moelders.de | Bestellung Abholung | 2026-11-16T10:00:00Z |
    When triage-agent calls <call_without_secret>
    Then the response decision is ALLOW
    And the response field <witness_field> equals <witness_value>
    And the JSON response is remembered as "world_without_none"
    When additional messages are seeded into folder "Sent":
      | uid | from_address    | to_address                    | subject            | date                 |
      | 102 | me@scaratec.com | lebensversicherung@allianz.de | Widerspruch AL2006 | 2026-11-16T15:00:00Z |
      | 103 | me@scaratec.com | info@ra-buechner.de           | Mandatsanfrage BU  | 2026-11-17T06:18:00Z |
    And triage-agent calls <call_without_secret>
    Then the JSON response equals the remembered "world_without_none"
    And the IMAP message at "Sent" uid 102 does NOT have keyword "$Probed"
    And the IMAP message at "Sent" uid 103 does NOT have keyword "$Probed"

    Examples:
      | tool             | witness_field | witness_value | call_without_secret                                                                                                                     |
      | search_all       | uids          | [101]         | search with account "gupta-scaratec", folder "Sent", criteria {"newer_than": "3d"}, scope "all"                                         |
      | search_targeted  | total_visible | 0             | search with account "gupta-scaratec", folder "Sent", criteria {"to_domain": "allianz.de", "newer_than": "3d"}, scope "all"              |
      | list_messages    | total_visible | 1             | list_messages with account "gupta-scaratec", folder "Sent", criteria {}, scope "recent"                                                 |
      | folder_stats     | visible_count | 1             | folder_stats with account "gupta-scaratec", folder "Sent"                                                                               |
      | bulk_mark_tagged | tagged_count  | 1             | bulk_mark_tagged with account "gupta-scaratec", folder "Sent", criteria {"newer_than": "3d"}, tags ["$Probed"], mode "add", scope "all" |

  Scenario Outline: Direct UID access via <tool> returns identical not_found for a NONE message and leaves it unmutated on IMAP
    Given policy "triage-policy" grants the following folder policies:
      | folder | mode      | default | mark_seen | move_out | accept_incoming | rules                             |
      | Sent   | whitelist | NONE    | true      | true     | false           | [{to_domain=moelders.de -> FULL}] |
      | Archiv | whitelist | NONE    | false     | false    | true            | []                                |
    And the folder "Sent" holds messages:
      | uid | from_address    | to_address          | subject               | flags | date                 |
      | 201 | me@scaratec.com | info@ra-buechner.de | Mandatsanfrage Geheim | []    | 2026-11-17T06:18:00Z |
    And the folder "Sent" contains no message with uid 9999
    When triage-agent calls <call_none_uid>
    And the JSON response is remembered as "none_uid_response"
    When triage-agent calls <call_missing_uid>
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    And the JSON response has the exact same keys and values except "uid" as "none_uid_response"
    And the IMAP message at "Sent" uid 201 does NOT have flag "\Seen"
    And the IMAP folder "Sent" still contains uid 201
    And the IMAP folder "Archiv" is empty

    Examples:
      | tool           | call_none_uid                                                                                                                         | call_missing_uid                                                                                                                       |
      | fetch_envelope | fetch_envelope with account "gupta-scaratec", folder "Sent", uid 201                                                                  | fetch_envelope with account "gupta-scaratec", folder "Sent", uid 9999                                                                  |
      | mark_seen      | mark_seen with account "gupta-scaratec", folder "Sent", uid 201, seen true                                                            | mark_seen with account "gupta-scaratec", folder "Sent", uid 9999, seen true                                                            |
      | move           | move with source {"account": "gupta-scaratec", "folder": "Sent", "uid": 201}, target {"account": "gupta-scaratec", "folder": "Archiv"} | move with source {"account": "gupta-scaratec", "folder": "Sent", "uid": 9999}, target {"account": "gupta-scaratec", "folder": "Archiv"} |

  Scenario Outline: Folder whose maximum visibility is below a criterion's required level denies search before IMAP I/O
    Given the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And policy "triage-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default   | rules |
      | INBOX  | blacklist | <default> | []    |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address      | subject         | date                 |
      | 301 | alice@example.com | me@scaratec.com | Geheimnis 12345 | 2026-11-16T09:00:00Z |
    And the message at uid 301 has attachment "secret.pdf" of type "application/pdf" with content byte "S" repeated 128 times
    When triage-agent calls search with account "gupta-scaratec", folder "INBOX", criteria <criteria>, scope "all"
    Then the response decision is DENY
    And the response field reason equals "<expected_reason>"
    And the IMAP command log for "gupta-scaratec" contains no SEARCH command

    Examples:
      | default  | criteria                          | expected_reason           |
      | METADATA | {"subject_contains": "Geheimnis"} | visibility_below_ENVELOPE |
      | ENVELOPE | {"has_attachment": true}          | visibility_below_BODY     |

  Scenario: Per-message predicate gating excludes METADATA-capped messages from subject_contains queries
    Given policy "triage-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules                                   |
      | INBOX  | blacklist | FULL    | [{from_domain=bank.de -> cap METADATA}] |
    And the folder "INBOX" holds messages:
      | uid | from_address         | to_address      | subject              | date                 |
      | 401 | noreply@bank.de      | me@scaratec.com | Rechnung Geheimkonto | 2026-11-16T09:00:00Z |
      | 402 | rechnung@hornbach.de | me@scaratec.com | Rechnung Baumarkt    | 2026-11-16T10:00:00Z |
    When triage-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"subject_contains": "Rechnung"}, scope "all"
    Then the response field uids equals in exact order [402]
    And the response field total_visible equals 1
    When triage-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"subject_contains": "Geheimkonto"}, scope "all"
    Then the response field uids equals []
    And the response field total_visible equals 0

  Scenario: Per-message predicate gating excludes ENVELOPE-capped messages from has_attachment queries
    Given policy "triage-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules                                   |
      | INBOX  | blacklist | FULL    | [{from_domain=bank.de -> cap ENVELOPE}] |
    And the folder "INBOX" holds messages:
      | uid | from_address         | to_address      | subject    | date                 |
      | 411 | noreply@bank.de      | me@scaratec.com | Bankbeleg  | 2026-11-16T09:00:00Z |
      | 412 | rechnung@hornbach.de | me@scaratec.com | Marktbeleg | 2026-11-16T10:00:00Z |
    And the message at uid 411 has attachment "bank.pdf" of type "application/pdf" with content byte "K" repeated 128 times
    And the message at uid 412 has attachment "markt.pdf" of type "application/pdf" with content byte "B" repeated 128 times
    When triage-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"has_attachment": true}, scope "all"
    Then the response field uids equals in exact order [412]
    And the response field total_visible equals 1
