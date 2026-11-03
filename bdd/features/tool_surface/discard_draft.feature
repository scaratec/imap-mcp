@pending
Feature: discard_draft moves every revision of a draft to the account's trash by its handle

  ADR 0030 hides UIDs: create_draft, get_draft, list_drafts and
  update_draft address a draft only by its draft_id. ADR 0031 says a
  draft is discarded with move, and move takes a UID. A caller holding
  only the handle therefore has to find the UID by searching the drafts
  folder for the subject, and may pick a human's draft with the same
  subject. This feature closes the gap without reopening ADR 0018: the
  draft is moved to the trash folder, never hard-deleted, and the same
  capabilities as for move apply (move_out on the drafts folder,
  accept_incoming on the trash folder).

  ADR 0034 (amends 0018, 0027 with trash_unavailable, and 0031).

  What is at stake. Agents create a corrected draft instead of revising
  the old one, and the old one stays behind for the owner to delete by
  hand. Without a handle-based discard, the clean-up step is a guess.

  Independent-channel rule. Every Then about where a draft ended up is
  a direct IMAP SEARCH against the IMAP server for the X-SC-Draft-Id
  header, independent of the server's response and of list_drafts.

  Rules used for the numbers below:
    - The trash folder is the folder carrying the RFC 6154 \Trash
      special-use flag on that account.
    - Every message carrying the handle in the named folder is moved,
      residue of an interrupted revision included (ADR 0030 §2).

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path, Gmail with localized folder names                : 1
    - Happy path, standard IMAP with a \Trash folder               : 1
    - Residue revisions are discarded together                     : 1
    - move_out missing on the drafts folder                        : 1
    - accept_incoming missing on the trash folder                  : 1
    - Account has no \Trash folder                                 : 1
    - Unknown handle                                               : 1
    - Message without a handle is never touched                    : 1
    Total enumerated                                               : 8   covered by this feature: 8

  Background:
    Given the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
      | Trash       | \Trash      |
    And the server is configured with caller "draft-agent" using policy "draft-policy"
    And policy "draft-policy" grants account "gupta-scaratec"

  # --- Main path -------------------------------------------------------

  Scenario: A draft created through the server is discarded by its handle on standard IMAP
    Given policy "draft-policy" grants folder:
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
    And the audit log contains an entry with:
      | field    | value         |
      | tool     | discard_draft |
      | decision | ALLOW         |
      | result   | OK            |

  Scenario: A draft is discarded by its handle on a Gmail account with localized folder names
    Given the IMAP account "gupta-gmail-de" exists with provider "google" and folders:
      | folder path      |
      | INBOX            |
      | [Gmail]/All Mail |
      | [Gmail]/Drafts   |
      | [Gmail]/Trash    |
    And the mock-gmail server uses localized folder names:
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

  Scenario: Residue of an interrupted revision is discarded together with the current revision
    Given policy "draft-policy" grants folder:
      | folder | mode      | default  | rules | move_out | accept_incoming |
      | Drafts | blacklist | FULL     | []    | true     | false           |
      | Trash  | blacklist | METADATA | []    | false    | true            |
    And the IMAP folder "Drafts" holds drafts carrying the handle "8f14e45f-ceea-4e7a-9d2b-0a1c3b5d7e90":
      | uid | revision | subject          |
      | 41  | 2        | Angebot (alt)    |
      | 42  | 3        | Angebot (neu)    |
    When draft-agent calls discard_draft with account "gupta-scaratec", folder "Drafts", draft_id "8f14e45f-ceea-4e7a-9d2b-0a1c3b5d7e90"
    Then the response decision is ALLOW
    And the response field discarded_count equals 2
    And a direct IMAP SEARCH on "gupta-scaratec:Drafts" for header X-SC-Draft-Id "8f14e45f-ceea-4e7a-9d2b-0a1c3b5d7e90" returns zero results
    And a direct IMAP SEARCH on "gupta-scaratec:Trash" for header X-SC-Draft-Id "8f14e45f-ceea-4e7a-9d2b-0a1c3b5d7e90" returns exactly 2 results

  # --- Negative controls: capabilities ---------------------------------

  Scenario Outline: discard_draft is refused when a capability of move is missing
    Given policy "draft-policy" grants folder:
      | folder | mode      | default  | rules | move_out          | accept_incoming          |
      | Drafts | blacklist | FULL     | []    | <drafts_move_out> | false                    |
      | Trash  | blacklist | METADATA | []    | false             | <trash_accept_incoming>  |
    And the IMAP folder "Drafts" holds drafts carrying the handle "c9f0f895-fb98-4b91-9d4e-5a6b7c8d9e01":
      | uid | revision | subject       |
      | 51  | 1        | Kurze Notiz   |
    When draft-agent calls discard_draft with account "gupta-scaratec", folder "Drafts", draft_id "c9f0f895-fb98-4b91-9d4e-5a6b7c8d9e01"
    Then the response decision is DENY
    And the response field reason equals "capability_missing"
    And the response field missing_capability equals "<missing>"
    And the IMAP folder "Drafts" still contains uid 51
    And a direct IMAP SEARCH on "gupta-scaratec:Trash" for header X-SC-Draft-Id "c9f0f895-fb98-4b91-9d4e-5a6b7c8d9e01" returns zero results

    Examples:
      | drafts_move_out | trash_accept_incoming | missing         |
      | false           | true                  | move_out        |
      | true            | false                 | accept_incoming |

  # --- Error paths -----------------------------------------------------

  Scenario: An account without a trash folder reports trash_unavailable and moves nothing
    Given the IMAP account "info-bg" exists with folders:
      | folder path  | special use |
      | INBOX        |             |
      | INBOX/Drafts | \Drafts     |
    And policy "draft-policy" grants account "info-bg"
    And policy "draft-policy" grants folder on account "info-bg":
      | folder       | mode      | default | rules | move_out |
      | INBOX/Drafts | blacklist | FULL    | []    | true     |
    And the IMAP folder "INBOX/Drafts" on "info-bg" holds drafts carrying the handle "45c48cce-2e2d-4fbd-a1b2-c3d4e5f60718":
      | uid | revision | subject     |
      | 7   | 1        | Entwurf BG  |
    When draft-agent calls discard_draft with account "info-bg", folder "INBOX/Drafts", draft_id "45c48cce-2e2d-4fbd-a1b2-c3d4e5f60718"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "trash_unavailable"
    And the IMAP folder "INBOX/Drafts" on "info-bg" still contains uid 7

  Scenario: An unknown handle reports draft_not_found and moves nothing
    Given policy "draft-policy" grants folder:
      | folder | mode      | default  | rules | move_out | accept_incoming |
      | Drafts | blacklist | FULL     | []    | true     | false           |
      | Trash  | blacklist | METADATA | []    | false    | true            |
    And the IMAP folder "Drafts" holds messages:
      | uid | from                   | subject          | date                 |
      | 61  | owner@example.org      | Eigener Entwurf  | 2026-10-30T08:00:00Z |
    When draft-agent calls discard_draft with account "gupta-scaratec", folder "Drafts", draft_id "00000000-0000-4000-8000-000000000000"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "draft_not_found"
    And the IMAP folder "Drafts" still contains uid 61

  Scenario: A draft without a handle in the same folder is never touched by a discard
    # The owner's own drafts (written in a mail client) carry no
    # X-SC-Draft-Id. A discard of the agent's draft must not touch them,
    # even when the subject is identical.
    Given policy "draft-policy" grants folder:
      | folder | mode      | default  | rules | move_out | accept_incoming |
      | Drafts | blacklist | FULL     | []    | true     | false           |
      | Trash  | blacklist | METADATA | []    | false    | true            |
    And the IMAP folder "Drafts" holds messages:
      | uid | from              | subject         | date                 |
      | 71  | owner@example.org | Terminvorschlag | 2026-10-30T08:00:00Z |
    And the IMAP folder "Drafts" holds drafts carrying the handle "d3d94468-02a4-4b5a-8c6d-7e8f9a0b1c2d":
      | uid | revision | subject         |
      | 72  | 1        | Terminvorschlag |
    When draft-agent calls discard_draft with account "gupta-scaratec", folder "Drafts", draft_id "d3d94468-02a4-4b5a-8c6d-7e8f9a0b1c2d"
    Then the response field discarded_count equals 1
    And the IMAP folder "Drafts" still contains uid 71
    And the IMAP folder "Drafts" does not contain uid 72
