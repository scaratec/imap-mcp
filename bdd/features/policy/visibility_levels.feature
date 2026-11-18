Feature: Linear visibility levels

  Each message resolves to one effective visibility level on the monotone scale:
    NONE < COUNT < METADATA < ENVELOPE < HEADERS < BODY < FULL
  A read tool succeeds only when the target object reaches its minimum level;
  fields above the granted level are omitted or set to null and named in
  redacted_fields deterministically by level (ADR 0002, ADR 0043, ADR 0044).

  Covered error layers (per BDD Guidelines §4.5):
    - Visibility floor denials (COUNT, METADATA, ENVELOPE, HEADERS, BODY, FULL) : 6
    - Partial-content field redaction per granted level (incl. fetch_headers)   : 5
    - FULL level byte-exact attachment delivery                                 : 1
    Total enumerated                                                            : 12  covered by this feature: 12

  Background:
    Given the server clock is pinned to "2026-05-07T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path      |
      | INBOX            |
      | INBOX/Rechnungen |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject       | date                 |
      | 100 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7823 | 2026-05-06T09:00:00Z |
    And the message at uid 100 has attachment "invoice.pdf" of type "application/pdf" with content byte "P" repeated 4096 times
    And the message at uid 100 has headers including "X-Hornbach-Order: 4711"
    And the message at uid 100 has plain text body "Sehr geehrte Damen und Herren, ..."
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"

  Scenario: Folder with maximum visibility NONE denies folder_stats with visibility_below_COUNT
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules |
      | INBOX/Rechnungen | whitelist | NONE    | []    |
    When invoice-agent calls folder_stats with account "gupta-scaratec", folder "INBOX/Rechnungen"
    Then the response decision is DENY
    And the response field reason equals "visibility_below_COUNT"

  Scenario: COUNT level exposes exact visible_count in folder_stats and denies search with visibility_below_METADATA
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules                                |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> COUNT}] |
    When invoice-agent calls folder_stats with account "gupta-scaratec", folder "INBOX/Rechnungen"
    Then the response decision is ALLOW
    And the response field visible_count equals 1
    And the response field completeness equals "policy_may_hide"
    When invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    Then the response decision is DENY
    And the response field reason equals "visibility_below_METADATA"

  Scenario Outline: A fetch tool below its minimum level is denied with visibility_below_<level>
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules                                    |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> <granted>}] |
    When invoice-agent calls <tool> with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 100
    Then the response decision is DENY
    And the response field reason equals "<expected_reason>"

    Examples:
      | tool           | granted  | expected_reason           |
      | fetch_envelope | METADATA | visibility_below_ENVELOPE |
      | fetch_headers  | ENVELOPE | visibility_below_HEADERS  |
      | fetch_body     | HEADERS  | visibility_below_BODY     |

  Scenario: fetch_attachment at BODY grant is denied with visibility_below_FULL
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules                               |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> BODY}] |
    When invoice-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 100, part_id 0
    Then the response decision is DENY
    And the response field reason equals "visibility_below_FULL"

  Scenario: METADATA level exposes UIDs and sizes in list_messages while redacting envelope and attachment fields
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules                                   |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> METADATA}] |
    When invoice-agent calls list_messages with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the response contains 1 messages
    And message 0 has field "uid" equal to 100
    And message 0 has integer field "size_bytes" > 0
    And message 0 has field "from" equal to null
    And message 0 has field "subject" equal to null
    And message 0 has field "has_attachment" equal to null
    And the JSON response does NOT contain the literal strings:
      | forbidden_string     |
      | rechnung@hornbach.de |
      | Rechnung 7823        |
      | invoice.pdf          |

  Scenario Outline: fetch_envelope at ENVELOPE or HEADERS returns envelope fields and flags body and attachments as redacted
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules                                    |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> <granted>}] |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 100
    Then the response decision is ALLOW
    And the response field visibility_applied equals "<granted>"
    And the response field from equals "rechnung@hornbach.de"
    And the response field subject equals "Rechnung 7823"
    And the response field body equals null
    And the response field attachments equals null
    And the response field redacted_fields contains "body"
    And the response field redacted_fields contains "attachments"

    Examples:
      | granted  |
      | ENVELOPE |
      | HEADERS  |

  Scenario: fetch_headers at HEADERS grant returns custom headers and flags body and attachments as redacted
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules                                  |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> HEADERS}] |
    When invoice-agent calls fetch_headers with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 100
    Then the response decision is ALLOW
    And the response field visibility_applied equals "HEADERS"
    And the response field headers contains key "X-Hornbach-Order" equal to "4711"
    And the response field redacted_fields equals ["body", "attachments"]
    And the JSON response does NOT contain the literal strings:
      | forbidden_string                   |
      | Sehr geehrte Damen und Herren, ... |

  Scenario: BODY level reveals text body and attachment metadata while flagging attachment_bytes as redacted
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules                               |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> BODY}] |
    When invoice-agent calls fetch_body with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 100
    Then the response decision is ALLOW
    And the response field visibility_applied equals "BODY"
    And the response field text_body equals "Sehr geehrte Damen und Herren, ..."
    And the response field attachments has 1 entries
    And attachment 0 has field "filename" equal to "invoice.pdf"
    And the response field redacted_fields equals ["attachment_bytes"]

  Scenario: FULL level writes exact attachment bytes to the caller's isolated artifact directory
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules                               |
      | INBOX/Rechnungen | whitelist | NONE    | [{from_domain=hornbach.de -> FULL}] |
    And the server attachment sink directory is a fresh writable directory
    When invoice-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 100, part_id 0
    Then the response decision is ALLOW
    And the response field visibility_applied equals "FULL"
    And the response field mime_type equals "application/pdf"
    And the response field size_bytes equals 4096
    And the file at saved_path exists with mode 0400 and exact content byte "P" repeated 4096 times
    And the response field content_hash equals the sha256 hex digest of byte "P" repeated 4096 times
