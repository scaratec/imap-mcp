Feature: Search on blacklist folders with permissive defaults responds quickly

  When a blacklist folder has default visibility >= METADATA and no
  sender rules, the PDP outcome is predetermined for every message.
  The server must skip per-message envelope fetching and return UIDs
  directly from the IMAP SEARCH result.
  See LIM-0011.

  Covered error layers (per BDD Guidelines §4.5):
    - Blacklist, no rules, default=ENVELOPE   : 1
    - No per-message fetch (audit proof §4.3) : 1
    - Fast path still applies pagination      : 1
    - Blacklist with rules still filters       : 1
    Total enumerated                           : 4   covered by this feature: 4

  Background:
    Given the audit log directory is a fresh $TMPDIR/audit
    And the IMAP account "gupta-scaratec" exists with folder "INBOX"
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"

  Scenario: Blacklist folder with no rules returns all UIDs without per-message filtering
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                   | subject      |
      | 101 | alice@example.com      | Mail A       |
      | 102 | bob@example.com        | Mail B       |
      | 103 | carol@example.com      | Mail C       |
      | 104 | dave@example.com       | Mail D       |
      | 105 | eve@example.com        | Mail E       |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, limit 3
    Then the response field matched_total equals 5
    And the response field matched_visible equals 5
    And the response field filtered_out equals 0
    And the response field uids has length 3
    And the response field has_more equals true
    And the audit log does not contain any entry with tool "fetch_envelope"

  Scenario: Fast path still respects pagination offset
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from                   | subject      |
      | 201 | alice@example.com      | Mail A       |
      | 202 | bob@example.com        | Mail B       |
      | 203 | carol@example.com      | Mail C       |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, limit 2, offset 1
    Then the response field uids contains exactly [202, 203]
    And the response field has_more equals false
    And the response field matched_visible equals 3

  Scenario: Blacklist folder with sender rules still applies per-message filtering
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And policy "inbox-policy" sets folder "INBOX" rules to:
      | match                     | cap  |
      | from_domain=spammer.net   | NONE |
    And the folder "INBOX" holds messages:
      | uid | from                   | subject      |
      | 301 | alice@example.com      | Legit        |
      | 302 | spam@spammer.net       | Buy now      |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {"newer_than": "365d"}
    Then the response field uids contains exactly [301]
    And the response field matched_visible equals 1
    And the response field filtered_out equals 1
