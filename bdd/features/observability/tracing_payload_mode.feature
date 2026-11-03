Feature: Trace payloads follow an operator-chosen mode: off, redacted or full

  Each tool call produces a span tool.<name> (tracing.py, dispatch.py).
  Today the span carries the complete request arguments as
  mcp.request, draft bodies and recipients included, and a partly
  filtered response as mcp.response. The audit log, by contrast, never
  records content (ADR 0021).

  Full payloads are valuable when the operator alone reads the trace
  store and wants to see exactly what an agent sent and received; they
  are a liability anywhere else. The company observability standard
  (.scaratec/handbuch/observability.md §1, §7) forbids personal data in
  telemetry. The mode is therefore an explicit operator choice through
  IMAP_MCP_TRACE_PAYLOADS, the default is redacted, and full is an
  opt-in that is visible on every span it affects.

  ADR 0037 records the modes and the deviation of mode full from the
  observability standard.

  Rules used below:
    - off: the span has no mcp.request and no mcp.response attribute.
    - redacted: mcp.request holds the arguments as the audit log records
      them (ADR 0021); mcp.response holds the response without message
      content, addresses, subjects, headers, bodies or attachment data.
    - full: both attributes hold the complete JSON, each cut at
      IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS (default 16384) and marked
      "…[truncated <n> chars]" when cut.
    - In every mode each tool span carries imap_mcp.trace.payloads with
      the active mode, so a reader can tell "redacted" from "empty".
    - Secrets from the secret store never appear in any mode.

  What is at stake. In redacted mode a single leaking field puts mail
  subjects and addresses into a trace store that was assumed to be
  clean. In full mode a silent truncation, or a mode nobody can see on
  the span, makes the trace misleading exactly when it is consulted.

  Independent-channel rule. Every Then reads the test trace sink, which
  receives OTLP from the server process like a production collector and
  is never consulted by the server. Negative witnesses search every
  attribute of every span in the sink.

  Covered error layers (per BDD Guidelines §4.5):
    - Default mode is redacted                                 : 1
    - redacted: no content in request (write tool)              : 1
    - redacted: no content in response (read tool)              : 1
    - full: request and response complete                       : 1
    - full: oversized payload truncated and marked              : 1
    - off: no payload attributes, span still present            : 1
    - Active mode recorded on the span                          : 3 (one per mode)
    - Secrets absent in every mode                              : 3 (one per mode)
    - Unknown mode value refused at startup                     : 1
    Total enumerated                                            : 13  covered by this feature: 13

  Background:
    Given a test trace collector is listening and the server exports traces to it
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
      | Drafts      |
    And the secret store holds the password "pw-Q7c!kX29" for account "gupta-scaratec"
    And the server is configured with caller "trace-agent" using policy "trace-policy"
    And policy "trace-policy" grants account "gupta-scaratec"
    And policy "trace-policy" grants folder:
      | folder | mode      | default | rules | draft_append |
      | INBOX  | blacklist | FULL    | []    | false        |
      | Drafts | blacklist | FULL    | []    | true         |
    And the folder "INBOX" holds messages:
      | uid | from                       | subject               | date                 |
      | 311 | mara.lindqvist@example.org | Kündigung Mietvertrag | 2026-10-30T07:00:00Z |

  # --- Default ---------------------------------------------------------

  Scenario: Without a configured mode the payloads are redacted
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is not set
    When trace-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 311
    Then within 10 seconds the trace sink contains a span "tool.fetch_envelope" with attribute "imap_mcp.trace.payloads" equal to "redacted"

  # --- redacted --------------------------------------------------------

  Scenario: redacted keeps draft content and recipients out of the request attribute
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

  Scenario: redacted keeps subjects and addresses out of the response attribute
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "redacted"
    When trace-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 311
    Then within 10 seconds the trace sink contains a span "tool.fetch_envelope" whose attribute "mcp.response" contains "\"decision\": \"ALLOW\""
    And no span in the trace sink contains the value "mara.lindqvist@example.org"
    And no span in the trace sink contains the value "Kündigung Mietvertrag"

  # --- full ------------------------------------------------------------

  Scenario: full records the complete request and response
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "full"
    When trace-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 311
    Then within 10 seconds the trace sink contains a span "tool.fetch_envelope" whose attribute "mcp.request" contains "\"uid\": 311"
    And the span "tool.fetch_envelope" in the trace sink has attribute "mcp.response" containing "Kündigung Mietvertrag"
    And the span "tool.fetch_envelope" in the trace sink has attribute "mcp.response" containing "mara.lindqvist@example.org"

  Scenario: full cuts an oversized payload at the configured limit and marks the cut
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "full"
    And the environment variable "IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS" is set to "2000"
    And the folder "INBOX" holds a message with uid 312 whose plain-text body is the letter "x" repeated 5000 times
    When trace-agent calls fetch_body with account "gupta-scaratec", folder "INBOX", uid 312
    Then within 10 seconds the trace sink contains a span "tool.fetch_body" whose attribute "mcp.response" is at most 2000 characters before the marker
    And the span "tool.fetch_body" in the trace sink has attribute "mcp.response" ending with a marker of the form "…[truncated <n> chars]" where n is greater than 3000

  # --- off -------------------------------------------------------------

  Scenario: off drops both payload attributes but keeps the span and its timing
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "off"
    When trace-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 311
    Then within 10 seconds the trace sink contains a span "tool.fetch_envelope" with attribute "imap_mcp.trace.payloads" equal to "off"
    And the span "tool.fetch_envelope" in the trace sink has an attribute "mcp.latency_ms"
    And the span "tool.fetch_envelope" in the trace sink has no attribute "mcp.request"
    And the span "tool.fetch_envelope" in the trace sink has no attribute "mcp.response"

  # --- Invariants across modes -----------------------------------------

  Scenario Outline: The active mode is on the span and secrets never appear
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "<mode>"
    When trace-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 311
    Then within 10 seconds the trace sink contains a span "tool.fetch_envelope" with attribute "imap_mcp.trace.payloads" equal to "<mode>"
    And no span in the trace sink contains the value "pw-Q7c!kX29"

    Examples:
      | mode     |
      | off      |
      | redacted |
      | full     |

  # --- Error path: configuration ---------------------------------------

  Scenario: An unknown mode is refused at startup instead of falling back silently
    # Falling back to redacted would hide a typo in "full"; falling back
    # to full would leak. Refusing is the only choice that cannot surprise.
    Given the environment variable "IMAP_MCP_TRACE_PAYLOADS" is set to "verbose"
    When the server process is started with transport "stdio"
    Then the server refuses to start
    And the startup error names "IMAP_MCP_TRACE_PAYLOADS" and the allowed values "off, redacted, full"
