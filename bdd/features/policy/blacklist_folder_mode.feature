Feature: Blacklist folder mode

  A folder declared with mode=blacklist has a non-NONE default and
  cap rules that reduce the effective visibility level for matching
  messages: effective = min(default, min(cap-of-matching-rules)).
  Capping to NONE makes a message indistinguishable from non-existent
  to the caller while recording message_capped in the operator audit
  log with a keyed HMAC domain digest (ADR 0040, ADR 0043, ADR 0044, ADR 0048).

  Covered error layers (per BDD Guidelines §4.5):
    - Policy validation : 2 (NONE default in blacklist, grant rule in blacklist)
    - Authorization     : 4 (no matching cap, cap to METADATA, cap to NONE, overlapping caps)
    - Audit forensics   : 1 (message_capped vs message_not_granted without cleartext sender)
    Total enumerated    : 7   covered by this feature: 7

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "personal" exists with folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "overview-agent" using policy "overview-policy"
    And policy "overview-policy" grants account "personal"

  Scenario: Message without any matching cap rule keeps the folder default
    Given policy "overview-policy" grants folder:
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    And the folder "INBOX" holds messages:
      | uid | from_address       | to_address      | subject        | date                 |
      | 301 | friend@example.com | me@personal.org | Birthday plans | 2026-05-06T09:00:00Z |
    When overview-agent calls fetch_envelope with account "personal", folder "INBOX", uid 301
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field visibility_applied equals "ENVELOPE"
    And the response field from equals "friend@example.com"
    And the response field subject equals "Birthday plans"

  Scenario: A cap to METADATA denies fetch_envelope with visibility_below_ENVELOPE while keeping UID visible
    Given policy "overview-policy" grants folder:
      | folder | mode      | default  | rules                                   |
      | INBOX  | blacklist | ENVELOPE | [{from_domain=bank.de -> cap METADATA}] |
    And the folder "INBOX" holds messages:
      | uid | from_address    | to_address      | subject     | date                 |
      | 310 | noreply@bank.de | me@personal.org | Kontoauszug | 2026-05-06T09:00:00Z |
    When overview-agent calls fetch_envelope with account "personal", folder "INBOX", uid 310
    Then the response decision is DENY
    And the response field reason equals "visibility_below_ENVELOPE"
    When overview-agent calls folder_stats with account "personal", folder "INBOX"
    Then the response decision is ALLOW
    And the response field visible_count equals 1
    And the response field completeness equals "policy_may_hide"

  Scenario: A cap of NONE makes a matching message invisible in search and returns opaque not_found on fetch
    Given policy "overview-policy" grants folder:
      | folder | mode      | default  | rules                               |
      | INBOX  | blacklist | ENVELOPE | [{from_domain=bank.de -> cap NONE}] |
    And the folder "INBOX" holds messages:
      | uid | from_address       | to_address      | subject     | date                 |
      | 320 | noreply@bank.de    | me@personal.org | Kontoauszug | 2026-05-06T09:00:00Z |
      | 321 | friend@example.com | me@personal.org | Hello       | 2026-05-06T09:00:00Z |
    When overview-agent calls search with account "personal", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals [321]
    And the response field total_visible equals 1
    And the response does not contain any field named "matched_total"
    And the response does not contain any field named "filtered_out"
    When overview-agent calls fetch_envelope with account "personal", folder "INBOX", uid 320
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"

  Scenario: Multiple overlapping caps apply the strictest cap
    Given policy "overview-policy" grants folder:
      | folder | mode      | default  | rules                                                                             |
      | INBOX  | blacklist | ENVELOPE | [{from_domain=bank.de -> cap METADATA}, {subject_contains=VERTRAULICH -> cap NONE}] |
    And the folder "INBOX" holds messages:
      | uid | from_address    | to_address      | subject                   | date                 |
      | 330 | noreply@bank.de | me@personal.org | [VERTRAULICH] Neues Konto | 2026-05-06T09:00:00Z |
    When overview-agent calls folder_stats with account "personal", folder "INBOX"
    Then the response field visible_count equals 0
    When overview-agent calls fetch_envelope with account "personal", folder "INBOX", uid 330
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"

  Scenario: Policy loader rejects a blacklist folder with default NONE
    Given the server loads a policy file containing:
      """
      name: overview-policy
      accounts:
        personal:
          - path: INBOX
            mode: blacklist
            default: NONE
            rules: []
      """
    Then the server refuses to start
    And the startup error indicates the folder "INBOX" as "blacklist mode requires default > NONE"

  Scenario: Policy loader rejects grant rules in a blacklist folder
    Given the server loads a policy file containing:
      """
      name: overview-policy
      accounts:
        personal:
          - path: INBOX
            mode: blacklist
            default: ENVELOPE
            rules:
              - match: { from_domain: friend.example }
                grant: FULL
      """
    Then the server refuses to start
    And the startup error indicates the rule as "blacklist mode forbids 'grant'; use 'cap'"

  Scenario: Operator audit distinguishes message_capped from message_not_granted without cleartext addresses
    Given the IMAP account "personal" also has folder "Restricted"
    And policy "overview-policy" grants folder:
      | folder     | mode      | default  | rules                                   |
      | INBOX      | blacklist | ENVELOPE | [{from_domain=bank.de -> cap NONE}]     |
      | Restricted | whitelist | NONE     | [{from_domain=allowed.org -> ENVELOPE}] |
    And the folder "INBOX" holds messages:
      | uid | from_address    | to_address      | subject   | date                 |
      | 340 | noreply@bank.de | me@personal.org | Statement | 2026-05-06T09:00:00Z |
    And the folder "Restricted" holds messages:
      | uid | from_address      | to_address      | subject | date                 |
      | 341 | secret@lawyer.org | me@personal.org | Mandate | 2026-05-06T09:00:00Z |
    When overview-agent calls fetch_envelope with account "personal", folder "INBOX", uid 340
    And overview-agent calls fetch_envelope with account "personal", folder "Restricted", uid 341
    Then the audit log contains an entry with:
      | field               | value          |
      | caller_id           | overview-agent |
      | tool                | fetch_envelope |
      | audit_detail_reason | message_capped |
    And the audit log contains an entry with:
      | field               | value               |
      | caller_id           | overview-agent      |
      | tool                | fetch_envelope      |
      | audit_detail_reason | message_not_granted |
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string  |
      | noreply@bank.de   |
      | bank.de           |
      | secret@lawyer.org |
      | lawyer.org        |
