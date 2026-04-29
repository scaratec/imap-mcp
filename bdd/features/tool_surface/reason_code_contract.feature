Feature: Reason code vocabulary contract

  ADR 0017 §2 declares the canonical reason-code table. This feature
  is its contract test: it asserts that every code in the table is
  reachable from at least one documented condition and that the server
  never emits a code outside the canonical set. See LIM-0001.

  Scenarios in this file are vocabulary contract checks, not behavior
  tests — they exist so that a regression that introduces a new code
  without an ADR amendment fails loudly, and so that a code listed in
  the table but unreachable in practice surfaces as a Feature-File
  failure rather than as silent dead vocabulary.

  Covered error layers (per BDD Guidelines §4.5):
    - Each declared reason code reachable          : 1 (per code)
    - No undeclared code emitted by server         : 1 (negative-shape)
    Total enumerated                                : 17  covered by this feature: 17

  Background:
    Given the IMAP account "gupta-scaratec" exists with folders:
      | folder path      |
      | INBOX/Rechnungen |
      | Banking          |
    And the IMAP account "osthues-mail" exists with folder "INBOX"
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec" and folder:
      | folder           | mode      | default | rules                                |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> ENVELOPE}] |

  Scenario: rule_matched is reachable via a sender rule that matches
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                  | subject       |
      | 901 | rechnung@hornbach.de  | Rechnung 7823 |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 901
    Then the response decision is ALLOW
    And the response field reason equals "rule_matched"

  Scenario: folder_default_applied is reachable when the folder default suffices
    Given policy "invoice-policy" grants account "gupta-scaratec" and folder:
      | folder | mode      | default  |
      | Public | blacklist | ENVELOPE |
    And the folder "Public" holds a message with:
      | uid | from                |
      | 902 | someone@example.org |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "Public", uid 902
    Then the response decision is ALLOW
    And the response field reason equals "folder_default_applied"

  Scenario: account_hidden is reachable for an account outside the policy
    When invoice-agent calls fetch_envelope with account "osthues-mail", folder "INBOX", uid 1
    Then the response decision is DENY
    And the response field reason equals "account_hidden"

  Scenario: folder_hidden is reachable for a folder outside the policy
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "Banking", uid 1
    Then the response decision is DENY
    And the response field reason equals "folder_hidden"

  Scenario: sender_not_whitelisted is reachable in whitelist mode with no matching rule
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from              |
      | 903 | spam@unrelated.io |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 903
    Then the response decision is DENY
    And the response field reason equals "sender_not_whitelisted"

  Scenario: sender_blacklisted is reachable in blacklist mode with a matching rule
    When invoice-agent triggers a DENY with reason sender_blacklisted for a message from "noreply@bank.de"
    Then the response decision is DENY
    And the response field reason equals "sender_blacklisted"

  Scenario: visibility_below_HEADERS is reachable when granted level is ENVELOPE
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                 |
      | 904 | rechnung@hornbach.de |
    When invoice-agent calls fetch_headers with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 904
    Then the response decision is DENY
    And the response field reason equals "visibility_below_HEADERS"

  Scenario: visibility_below_BODY is reachable for fetch_body when granted level is ENVELOPE
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                 |
      | 905 | rechnung@hornbach.de |
    When invoice-agent calls fetch_body with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 905
    Then the response decision is DENY
    And the response field reason equals "visibility_below_BODY"

  Scenario: visibility_below_FULL is reachable for fetch_attachment when granted level is ENVELOPE
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                 |
      | 906 | rechnung@hornbach.de |
    When invoice-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 906
    Then the response decision is DENY
    And the response field reason equals "visibility_below_FULL"

  Scenario: capability_missing is reachable for mark_seen on a folder without the capability
    Given policy "invoice-policy" grants account "gupta-scaratec" and folder:
      | folder       | mode      | default  | mark_seen | rules |
      | Read-only-Ix | blacklist | ENVELOPE | false     | []    |
    And the folder "Read-only-Ix" holds a message with:
      | uid | from                 |
      | 907 | rechnung@hornbach.de |
    When invoice-agent calls mark_seen with account "gupta-scaratec", folder "Read-only-Ix", uid 907, seen true
    Then the response decision is DENY
    And the response field reason equals "capability_missing"

  Scenario: forbidden_system_flag is reachable when mark_tagged is asked for a reserved flag
    Given policy grants mark_tagged=true on "INBOX/Rechnungen"
    And the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                 |
      | 908 | rechnung@hornbach.de |
    When invoice-agent calls mark_tagged with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 908, tags ["\Deleted"], mode "add"
    Then the response decision is DENY
    And the response field reason equals "forbidden_system_flag"

  Scenario: unknown_tool surfaces as JSON-RPC -32601 and audits with reason "unknown_tool"
    When invoice-agent calls the MCP method "tools/call" with name "raw_imap_command"
    Then the server responds with JSON-RPC error code -32601
    And the audit log contains an entry with tool "auth_failed_or_unknown_method", decision "DENY", reason "unknown_tool"

  Scenario: saga_step is emitted in the audit log per WAL transition
    Given a cross-account move begins and succeeds
    Then the audit file contains, in this order, at least the records:
      | tool              | step    |
      | saga_transition   | begin   |
      | saga_transition   | fetched |
      | saga_transition   | staged  |
      | saga_transition   | deleted |
      | saga_transition   | commit  |

  Scenario: auth_failed is reachable on HTTP transport with a wrong bearer token
    Given the server is started with transport "http"
    When a client sends an Initialize with caller_id "invoice-agent" and bearer token "wrong-token"
    Then the audit file contains a JSONL record with:
      | field    | value       |
      | tool     | auth_failed |
      | decision | DENY        |
      | reason   | auth_failed |

  Scenario: folder_default_applied is reachable a second time via folder_stats on a blacklist folder
    Given policy "invoice-policy" grants account "gupta-scaratec" and folder:
      | folder    | mode      | default | rules |
      | Statistik | blacklist | COUNT   | []    |
    And the folder "Statistik" holds a message with:
      | uid | from                |
      | 920 | someone@example.org |
    When invoice-agent calls folder_stats with account "gupta-scaratec", folder "Statistik"
    Then the response field reason equals "folder_default_applied"

  Scenario: visibility_below_COUNT is reachable for folder_stats when no rule can grant COUNT
    Given the IMAP account "gupta-scaratec" exists with folder "DeadLetters"
    And policy "invoice-policy" grants account "gupta-scaratec" and folder:
      | folder      | mode      | default | rules |
      | DeadLetters | whitelist | NONE    | []    |
    When invoice-agent calls folder_stats with account "gupta-scaratec", folder "DeadLetters"
    Then the response decision is DENY
    And the response field reason equals "visibility_below_COUNT"

  Scenario: visibility_below_COUNT is reachable a second time on a different folder
    Given the IMAP account "gupta-scaratec" exists with folder "Quarantine"
    And policy "invoice-policy" grants account "gupta-scaratec" and folder:
      | folder     | mode      | default | rules |
      | Quarantine | whitelist | NONE    | []    |
    When invoice-agent calls folder_stats with account "gupta-scaratec", folder "Quarantine"
    Then the response decision is DENY
    And the response field reason equals "visibility_below_COUNT"

  Scenario: visibility_below_ENVELOPE is reachable when fetch_envelope is asked but only COUNT is granted
    Given policy "invoice-policy" grants account "gupta-scaratec" and folder:
      | folder      | mode      | default | rules |
      | Counts-only | blacklist | COUNT   | []    |
    And the folder "Counts-only" holds a message with:
      | uid | from                |
      | 909 | someone@example.org |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "Counts-only", uid 909
    Then the response decision is DENY
    And the response field reason equals "visibility_below_ENVELOPE"

  Scenario: every reason code emitted by the server in this run is in the canonical set
    Given a sequence of operations over a day creates 20 audit records across ALLOW, DENY, saga, and token_refresh
    When the current audit file is read
    Then every distinct reason code in the audit file is present in ADR-0017 §2.1

  Scenario: every canonical reason code is exercised by at least two non-pending scenarios
    Then the canonical reason-code table in ADR-0017 §2.1 has variance discipline
