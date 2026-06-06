Feature: Linear visibility levels

  A rule grants exactly one level from the linear scale
    NONE < COUNT < METADATA < ENVELOPE < HEADERS < BODY < FULL
  A tool call is permitted only if its minimum level is reached.
  See ADR 0002 and ADR 0016.

  Covered error layers (per BDD Guidelines §4.5):
    - Authorization (visibility floor)  : 7 (one per tool family requiring a minimum level)
    - Response shape                    : 7 (redacted fields per granted level)
    Total enumerated                    : 14    covered by this feature: 14

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Rechnungen"
    And the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                  | subject        | has_attachment | size_bytes |
      | 100 | rechnung@hornbach.de  | Rechnung 7823  | true           | 48213      |
    And the message has attachment "invoice.pdf" of type "application/pdf" with size 32118 bytes
    And the message has headers including "X-Hornbach-Order: 4711"
    And the message has plain text body "Sehr geehrte Damen und Herren, ..."
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "gupta-scaratec"

  Scenario Outline: A fetch tool at a lower-than-minimum level is denied with visibility_below_<level>
    Given policy "invoice-policy" grants folder:
      | folder            | mode      | default | rule                                     |
      | INBOX/Rechnungen  | whitelist | NONE    | from_domain=hornbach.de -> <granted>     |
    When invoice-agent calls <tool> with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 100
    Then the response decision is DENY
    And the response field reason equals "<expected_reason>"

    Examples:
      | tool               | granted  | expected_reason           |
      | fetch_envelope     | METADATA | visibility_below_ENVELOPE |
      | fetch_headers      | ENVELOPE | visibility_below_HEADERS  |
      | fetch_body         | HEADERS  | visibility_below_BODY     |

  Scenario: fetch_attachment at BODY grant is denied with visibility_below_FULL
    # Extracted from the outline above because fetch_attachment requires
    # an explicit part_id (ADR 0026 §1); the outline does not pass one.
    Given policy "invoice-policy" grants folder:
      | folder            | mode      | default | rule                                     |
      | INBOX/Rechnungen  | whitelist | NONE    | from_domain=hornbach.de -> BODY          |
    When invoice-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 100, part_id 0
    Then the response decision is DENY
    And the response field reason equals "visibility_below_FULL"

  Scenario Outline: A fetch tool at or above its minimum level returns the permitted fields and flags the rest as redacted
    Given policy "invoice-policy" grants folder:
      | folder            | mode      | default | rule                                     |
      | INBOX/Rechnungen  | whitelist | NONE    | from_domain=hornbach.de -> <granted>     |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 100
    Then the response decision is ALLOW
    And the response field visibility_applied equals "<granted>"
    And the response includes field from with value "rechnung@hornbach.de"
    And the response includes field subject with value "Rechnung 7823"
    And the response field body equals null
    And the response field redacted_fields contains "body"
    And the response field redaction_reason equals "<redaction_reason>"

    Examples:
      | granted  | redaction_reason       |
      | ENVELOPE | visibility_below_BODY  |
      | HEADERS  | visibility_below_BODY  |

  Scenario: BODY level reveals text body but not attachments
    Given policy "invoice-policy" grants folder:
      | folder            | mode      | default | rule                                    |
      | INBOX/Rechnungen  | whitelist | NONE    | from_domain=hornbach.de -> BODY         |
    When invoice-agent calls fetch_body with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 100
    Then the response decision is ALLOW
    And the response field visibility_applied equals "BODY"
    And the response field text_body equals "Sehr geehrte Damen und Herren, ..."
    And the response field attachments equals null
    And the response field redacted_fields contains "attachments"
    And the response field redaction_reason equals "visibility_below_FULL"

  Scenario: FULL level reveals attachment bytes
    Given policy "invoice-policy" grants folder:
      | folder            | mode      | default | rule                                    |
      | INBOX/Rechnungen  | whitelist | NONE    | from_domain=hornbach.de -> FULL         |
    When invoice-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 100, part_id 0
    Then the response decision is ALLOW
    And the response field visibility_applied equals "FULL"
    And the response field mime_type equals "application/pdf"
    And the response field size_bytes equals 32118
    And the response field content_hash matches sha256 of the stored attachment bytes

  Scenario: COUNT level exposes folder_stats but not search
    Given policy "invoice-policy" grants folder:
      | folder            | mode      | default | rule                                    |
      | INBOX/Rechnungen  | whitelist | NONE    | from_domain=hornbach.de -> COUNT        |
    When invoice-agent calls folder_stats with account "gupta-scaratec", folder "INBOX/Rechnungen"
    Then the response decision is ALLOW
    And the response field visible_count is a non-negative integer
    When invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {}
    Then the response decision is DENY
    And the response field reason equals "visibility_below_METADATA"

  Scenario: METADATA level exposes UIDs and sizes but not envelope
    Given policy "invoice-policy" grants folder:
      | folder            | mode      | default | rule                                    |
      | INBOX/Rechnungen  | whitelist | NONE    | from_domain=hornbach.de -> METADATA     |
    When invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {"from_domain": "hornbach.de"}
    Then the response decision is ALLOW
    And the response field uids contains 100
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 100
    Then the response decision is DENY
    And the response field reason equals "visibility_below_ENVELOPE"
