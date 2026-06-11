Feature: fetch_attachment writes the bytes to the operator-configured sink

  After ADR 0028, fetch_attachment no longer returns the attachment
  bytes inline. The server writes them to the configured
  attachment_sink_directory and returns only the filename in
  `saved_to`; the absolute path lives in the tool description so it
  is not re-paid on every call. The filename pattern is
  `<sanitized_base>_<8hex>.<extension>` where the hex is the first
  eight characters of md5(decoded bytes), making re-fetch idempotent.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path: file written, saved_to returned          : 1
    - Filename pattern: base + _ + 8 hex + ext             : 1
    - Idempotency: re-fetch overwrites same filename       : 1
    - Authorization: FULL-granted caller succeeds          : 1
    - Authorization: BODY-only caller is denied            : 1
    - Schema: missing part_id is rejected at JSON-RPC      : 1
    - Out-of-range part_id surfaces attachment_not_found   : 1
    - No inline blob: response omits _blob / EmbeddedResource: 1
    Total enumerated                                        : 8   covered by this feature: 8

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Documents"
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"
    And policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | FULL    |
    And the server attachment sink directory is a fresh writable directory
    And the folder "INBOX/Documents" holds a message with:
      | uid | from                 | subject          |
      | 601 | sender@test.example  | Quarterly report |
    And the message has attachment "report.pdf" of type "application/pdf" with size 4096 bytes

  Scenario: fetch_attachment writes the file and returns saved_to
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mime_type equals "application/pdf"
    And the response field size_bytes equals 4096
    And the response field saved_to is a string of at most 255 characters
    And the response does not contain any field named "_blob"
    And the response does not contain any field named "_blob_mime_type"
    And the response does not contain any field named "_blob_uri"
    And the response content has no resource block

    # Persistence-Validierung (§13.2 Pruefung 1): the file really
    # exists in the sink and its bytes match the size header.
    Then the file named saved_to exists in the sink directory
    And the file named saved_to in the sink has size 4096 bytes
    And the sha256 of the file named saved_to in the sink equals the response field content_hash

  Scenario: saved_to filename matches the <base>_<8hex>.<ext> pattern
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    Then the response field saved_to matches the regex "^report_[0-9a-f]{8}\.pdf$"
    And the 8-hex segment in saved_to equals the first 8 hex chars of md5 of the file bytes

  Scenario: re-fetch overwrites the same filename without piling up
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    Then the saved_to value matches the bytes-md5 pattern "report_<hex>.pdf"
    And the sink directory contains exactly 1 file

    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    Then the saved_to value matches the bytes-md5 pattern "report_<hex>.pdf"
    And the sink directory contains exactly 1 file

  Scenario: fetch_attachment at BODY grant is denied with visibility_below_FULL
    Given policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | BODY    |
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    Then the response decision is DENY
    And the response field reason equals "visibility_below_FULL"
    And the sink directory contains exactly 0 files

  Scenario: fetch_attachment without part_id is rejected at JSON-RPC -32602
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601
    Then the server responds with JSON-RPC error code -32602
    And the response error message contains "part_id"
    And the sink directory contains exactly 0 files

  Scenario: fetch_attachment with out-of-range part_id returns attachment_not_found
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 99
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "attachment_not_found"
    And the sink directory contains exactly 0 files
