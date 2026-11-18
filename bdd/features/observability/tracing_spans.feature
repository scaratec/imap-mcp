Feature: Non-interfering OpenTelemetry spans for usage efficiency, IMAP round trips, and shutdown flush

  ADR 0053 §1 specifies OpenTelemetry span attributes for usage
  measurement while preserving strict non-interference:
    - Resource attributes include service.name, service.version,
      service.instance.id, and OTEL_RESOURCE_ATTRIBUTES.
    - Tool spans record bounded mcp.client.name / mcp.client.version,
      mcp.response.size_bytes, and post-policy counts ONLY
      (mcp.result.visible, mcp.result.returned, mcp.result.has_more;
      pre-policy mcp.result.matched is never emitted).
    - Policy DENY sets domain.outcome="rejected" and status UNSET;
      ALLOW + ERROR sets error.type and status ERROR.
    - IMAP wire commands emit child spans imap.<cmd>, exporter failures
      never fail MCP calls, and queued spans flush on EOF and SIGTERM.

  Covered error layers (per BDD Guidelines §4.5):
    - Custom OTEL_RESOURCE_ATTRIBUTES on every span                   : 1
    - Standard service.* resource attributes on every span            : 1
    - Bounded MCP clientInfo recorded & truncated at 128 ASCII chars  : 2
    - mcp.response.size_bytes matches exact UTF-8 wire bytes          : 1
    - Listing spans record visible/returned/has_more + 2-world non-interference : 2
    - Policy DENY sets domain.outcome=rejected, status UNSET          : 1
    - ALLOW + ERROR sets error.type and span status ERROR             : 1
    - IMAP wire commands appear as ordered child spans                : 1
    - Unreachable OTLP collector never fails MCP tool calls           : 1
    - Queued spans flush on stdin EOF and SIGTERM (with 60s BSP delay): 2
    Total enumerated                                                  : 13  covered by this feature: 13

  Background:
    Given the server clock is pinned to "2026-11-02T12:00:00Z"
    And a test trace collector is listening and the server exports traces to it
    And the environment variable "OTEL_RESOURCE_ATTRIBUTES" is set to "run.id=pilot-0042,harness=opencode,model=claude-sonnet-5.5,scenario=S1"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the server is configured with caller "trace-agent" using policy "trace-policy"
    And policy "trace-policy" grants account "gupta-scaratec"
    And policy "trace-policy" grants folder:
      | folder | mode      | default | rules                                                                                                        |
      | INBOX  | whitelist | NONE    | [{from_domain=nordlicht.example.org -> ENVELOPE}, {from_domain=meta-only.example.org -> METADATA}]           |
    And the folder "INBOX" holds messages:
      | uid | from_address                   | to_address            | subject               | date                 |
      | 401 | rechnung@nordlicht.example.org | me@gupta-scaratec.com | Ihre Rechnung 2026-10 | 2026-10-30T07:00:00Z |
      | 402 | rechnung@nordlicht.example.org | me@gupta-scaratec.com | Ihre Rechnung 2026-09 | 2026-09-29T07:00:00Z |
      | 403 | rechnung@nordlicht.example.org | me@gupta-scaratec.com | Ihre Rechnung 2026-08 | 2026-08-28T07:00:00Z |
      | 404 | werbung@angebote.example.org   | me@gupta-scaratec.com | Nur heute             | 2026-10-31T07:00:00Z |
      | 405 | status@meta-only.example.org   | me@gupta-scaratec.com | Meta-Sichtbar         | 2026-10-31T08:00:00Z |
    And the MCP test client identifies itself as "usability-harness" version "0.3.1"

  Scenario: Every span carries the operator-configured OTEL_RESOURCE_ATTRIBUTES
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then within 10 seconds every span in the trace sink has the resource attributes:
      | attribute | value             |
      | run.id    | pilot-0042        |
      | harness   | opencode          |
      | model     | claude-sonnet-5.5 |
      | scenario  | S1                |

  Scenario: Every span carries service.name, service.version, and service.instance.id
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then within 10 seconds every span in the trace sink has the resource attribute "service.name" equal to "imap-mcp"
    And every span in the trace sink has the resource attribute "service.version" equal to "3.0.0"
    And every span in the trace sink has a non-empty resource attribute "service.instance.id"

  Scenario: Tool spans record mcp.client.name and mcp.client.version from Initialize
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then within 10 seconds the trace sink contains a span "tool.list_messages" with attributes:
      | attribute          | value             |
      | mcp.client.name    | usability-harness |
      | mcp.client.version | 0.3.1             |

  Scenario: Oversized or control-character clientInfo values are sanitized to 128 printable ASCII chars
    Given the MCP test client reconnects with client name consisting of 200 "C" characters plus "\n\t" and version "1.0.0"
    When trace-agent calls list_accounts
    Then within 10 seconds the trace sink contains a span "tool.list_accounts" whose attribute "mcp.client.name" equals 128 "C" characters

  Scenario: mcp.response.size_bytes equals the UTF-8 byte length received by the client
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all", limit 2
    Then within 10 seconds the trace sink contains a span "tool.list_messages" whose attribute "mcp.response.size_bytes" equals the UTF-8 length of the text content the MCP test client received

  Scenario: Listing tools record visible, returned, and has_more while omitting pre-policy mcp.result.matched
    # 5 messages in INBOX: 404 is NONE (hidden), 401/402/403/405 are visible (4 total); limit 2 returns 2.
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all", limit 2
    Then within 10 seconds the trace sink contains a span "tool.list_messages" with attributes:
      | attribute           | value |
      | mcp.result.visible  | 4     |
      | mcp.result.returned | 2     |
      | mcp.result.has_more | true  |
    And the span "tool.list_messages" in the trace sink has no attribute "mcp.result.matched"

  Scenario: Adding NONE messages leaves redacted tool.search span count and outcome attributes unchanged
    Given the folder "INBOX" is reset to hold messages:
      | uid | from_address                   | to_address            | subject               | date                 |
      | 401 | rechnung@nordlicht.example.org | me@gupta-scaratec.com | Ihre Rechnung 2026-10 | 2026-10-30T07:00:00Z |
    When trace-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then within 10 seconds the trace sink contains a span "tool.search" with attributes:
      | attribute           | value    |
      | mcp.result.visible  | 1        |
      | mcp.result.returned | 1        |
      | mcp.result.has_more | false    |
      | domain.outcome      | accepted |
    And the attributes ["mcp.result.visible", "mcp.result.returned", "mcp.result.has_more", "domain.outcome", "mcp.response.size_bytes"] of span "tool.search" are remembered as "span_without_none"
    When the trace sink is cleared and additional messages are seeded into folder "INBOX":
      | uid | from_address              | to_address            | subject       | date                 |
      | 402 | secret@hidden.example.org | me@gupta-scaratec.com | Streng geheim | 2026-10-30T08:00:00Z |
    And trace-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then within 10 seconds the trace sink contains a span "tool.search" whose attributes ["mcp.result.visible", "mcp.result.returned", "mcp.result.has_more", "domain.outcome", "mcp.response.size_bytes"] equal the remembered "span_without_none"
    And no span in the trace sink contains the value "Streng geheim"

  Scenario: A policy DENY sets domain.outcome=rejected and keeps span status UNSET
    When trace-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 405
    Then the response decision is DENY
    And within 10 seconds the trace sink contains a span "tool.fetch_envelope" with attributes:
      | attribute               | value                     |
      | domain.outcome          | rejected                  |
      | domain.rejection.reason | visibility_below_ENVELOPE |
    And the span "tool.fetch_envelope" in the trace sink has status "UNSET"

  Scenario: An ALLOW + ERROR envelope sets error.type and span status ERROR
    Given policy "trace-policy" grants folder:
      | folder  | mode      | default  | rules |
      | Archiv7 | blacklist | ENVELOPE | []    |
    When trace-agent calls list_messages with account "gupta-scaratec", folder "Archiv7", criteria {}, scope "all"
    Then within 10 seconds the trace sink contains a span "tool.list_messages" with attribute "error.type" equal to "folder_absent"
    And the span "tool.list_messages" in the trace sink has status "ERROR"

  Scenario: IMAP wire commands of a call appear as child spans matching the proxy command count
    When trace-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "nordlicht.example.org"}, scope "all"
    Then within 10 seconds the trace sink contains one connected trace rooted at span "tool.search" with child spans in order:
      | span              |
      | imap.connect      |
      | imap.authenticate |
      | imap.select       |
      | imap.uid_search   |
      | imap.uid_fetch    |
    And the number of "imap.*" child spans of "tool.search" equals the number of commands the IMAP command log for "gupta-scaratec" recorded for that call

  Scenario: An unreachable OTLP collector never causes MCP tool calls to fail
    Given the OTLP trace endpoint points to a closed loopback port
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response decision is ALLOW
    And the response field total_visible equals 4

  Scenario Outline: Queued spans flush cleanly when the server terminates via <ends_the_server>
    Given the environment variable "OTEL_BSP_SCHEDULE_DELAY" is set to "60000"
    When trace-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    And the MCP test client <ends_the_server> immediately after the response
    Then within 5 seconds the server process has exited
    And within 5 seconds the trace sink contains a span "tool.list_messages"

    Examples:
      | ends_the_server             |
      | closes the server's stdin   |
      | sends SIGTERM to the server |
