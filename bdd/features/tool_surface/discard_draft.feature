Feature: discard_draft moves all verified own revisions in \Drafts to \Trash by handle

  ADR 0042, ADR 0044, and ADR 0063 §3 specify discard_draft:
    - Evaluates gates in deterministic order: source \Drafts role +
      move_out=true -> account \Trash existence (trash_unavailable) ->
      \Trash accept_incoming=true (capability_missing) -> caller
      provenance verification.
    - Verifies authentic caller provenance (X-SC-Provenance + WAL
      ownership) on all matching revisions and moves them to \Trash.
    - Returns ALLOW + ERROR not_found for unknown or foreign handles,
      ALLOW + ERROR trash_unavailable if the account lacks \Trash, and
      never touches human-authored drafts without provenance.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path on standard IMAP with \Trash                      : 1
    - Happy path on localized German Gmail                         : 1
    - Residue revisions discarded together with current revision   : 1
    - Missing move_out or accept_incoming -> capability_missing    : 2
    - Account without \Trash -> ALLOW + ERROR trash_unavailable    : 1
    - Unknown handle -> ALLOW + ERROR not_found                    : 1
    - Human draft with identical subject never touched             : 1
    Total enumerated                                               : 8   covered by this feature: 8

  Background:
    Given the server clock is pinned to "2026-10-31T12:00:00Z"
    And the audit log directory is a fresh $TMPDIR/audit
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
      | Trash       | \Trash      |
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"

  Scenario: An authentic own draft is discarded by handle to Trash on standard IMAP
    Given policy "draft-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | rules | draft_append | move_out | accept_incoming |
      | Drafts | blacklist | FULL     | []    | true         | true     | false           |
      | Trash  | blacklist | METADATA | []    | false        | false    | true            |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: draft-agent@example.org
      To: mara.lindqvist@example.org
      Subject: Terminvorschlag
      MIME-Version: 1.0
      Content-Type: text/plain; charset=UTF-8

      Passt Ihnen Dienstag?
      """
    When draft-agent calls discard_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field discarded_count equals 1
    And the response field moved_to equals "Trash"
    And a direct IMAP SEARCH on "gupta-scaratec:Drafts" for header X-SC-Draft-Id equal to the returned draft_id returns zero results
    And a direct IMAP SEARCH on "gupta-scaratec:Trash" for header X-SC-Draft-Id equal to the returned draft_id returns exactly one result
    And the audit log contains an outcome record for tool "discard_draft" with decision "ALLOW" and result "OK"

  Scenario: An authentic own draft is discarded by handle on a localized German Gmail account
    Given the IMAP account "gupta-gmail-de" exists with provider "google" and folders:
      | folder path      |
      | INBOX            |
      | [Gmail]/All Mail |
      | [Gmail]/Drafts   |
      | [Gmail]/Trash    |
    And the mock-gmail server for "gupta-gmail-de" uses localized folder names:
      | canonical        | localized                | flags                  |
      | [Gmail]/All Mail | [Gmail]/Alle Nachrichten | \All \HasNoChildren    |
      | [Gmail]/Drafts   | [Gmail]/Entw&APw-rfe     | \Drafts \HasNoChildren |
      | [Gmail]/Trash    | [Gmail]/Papierkorb       | \Trash \HasNoChildren  |
    And policy "draft-policy" grants account "gupta-gmail-de"
    And policy "draft-policy" grants folder on account "gupta-gmail-de":
      | folder         | mode      | default  | rules | draft_append | move_out | accept_incoming |
      | [Gmail]/Drafts | blacklist | FULL     | []    | true         | true     | false           |
      | [Gmail]/Trash  | blacklist | METADATA | []    | false        | false    | true            |
    And draft-agent calls create_draft with account "gupta-gmail-de", folder "[Gmail]/Drafts", rfc822 payload:
      """
      From: draft-agent@example.org
      To: jonas.hartmann@example.org
      Subject: Rueckfrage Lieferung
      MIME-Version: 1.0
      Content-Type: text/plain; charset=UTF-8

      Wann koennen Sie liefern?
      """
    When draft-agent calls discard_draft with account "gupta-gmail-de", folder "[Gmail]/Drafts", the returned draft_id
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field moved_to equals "[Gmail]/Trash"
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Entw&APw-rfe" for header X-SC-Draft-Id equal to the returned draft_id returns zero results
    And a direct IMAP SEARCH on "gupta-gmail-de:[Gmail]/Papierkorb" for header X-SC-Draft-Id equal to the returned draft_id returns exactly one result

  Scenario: Authentic residue of an interrupted revision is discarded together with the current revision
    Given policy "draft-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | rules | draft_append | draft_edit | move_out | accept_incoming |
      | Drafts | blacklist | FULL     | []    | true         | true       | true     | false           |
      | Trash  | blacklist | METADATA | []    | false        | false      | false    | true            |
    And draft-agent creates a draft in "gupta-scaratec:Drafts" with subject "Angebot (alt)" and body "Woche 14"
    And an interrupted revision leaves both authentic revision 1 ("Angebot (alt)") and authentic revision 2 ("Angebot (neu)") for that draft_id in "gupta-scaratec:Drafts" with no active WAL entry
    When draft-agent calls discard_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response decision is ALLOW
    And the response field discarded_count equals 2
    And a direct IMAP SEARCH on "gupta-scaratec:Drafts" for header X-SC-Draft-Id equal to the returned draft_id returns zero results
    And a direct IMAP SEARCH on "gupta-scaratec:Trash" for header X-SC-Draft-Id equal to the returned draft_id returns 2 results

  Scenario Outline: discard_draft is denied with capability_missing when move_out or accept_incoming is missing
    Given policy "draft-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | rules | draft_append | move_out          | accept_incoming         |
      | Drafts | blacklist | FULL     | []    | true         | <drafts_move_out> | false                   |
      | Trash  | blacklist | METADATA | []    | false        | false             | <trash_accept_incoming> |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["mara@example.org"], "subject": "Kurze Notiz", "body_text": "Text"}
      """
    When draft-agent calls discard_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response does not contain any field named "missing_capability"
    And a direct IMAP SEARCH on "gupta-scaratec:Drafts" for header X-SC-Draft-Id equal to the returned draft_id returns exactly one result
    And a direct IMAP SEARCH on "gupta-scaratec:Trash" for header X-SC-Draft-Id equal to the returned draft_id returns zero results

    Examples:
      | drafts_move_out | trash_accept_incoming |
      | false           | true                  |
      | true            | false                 |

  Scenario: Account without a \Trash folder returns trash_unavailable and moves nothing
    Given the IMAP account "info-bg" exists with folders:
      | folder path  | special use |
      | INBOX        |             |
      | INBOX/Drafts | \Drafts     |
    And policy "draft-policy" grants account "info-bg"
    And policy "draft-policy" grants folder on account "info-bg":
      | folder       | mode      | default | rules | draft_append | move_out |
      | INBOX/Drafts | blacklist | FULL    | []    | true         | true     |
    And draft-agent calls create_draft with account "info-bg", folder "INBOX/Drafts", fields:
      """
      {"to": ["mara@example.org"], "subject": "Entwurf BG", "body_text": "Text"}
      """
    When draft-agent calls discard_draft with account "info-bg", folder "INBOX/Drafts", the returned draft_id
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "trash_unavailable"
    And a direct IMAP SEARCH on "info-bg:INBOX/Drafts" for header X-SC-Draft-Id equal to the returned draft_id returns exactly one result

  Scenario: Unknown handle returns ALLOW + ERROR not_found and leaves existing mail untouched
    Given policy "draft-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | rules | move_out | accept_incoming |
      | Drafts | blacklist | FULL     | []    | true     | false           |
      | Trash  | blacklist | METADATA | []    | false    | true            |
    And the folder "Drafts" holds messages:
      | uid | from_address      | to_address     | subject         | date                 |
      | 61  | owner@example.org | b@example.org  | Eigener Entwurf | 2026-10-30T08:00:00Z |
    When draft-agent calls discard_draft with account "gupta-scaratec", folder "Drafts", draft_id "00000000-0000-4000-8000-000000000000"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    And the IMAP folder "Drafts" still contains uid 61

  Scenario: Human-authored draft with identical subject is untouched when discarding agent draft
    Given policy "draft-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | rules | draft_append | move_out | accept_incoming |
      | Drafts | blacklist | FULL     | []    | true         | true     | false           |
      | Trash  | blacklist | METADATA | []    | false        | false    | true            |
    And the folder "Drafts" holds messages:
      | uid | from_address      | to_address             | subject         | date                 |
      | 71  | owner@example.org | mara.lindqvist@example.org | Terminvorschlag | 2026-10-30T08:00:00Z |
    And draft-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["mara.lindqvist@example.org"], "subject": "Terminvorschlag", "body_text": "Dienstag?"}
      """
    When draft-agent calls discard_draft with account "gupta-scaratec", folder "Drafts", the returned draft_id
    Then the response field discarded_count equals 1
    And the IMAP folder "Drafts" still contains uid 71
    And a direct IMAP SEARCH on "gupta-scaratec:Drafts" for header X-SC-Draft-Id equal to the returned draft_id returns zero results
