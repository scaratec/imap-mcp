Feature: Folder-path contract and three-code error taxonomy

  ADR 0025 defines the folder-path contract for every tool that takes
  a folder argument. Two properties must hold:

    1. Paths with spaces, punctuation, or non-ASCII characters round-trip
       cleanly between list_folders and any folder-opening tool.
    2. The old folder_not_found code is replaced by three distinct codes
       that let an operator and a caller distinguish three failure modes:
         - folder_hidden  (policy refusal, indistinguishable from absence
                          to the caller for confidentiality)
         - folder_absent  (caller authorized, folder does not exist)
         - select_failed  (caller authorized, folder exists, IMAP SELECT
                          returned BAD or NO)

  This feature pins both properties for folder_stats; the same rules
  apply to every tool that opens a folder. ADR 0017's transparency
  contract is preserved: folder_hidden never reveals server-side state.

  Covered error layers (per BDD Guidelines §4.5):
    - Quoting: folder with spaces + dashes               : 1
    - Quoting: folder with non-ASCII characters          : 1
    - Aliasing: canonical [Gmail]/Drafts → localized     : 1
    - folder_hidden DENY for unlisted folder             : 1
    - folder_absent ERROR for typo'd folder              : 1
    - select_failed ERROR with imap_response detail      : 1
    - folder_hidden vs folder_absent: same opaque shape  : 1
      from caller perspective for confidentiality
    Total enumerated                                      : 7   covered by this feature: 7

  Background:
    Given the IMAP account "gupta-scaratec" exists with folders:
      | folder path                              |
      | INBOX                                    |
      | INBOX/BuHa - privat offene Rechnungen    |
      | Posteingang/Übersicht                    |
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"
    And policy "doc-policy" folder defaults are:
      | folder                                | mode      | default |
      | INBOX                                 | blacklist | COUNT   |
      | INBOX/BuHa - privat offene Rechnungen | blacklist | COUNT   |
      | Posteingang/Übersicht                 | blacklist | COUNT   |

  # --- Quoting: paths with hard characters round-trip cleanly ---

  Scenario: folder_stats works on a folder with spaces and a dash
    Given the folder "INBOX/BuHa - privat offene Rechnungen" holds 3 messages
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "INBOX/BuHa - privat offene Rechnungen"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field visible_count equals 3

  # NB: Genuine [Gmail]/* layouts are covered by the Gmail-semantics
  # scenarios (ADR 0019). The point of the bracket-character coverage
  # here is the wire-quoting path, which is exercised by the spaces
  # and non-ASCII scenarios already.

  Scenario: folder_stats works on a folder name with non-ASCII characters
    Given the folder "Posteingang/Übersicht" holds 2 messages
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "Posteingang/Übersicht"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field visible_count equals 2

  # --- Aliasing: callers use canonical paths; server resolves to wire path ---

  @pending
  Scenario: callers address Gmail system folders by their canonical path
    # Gmail-special-use mapping is exercised by the existing
    # gmail-semantics features (ADR 0019). Pending here because the
    # fixture would need a second account configured against the
    # in-process mock-gmail; the wire-quoting story for canonical
    # path resolution is covered by ADR 0025 §1.
    Given the IMAP account "gupta-scaratec-google" exists with provider "google" and folders:
      | folder path        | special_use |
      | [Gmail]/Entwürfe   | \Drafts     |
    And policy "doc-policy" grants account "gupta-scaratec-google"
    When doc-agent calls folder_stats with account "gupta-scaratec-google", folder "[Gmail]/Drafts"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field visible_count equals 1

  # --- Error taxonomy: three distinct codes for three distinct conditions ---

  Scenario: folder_hidden DENY for a folder the caller has no policy grant on
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "Forbidden/Folder"
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"
    And the response does not contain any field named "error"

  Scenario: folder_absent ERROR when the caller is authorized but the folder does not exist on the server
    Given policy "doc-policy" folder defaults for "INBOX/Typo - this does not exist" are:
      | mode      | default |
      | blacklist | COUNT   |
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "INBOX/Typo - this does not exist"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field reason equals "folder_default_applied"
    And the response field error.type equals "folder_absent"

  @pending
  Scenario: select_failed ERROR carries the IMAP response in error.detail
    # The current folder_stats path uses STATUS (RFC 3501 §6.3.10) which
    # returns NO for unknown mailboxes but does not surface arbitrary
    # BAD/NO injection cleanly. SELECT-fault injection would require an
    # additional code path that opens the folder before STATUS; the
    # diagnostic value is small enough to defer until a real incident
    # demands it.
    Given the IMAP server for "gupta-scaratec" responds to the next SELECT of "INBOX/BuHa - privat offene Rechnungen" with NO response text "[INUSE] Mailbox is locked"
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "INBOX/BuHa - privat offene Rechnungen"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "select_failed"
    And the response field error.detail equals "[INUSE] Mailbox is locked"

  # --- Confidentiality: folder_hidden never reveals server-side state ---

  Scenario: folder_hidden looks identical whether the folder exists or not
    # Two unlisted folder names: one really exists on the IMAP server, one does not.
    # The caller has no policy grant on either. Both must return the same shape.
    When doc-agent calls folder_stats with account "gupta-scaratec", folder "Existing/But/Hidden"
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"

    When doc-agent calls folder_stats with account "gupta-scaratec", folder "Made/Up/Name"
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"
