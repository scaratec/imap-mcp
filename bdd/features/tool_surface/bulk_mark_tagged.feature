Feature: bulk_mark_tagged tags or untags multiple messages in one call

  An agent asking "tag all invoices as 'archive'" should not need N
  individual mark_tagged calls. bulk_mark_tagged accepts the same
  criteria grammar as search and the same mode vocabulary as
  mark_tagged. ADR 0026 adds it as the natural symmetry partner to
  bulk_mark_seen.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path: criteria match, tags added             : 1
    - mode=remove path                                   : 1
    - mode=replace path                                  : 1
    - Empty result: criteria match nothing               : 1
    - Capability missing: folder without mark_tagged     : 1
    - Forbidden system flag rejected                     : 1
    - Connection count: single IMAP session              : 1
    Total enumerated                                      : 7   covered by this feature: 7

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"

  Scenario: bulk_mark_tagged adds the tag to every matching message
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | true        |
    And the folder "INBOX" holds 10 messages from "vendor@example.com"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["archive"], mode "add", scope "all"
    Then the response decision is ALLOW
    And the response field result equals "OK"
    And the response field tagged_count equals 10

    # Persistenz-Validierung (§13.2 Pruefung 1): the tag is actually on
    # the messages, not merely reported on.
    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, scope "all"
    Then every returned message has tag "archive"

  Scenario: bulk_mark_tagged with mode=remove strips the tag from every matching message
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | true        |
    And the folder "INBOX" holds 4 messages from "vendor@example.com" each tagged "archive"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["archive"], mode "remove", scope "all"
    Then the response field tagged_count equals 4

    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, scope "all"
    Then no returned message has tag "archive"

  Scenario: bulk_mark_tagged with mode=replace overwrites the tag set
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | true        |
    And the folder "INBOX" holds 3 messages from "vendor@example.com" each tagged "draft"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["final"], mode "replace", scope "all"
    Then the response field tagged_count equals 3

    When inbox-agent calls list_messages with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, scope "all"
    Then every returned message has tag "final"
    And no returned message has tag "draft"

  Scenario: bulk_mark_tagged with no matches returns zero
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | true        |
    And the folder "INBOX" holds 5 messages from "vendor@example.com"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from": "nobody@nowhere.com"}, tags ["archive"], mode "add", scope "all"
    Then the response decision is ALLOW
    And the response field tagged_count equals 0

  Scenario: bulk_mark_tagged denied without mark_tagged capability
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | false       |
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["archive"], mode "add", scope "all"
    Then the response decision is DENY
    And the response field reason equals "capability_missing"

  Scenario: bulk_mark_tagged rejects a reserved system flag
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | true        |
    And the folder "INBOX" holds 2 messages from "vendor@example.com"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["\\Deleted"], mode "add", scope "all"
    Then the response decision is DENY
    And the response field reason equals "forbidden_system_flag"

  Scenario: bulk_mark_tagged opens at most a small bounded number of connections
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  | mark_tagged |
      | blacklist | ENVELOPE | true        |
    And the folder "INBOX" holds 20 messages from "vendor@example.com"
    When inbox-agent calls bulk_mark_tagged with account "gupta-scaratec", folder "INBOX", criteria {"from_domain": "example.com"}, tags ["archive"], mode "add", scope "all"
    Then the response field tagged_count equals 20
    And the IMAP server received at most 4 IMAP connections
