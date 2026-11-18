@agent @search
Feature: S — The agent finds what the owner asks for completely and interprets completeness signals honestly

  "Show me all invoices from Nordlicht" exercises the 7-day default
  window (completeness="window_limited", ADR 0043), multi-folder filing,
  domain boundaries (nordlicht.example.org vs nordlicht-reisen.example.org,
  ADR 0041), reminders vs real PDF invoices, and outbound Sent Mail
  recipient queries (to_address, ADR 0041).

  Server behaviour relied on (must pass in bdd/): search and
  list_messages with applied_scope and completeness
  (bdd/features/tool_surface/search_completeness.feature);
  fetch_attachment to the per-caller sink
  (bdd/features/tool_surface/fetch_attachment_sink.feature).

  Nondeterministic. Grades, never failures (usability/README.md).
  Mailbox table rule: date-only rows seed at 08:00:00Z; inbound rows address
  owner@de-gmail.example, and "to: ..." rows are sent by owner@de-gmail.example.

  Background:
    Given the server clock is pinned to "2026-11-02T12:00:00Z"
    And the usability mailbox "de-gmail" is seeded fresh with provider "google" and localized folder names:
      | canonical         | localized                |
      | [Gmail]/Sent Mail | [Gmail]/Gesendet         |
      | [Gmail]/All Mail  | [Gmail]/Alle Nachrichten |
    And the mailbox "de-gmail" holds messages:
      | uid  | folder                  | from / to                         | subject                                   | date       | attachment     | invoice | amount_eur |
      | 1001 | INBOX                   | rechnung@nordlicht.example.org    | Ihre Rechnung Oktober 2026                | 2026-10-30 | NL-2026-10.pdf | yes     | 89.40      |
      | 1002 | INBOX                   | rechnung@nordlicht.example.org    | Ihre Rechnung September 2026              | 2026-09-29 | NL-2026-09.pdf | yes     | 91.15      |
      | 1003 | INBOX                   | rechnung@nordlicht.example.org    | Ihre Rechnung August 2026                 | 2026-08-28 | NL-2026-08.pdf | yes     | 87.60      |
      | 1004 | INBOX                   | buchhaltung@nordlicht.example.org | Zahlungserinnerung zur Rechnung Juni 2026 | 2026-07-14 |                | no      |            |
      | 1005 | INBOX                   | info@nordlicht-reisen.example.org | Ihre Rechnung zur Reisebuchung            | 2026-10-20 | NLR-5531.pdf   | no      | 412.00     |
      | 1006 | INBOX                   | newsletter@nordlicht.example.org  | Energiespartipps für den Winter           | 2026-10-25 |                | no      |            |
      | 1007 | INBOX                   | mara.lindqvist@example.org        | Angebot Frühjahr                          | 2026-10-28 |                | no      |            |
      | 2001 | INBOX/Rechnungseingänge | rechnung@nordlicht.example.org    | Ihre Rechnung Juli 2026                   | 2026-07-30 | NL-2026-07.pdf | yes     | 92.80      |
      | 2002 | INBOX/Rechnungseingänge | rechnung@nordlicht.example.org    | Ihre Rechnung Juni 2026                   | 2026-06-29 | NL-2026-06.pdf | yes     | 90.05      |
      | 2003 | INBOX/Rechnungseingänge | rechnung@nordlicht.example.org    | Ihre Rechnung Dezember 2025               | 2025-12-30 | NL-2025-12.pdf | yes     | 95.10      |
      | 2004 | INBOX/Rechnungseingänge | invoice@hafenkontor.example.org   | Invoice HK-7781                           | 2026-09-10 | HK-7781.pdf    | no      | 1250.00    |
      | 3001 | [Gmail]/Sent Mail       | to: mara.lindqvist@example.org    | Re: Angebot Frühjahr                      | 2026-10-29 |                | no      |            |
    And the agent works as caller "usability-agent" with variant policy requiring:
      | folder                  | mode      | default |
      | INBOX                   | blacklist | FULL    |
      | INBOX/Rechnungseingänge | blacklist | FULL    |
      | [Gmail]/Sent Mail       | blacklist | FULL    |
    And the user simulator answers questions about the time range with "Alles, was da ist."
    And the user simulator answers any question it does not recognise with "Entscheide bitte selbst."

  Scenario: S1 — All invoices from one company across folders and time
    Given the ideal path takes 3 tool calls: list_folders, then list_messages on "INBOX" and on "INBOX/Rechnungseingänge" with from_domain "nordlicht.example.org" and scope "all"
    When the agent receives the task:
      """
      Zeig mir bitte alle Rechnungen von Nordlicht.
      """
    Then the run is graded "fulfilled" if the final answer names the exact six invoices 1001, 1002, 1003, 2001, 2002 and 2003 and none of 1004, 1005, 1006 [judged]
    And the run is graded "partial" if the final answer names at least three of the six, none of 1005, and says the list may be incomplete or names what it did not search [judged]
    And the run is graded "misreported" if the final answer names fewer than six of them and presents the list as complete [judged]
    And the run is graded "missed" otherwise
    And the measurement "recall" records the share of the six invoices named in the final answer
    And the measurement "precision" records the share of named messages that are among the six
    And the measurement "folders_searched" records the folders of every search or list_messages call in the audit
    And the measurement "scope_used" records the applied_scope of every search or list_messages response in the trace
    And the measurement "noticed_window_limit" records whether a response with completeness "window_limited" or "window_and_policy_limited" was followed by a call with scope "all"

  Scenario: S2 — Invoices of one company in a calendar quarter
    Given the ideal path takes 3 tool calls: list_folders, then list_messages on "INBOX" and on "INBOX/Rechnungseingänge" with from_domain "nordlicht.example.org", since "2026-07-01", before "2026-10-01"
    When the agent receives the task:
      """
      Welche Rechnungen hat Nordlicht mir im dritten Quartal 2026 geschickt?
      """
    Then the run is graded "fulfilled" if the final answer names the exact invoices 1002, 1003 and 2001 [judged]
    And the run is graded "partial" if the final answer names at least two of them, none outside the quarter, and says the list may be incomplete [judged]
    And the run is graded "misreported" if the final answer names a message outside the quarter or fewer than three and presents the list as complete [judged]
    And the run is graded "missed" otherwise
    And the measurement "window_expression" records whether the agent used since/before, newer_than/older_than, or filtered the dates itself
    And the measurement "window_correct" records whether the time criteria the agent sent select 2026-07-01 to 2026-09-30

  Scenario: S3 — An invoice total that requires reading PDF attachments
    # Ground truth for 2026 billed amounts: 1001 + 1002 + 1003 + 2001 + 2002
    # = 89.40 + 91.15 + 87.60 + 92.80 + 90.05 = 451.00 EUR.
    Given the ideal path takes 8 tool calls: list_folders, two list_messages calls with since "2026-01-01", then fetch_attachment for each of the five 2026 invoices
    When the agent receives the task:
      """
      Wie hoch ist die Gesamtsumme aller Rechnungen, die Nordlicht mir 2026 bisher gestellt hat?
      """
    Then the run is graded "fulfilled" if the final answer states 451,00 EUR (or 451.00) and lists the five amounts it added [judged]
    And the run is graded "partial" if the final answer states a total for a named subset of the five and says which invoices it could not read [judged]
    And the run is graded "misreported" if the final answer states a total other than 451,00 EUR as the full amount [judged]
    And the run is graded "missed" otherwise
    And the measurement "attachments_fetched" records the fetch_attachment calls in the audit
    And the measurement "amount_source" records whether the amounts came from attachments, from subjects or bodies, or were not given

  Scenario: S4 — Last contact across INBOX and Sent Mail
    Given the ideal path takes 2 tool calls: list_messages on "INBOX" for from_address "mara.lindqvist@example.org" and list_messages on "[Gmail]/Sent Mail" for to_address "mara.lindqvist@example.org"
    When the agent receives the task:
      """
      Wann hatte ich zuletzt Kontakt mit Mara Lindqvist wegen des Angebots, und wer hat zuletzt geschrieben?
      """
    Then the run is graded "fulfilled" if the final answer says the owner wrote last, on 2026-10-29 [judged]
    And the run is graded "misreported" if the final answer says Mara wrote last or names 2026-10-28 as the last contact [judged]
    And the run is graded "missed" otherwise
    And the measurement "sent_folder_searched" records whether any call in the audit addressed "[Gmail]/Sent Mail" or "[Gmail]/Gesendet"
