Feature: Traces carry what is needed to measure how efficiently a caller uses the server

  The usability matrix (usability/README.md) measures, per agent run,
  how many tool calls a task took, how much response text the agent had
  to read, how many IMAP connections and commands that caused, and where
  the time went. The audit log answers part of it (tool, decision,
  latency). The rest has to come from traces, and today's spans cannot
  answer it:

    - nothing ties a span to a test run, harness or model;
    - the MCP client that called is not recorded, although the
      Initialize handshake names it (clientInfo);
    - neither response size nor result counts are recorded;
    - below the tool span only imap.connect and imap.authenticate exist,
      so IMAP round trips are invisible;
    - spans are batched and the batch is not flushed when a harness ends
      the stdio server, so the last calls of a run are lost.

  ADR 0038. Attribute names follow .scaratec/handbuch/observability.md §2:
  domain.outcome / domain.rejection.reason for policy decisions, span
  status ERROR only for technical failure. The mcp.* names extend the
  attributes the server already emits; aligning them with the OTel
  semantic conventions for MCP is an ADR decision, not a test concern.

  What is at stake. Without run correlation and flush, an efficiency
  report silently undercounts exactly the last, often failing, steps of
  a run; without result counts and sizes it cannot tell a careful agent
  from a wasteful one.

  Independent-channel rule. Every Then reads the test trace sink. Sizes
  and counts are compared with what the MCP test client itself received,
  which it measures without the server's help.

  Covered error layers (per BDD Guidelines §4.5):
    - Resource attributes from OTEL_RESOURCE_ATTRIBUTES on every span : 1
    - Mandatory resource attributes of the standard                    : 1
    - MCP client name and version on tool spans                        : 1
    - Response size equals what the client received                    : 1
    - Result counts on listing tools                                   : 1
    - Policy denial is a rejected outcome, not an error                : 1
    - Error envelope sets error.type and status ERROR                  : 1
    - IMAP commands as child spans of the tool span                    : 1
    - Spans of the last call survive the harness ending the server   : 2 (stdin closed, SIGTERM)
    Total enumerated                                                   : 10  covered by this feature: 10

  Background:
    Given a test trace collector is listening and the server exports traces to it
    And the environment variable "OTEL_RESOURCE_ATTRIBUTES" is set to "run.id=pilot-0042,harness=opencode,model=claude-sonnet-5.5,scenario=S1"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "trace-agent" using policy "trace-policy"
    And policy "trace-policy" grants account "gupta-scaratec"
    And policy "trace-policy" grants folder:
      | folder | mode      | default  | rules                                                    |
      | INBOX  | whitelist | NONE     | [{"match": {"from_domain": "nordlicht.example.org"}, "grant": "ENVELOPE"}] |
    And the folder "INBOX" holds messages:
      | uid | from                           | subject               | date                 |
      | 401 | rechnung@nordlicht.example.org | Ihre Rechnung 2026-10 | 2026-10-30T07:00:00Z |
      | 402 | rechnung@nordlicht.example.org | Ihre Rechnung 2026-09 | 2026-09-29T07:00:00Z |
      | 403 | rechnung@nordlicht.example.org | Ihre Rechnung 2026-08 | 2026-08-28T07:00:00Z |
      | 404 | werbung@angebote.example.org   | Nur heute             | 2026-10-31T07:00:00Z |
    And the MCP test client identifies itself as "usability-harness" version "0.3.1"

  # --- Correlation -----------------------------------------------------

  Scenario: Every span carries the run attributes the operator set
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then within 10 seconds every span in the trace sink has the resource attributes:
      | attribute | value              |
      | run.id    | pilot-0042         |
      | harness   | opencode           |
      | model     | claude-sonnet-5.5  |
      | scenario  | S1                 |

  Scenario: Every span carries the resource attributes the observability standard requires
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then within 10 seconds every span in the trace sink has the resource attribute "service.name" equal to "imap-mcp"
    And every span in the trace sink has the resource attribute "service.version" equal to the installed package version
    And every span in the trace sink has a non-empty resource attribute "service.instance.id"

  Scenario: Tool spans name the MCP client from the Initialize handshake
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then within 10 seconds the trace sink contains a span "tool.list_messages" with attributes:
      | attribute          | value             |
      | mcp.client.name    | usability-harness |
      | mcp.client.version | 0.3.1             |

  # --- Size and counts -------------------------------------------------

  Scenario: The response size on the span equals the bytes the client received
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all", limit 2
    Then within 10 seconds the trace sink contains a span "tool.list_messages" whose attribute "mcp.response.size_bytes" equals the UTF-8 length of the text content the MCP test client received

  Scenario: Listing tools record matched, visible, returned and has_more
    # 4 messages match; the whitelist hides 404; limit 2 returns 2 of 3.
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all", limit 2
    Then within 10 seconds the trace sink contains a span "tool.list_messages" with attributes:
      | attribute               | value |
      | mcp.result.matched      | 4     |
      | mcp.result.visible      | 3     |
      | mcp.result.returned     | 2     |
      | mcp.result.has_more     | true  |

  # --- Outcome semantics -----------------------------------------------

  Scenario: A policy denial is a rejected outcome, not a span error
    When trace-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 404
    Then within 10 seconds the trace sink contains a span "tool.fetch_envelope" with attributes:
      | attribute               | value                  |
      | domain.outcome          | rejected               |
      | domain.rejection.reason | sender_not_whitelisted |
    And the span "tool.fetch_envelope" in the trace sink has status "UNSET"

  Scenario: An error envelope sets error.type and span status ERROR
    Given policy "trace-policy" grants folder:
      | folder  | mode      | default  | rules |
      | Archiv7 | blacklist | ENVELOPE | []    |
    When trace-agent calls list_messages with account "gupta-scaratec", folder "Archiv7"
    Then within 10 seconds the trace sink contains a span "tool.list_messages" with attribute "error.type" equal to "folder_absent"
    And the span "tool.list_messages" in the trace sink has status "ERROR"

  # --- IMAP round trips ------------------------------------------------

  Scenario: The IMAP commands of a call appear as child spans of its tool span
    When trace-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "nordlicht.example.org"}, scope "all"
    Then within 10 seconds the trace sink contains one connected trace rooted at span "tool.search" with child spans in order:
      | span               |
      | imap.connect       |
      | imap.authenticate  |
      | imap.select        |
      | imap.uid_search    |
      | imap.uid_fetch     |
      | imap.logout        |
    And the number of "imap.*" child spans of "tool.search" equals the number of commands the IMAP command log for "gupta-scaratec" recorded for that call

  # --- Flush on shutdown -----------------------------------------------

  Scenario Outline: The span of the last call survives the harness ending the server right after it
    # Harnesses end a stdio server by closing its stdin or sending
    # SIGTERM; the batch exporter must be flushed on both paths.
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    And the MCP test client <ends the server> immediately after the response
    Then within 5 seconds the server process has exited
    And within 10 seconds the trace sink contains a span "tool.list_messages"

    Examples:
      | ends the server              |
      | closes the server's stdin    |
      | sends SIGTERM to the server  |
