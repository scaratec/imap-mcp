Feature: Localized Gmail folder names resolve via RFC 6154 flags

  Gmail shows localized folder names (e.g. [Gmail]/Entwürfe in
  German) but policies use canonical English paths. The server
  resolves localized names to canonical paths via RFC 6154
  special-use flags (\Drafts, \Sent, \Trash).

  Background:
    Given the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path          |
      | INBOX                |
      | [Gmail]/All Mail     |
      | [Gmail]/Drafts       |
      | [Gmail]/Sent Mail    |
      | [Gmail]/Trash        |
    And the server is configured with caller:
      | caller_id      | policy              |
      | test-agent     | localized-policy    |
    And policy "localized-policy" grants account access:
      | account         |
      | scaratec-gmail  |

  Scenario: create_draft succeeds on localized Drafts folder
    Given the mock-gmail server uses localized folder names:
      | canonical           | localized              | flags                    |
      | [Gmail]/Drafts      | [Gmail]/Entw&APw-rfe   | \Drafts \HasNoChildren   |
      | [Gmail]/Sent Mail   | [Gmail]/Gesendet       | \Sent \HasNoChildren     |
      | [Gmail]/Trash       | [Gmail]/Papierkorb     | \Trash \HasNoChildren    |
    And policy "localized-policy" grants the following folder policies:
      | account        | folder      | mode      | default  | draft_append |
      | scaratec-gmail | INBOX            | blacklist | ENVELOPE | false        |
      | scaratec-gmail | [Gmail]/Drafts   | blacklist | FULL     | true         |
      | scaratec-gmail | [Gmail]/Sent Mail| blacklist | ENVELOPE | false        |
      | scaratec-gmail | [Gmail]/All Mail | blacklist | ENVELOPE | false        |
    When test-agent calls list_folders with account "scaratec-gmail"
    And test-agent calls create_draft with account "scaratec-gmail", folder "[Gmail]/Drafts", rfc822 payload:
      """
      From: user@example.com
      To: draft@example.com
      Subject: Localized draft test

      This is a draft on a localized Gmail.
      """
    Then the response decision is ALLOW
    And the draft is stored in "[Gmail]/Entw&APw-rfe" on IMAP account "scaratec-gmail"

  Scenario: list_folders shows canonical names, not localized
    Given the mock-gmail server uses localized folder names:
      | canonical           | localized              | flags                    |
      | [Gmail]/Drafts      | [Gmail]/Entw&APw-rfe   | \Drafts \HasNoChildren   |
      | [Gmail]/Sent Mail   | [Gmail]/Gesendet       | \Sent \HasNoChildren     |
      | [Gmail]/Trash       | [Gmail]/Papierkorb     | \Trash \HasNoChildren    |
    And policy "localized-policy" grants the following folder policies:
      | account        | folder      | mode      | default  |
      | scaratec-gmail | INBOX            | blacklist | ENVELOPE |
      | scaratec-gmail | [Gmail]/Drafts   | blacklist | FULL     |
      | scaratec-gmail | [Gmail]/Sent Mail| blacklist | ENVELOPE |
      | scaratec-gmail | [Gmail]/All Mail | blacklist | ENVELOPE |
    When test-agent calls list_folders with account "scaratec-gmail"
    Then the response contains folder "[Gmail]/Drafts"
    And the response does not contain folder "[Gmail]/Entw&APw-rfe"

  Scenario: search on canonical Sent folder works
    Given the mock-gmail server uses localized folder names:
      | canonical           | localized              | flags                    |
      | [Gmail]/Drafts      | [Gmail]/Entw&APw-rfe   | \Drafts \HasNoChildren   |
      | [Gmail]/Sent Mail   | [Gmail]/Gesendet       | \Sent \HasNoChildren     |
      | [Gmail]/Trash       | [Gmail]/Papierkorb     | \Trash \HasNoChildren    |
    And policy "localized-policy" grants the following folder policies:
      | account        | folder       | mode      | default  |
      | scaratec-gmail | INBOX             | blacklist | ENVELOPE |
      | scaratec-gmail | [Gmail]/Sent Mail | blacklist | ENVELOPE |
      | scaratec-gmail | [Gmail]/All Mail  | blacklist | ENVELOPE |
    When test-agent calls list_folders with account "scaratec-gmail"
    And test-agent calls search with account "scaratec-gmail", folder "[Gmail]/Sent Mail", criteria {}
    Then the response decision is ALLOW
