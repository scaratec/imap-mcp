Feature: Symlink-safe per-caller attachment sink, filename sanitization, and static tool-description hygiene

  ADR 0047 hardens attachment delivery against path traversal, shell
  glyphs, RFC 2047 / raw-8-bit header quirks, symlink escapes, and host
  layout disclosure:
    - Every path segment (<sink_base> and <sink_base>/<caller_id>) is
      opened relative to its parent directory descriptor with no-follow
      semantics, and every artifact file is created with exclusive-create,
      no-follow, and read-only mode 0400 semantics.
    - Pre-existing symlinks at either <sink_base> or <sink_base>/<caller_id>
      fail closed with sink_not_writable without touching the symlink target.
    - tools/list publishes a static description for fetch_attachment and
      never discloses the host filesystem path (LIM 0013 & LIM 0016 resolved).

  Covered error layers (per BDD Guidelines §4.5):
    - Sanitization: path separators, shell glyphs, leading dot : 3
    - Length cap: 500-byte filename fits <= 255-byte limit     : 1
    - RFC 2047 B-encoded, Q-encoded, and raw 8-bit filenames   : 3
    - Symlink escape blocked on base dir & caller subdir       : 2
    - sink_not_configured & sink_not_writable (missing, ro)    : 3
    - tools/list description omits host filesystem sink path   : 1
    Total enumerated                                           : 13  covered by this feature: 13

  Background:
    Given the server clock is pinned to "2026-06-18T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path     |
      | INBOX           |
      | INBOX/Documents |
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"
    And policy "doc-policy" grants folder on account "gupta-scaratec":
      | folder          | mode      | default | rules |
      | INBOX/Documents | blacklist | FULL    | []    |
    And the folder "INBOX/Documents" holds messages:
      | uid | from_address        | to_address            | subject | date                 |
      | 701 | sender@test.example | me@gupta-scaratec.com | Pinned  | 2026-06-17T09:00:00Z |

  Scenario: Filename containing path traversal segments is sanitized inside the caller directory
    Given the server attachment sink directory is a fresh writable directory
    And the message at uid 701 has attachment "../../etc/passwd.pdf" of type "application/pdf" with content byte "A" repeated 128 times
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field saved_to matches "^doc-agent/[0-9a-f]{32}_[A-Za-z0-9._-]+\.pdf$"
    And the response field saved_to does NOT contain the literal string ".."
    And no file was written outside the sink directory
    And the file at saved_path exists with mode 0400 and exact content byte "A" repeated 128 times

  Scenario: Filename containing shell-active characters and spaces is sanitized to safe ASCII
    Given the server attachment sink directory is a fresh writable directory
    And the message at uid 701 has attachment "invoice;rm -rf /.pdf" of type "application/pdf" with content byte "B" repeated 64 times
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response field saved_to matches "^doc-agent/[0-9a-f]{32}_[A-Za-z0-9._-]+\.pdf$"
    And the response field saved_to does NOT contain the literal string ";"
    And the response field saved_to does NOT contain the literal string " "
    And the file at saved_path exists with mode 0400 and exact content byte "B" repeated 64 times

  Scenario: Filename with a leading dot is prefixed by artifact_id and stripped of leading dots in base
    Given the server attachment sink directory is a fresh writable directory
    And the message at uid 701 has attachment ".hidden.pdf" of type "application/pdf" with content byte "C" repeated 32 times
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response field saved_to matches "^doc-agent/[0-9a-f]{32}_hidden\.pdf$"
    And the file at saved_path exists with mode 0400 and exact content byte "C" repeated 32 times

  Scenario: 500-byte original filename is truncated so the leaf filename is at most 255 bytes
    Given the server attachment sink directory is a fresh writable directory
    And the message at uid 701 has an attachment whose original filename is 500 "a" characters plus ".pdf", mime type "application/pdf", content byte "D" repeated 256 times
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the leaf filename of saved_to has byte length at most 255
    And the response field saved_to matches "^doc-agent/[0-9a-f]{32}_a+\.pdf$"
    And the file at saved_path exists with mode 0400 and exact content byte "D" repeated 256 times

  Scenario: RFC 2047 B-encoded filename is decoded before sanitization
    Given the server attachment sink directory is a fresh writable directory
    And the message at uid 701 has attachment with raw wire filename "=?utf-8?b?ZmEgMjAyNi02IEt1YmEucGRm?=" of type "application/pdf" with content byte "E" repeated 96 times
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field saved_to matches "^doc-agent/[0-9a-f]{32}_fa_2026-6_Kuba\.pdf$"
    And the response field saved_to does NOT contain the literal string "utf-8"
    And the file at saved_path exists with mode 0400 and exact content byte "E" repeated 96 times

  Scenario: RFC 2047 Q-encoded filename is decoded before sanitization
    Given the server attachment sink directory is a fresh writable directory
    And the message at uid 701 has attachment with raw wire filename "=?utf-8?q?Rechnung_M=C3=A4rz.pdf?=" of type "application/pdf" with content byte "F" repeated 96 times
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field saved_to matches "^doc-agent/[0-9a-f]{32}_Rechnung_M_rz\.pdf$"
    And the response field saved_to does NOT contain the literal string "?"
    And the file at saved_path exists with mode 0400 and exact content byte "F" repeated 96 times

  Scenario: Raw 8-bit non-ASCII Content-Disposition header is sanitized cleanly without crashing
    Given the server attachment sink directory is a fresh writable directory
    And the message at uid 701 has attachment with raw 8-bit wire filename "Rechnung Dürr.pdf" of type "application/pdf" with content byte "G" repeated 96 times
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field saved_to matches "^doc-agent/[0-9a-f]{32}_Rechnung_D[A-Za-z0-9._-]*\.pdf$"
    And the file at saved_path exists with mode 0400 and exact content byte "G" repeated 96 times

  Scenario: Symlink at the configured sink base directory is rejected with sink_not_writable
    Given "$TMPDIR/outside_target" is an empty writable directory and the configured sink path is a symlink pointing to "$TMPDIR/outside_target"
    And the message at uid 701 has attachment "report.pdf" of type "application/pdf" with content byte "H" repeated 64 times
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "sink_not_writable"
    And the directory "$TMPDIR/outside_target" contains 0 files

  Scenario: Pre-planted symlink at <sink_base>/<caller_id> is rejected via O_NOFOLLOW without writing to target
    Given the server attachment sink directory is a fresh writable directory
    And "$TMPDIR/victim_policy_dir" is an empty writable directory and "<sink_base>/doc-agent" is a symlink pointing to "$TMPDIR/victim_policy_dir"
    And the message at uid 701 has attachment "evil.yaml" of type "application/x-yaml" with content byte "Y" repeated 64 times
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "sink_not_writable"
    And the directory "$TMPDIR/victim_policy_dir" contains 0 files

  Scenario: fetch_attachment returns sink_not_configured when no sink directory is configured
    Given the server attachment sink directory is not configured
    And the message at uid 701 has attachment "report.pdf" of type "application/pdf" with content byte "I" repeated 64 times
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "sink_not_configured"

  Scenario: fetch_attachment returns sink_not_writable when the configured base directory does not exist
    Given the server attachment sink directory points at a path that does not exist
    And the message at uid 701 has attachment "report.pdf" of type "application/pdf" with content byte "J" repeated 64 times
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "sink_not_writable"

  Scenario: fetch_attachment returns sink_not_writable when the configured base directory is read-only
    Given the test runner is not executing as root
    And the server attachment sink directory is a read-only directory
    And the message at uid 701 has attachment "report.pdf" of type "application/pdf" with content byte "K" repeated 64 times
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "sink_not_writable"

  Scenario: tools/list publishes a static description for fetch_attachment and never discloses the host sink path
    Given the server attachment sink directory is a fresh writable directory
    When doc-agent calls the MCP list_tools method
    Then the description of tool "fetch_attachment" contains "ADR 0047"
    And the description of tool "fetch_attachment" does NOT contain the configured sink path
