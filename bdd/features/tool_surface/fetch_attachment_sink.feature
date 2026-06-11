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
    - Length cap: base name truncated to 200 bytes        : 1
    - sink_not_configured surfaces in tool description    : 1
    - sink_not_configured surfaces on fetch_attachment    : 1
    - sink_not_writable when dir does not exist           : 1
    - sink_not_writable when dir is read-only             : 1
    - Tool description names the configured sink path     : 1
    - Health check passes silently when sink is healthy   : 1
    Total enumerated                                       : 10   covered by this feature: 10

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
