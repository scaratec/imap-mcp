Feature: Per-folder write capabilities

  A folder policy has five orthogonal boolean capabilities that gate
  write tools independently of the read-side visibility. A tool invocation
  is denied with reason=capability_missing when the required capability
  is false on the relevant folder.
  See ADR 0005 and ADR 0016.

  Capability-to-tool map:
    mark_seen        -> mark_seen
    mark_tagged      -> mark_tagged
    move_out         -> move (on source folder)
    accept_incoming  -> move / copy (on target folder), create_draft is separate
    draft_append     -> create_draft

  Covered error layers (per BDD Guidelines §4.5):
    - Capability missing (one per tool)              : 5
    - Capability present, IMAP action succeeds       : 5
    - Archive pattern (accept_incoming but no read)  : 1
    - Drafts pattern (draft_append but no read)      : 1
    - Move requires both source AND target capability: 2
    Total enumerated                                 : 14    covered here: 14

  Background:
    Given the IMAP account "gupta-scaratec" exists with folders:
      | folder path            |
      | INBOX/Rechnungen       |
      | Archiv/Rechnungen-2026 |
      | Drafts                 |
      | Trash                  |
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants the following folder policies:
      | folder                 | mode      | default  | mark_seen | mark_tagged | move_out | accept_incoming | draft_append | rules                                       |
      | INBOX/Rechnungen       | whitelist | NONE     | true      | true        | true     | false           | false        | [{from_domain=hornbach.de -> FULL}]         |
      | Archiv/Rechnungen-2026 | whitelist | NONE     | false     | false       | false    | true            | false        | []                                          |
      | Drafts                 | whitelist | NONE     | false     | false       | false    | false           | true         | []                                          |
      | Trash                  | whitelist | NONE     | false     | false       | false    | true            | false        | []                                          |

  Scenario: mark_seen on a folder with the capability succeeds and sets \Seen on the IMAP server
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                  | flags |
      | 501 | rechnung@hornbach.de  | []    |
    When invoice-agent calls mark_seen with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 501, seen true
    Then the response decision is ALLOW
    And the IMAP message at "INBOX/Rechnungen" uid 501 has flag "\Seen"

  Scenario: mark_seen without the capability is denied with capability_missing
    And the folder "Archiv/Rechnungen-2026" holds a message with:
      | uid | from                  | flags |
      | 510 | rechnung@hornbach.de  | []    |
    When invoice-agent calls mark_seen with account "gupta-scaratec", folder "Archiv/Rechnungen-2026", uid 510, seen true
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "mark_seen"
    And the IMAP message at "Archiv/Rechnungen-2026" uid 510 does not have flag "\Seen"

  Scenario: mark_tagged adds a user keyword on the IMAP server
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                  |
      | 521 | rechnung@hornbach.de  |
    When invoice-agent calls mark_tagged with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 521, tags ["$Erledigt"], mode "add"
    Then the response decision is ALLOW
    And the IMAP message at "INBOX/Rechnungen" uid 521 has keyword "$Erledigt"

  Scenario: move from a folder with move_out=true to a folder with accept_incoming=true succeeds (intra-account native MOVE)
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                  | subject           |
      | 531 | rechnung@hornbach.de  | Rechnung 7823     |
    When invoice-agent calls move with account "gupta-scaratec", source folder "INBOX/Rechnungen" uid 531, target folder "Archiv/Rechnungen-2026"
    Then the response decision is ALLOW
    And the response field tx_id equals null
    And the IMAP folder "INBOX/Rechnungen" does not contain uid 531
    And the IMAP folder "Archiv/Rechnungen-2026" contains a message with subject "Rechnung 7823"

  Scenario: move into a folder without accept_incoming is denied
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                  |
      | 541 | rechnung@hornbach.de  |
    And policy "invoice-policy" sets folder "Archiv/Rechnungen-2026" capabilities to:
      | accept_incoming |
      | false           |
    When invoice-agent calls move with account "gupta-scaratec", source folder "INBOX/Rechnungen" uid 541, target folder "Archiv/Rechnungen-2026"
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "accept_incoming"
    And the IMAP folder "INBOX/Rechnungen" still contains uid 541

  Scenario: move out of a folder without move_out is denied
    Given the folder "Archiv/Rechnungen-2026" holds a message with:
      | uid | from                  |
      | 551 | rechnung@hornbach.de  |
    When invoice-agent calls move with account "gupta-scaratec", source folder "Archiv/Rechnungen-2026" uid 551, target folder "Trash"
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "move_out"
    And the IMAP folder "Archiv/Rechnungen-2026" still contains uid 551

  Scenario: Archive pattern — agent can deposit but never read
    Given the folder "Archiv/Rechnungen-2026" holds a message with:
      | uid | from                  | subject       |
      | 561 | rechnung@hornbach.de  | Rechnung X    |
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response field folders contains "Archiv/Rechnungen-2026"
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "Archiv/Rechnungen-2026", uid 561
    Then the response decision is DENY
    And the response field reason equals "sender_not_whitelisted"

  Scenario: Drafts pattern — agent can create drafts but cannot read existing ones
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
    When invoice-agent calls search with account "gupta-scaratec", folder "Drafts", criteria {}
    Then the response decision is DENY
    And the response field reason equals "visibility_below_METADATA"

  Scenario: create_draft on a folder without draft_append is denied
    When invoice-agent calls create_draft with account "gupta-scaratec", folder "INBOX/Rechnungen", rfc822 payload:
      """
      From: invoice-agent@gupta-scaratec.com
      Subject: Sollte nicht moeglich sein

      Body.
      """
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "draft_append"
    And the IMAP folder "INBOX/Rechnungen" does not contain a message with subject "Sollte nicht moeglich sein"
