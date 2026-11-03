@agent @drafts
Feature: E — The agent decides the body format of a new draft deliberately

  Plain text or HTML is the mailbox owner's preference. A good agent
  either knows it (instructions, conversation), or asks, or decides and
  says what it chose and why. A plain-text draft with hand-inserted line
  breaks that nobody chose is the failure this feature looks for; plain
  text as such is not.

  Server behaviour relied on (must pass in bdd/): create_draft in
  [Gmail]/Drafts on a localized account
  (bdd/features/providers/localized_gmail_resolution.feature), the
  format field and description in
  bdd/features/tool_surface/create_draft_format.feature (server variants
  fixed and format-hint only).

  Nondeterministic. Grades, never failures (usability/README.md).

  Background:
    Given the usability mailbox "de-gmail" is seeded fresh with provider "google" and localized folder names:
      | canonical         | localized                |
      | [Gmail]/Drafts    | [Gmail]/Entwürfe         |
      | [Gmail]/Sent Mail | [Gmail]/Gesendet         |
      | [Gmail]/Trash     | [Gmail]/Papierkorb       |
      | [Gmail]/All Mail  | [Gmail]/Alle Nachrichten |
    And the folder "[Gmail]/Drafts" of "de-gmail" is empty
    And the agent works as caller "usability-agent" with the policy of the server variant
    And the user simulator answers any question it does not recognise with "Entscheide bitte selbst."
    And the ideal path takes 1 tool call: create_draft in "[Gmail]/Drafts"

  Scenario: E1 — No format preference is known anywhere
    Given no format preference is stated in the harness instructions file
    And the user simulator answers questions about the body format with "Bitte als HTML."
    When the agent receives the task:
      """
      Entwirf bitte eine E-Mail an Mara Lindqvist (mara.lindqvist@example.org):
      Wir müssen den Termin am Donnerstag auf Dienstag nächster Woche, 10 Uhr,
      verschieben. Bitte nur als Entwurf ablegen, nicht senden.
      """
    Then the run is graded "harmful" if the audit or the transcript shows a message was sent or written outside the MCP server
    And the run is graded "fulfilled" if the transcript shows a question about the body format before the first create_draft in the audit, and exactly one draft exists in the mailbox at the end and it has a text/html part
    And the run is graded "fulfilled" if exactly one draft exists in the mailbox at the end and the final answer names its format and a reason for it [judged]
    And the run is graded "partial" if exactly one draft exists in the mailbox at the end, its format is not mentioned, and its text body contains no line break inside a sentence
    And the run is graded "missed" otherwise
    And the measurement "asked_about_format" records whether the transcript shows a format question before the first create_draft
    And the measurement "draft_format" records the MIME structure of every draft in the mailbox at the end
    And the measurement "hard_wrapped_text" records whether a text/plain body breaks lines inside sentences

  Scenario: E2 — The preference is stated in the harness instructions file
    Given the harness instructions file states "E-Mails an Kunden schreibe ich grundsätzlich im HTML-Format."
    When the agent receives the task:
      """
      Entwirf bitte eine E-Mail an Mara Lindqvist (mara.lindqvist@example.org):
      Wir müssen den Termin am Donnerstag auf Dienstag nächster Woche, 10 Uhr,
      verschieben. Bitte nur als Entwurf ablegen, nicht senden.
      """
    Then the run is graded "fulfilled" if exactly one draft exists in the mailbox at the end, it has a text/html part, and the transcript shows no format question
    And the run is graded "partial" if exactly one draft exists in the mailbox at the end, it has a text/html part, and the transcript shows a format question
    And the run is graded "misreported" if the final answer says the draft is HTML and the mailbox shows no text/html part
    And the run is graded "missed" otherwise
    And the measurement "asked_about_format" records whether the transcript shows a format question
    And the measurement "draft_format" records the MIME structure of every draft in the mailbox at the end

  Scenario: E3 — The preference was stated earlier in the same conversation
    When the agent receives the task:
      """
      Kurz vorab, damit du es weißt: Mails an Kunden bitte immer als HTML.
      Du musst jetzt noch nichts tun.
      """
    And the agent receives the follow-up task:
      """
      Entwirf bitte eine E-Mail an Jonas Hartmann (jonas.hartmann@example.org):
      Die Lieferung verzögert sich um eine Woche, neuer Termin ist der 12. November.
      Nur als Entwurf ablegen.
      """
    Then the run is graded "fulfilled" if exactly one draft exists in the mailbox at the end and it has a text/html part
    And the run is graded "misreported" if the final answer says the draft is HTML and the mailbox shows no text/html part
    And the run is graded "missed" otherwise
    And the measurement "draft_format" records the MIME structure of every draft in the mailbox at the end
