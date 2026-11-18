Feature: Non-interference transparency, static completeness, and normalized 3.0.0 response envelope

  ADR 0043 and ADR 0044 replace data-dependent hidden counts and legacy
  error fields with:
    1. Zero hidden counts or rule identifiers across list_accounts,
       list_folders (sorted lexicographically), search, folder_stats,
       fetch_*, and describe_policy.
    2. Static completeness indicators derived solely from folder policy
       and scope ("complete", "policy_may_hide", "window_limited",
       "window_and_policy_limited").
    3. Three normalized envelopes:
         - DENY: decision, reason, account/folder (never result or error)
         - ALLOW + OK: decision, result="OK", reason, payload
         - ALLOW + ERROR: decision, result="ERROR", reason,
           error.{type, detail} (<= 256 chars, never echoing caller input)
    4. Deterministic multi-folder evaluation order: source folder gate ->
       target folder gate (folder_hidden) -> source capability -> target
       capability (capability_missing).

  Covered error layers (per BDD Guidelines §4.5):
    - list_accounts & list_folders omit hidden counts and names      : 2
    - search emits total_visible + completeness, omits pre-PDP counts: 1
    - DENY reason codes & deterministic 2-folder evaluation priority : 6
    - Partial-content redacted_fields at ENVELOPE without rule index : 1
    - describe_policy exposes own static profile, leaks zero secrets : 2
    - Normalized DENY vs ALLOW+ERROR shapes & poison-marker safety   : 4
    Total enumerated                                                 : 16  covered by this feature: 16

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with provider "imap-standard" and folders:
      | folder path      |
      | INBOX            |
      | INBOX/Rechnungen |
      | Banking          |
      | Archiv           |
    And the IMAP account "osthues-mail" exists with folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder           | mode      | default | move_out | accept_incoming | rules                                   |
      | gupta-scaratec | INBOX/Rechnungen | whitelist | NONE    | true     | false           | [{from_domain=hornbach.de -> ENVELOPE}] |
      | gupta-scaratec | Archiv           | whitelist | NONE    | false    | false           | []                                      |

  Scenario: list_accounts exposes visible accounts without hidden_accounts_count or hidden account names
    When invoice-agent calls list_accounts
    Then the response decision is ALLOW
    And the visible account ids equal ["gupta-scaratec"]
    And the response does not contain any field named "hidden_accounts_count"
    And the JSON response does NOT contain the literal strings:
      | forbidden_string |
      | osthues-mail     |

  Scenario: list_folders exposes visible folders without hidden_folders_count or message_count
    When invoice-agent calls list_folders with account "gupta-scaratec"
    Then the response decision is ALLOW
    And the response field folders equals ["Archiv", "INBOX/Rechnungen"]
    And the response does not contain any field named "hidden_folders_count"
    And the JSON response does NOT contain the literal strings:
      | forbidden_string |
      | Banking          |
      | message_count    |

  Scenario: search reports total_visible and static completeness while omitting matched_total and filtered_out
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject    | date                 |
      | 601 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung A | 2026-05-06T09:00:00Z |
      | 602 | invoice@obi.de       | me@gupta-scaratec.com | Rechnung B | 2026-05-06T09:00:00Z |
      | 603 | billing@bauhaus.info | me@gupta-scaratec.com | Rechnung C | 2026-05-06T09:00:00Z |
      | 604 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung D | 2026-05-06T09:00:00Z |
    When invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the response field uids equals [601, 604]
    And the response field total_visible equals 2
    And the response field returned_count equals 2
    And the response field completeness equals "policy_may_hide"
    And the response does not contain any field named "matched_total"
    And the response does not contain any field named "filtered_out"

  Scenario Outline: DENY responses follow deterministic gate priority and omit rule patterns
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject    | date                 |
      | 610 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung Z | 2026-05-06T09:00:00Z |
    When invoice-agent calls <tool_call>
    Then the response decision is DENY
    And the response field reason equals "<reason_code>"
    And the response does not contain any field named "matched_rule"
    And the response does not contain any field named "rule_pattern"
    And the response does not contain any field named "missing_capability"

    Examples:
      | tool_call                                                                                                                                      | reason_code            |
      | fetch_envelope with account "gupta-scaratec", folder "Banking", uid 1                                                                          | folder_hidden          |
      | fetch_envelope with account "osthues-mail", folder "INBOX", uid 1                                                                              | account_hidden         |
      | fetch_body with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 610                                                                   | visibility_below_BODY  |
      | move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 610}, target {"account": "gupta-scaratec", "folder": "Banking"} | folder_hidden     |
      | move with source {"account": "gupta-scaratec", "folder": "Archiv", "uid": 1}, target {"account": "gupta-scaratec", "folder": "Banking"}            | folder_hidden     |
      | move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 610}, target {"account": "gupta-scaratec", "folder": "Archiv"}  | capability_missing |

  Scenario: Partial-content ALLOW responses list redacted_fields deterministically without matched_rule_index
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject     | date                 |
      | 620 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 42 | 2026-05-06T09:00:00Z |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 620
    Then the response decision is ALLOW
    And the response field visibility_applied equals "ENVELOPE"
    And the response field body equals null
    And the response field attachments equals null
    And the response field redacted_fields contains "headers"
    And the response field redacted_fields contains "body"
    And the response field redacted_fields contains "attachments"
    And the response does not contain any field named "matched_rule_index"

  Scenario: describe_policy exposes static may_hide and max_visibility without sender_rules_count or hidden counts
    When invoice-agent calls describe_policy
    Then the response decision is ALLOW
    And the response field reason equals "authenticated"
    And the response field caller_id equals "invoice-agent"
    And the response field tool_set_version equals "3.0.0"
    And the accounts entry for "gupta-scaratec" contains field "semantics" equal to "imap-standard"
    And the visible folder entry "INBOX/Rechnungen" for "gupta-scaratec" in describe_policy has:
      | field                            | value     |
      | mode                             | whitelist |
      | default_visibility               | NONE      |
      | max_visibility                   | ENVELOPE  |
      | may_hide                         | true      |
      | has_draft_recipient_restrictions | false     |
    And the response does not contain any field named "hidden_accounts_count"
    And the JSON response does NOT contain the literal strings:
      | forbidden_string     |
      | sender_rules_count   |
      | hidden_folders_count |

  Scenario: describe_policy never discloses rule domains, hidden folder names, or unlisted accounts
    When invoice-agent calls describe_policy
    Then the JSON response does NOT contain the literal strings:
      | forbidden_string |
      | hornbach.de      |
      | Banking          |
      | osthues-mail     |

  Scenario: DENY responses carry decision and reason and never carry result or error blocks
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "Banking", uid 1
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"
    And the response does not contain any field named "result"
    And the response does not contain any field named "error"
    And the response does not contain any field named "error_type"
    And the response does not contain any field named "imap_response"

  Scenario: Direct UID fetch on a NONE message returns opaque ALLOW + ERROR not_found
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address      | to_address            | subject | date                 |
      | 630 | spam@unrelated.io | me@gupta-scaratec.com | Spam    | 2026-05-06T09:00:00Z |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 630
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    And the response does not contain any field named "error_type"
    And the response does not contain any field named "imap_response"

  Scenario: ALLOW + ERROR responses carry bounded error.type and error.detail
    Given policy "invoice-policy" grants folder on account "gupta-scaratec":
      | folder              | mode      | default | rules |
      | INBOX/Typo-no-exist | blacklist | COUNT   | []    |
    When invoice-agent calls folder_stats with account "gupta-scaratec", folder "INBOX/Typo-no-exist"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field reason equals "folder_default_applied"
    And the response field error.type equals "folder_absent"
    And the response field error.detail is a string of at most 256 characters
    And the response does not contain any field named "error_type"
    And the response does not contain any field named "imap_response"

  Scenario: error.detail never echoes caller-controlled input strings
    Given policy "invoice-policy" grants folder on account "gupta-scaratec":
      | folder                            | mode      | default | rules |
      | INBOX/POISON-MARKER-MUST-NOT-LEAK | blacklist | COUNT   | []    |
    When invoice-agent calls folder_stats with account "gupta-scaratec", folder "INBOX/POISON-MARKER-MUST-NOT-LEAK"
    Then the response field error.type equals "folder_absent"
    And the response field error.detail does NOT contain the literal string "POISON-MARKER-MUST-NOT-LEAK"
