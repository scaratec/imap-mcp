@agent @metrics
Feature: M — Every run records how efficiently the agent used the server

  Efficiency is measured, never graded: an agent that asks, verifies, or
  pages through results honestly needs more calls than one that guesses,
  and reports show efficiency metrics alongside the task grade (ADR 0048,
  ADR 0053). Every scenario in features 01–05 declares its ideal call
  count so ideal_ratio always has a well-defined denominator.

  Sources: audit log (tool calls including -32602 schema rejections,
  decisions, results, latency), trace (response size, post-policy visible
  result counts, IMAP child spans, client identity; see
  bdd/features/observability/tracing_spans.feature), transcript (turns,
  tokens where the harness reports them).

  Nondeterministic. Grades, never failures (usability/README.md).

  Background:
    Given a usability run of any scenario has ended
    And its spans in the test collector carry run.id, harness, model and scenario

  Scenario: M1 — Call counts and the ideal path
    Then the measurement "tool_calls" records the number of MCP tool invocations (outcome/single records) in the audit log for this run
    And the measurement "ideal_ratio" records tool_calls divided by the ideal path's call count
    And the measurement "calls_by_tool" records the tool calls grouped by tool name

  Scenario: M2 — Waste
    Then the measurement "failed_calls" records the calls with result ERROR (including invalid_arguments schema rejections)
    And the measurement "denied_calls" records the calls with decision DENY
    And the measurement "repeated_calls" records calls identical in tool and arguments to an earlier call in the same run
    And the measurement "refetch_after_listing" records fetch_envelope calls for a uid that an earlier list_messages response in the same run already returned with the same fields
    And the measurement "repeated_discovery" records list_accounts, list_folders and describe_policy calls beyond the first of each per account
    And the measurement "wrong_folder_name_calls" records calls whose folder argument does not exist on the account and is not a canonical special-use name
    And the measurement "unfinished_paging" records listing responses with has_more true that no later call continued

  Scenario: M3 — Load on the agent
    Then the measurement "response_bytes" records the sum of mcp.response.size_bytes over all tool spans of the run
    And the measurement "largest_response_bytes" records the largest single mcp.response.size_bytes and its tool
    And the measurement "tokens" records input and output tokens where the harness reports them, and "unknown" otherwise
    And the measurement "response_bytes_per_input_token" records response_bytes divided by input tokens where both are known

  Scenario: M4 — Time
    Then the measurement "wall_clock_seconds" records the time from the first prompt to the final answer
    And the measurement "server_seconds" records the sum of the durations of all tool spans
    And the measurement "imap_seconds" records the sum of the durations of all imap.* spans
    And the measurement "imap_connections" records the number of imap.connect spans
    And the measurement "turns" records the number of agent turns including simulator answers

  Scenario: M5 — Identity of the run
    Then the run record stores harness, harness version, model as requested, model as resolved, server version, server variant, scenario, repetition and run.id
    And the run record stores mcp.client.name and mcp.client.version as seen on the tool spans
