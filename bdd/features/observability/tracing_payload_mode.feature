Feature: Trace payloads follow an operator-chosen mode: off, redacted, or full

  ADR 0053 §1 controls request/response attributes on
  OpenTelemetry tool.<name> spans via IMAP_MCP_TRACE_PAYLOADS:
    - redacted (default): mcp.request and mcp.response omit message
      bodies, subjects, addresses, headers, and attachment filenames or
      paths (saved_to, saved_path).
    - full: complete JSON up to IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS
      (default 16384), marked "…[truncated <n> chars]" when cut.
    - off: neither mcp.request nor mcp.response is emitted.
    - Secrets never appear in any mode, and unknown mode values refuse
      server startup.

  Covered error layers (per BDD Guidelines §4.5):
    - Default mode when env unset is redacted                    : 1
    - redacted scrubs draft content & recipients from request    : 1
    - redacted scrubs subjects & addresses from fetch_envelope   : 1
    - redacted scrubs attachment filenames & paths on sink fetch : 1
    - full records complete request and response                 : 1
    - full truncates oversized payloads with explicit marker     : 1
    - off omits payload attributes while keeping timing          : 1
    - Active mode recorded & secrets absent across all 3 modes   : 3
    - Unknown mode value refuses startup                         : 1
    Total enumerated                                             : 11  covered by this feature: 11

  Background:
    Given the server clock is pinned to "2026-10-31T12:00:00Z"
    And a test trace collector is listening and the server exports traces to it
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the secret store holds the password "pw-Q7c!kX29" for account "gupta-scaratec"
    And the server is configured with caller "trace-agent" using policy "trace-policy"
    And policy "trace-policy" grants account "gupta-scaratec"
    And policy "trace-policy" grants folder:
      | folder | mode      | default | rules | draft_append |
      | INBOX  | blacklist | FULL    | []    | false        |
      | Drafts | blacklist | FULL    | []    | true         |
    And the folder "INBOX" holds messages:
      | uid | from_address               | to_address            | subject               | date                 |
      | 311 | mara.lindqvist@example.org | me@gupta-scaratec.com | Kündigung Mietvertrag | 2026-10-30T07:00:00Z |

  Scenario: Without a configured mode the payloads default to redacted
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is not set
    When trace-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 311
    Then within 10 seconds the trace sink contains a span "tool.fetch_envelope" with attribute "imap_mcp.trace.payloads" equal to "redacted"

  Scenario: redacted keeps draft content and recipients out of mcp.request
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "redacted"
    When trace-agent calls create_draft with account "gupta-scaratec", folder "Drafts", rfc822 payload:
      """
      From: trace-agent@example.org
      To: jonas.hartmann@example.org
      Subject: Mieterhöhung zum 1. Januar
      MIME-Version: 1.0
      Content-Type: text/plain; charset=UTF-8

      Die Miete steigt um 42 Euro.
      """
    Then within 10 seconds the trace sink contains a span "tool.create_draft" whose attribute "mcp.request" contains "\"rfc822\": \"<redacted>\""
    And no span in the trace sink contains the value "jonas.hartmann@example.org"
    And no span in the trace sink contains the value "Mieterhöhung"
    And no span in the trace sink contains the value "42 Euro"

  Scenario: redacted keeps subjects and addresses out of mcp.response
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "redacted"
    When trace-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 311
    Then within 10 seconds the trace sink contains a span "tool.fetch_envelope" whose attribute "mcp.response" contains "\"decision\": \"ALLOW\""
    And no span in the trace sink contains the value "mara.lindqvist@example.org"
    And no span in the trace sink contains the value "Kündigung Mietvertrag"

  Scenario: redacted scrubs attachment filenames and filesystem paths on fetch_attachment
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "redacted"
    And the server attachment sink directory is a fresh writable directory
    And the message at uid 311 has attachment "Geheimer_Mietvertrag.pdf" of type "application/pdf" with content byte "M" repeated 128 times
    When trace-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX", uid 311, part_id 0
    Then within 10 seconds the trace sink contains a span "tool.fetch_attachment" whose attribute "mcp.response" contains "\"decision\": \"ALLOW\""
    And no span in the trace sink contains the value "Geheimer_Mietvertrag"

  Scenario: full records the complete request and response
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "full"
    When trace-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 311
    Then within 10 seconds the trace sink contains a span "tool.fetch_envelope" whose attribute "mcp.request" contains "\"uid\": 311"
    And the span "tool.fetch_envelope" in the trace sink has attribute "mcp.response" containing "Kündigung Mietvertrag"
    And the span "tool.fetch_envelope" in the trace sink has attribute "mcp.response" containing "mara.lindqvist@example.org"

  Scenario: full truncates an oversized payload at IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS and appends the marker
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "full"
    And the environment variable "IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS" is set to "2000"
    And the folder "INBOX" holds a message with uid 312 whose plain-text body is the letter "x" repeated 5000 times
    When trace-agent calls fetch_body with account "gupta-scaratec", folder "INBOX", uid 312
    Then within 10 seconds the trace sink contains a span "tool.fetch_body" whose attribute "mcp.response" is at most 2000 characters before the marker
    And the span "tool.fetch_body" in the trace sink has attribute "mcp.response" ending with a marker of the form "…[truncated <n> chars]" where n is greater than 3000

  Scenario: off drops both payload attributes while preserving the span and latency
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "off"
    When trace-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 311
    Then within 10 seconds the trace sink contains a span "tool.fetch_envelope" with attribute "imap_mcp.trace.payloads" equal to "off"
    And the span "tool.fetch_envelope" in the trace sink has an attribute "mcp.latency_ms"
    And the span "tool.fetch_envelope" in the trace sink has no attribute "mcp.request"
    And the span "tool.fetch_envelope" in the trace sink has no attribute "mcp.response"

  Scenario Outline: The active mode is recorded on the span and secrets never appear in any mode
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "<mode>"
    When trace-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 311
    Then within 10 seconds the trace sink contains a span "tool.fetch_envelope" with attribute "imap_mcp.trace.payloads" equal to "<mode>"
    And no span in the trace sink contains the value "pw-Q7c!kX29"

    Examples:
      | mode     |
      | off      |
      | redacted |
      | full     |

  Scenario: An unknown mode value is refused at startup
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "verbose"
    When the server process is started with transport "stdio"
    Then the server refuses to start
    And the startup error names "IMAP_MCP_TRACE_PAYLOADS" and the allowed values "off, redacted, full"
