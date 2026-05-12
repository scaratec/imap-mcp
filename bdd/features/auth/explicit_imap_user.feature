Feature: Accounts with explicit IMAP username

  Some mail providers use an IMAP login name that differs from the
  account's email address (e.g. "mail2223" instead of
  "accounting@scaratec.bg").  The server must accept an optional
  "user" field on an account and use it for IMAP authentication
  instead of deriving the username from the account id.

  Scenario: list_folders succeeds with explicit IMAP user
    Given the IMAP account "ext-user" exists with explicit user "gupta" and folder "INBOX"
    And the server is configured with caller:
      | caller_id  | policy          |
      | test-agent | explicit-policy |
    And policy "explicit-policy" grants account access:
      | account  |
      | ext-user |
    And policy "explicit-policy" grants the following folder policies:
      | account  | folder | mode      | default  |
      | ext-user | INBOX  | blacklist | ENVELOPE |
    When test-agent calls list_folders with account "ext-user"
    Then the response contains folder "INBOX"
