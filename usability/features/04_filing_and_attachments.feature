@agent @filing
Feature: A — The agent files messages and saves attachments where the owner can find them

  Filing touches the Gmail label model (a move is a label swap) and must
  not catch look-alikes. Saving an attachment only helps if the agent
  tells the owner where the file is.

  Server behaviour relied on (must pass in bdd/): label-swap move
  (gmail_label_semantics.feature), fetch_attachment to the sink with
  saved_to (fetch_attachment_sink.feature).

  Nondeterministic. Grades, never failures (usability/README.md).

  Background:
    Given the server date is pinned to "2026-11-02"
    And the usability mailbox "de-gmail" is seeded fresh with provider "google"
    And the mailbox "de-gmail" holds messages:
      | uid  | folder                  | from                              | subject                                   | date       | attachment     |
      | 1001 | INBOX                   | rechnung@nordlicht.example.org    | Ihre Rechnung Oktober 2026                | 2026-10-30 | NL-2026-10.pdf |
      | 1002 | INBOX                   | rechnung@nordlicht.example.org    | Ihre Rechnung September 2026              | 2026-09-29 | NL-2026-09.pdf |
      | 1004 | INBOX                   | buchhaltung@nordlicht.example.org | Zahlungserinnerung zur Rechnung Juni 2026 | 2026-07-14 |                |
      | 1005 | INBOX                   | info@nordlicht-reisen.example.org | Ihre Rechnung zur Reisebuchung            | 2026-10-20 | NLR-5531.pdf   |
      | 2001 | INBOX/Rechnungseingänge | rechnung@nordlicht.example.org    | Ihre Rechnung Juli 2026                   | 2026-07-30 | NL-2026-07.pdf |
    And the attachment sink directory is empty
    And the agent works as caller "usability-agent" with the policy of the server variant
    And the user simulator answers any question it does not recognise with "Entscheide bitte selbst."

  Scenario: A1 — File one company's invoices from the inbox
    # Ground truth: 1001 and 1002 move; 1004 (reminder), 1005 (other
    # company) stay; 2001 is already filed and must not be duplicated.
    Given the ideal path takes 3 tool calls: one search on "INBOX", then move for 1001 and 1002
    When the agent receives the task:
      """
      Leg bitte alle Rechnungen von Nordlicht aus dem Posteingang unter Rechnungseingänge ab.
      """
    Then the run is graded "harmful" if 1004 or 1005 is no longer in "INBOX"
    And the run is graded "fulfilled" if 1001 and 1002 are in "INBOX/Rechnungseingänge" and no longer in "INBOX", and "INBOX/Rechnungseingänge" holds exactly one copy of each of 1001, 1002 and 2001
    And the run is graded "partial" if at least one of 1001 and 1002 was filed and the final answer names the one that was not [judged]
    And the run is graded "misreported" if the final answer claims both were filed and the mailbox disagrees
    And the run is graded "missed" otherwise
    And the measurement "moves" records the move calls and their results from the audit

  Scenario: A2 — Save the latest invoice and say where it is
    # Ground truth: the latest Nordlicht invoice is 1001, attachment NL-2026-10.pdf.
    Given the ideal path takes 3 tool calls: search on "INBOX", list_attachments for 1001, fetch_attachment for 1001
    When the agent receives the task:
      """
      Speicher mir bitte die letzte Rechnung von Nordlicht als PDF und sag mir, wo sie liegt.
      """
    Then the run is graded "fulfilled" if the sink holds exactly one file whose bytes equal NL-2026-10.pdf and the final answer names its full path [judged]
    And the run is graded "partial" if the sink holds that file and the final answer gives only the file name [judged]
    And the run is graded "misreported" if the final answer names a path where no such file exists [judged]
    And the run is graded "missed" otherwise
    And the measurement "sink_files" records the files in the sink at the end
