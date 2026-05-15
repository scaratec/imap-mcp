Feature: Flagged predicate in sender rules

  The "flagged" predicate matches messages that carry the IMAP
  \Flagged flag (starred in Gmail).  This lets policy authors grant
  access to user-curated messages without maintaining per-sender
  whitelists.

  Covered error layers (per BDD Guidelines §4.5):
    - flagged=true match in whitelist mode    : 1
    - flagged=false match in whitelist mode   : 1
    - flagged=true in blacklist mode (cap)    : 1
    - flagged composes with from_domain (AND) : 1
    - Grammar acceptance at load time         : 1
    - flagged grant honoured by fetch_body    : 1
    Total enumerated                          : 6   covered: 6

  Background:
    Given the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"

  Scenario: flagged=true grants access to starred messages in whitelist mode
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | whitelist | NONE    |
    And policy "inbox-policy" sets folder "INBOX" rules to:
      | match          | grant |
      | flagged=true   | FULL  |
    And the folder "INBOX" holds messages:
      | uid | from              | flags      |
      | 501 | alice@example.com | [\Flagged] |
      | 502 | bob@example.com   | []         |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field uids contains exactly [501]

  Scenario: flagged=false matches only unflagged messages
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | whitelist | NONE    |
    And policy "inbox-policy" sets folder "INBOX" rules to:
      | match          | grant    |
      | flagged=false  | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from              | flags      |
      | 511 | alice@example.com | [\Flagged] |
      | 512 | bob@example.com   | []         |
      | 513 | carol@example.com | [\Seen]    |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field uids contains exactly [512, 513]

  Scenario: flagged in blacklist mode caps unflagged messages
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | blacklist | FULL    |
    And policy "inbox-policy" sets folder "INBOX" rules to:
      | match          | cap      |
      | flagged=false  | NONE     |
    And the folder "INBOX" holds messages:
      | uid | from              | flags      |
      | 521 | alice@example.com | [\Flagged] |
      | 522 | bob@example.com   | []         |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field uids contains exactly [521]

  Scenario: flagged composes with from_domain via AND
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | whitelist | NONE    |
    And policy "inbox-policy" sets folder "INBOX" rules to:
      | match                                    | grant |
      | flagged=true AND from_domain=example.com | FULL  |
    And the folder "INBOX" holds messages:
      | uid | from                | flags      |
      | 531 | alice@example.com   | [\Flagged] |
      | 532 | alice@example.com   | []         |
      | 533 | bob@other.com       | [\Flagged] |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}
    Then the response field uids contains exactly [531]

  Scenario: fetch_body honours a flagged-only whitelist grant
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | whitelist | NONE    |
    And policy "inbox-policy" sets folder "INBOX" rules to:
      | match          | grant |
      | flagged=true   | FULL  |
    And the folder "INBOX" holds a message with:
      | uid | from                  | subject                | flags      |
      | 541 | someone@unrelated.com | Starred by the user    | [\Flagged] |
    When inbox-agent calls fetch_body with account "gupta-scaratec", folder "INBOX", uid 541
    Then the response decision is ALLOW
    And the response field visibility_applied equals "FULL"
    And the response field matched_rule_index is 0

  Scenario: flagged is accepted as a valid V1 core grammar predicate
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | whitelist | NONE    |
    And policy "inbox-policy" sets folder "INBOX" rules to:
      | match          | grant |
      | flagged=true   | FULL  |
    When inbox-agent calls list_folders with account "gupta-scaratec"
    Then the response contains folder "INBOX"
