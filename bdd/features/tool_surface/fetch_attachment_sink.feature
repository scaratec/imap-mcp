Feature: fetch_attachment sink semantics — filename sanitization, length cap, health, configuration

  ADR 0028 specifies that fetch_attachment writes the decoded bytes to
  the operator-configured attachment_sink_directory. This file pins
  the safety- and configuration-relevant edges of that contract:
  filename sanitization (no path traversal, no shell-active glyphs,
  no leading dots), the 200-byte base / 255-byte total length cap,
  the sink health check that runs on every list_tools and every
  fetch_attachment call, and the two failure modes
  (sink_not_configured, sink_not_writable). The happy-path bytes-on-
  disk story lives in fetch_attachment_content.feature.

  Covered error layers (per BDD Guidelines §4.5):
    - Sanitization: path separators stripped              : 1
    - Sanitization: shell-active glyphs replaced          : 1
    - Sanitization: leading dot stripped                  : 1
    - Sanitization: RFC 2047 B-encoded word decoded       : 1
    - Sanitization: RFC 2047 Q-encoded word decoded       : 1
    - Sanitization: raw 8-bit filename header (no crash)  : 1
    - Length cap: base name truncated to 200 bytes        : 1
    - sink_not_configured surfaces in tool description    : 1
    - sink_not_configured surfaces on fetch_attachment    : 1
    - sink_not_writable when dir does not exist           : 1
    - sink_not_writable when dir is read-only             : 1
    - Tool description names the configured sink path     : 1
    - Health check passes silently when sink is healthy   : 1
    Total enumerated                                       : 13   covered by this feature: 13

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX/Documents"
    And the server is configured with caller "doc-agent" using policy "doc-policy"
    And policy "doc-policy" grants account "gupta-scaratec"
    And policy "doc-policy" folder defaults for "INBOX/Documents" are:
      | mode      | default |
      | blacklist | FULL    |
    And the folder "INBOX/Documents" holds a message with:
      | uid | from                 | subject |
      | 701 | sender@test.example  | Pinned  |

  # ----------------------------------------------------------- Sanitization

  Scenario: filename with path separators is stripped to a safe base
    Given the server attachment sink directory is a fresh writable directory
    And the message has attachment "../../etc/passwd.pdf" of type "application/pdf" with size 128 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field saved_to matches the regex "^[A-Za-z0-9._-]+_[0-9a-f]{8}\.pdf$"
    And the response field saved_to does NOT contain the literal string "/"
    And the response field saved_to does NOT contain the literal string ".."

    # Persistence-Validierung: no file ever got written outside the sink
    Then no file was written outside the sink directory
    And the file named saved_to exists in the sink directory

  Scenario: filename with shell-active and Unicode-control glyphs is sanitized
    Given the server attachment sink directory is a fresh writable directory
    And the message has attachment "invoice;rm -rf /.pdf" of type "application/pdf" with size 64 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response field saved_to matches the regex "^[A-Za-z0-9._-]+_[0-9a-f]{8}\.pdf$"
    And the response field saved_to does NOT contain the literal string ";"
    And the response field saved_to does NOT contain the literal string " "

  Scenario: filename with a leading dot becomes a non-hidden file
    Given the server attachment sink directory is a fresh writable directory
    And the message has attachment ".hidden.pdf" of type "application/pdf" with size 32 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response field saved_to matches the regex "^[A-Za-z0-9_-][A-Za-z0-9._-]*_[0-9a-f]{8}\.pdf$"
    And the response field saved_to does NOT start with the literal "."

  # ----------------------------------------------------------- Length cap

  Scenario: 500-byte filename is truncated to fit the 255-byte filesystem limit
    Given the server attachment sink directory is a fresh writable directory
    And the message has an attachment whose original filename is 500 bytes long, mime type "application/pdf", size 256 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response field saved_to has a byte length of at most 255
    And the response field saved_to matches the regex "^[A-Za-z0-9._-]{1,200}_[0-9a-f]{8}\.pdf$"
    And the file named saved_to exists in the sink directory

  # ----------------------------------------------------------- RFC 2047 decoding

  # Some mail servers (e.g. sgh-net.de) put an RFC 2047 encoded-word
  # directly in the Content-Disposition filename parameter on the
  # wire. The default email.compat32 parser does NOT decode it, so
  # get_filename() hands the server the raw `=?utf-8?b?...?=` token.
  # The server must decode the encoded-word BEFORE sanitization, or
  # the on-disk name becomes the unreadable mojibake reported on
  # 2026-06-15 (`__utf-8_b_..._<hash>`).

  Scenario: RFC 2047 B-encoded filename is decoded before sanitization
    Given the server attachment sink directory is a fresh writable directory
    And the message has attachment with raw wire filename "=?utf-8?b?ZmEgMjAyNi02IEt1YmEucGRm?=" of type "application/pdf" with size 96 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "OK"
    # decoded "fa 2026-6 Kuba.pdf" -> spaces sanitized to "_"
    And the response field saved_to matches the regex "^fa_2026-6_Kuba_[0-9a-f]{8}\.pdf$"
    And the response field saved_to does NOT contain the literal string "utf-8"
    And the file named saved_to exists in the sink directory

  Scenario: RFC 2047 Q-encoded filename is decoded before sanitization
    Given the server attachment sink directory is a fresh writable directory
    And the message has attachment with raw wire filename "=?utf-8?q?Rechnung_M=C3=A4rz.pdf?=" of type "application/pdf" with size 96 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "OK"
    # decoded "Rechnung März.pdf": Q-underscore -> space -> "_",
    # the umlaut "ä" is outside [A-Za-z0-9._-] -> "_". The presence
    # of "Rechnung_M" with NO "?q?" or "C3" proves the decode ran
    # before sanitization.
    And the response field saved_to matches the regex "^Rechnung_M_rz_[0-9a-f]{8}\.pdf$"
    And the response field saved_to does NOT contain the literal string "?"
    And the file named saved_to exists in the sink directory

  # ------------------------------------------ raw 8-bit filename header (no crash)

  # Reported 2026-06-18: fetch_attachment crashes with
  #   "'Header' object has no attribute 'lower'"
  # on any message whose headers carry RAW 8-bit (non-ASCII) bytes —
  # e.g. a "Rechnungsausgang" PDF from a sender with a German umlaut.
  # When a header value is not pure ASCII and is NOT an RFC 2047
  # encoded-word, Python's email.compat32 parser returns an
  # email.header.Header object, not a str. The attachment walk calls
  # `.lower()` on the Content-Disposition value directly; Header has no
  # `.lower()`, so the whole call dies with an unhandled AttributeError
  # instead of writing the attachment. RFC 2047 encoded-words (the two
  # scenarios above) parse to str and are unaffected — this is the
  # distinct raw-8-bit path the bug report mis-attributed to RFC 2047.

  Scenario: attachment on a message with a raw 8-bit Content-Disposition header is delivered, not crashed
    Given the server attachment sink directory is a fresh writable directory
    And the message has attachment with raw 8-bit wire filename "Rechnung Dürr.pdf" of type "application/pdf" with size 96 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "OK"
    # raw "Rechnung Dürr.pdf": space and the non-ASCII "ü" bytes are
    # outside [A-Za-z0-9._-] and collapse to "_". The decisive proof is
    # that a response comes back at all instead of an AttributeError.
    And the response field saved_to matches the regex "^Rechnung_D.*_[0-9a-f]{8}\.pdf$"
    And the file named saved_to exists in the sink directory

  # ----------------------------------------------------------- sink_not_configured

  Scenario: tool description names the absence of a sink when not configured
    Given the server attachment sink directory is not configured
    When doc-agent calls the MCP list_tools method
    Then the description of tool "fetch_attachment" contains the literal string "not configured"

  Scenario: fetch_attachment returns sink_not_configured when no sink is set
    Given the server attachment sink directory is not configured
    And the message has attachment "report.pdf" of type "application/pdf" with size 64 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "sink_not_configured"
    And the response field error.detail contains the literal string "attachment_sink_directory"

  # ----------------------------------------------------------- sink_not_writable

  Scenario: fetch_attachment returns sink_not_writable when the directory is missing
    Given the server attachment sink directory points at a path that does not exist
    And the message has attachment "report.pdf" of type "application/pdf" with size 64 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "sink_not_writable"
    And the response field error.detail contains the literal string "does not exist"

  Scenario: fetch_attachment returns sink_not_writable when the directory is read-only
    Given the server attachment sink directory is a read-only directory
    And the message has attachment "report.pdf" of type "application/pdf" with size 64 bytes
    When doc-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Documents", uid 701, part_id 0
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "sink_not_writable"
    And the response field error.detail contains the literal string "not writable"

  # ----------------------------------------------------------- Tool description rendering

  Scenario: tool description names the configured sink path when healthy
    Given the server attachment sink directory is a fresh writable directory
    When doc-agent calls the MCP list_tools method
    Then the description of tool "fetch_attachment" contains the configured sink path
    And the description of tool "fetch_attachment" does NOT contain the literal string "not configured"
    And the description of tool "fetch_attachment" does NOT contain the literal string "not writable"

  Scenario: tool description names the sink-not-writable state on list_tools
    Given the server attachment sink directory points at a path that does not exist
    When doc-agent calls the MCP list_tools method
    Then the description of tool "fetch_attachment" contains the literal string "not writable"
    And the description of tool "fetch_attachment" contains the literal string "does not exist"
