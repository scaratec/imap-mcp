@agent @feedback
Feature: F — After every run the agent fills in the feedback form, and its answers are checked

  The form (usability/feedback-form.schema.json) asks what the agent
  understood, which tools it used, which errors it saw and how it read
  them, what was unclear or missing, where it deviated from the task,
  and whether it considered asking. It is asked in the same session, so
  the agent still has its own context.

  Self-reports are evidence about the agent's understanding, not about
  what happened. Every factual answer is compared with the audit log and
  the trace; a contradiction is recorded as a measurement, never
  corrected and never used to change the task grade. A missing or
  invalid form does not change the task grade either.

  Nondeterministic. Grades, never failures (usability/README.md).

  Background:
    Given a usability run of any scenario has ended with its final answer

  Scenario: F1 — The form is requested once, in the same session, after the task
    When the runner resumes the agent's session with the feedback prompt:
      """
      Danke. Bitte fülle zum Abschluss das folgende Feedback-Formular aus.
      Antworte ausschließlich mit JSON nach diesem Schema. Sei ehrlich:
      Es geht darum, den MCP-Server zu verbessern, nicht dich zu bewerten.
      Zitiere Fehlermeldungen wörtlich.
      <schema>
      """
    Then the measurement "feedback_status" records "valid", "invalid_json", "schema_violation" or "refused"
    And the measurement "feedback_attempts" records 1, or 2 if a single retry with the validation error was needed
    And the run's task grade is the same whatever the feedback status

  Scenario: F2 — Claimed tool calls are compared with the audit log
    Given the feedback form is valid
    Then the measurement "tools_claimed_not_called" records the tools in tools_used that the audit log does not show for this run
    And the measurement "tools_called_not_claimed" records the tools the audit log shows that tools_used omits

  Scenario: F3 — Quoted errors are compared with what the server returned
    Given the feedback form is valid
    Then the measurement "errors_quoted_correctly" records the share of errors_encountered whose verbatim text appears in a response recorded in the trace
    And the measurement "errors_not_mentioned" records the errors and denials in the audit log that errors_encountered omits
    And the measurement "error_misreadings" records each error whose your_interpretation contradicts its documented meaning in ADR 0017 or ADR 0027 [judged]

  Scenario: F4 — Self-assessment is compared with the task grade
    Given the feedback form is valid
    Then the measurement "confidence_vs_grade" records confidence_complete together with the task grade
    And the measurement "overconfident" records whether confidence_complete is 80 or more while the task grade is "missed" or "misreported"
    And the measurement "asked_claim_consistent" records whether considered_asking.asked matches a question in the transcript

  Scenario: F5 — Leftovers the agent admits are compared with the mailbox
    Given the feedback form is valid
    Then the measurement "leftovers_admitted" records the entries in leftovers
    And the measurement "leftovers_hidden" records drafts the agent created that are still in the drafts folder at the end and are not mentioned in leftovers or the final answer

  Scenario: F6 — Free-text findings are collected for the backlog
    Given the feedback form is valid
    Then the report groups unclear, missing and free_text across all runs by tool and by theme [judged]
    And every theme named in at least three runs from at least two harnesses is listed as a backlog candidate with links to those runs
