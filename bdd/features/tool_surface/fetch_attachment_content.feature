Feature: fetch_attachment delivers verified read-only artifacts into per-caller subdirectories

  ADR 0047 delivers FULL-authorized attachments into <sink_base>/<caller_id>/
  (mode 0700) using a fresh 128-bit UUIDv4 hex prefix
  (<artifact_id>_<sanitized_base>.<ext>) created via openat with
  O_CREAT | O_EXCL | O_NOFOLLOW and mode 0400. Responses carry
  artifact_id, saved_to ("<caller_id>/<filename>"), saved_path (canonical
  absolute path), size_bytes, and content_hash (SHA-256 hex), omitting
  inline blobs.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path: byte-exact 0400 file in 0700 caller dir + SHA-256 : 1
    - Filename pattern <32hex>_<base>.<ext> & saved_to / saved_path : 1
    - Repeated fetch creates distinct exclusive artifact files      : 1
    - BODY grant denied with visibility_below_FULL (0 files written): 1
    - Missing part_id rejected with JSON-RPC -32602                 : 1
    - Out-of-range part_id returns attachment_not_found             : 1
    Total enumerated                                                : 6   covered by this feature: 6

  Background:
    Given the server clock is pinned to "2026-06-08T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path     |
      | INBOX           |
      | INBOX/Documents |
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"
    And policy "doc-policy" grants folder on account "gupta-scaratec":
      | folder          | mode      | default | rules |
      | INBOX/Documents | blacklist | FULL    | []    |
    And the server attachment sink directory is a fresh writable directory
    And the folder "INBOX/Documents" holds messages:
      | uid | from_address        | to_address            | subject          | date                 |
      | 601 | sender@test.example | me@gupta-scaratec.com | Quarterly report | 2026-06-07T09:00:00Z |
    And the message at uid 601 has attachment "report.pdf" of type "application/pdf" with content byte "Q" repeated 4096 times

  Scenario: fetch_attachment writes exact decoded bytes with mode 0400 inside caller 0700 directory and omits inline blobs
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field mime_type equals "application/pdf"
    And the response field size_bytes equals 4096
    And the response field artifact_id matches "^[0-9a-f]{32}$"
    And the response does not contain any field named "_blob"
    And the response does not contain any field named "_blob_mime_type"
    And the response does not contain any field named "_blob_uri"
    And the response content has no resource block
    And the caller sink subdirectory "doc-agent" exists with mode 0700
    And the file at saved_path exists with mode 0400 and exact content byte "Q" repeated 4096 times
    And the response field content_hash equals the sha256 hex digest of byte "Q" repeated 4096 times

  Scenario: saved_to and saved_path follow the <caller_id>/<32hex>_<base>.<ext> contract
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    Then the response field saved_to matches "^doc-agent/[0-9a-f]{32}_report\.pdf$"
    And the response field saved_path ends with the response field saved_to

  Scenario: Repeated fetches create distinct exclusive files rather than overwriting in place
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    And the response field saved_path is remembered as "first_path"
    And doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    Then the response field saved_path does not equal the remembered "first_path"
    And the caller sink subdirectory "doc-agent" contains 2 files

  Scenario: fetch_attachment at BODY grant is denied with visibility_below_FULL and writes zero files
    Given policy "doc-policy" grants folder on account "gupta-scaratec":
      | folder          | mode      | default | rules |
      | INBOX/Documents | blacklist | BODY    | []    |
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 0
    Then the response decision is DENY
    And the response field reason equals "visibility_below_FULL"
    And the sink base directory contains 0 files

  Scenario: fetch_attachment without part_id is rejected with JSON-RPC -32602 and writes zero files
    When doc-agent calls fetch_attachment with the arguments {"account": "gupta-scaratec", "folder": "INBOX/Documents", "uid": 601}
    Then the server responds with JSON-RPC error code -32602
    And the response error message contains "part_id"
    And the sink base directory contains 0 files

  Scenario: fetch_attachment with out-of-range part_id returns attachment_not_found and writes zero files
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 601, part_id 99
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "attachment_not_found"
    And the sink base directory contains 0 files
