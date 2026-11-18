Feature: Per-folder write capabilities

  A folder policy declares seven boolean capabilities (mark_seen,
  mark_tagged, move_out, copy_out, accept_incoming, draft_append,
  draft_edit), all defaulting to false. Missing capabilities yield DENY
  with reason=capability_missing in the normalized envelope, and the
  retired 2.x capability modify_message is rejected by the policy loader
  (ADR 0044, ADR 0045).

  Covered error layers (per BDD Guidelines §4.5):
    - mark_seen allowed (true & false) and denied (2 scenarios)  : 3
    - mark_tagged allowed (add/replace/remove) and denied        : 2
    - move allowed and denied (missing move_out / incoming)      : 3
    - copy allowed and denied (missing copy_out / incoming)      : 3
    - Archive deposit-only & Drafts write-only patterns          : 2
    - create_draft denied without draft_append                   : 1
    - Policy loader rejects retired modify_message key           : 1
    Total enumerated                                             : 15  covered here: 15

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path            | special use |
      | INBOX                  |             |
      | INBOX/Rechnungen       |             |
      | Archiv/Rechnungen-2026 |             |
      | Backup/Rechnungen      |             |
      | Drafts                 | \Drafts     |
      | Trash                  | \Trash      |
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants the following folder policies:
      | folder                 | mode      | default | mark_seen | mark_tagged | move_out | copy_out | accept_incoming | draft_append | rules                               |
      | INBOX/Rechnungen       | whitelist | NONE    | true      | true        | true     | true     | false           | false        | [{from_domain=hornbach.de -> FULL}] |
      | Archiv/Rechnungen-2026 | whitelist | NONE    | false     | false       | false    | false    | true            | false        | []                                  |
      | Backup/Rechnungen      | whitelist | NONE    | false     | false       | false    | false    | true            | false        | [{from_domain=hornbach.de -> FULL}] |
      | Drafts                 | whitelist | NONE    | false     | false       | false    | false    | false           | true         | []                                  |
      | Trash                  | whitelist | NONE    | false     | false       | false    | false    | true            | false        | []                                  |

  Scenario: mark_seen sets and clears \Seen on the IMAP server when permitted
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject    | flags | date                 |
      | 501 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 1 | []    | 2026-05-06T09:00:00Z |
    When invoice-agent calls mark_seen with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 501, seen true
    Then the response decision is ALLOW
    And the IMAP message at "INBOX/Rechnungen" uid 501 has flag "\Seen"
    When invoice-agent calls mark_seen with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 501, seen false
    Then the response decision is ALLOW
    And the IMAP message at "INBOX/Rechnungen" uid 501 does NOT have flag "\Seen"

  Scenario: mark_seen without the capability is denied with capability_missing
    Given the folder "Archiv/Rechnungen-2026" holds messages:
      | uid | from_address         | to_address            | subject    | flags | date                 |
      | 510 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 2 | []    | 2026-05-06T09:00:00Z |
    When invoice-agent calls mark_seen with account "gupta-scaratec", folder "Archiv/Rechnungen-2026", uid 510, seen true
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response does not contain any field named "missing_capability"
    And the IMAP message at "Archiv/Rechnungen-2026" uid 510 does NOT have flag "\Seen"

  Scenario: mark_seen=false clear path is also denied when mark_seen capability is missing
    Given the folder "Archiv/Rechnungen-2026" holds messages:
      | uid | from_address         | to_address            | subject    | flags   | date                 |
      | 511 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 3 | [\Seen] | 2026-05-06T09:00:00Z |
    When invoice-agent calls mark_seen with account "gupta-scaratec", folder "Archiv/Rechnungen-2026", uid 511, seen false
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the IMAP message at "Archiv/Rechnungen-2026" uid 511 has flag "\Seen"

  Scenario: mark_tagged adds, replaces, and removes user keywords while preserving \Seen and \Flagged
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject    | flags             | date                 |
      | 521 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 4 | [\Seen, \Flagged] | 2026-05-06T09:00:00Z |
    When invoice-agent calls mark_tagged with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 521, tags ["$Erledigt", "$Geprueft"], mode "add"
    Then the response decision is ALLOW
    And the IMAP message at "INBOX/Rechnungen" uid 521 has keyword "$Erledigt"
    And the IMAP message at "INBOX/Rechnungen" uid 521 has keyword "$Geprueft"
    When invoice-agent calls mark_tagged with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 521, tags ["$Gebucht"], mode "replace"
    Then the response decision is ALLOW
    And the IMAP message at "INBOX/Rechnungen" uid 521 has keyword "$Gebucht"
    And the IMAP message at "INBOX/Rechnungen" uid 521 does NOT have keyword "$Erledigt"
    And the IMAP message at "INBOX/Rechnungen" uid 521 has flag "\Seen"
    And the IMAP message at "INBOX/Rechnungen" uid 521 has flag "\Flagged"
    When invoice-agent calls mark_tagged with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 521, tags ["$Gebucht"], mode "remove"
    Then the response decision is ALLOW
    And the IMAP message at "INBOX/Rechnungen" uid 521 does NOT have keyword "$Gebucht"

  Scenario: mark_tagged without the capability is denied with capability_missing
    Given the folder "Backup/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject    | flags | date                 |
      | 525 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 5 | []    | 2026-05-06T09:00:00Z |
    When invoice-agent calls mark_tagged with account "gupta-scaratec", folder "Backup/Rechnungen", uid 525, tags ["$Test"], mode "add"
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the IMAP message at "Backup/Rechnungen" uid 525 does NOT have keyword "$Test"

  Scenario: move from a folder with move_out=true to a folder with accept_incoming=true succeeds
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject       | date                 |
      | 531 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7823 | 2026-05-06T09:00:00Z |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 531}, target {"account": "gupta-scaratec", "folder": "Archiv/Rechnungen-2026"}
    Then the response decision is ALLOW
    And the response field tx_id equals null
    And the IMAP folder "INBOX/Rechnungen" does not contain uid 531
    And the IMAP folder "Archiv/Rechnungen-2026" contains exactly one message with subject "Rechnung 7823"

  Scenario: move into a folder without accept_incoming is denied
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject       | date                 |
      | 541 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7824 | 2026-05-06T09:00:00Z |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 541}, target {"account": "gupta-scaratec", "folder": "Drafts"}
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the IMAP folder "INBOX/Rechnungen" still contains uid 541

  Scenario: move out of a folder without move_out is denied
    Given the folder "Archiv/Rechnungen-2026" holds messages:
      | uid | from_address         | to_address            | subject       | date                 |
      | 551 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7825 | 2026-05-06T09:00:00Z |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "Archiv/Rechnungen-2026", "uid": 551}, target {"account": "gupta-scaratec", "folder": "Trash"}
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the IMAP folder "Archiv/Rechnungen-2026" still contains uid 551

  Scenario: Intra-account copy requires both source copy_out and target accept_incoming
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject           | date                 |
      | 555 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung Kopie OK | 2026-05-06T09:00:00Z |
    When invoice-agent calls copy with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 555}, target {"account": "gupta-scaratec", "folder": "Backup/Rechnungen"}
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the IMAP folder "INBOX/Rechnungen" still contains uid 555
    And the IMAP folder "Backup/Rechnungen" contains exactly one message with subject "Rechnung Kopie OK"

  Scenario Outline: Intra-account copy is denied when source copy_out or target accept_incoming is missing
    Given the folder "<src_folder>" holds messages:
      | uid | from_address         | to_address            | subject            | date                 |
      | 558 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung Kopie No  | 2026-05-06T09:00:00Z |
    When invoice-agent calls copy with source {"account": "gupta-scaratec", "folder": "<src_folder>", "uid": 558}, target {"account": "gupta-scaratec", "folder": "<dst_folder>"}
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the IMAP folder "<dst_folder>" does not contain a message with subject "Rechnung Kopie No"

    Examples:
      | src_folder             | dst_folder        |
      | Archiv/Rechnungen-2026 | Backup/Rechnungen |
      | INBOX/Rechnungen       | Drafts            |

  Scenario: Archive deposit-only pattern — agent can list folder and deposit into Archive but can neither read nor copy back out
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject    | date                 |
      | 560 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung D | 2026-05-06T09:00:00Z |
    And the folder "Archiv/Rechnungen-2026" holds messages:
      | uid | from_address         | to_address            | subject    | date                 |
      | 561 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung X | 2026-05-06T09:00:00Z |
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response field folders contains "Archiv/Rechnungen-2026"
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 560}, target {"account": "gupta-scaratec", "folder": "Archiv/Rechnungen-2026"}
    Then the response decision is ALLOW
    And the IMAP folder "Archiv/Rechnungen-2026" contains exactly one message with subject "Rechnung D"
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "Archiv/Rechnungen-2026", uid 561
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    When invoice-agent calls copy with source {"account": "gupta-scaratec", "folder": "Archiv/Rechnungen-2026", "uid": 561}, target {"account": "gupta-scaratec", "folder": "Backup/Rechnungen"}
    Then the response decision is DENY
    And the response field reason equals "capability_missing"

  Scenario: Drafts write-only pattern — agent can create drafts without reading existing messages in Drafts
    When invoice-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: invoice-agent@gupta-scaratec.com
      To: buchhaltung@scaratec.com
      Subject: Rechnung Entwurf
      Content-Type: text/plain; charset=UTF-8

      Entwurf einer Antwort auf Rechnung 7823.
      """
    Then the response decision is ALLOW
    And the IMAP folder "Drafts" contains exactly one message with subject "Rechnung Entwurf"
    When invoice-agent calls search with account "gupta-scaratec", folder "Drafts", criteria {}, scope "all"
    Then the response decision is DENY
    And the response field reason equals "visibility_below_METADATA"

  Scenario: create_draft on a folder without draft_append is denied
    When invoice-agent calls create_draft with account "gupta-scaratec", folder "INBOX/Rechnungen", rfc822 payload:
      """
      From: invoice-agent@gupta-scaratec.com
      To: buchhaltung@scaratec.com
      Subject: Sollte nicht moeglich sein

      Body.
      """
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the IMAP folder "INBOX/Rechnungen" does not contain a message with subject "Sollte nicht moeglich sein"

  Scenario: Policy loader rejects the retired 2.x capability modify_message
    Given the server loads a policy file containing:
      """
      name: invoice-policy
      accounts:
        gupta-scaratec:
          - path: INBOX/Rechnungen
            mode: blacklist
            default: FULL
            modify_message: true
            rules: []
      """
    Then the server refuses to start
    And the startup error indicates "modify_message"
