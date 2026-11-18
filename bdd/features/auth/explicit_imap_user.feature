Feature: Accounts with explicit IMAP login username

  Some mail providers separate the mailbox email address used as policy
  account ID (e.g. "accounting@scaratec.example") from the IMAP login
  username (e.g. "gupta"). ADR 0050 §1 specifies that IMAP LOGIN / XOAUTH2
  authenticates with account.user when configured, while policies, tool
  calls, structured From headers (ADR 0045 §3), and audit records use
  account.id.

  Covered error layers (per BDD Guidelines §4.5):
    - Wire LOGIN uses explicit user while policy/response use account id : 1
    - Structured create_draft derives From from email-shaped account id  : 1
    - Wrong password with explicit user surfaces transient_imap_failure  : 1
    Total enumerated                                                     : 3   covered by this feature: 3

  Background:
    Given the audit log directory is a fresh $TMPDIR/audit
    And the IMAP account "accounting@scaratec.example" exists with explicit user "gupta" and folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the IMAP traffic for "accounting@scaratec.example" is captured through a proxy
    And the server is configured with caller "test-agent" using policy "explicit-policy"
    And policy "explicit-policy" grants account "accounting@scaratec.example"
    And policy "explicit-policy" grants the following folder policies:
      | account                     | folder | mode      | default  | draft_append |
      | accounting@scaratec.example | INBOX  | blacklist | ENVELOPE | false        |
      | accounting@scaratec.example | Drafts | blacklist | FULL     | true         |

  Scenario: list_folders authenticates on the wire with explicit user while exposing account id
    When test-agent calls list_folders with account "accounting@scaratec.example"
    Then the response decision is ALLOW
    And the response field folders contains "INBOX"
    And the IMAP command log for "accounting@scaratec.example" contains a LOGIN command for user "gupta"
    And the IMAP command log for "accounting@scaratec.example" contains no LOGIN command for user "accounting@scaratec.example"

  Scenario: Structured create_draft sets From to the email-shaped account id when explicit user has no domain
    When test-agent calls create_draft with account "accounting@scaratec.example", folder "Drafts", fields:
      """
      {"to": ["client@example.org"],
       "subject": "Rechnungsrueckfrage",
       "body_text": "Guten Tag."}
      """
    Then the response decision is ALLOW
    And the IMAP message in folder "Drafts" on "accounting@scaratec.example" with subject "Rechnungsrueckfrage" has header "From" equal to "accounting@scaratec.example"

  Scenario: Authentication failure with explicit user returns sanitized transient_imap_failure without leaking secret
    Given the secret store holds the wrong password "wrong-pw-secret-99" for account "accounting@scaratec.example"
    When test-agent calls list_folders with account "accounting@scaratec.example"
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "transient_imap_failure"
    And the JSON response does NOT contain the literal strings:
      | forbidden_string   |
      | wrong-pw-secret-99 |
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string   |
      | wrong-pw-secret-99 |
