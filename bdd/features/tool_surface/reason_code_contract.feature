Feature: Closed 3.0.0 reason-code and error-type registry contract

  ADR 0044 and ADR 0062 define docs/contract/registry.yaml as the
  single closed source of truth for caller ALLOW reasons, caller DENY
  reasons, caller error.type codes, and audit-only reasons (bounded by
  LIM 0024). Legacy reason codes (folder_not_found, sender_not_whitelisted,
  sender_blacklisted, forbidden_system_flag) are never emitted to callers.

  Covered error layers (per BDD Guidelines §4.5):
    - Registry matches visible ALLOW, DENY, and error.type tables   : 1
    - Reachability & variance of caller ALLOW & DENY codes          : 19
    - Multi-tool folder taxonomy sweep (hidden, absent, select_failed) : 6
    - Every audit/response code emitted across scenarios is in registry : 1
    Total enumerated                                                : 27  covered by this feature: 27

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the audit log directory is a fresh $TMPDIR/audit
    And secret_store configuration is:
      """
      backend: file_dir
      path: $TMPDIR/secrets
      acknowledge_plaintext: true
      """
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path      | special use |
      | INBOX            |             |
      | INBOX/Rechnungen |             |
      | Banking          |             |
      | Drafts           | \Drafts     |
    And the IMAP account "osthues-mail" exists with folders:
      | folder path |
      | INBOX       |
    And the server is configured with single caller:
      | caller_id     | auth_type     | policy         |
      | invoice-agent | stdio_trusted | invoice-policy |
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants folder on account "gupta-scaratec":
      | folder           | mode      | default | mark_tagged | draft_append | draft_recipient_domains | rules                                   |
      | INBOX/Rechnungen | whitelist | NONE    | true        | false        |                         | [{from_domain=hornbach.de -> ENVELOPE}] |
      | Drafts           | blacklist | FULL    | false       | true         | ["scaratec.com"]        | []                                      |

  Scenario: docs/contract/registry.yaml declares the exact 3.0.0 caller ALLOW and DENY reason sets
    Then docs/contract/registry.yaml declares caller_allow_reasons equal to:
      | code                   |
      | authenticated          |
      | folder_default_applied |
      | rule_matched           |
    And docs/contract/registry.yaml declares caller_deny_reasons equal to:
      | code                             |
      | auth_failed                      |
      | unknown_tool                     |
      | account_hidden                   |
      | needs_rebootstrap                |
      | folder_hidden                    |
      | policy_alias_conflict            |
      | visibility_below_COUNT           |
      | visibility_below_METADATA        |
      | visibility_below_ENVELOPE        |
      | visibility_below_HEADERS         |
      | visibility_below_BODY            |
      | visibility_below_FULL            |
      | capability_missing               |
      | forbidden_flag                   |
      | relocation_would_escalate        |
      | recipient_not_allowed            |
      | tool_not_applicable_for_provider |
    And docs/contract/registry.yaml declares caller_error_types equal to:
      | code                        |
      | not_found                   |
      | folder_absent               |
      | select_failed               |
      | uidvalidity_changed         |
      | append_rejected             |
      | append_timeout              |
      | append_failed               |
      | empty_reply_text            |
      | missing_message_id          |
      | attachment_not_found        |
      | sink_not_configured         |
      | sink_not_writable           |
      | draft_not_editable          |
      | draft_revision_conflict     |
      | draft_ambiguous             |
      | trash_unavailable           |
      | saga_aborted                |
      | transient_imap_failure      |
      | unsupported_imap_capability |
      | pool_exhausted              |
      | secret_unavailable          |
      | internal_error              |

  Scenario: authenticated is returned on meta tools
    When invoice-agent calls get_caller_identity
    Then the response decision is ALLOW
    And the response field reason equals "authenticated"

  Scenario: rule_matched is returned when a whitelist rule grants visibility
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject       | date                 |
      | 901 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7823 | 2026-05-06T09:00:00Z |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 901
    Then the response decision is ALLOW
    And the response field reason equals "rule_matched"

  Scenario: folder_default_applied is returned when a blacklist default grants visibility
    Given policy "invoice-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    And the folder "INBOX" holds messages:
      | uid | from_address        | to_address            | subject | date                 |
      | 902 | someone@example.org | me@gupta-scaratec.com | Hallo   | 2026-05-06T09:00:00Z |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 902
    Then the response decision is ALLOW
    And the response field reason equals "folder_default_applied"

  Scenario: account_hidden is returned for an ungranted account
    When invoice-agent calls fetch_envelope with account "osthues-mail", folder "INBOX", uid 1
    Then the response decision is DENY
    And the response field reason equals "account_hidden"

  Scenario: folder_hidden is returned for an ungranted folder
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "Banking", uid 1
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"

  Scenario Outline: visibility_below_<needed> is returned when granted level is <granted>
    Given policy "invoice-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules                                    |
      | INBOX  | whitelist | NONE    | [{from_domain=hornbach.de -> <granted>}] |
    And the folder "INBOX" holds messages:
      | uid | from_address         | to_address            | subject    | date                 |
      | 910 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung X | 2026-05-06T09:00:00Z |
    When invoice-agent calls <call>
    Then the response decision is DENY
    And the response field reason equals "visibility_below_<needed>"

    Examples:
      | granted  | needed   | call                                                                                    |
      | COUNT    | METADATA | search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"          |
      | METADATA | ENVELOPE | fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 910                   |
      | ENVELOPE | HEADERS  | fetch_headers with account "gupta-scaratec", folder "INBOX", uid 910                    |
      | HEADERS  | BODY     | fetch_body with account "gupta-scaratec", folder "INBOX", uid 910                       |
      | BODY     | FULL     | fetch_attachment with account "gupta-scaratec", folder "INBOX", uid 910, part_id 0      |

  Scenario: visibility_below_COUNT is returned on folder_stats when max_visibility is NONE
    Given policy "invoice-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules |
      | INBOX  | whitelist | NONE    | []    |
    When invoice-agent calls folder_stats with account "gupta-scaratec", folder "INBOX"
    Then the response decision is DENY
    And the response field reason equals "visibility_below_COUNT"

  Scenario: visibility_below_COUNT is also returned on list_labels when a Google account grants only write-only NONE folders
    Given the IMAP account "g-none" exists with provider "google" and folders:
      | folder path    |
      | [Gmail]/Drafts |
    And policy "invoice-policy" grants account "g-none"
    And policy "invoice-policy" grants folder on account "g-none":
      | folder         | mode      | default | draft_append | rules |
      | [Gmail]/Drafts | whitelist | NONE    | true         | []    |
    When invoice-agent calls list_labels with account "g-none"
    Then the response decision is DENY
    And the response field reason equals "visibility_below_COUNT"

  Scenario: visibility_below_HEADERS is also returned via a blacklist cap reducing FULL to ENVELOPE
    Given policy "invoice-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules                                   |
      | INBOX  | blacklist | FULL    | [{from_domain=bank.de -> cap ENVELOPE}] |
    And the folder "INBOX" holds messages:
      | uid | from_address    | to_address            | subject     | date                 |
      | 915 | noreply@bank.de | me@gupta-scaratec.com | Kontoauszug | 2026-05-06T09:00:00Z |
    When invoice-agent calls fetch_headers with account "gupta-scaratec", folder "INBOX", uid 915
    Then the response decision is DENY
    And the response field reason equals "visibility_below_HEADERS"

  Scenario: capability_missing is returned when mark_seen is false
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject | date                 |
      | 920 | rechnung@hornbach.de | me@gupta-scaratec.com | R 920   | 2026-05-06T09:00:00Z |
    When invoice-agent calls mark_seen with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 920, seen true
    Then the response decision is DENY
    And the response field reason equals "capability_missing"

  Scenario: forbidden_flag is returned when mark_tagged receives a system flag
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject | date                 |
      | 921 | rechnung@hornbach.de | me@gupta-scaratec.com | R 921   | 2026-05-06T09:00:00Z |
    When invoice-agent calls mark_tagged with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 921, tags ["\\Deleted"], mode "add"
    Then the response decision is DENY
    And the response field reason equals "forbidden_flag"

  Scenario: relocation_would_escalate is returned when moving an ENVELOPE message into a FULL folder
    Given policy "invoice-policy" grants folder on account "gupta-scaratec":
      | folder           | mode      | default  | move_out | accept_incoming | rules                                   |
      | INBOX/Rechnungen | whitelist | NONE     | true     | false           | [{from_domain=hornbach.de -> ENVELOPE}] |
      | INBOX            | blacklist | FULL     | false    | true            | []                                      |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject | date                 |
      | 922 | rechnung@hornbach.de | me@gupta-scaratec.com | R 922   | 2026-05-06T09:00:00Z |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 922}, target {"account": "gupta-scaratec", "folder": "INBOX"}
    Then the response decision is DENY
    And the response field reason equals "relocation_would_escalate"

  Scenario: recipient_not_allowed is returned when create_draft targets an unlisted domain
    When invoice-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["outside@evil.example"], "subject": "Blocked", "body_text": "No"}
      """
    Then the response decision is DENY
    And the response field reason equals "recipient_not_allowed"

  Scenario: tool_not_applicable_for_provider is returned when list_labels is called on standard IMAP
    When invoice-agent calls list_labels with account "gupta-scaratec"
    Then the response decision is DENY
    And the response field reason equals "tool_not_applicable_for_provider"

  Scenario: tool_not_applicable_for_provider is also returned on a second granted standard-IMAP account
    Given policy "invoice-policy" grants account "osthues-mail"
    And policy "invoice-policy" grants folder on account "osthues-mail":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    When invoice-agent calls list_labels with account "osthues-mail"
    Then the response decision is DENY
    And the response field reason equals "tool_not_applicable_for_provider"

  Scenario Outline: Folder-opening tool <tool> distinguishes folder_hidden, folder_absent, and select_failed without retired codes
    Given the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And policy "invoice-policy" grants folder on account "gupta-scaratec":
      | folder           | mode      | default  | mark_seen | rules |
      | INBOX            | blacklist | FULL     | true      | []    |
      | MissingOnServer  | blacklist | FULL     | true      | []    |
    When invoice-agent calls <hidden_call>
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"
    When invoice-agent calls <absent_call>
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "folder_absent"
    Given the IMAP proxy for "gupta-scaratec" rejects the next SELECT or EXAMINE of "INBOX" with "NO [INUSE] Mailbox locked"
    When invoice-agent calls <select_fail_call>
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "select_failed"
    And none of the captured responses or audit entries in this scenario carry any of ["folder_not_found", "sender_not_whitelisted", "sender_blacklisted"]

    Examples:
      | tool           | hidden_call                                                                  | absent_call                                                                         | select_fail_call                                                             |
      | folder_stats   | folder_stats with account "gupta-scaratec", folder "Banking"                 | folder_stats with account "gupta-scaratec", folder "MissingOnServer"                | folder_stats with account "gupta-scaratec", folder "INBOX"                   |
      | search         | search with account "gupta-scaratec", folder "Banking", criteria {}          | search with account "gupta-scaratec", folder "MissingOnServer", criteria {}         | search with account "gupta-scaratec", folder "INBOX", criteria {}            |
      | list_messages  | list_messages with account "gupta-scaratec", folder "Banking", criteria {}   | list_messages with account "gupta-scaratec", folder "MissingOnServer", criteria {}  | list_messages with account "gupta-scaratec", folder "INBOX", criteria {}     |
      | fetch_envelope | fetch_envelope with account "gupta-scaratec", folder "Banking", uid 1        | fetch_envelope with account "gupta-scaratec", folder "MissingOnServer", uid 1       | fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 1          |
      | list_drafts    | list_drafts with account "gupta-scaratec", folder "Banking"                  | list_drafts with account "gupta-scaratec", folder "MissingOnServer"                 | list_drafts with account "gupta-scaratec", folder "INBOX"                    |
      | mark_seen      | mark_seen with account "gupta-scaratec", folder "Banking", uid 1, seen true  | mark_seen with account "gupta-scaratec", folder "MissingOnServer", uid 1, seen true | mark_seen with account "gupta-scaratec", folder "INBOX", uid 1, seen true    |

  Scenario: Every reason and error.type recorded in the audit log belongs to docs/contract/registry.yaml
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address          | to_address            | subject | date                 |
      | 950 | rechnung@hornbach.de  | me@gupta-scaratec.com | A       | 2026-05-06T09:00:00Z |
      | 951 | spam@unrelated.io     | me@gupta-scaratec.com | B       | 2026-05-06T09:00:00Z |
    When invoice-agent calls list_accounts
    And invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 950
    And invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 951
    And invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "Banking", uid 1
    And invoice-agent calls the MCP method "tools/call" with name "raw_imap_command"
    Then file "2026-05-07.jsonl" contains at least 5 records and every record's reason and error_type belong to docs/contract/registry.yaml
