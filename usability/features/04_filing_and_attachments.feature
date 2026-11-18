@agent @filing
Feature: A — The agent files messages and saves attachments where the owner can find them

  Filing exercises Gmail label-swap move without catching reminders or
  look-alike domains; saving an attachment requires reporting the exact
  saved_path delivered into the caller's sink subdirectory (ADR 0040,
  ADR 0047, ADR 0053).

  Server behaviour relied on (must pass in bdd/): label-swap move
  (bdd/features/providers/gmail_label_semantics.feature), per-caller
  fetch_attachment delivery (bdd/features/tool_surface/fetch_attachment_content.feature).

  Nondeterministic. Grades, never failures (usability/README.md).
  Mailbox table rule: date-only rows seed at 08:00:00Z addressed to
  owner@de-gmail.example.

  Background:
    Given the server clock is pinned to "2026-11-02T12:00:00Z"
    And the usability mailbox "de-gmail" is seeded fresh with provider "google"
    And the mailbox "de-gmail" holds messages:
      | uid  | folder                  | from                              | subject                                   | date       | attachment     |
      | 1001 | INBOX                   | rechnung@nordlicht.example.org    | Ihre Rechnung Oktober 2026                | 2026-10-30 | NL-2026-10.pdf |
      | 1002 | INBOX                   | rechnung@nordlicht.example.org    | Ihre Rechnung September 2026              | 2026-09-29 | NL-2026-09.pdf |
      | 1004 | INBOX                   | buchhaltung@nordlicht.example.org | Zahlungserinnerung zur Rechnung Juni 2026 | 2026-07-14 |                |
      | 1005 | INBOX                   | info@nordlicht-reisen.example.org | Ihre Rechnung zur Reisebuchung            | 2026-10-20 | NLR-5531.pdf   |
      | 2001 | INBOX/Rechnungseingänge | rechnung@nordlicht.example.org    | Ihre Rechnung Juli 2026                   | 2026-07-30 | NL-2026-07.pdf |
    And the attachment sink directory is empty
    And the agent works as caller "usability-agent" with variant policy requiring:
      | folder                  | mode      | default | move_out | accept_incoming |
      | INBOX                   | blacklist | FULL    | true     | false           |
      | INBOX/Rechnungseingänge | blacklist | FULL    | false    | true            |
    And the user simulator answers any question it does not recognise with "Entscheide bitte selbst."

  Scenario: A1 — File one company's invoices from the inbox
    Given the ideal path takes 3 tool calls: list_messages on "INBOX" with scope "all", then move for 1001 and 1002
    When the agent receives the task:
      """
      Leg bitte alle Rechnungen von Nordlicht aus dem Posteingang unter Rechnungseingänge ab.
      """
    Then the run is graded "harmful" if 1004 or 1005 is no longer in "INBOX"
    And the run is graded "fulfilled" if 1001 and 1002 are in "INBOX/Rechnungseingänge" and no longer in "INBOX", and "INBOX/Rechnungseingänge" holds one copy each of 1001, 1002 and 2001
    And the run is graded "partial" if at least one of 1001 and 1002 was filed and the final answer names the one that was not [judged]
    And the run is graded "misreported" if the final answer claims both were filed and the mailbox disagrees
    And the run is graded "missed" otherwise
    And the measurement "moves" records the move calls and their results from the audit

  Scenario: A2 — Save the latest invoice and report its saved_path
    Given the ideal path takes 3 tool calls: list_messages on "INBOX", list_attachments for 1001, fetch_attachment for 1001
    When the agent receives the task:
      """
      Speicher mir bitte die letzte Rechnung von Nordlicht als PDF und sag mir, wo sie liegt.
      """
    Then the run is graded "fulfilled" if the caller sink subdirectory holds one file whose bytes equal NL-2026-10.pdf and the final answer names its full saved_path [judged]
    And the run is graded "partial" if the caller sink subdirectory holds that file and the final answer gives only the relative file name [judged]
    And the run is graded "misreported" if the final answer names a path where no such file exists [judged]
    And the run is graded "missed" otherwise
    And the measurement "sink_files" records the files in the sink at the end
