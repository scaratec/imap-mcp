Feature: Symmetric direction-aware predicate grammar

  Rules in folder policies combine predicates from the closed 3.0.0
  grammar (ADR 0041): parsed sender and recipient addresses/domains
  (from_address, from_domain, to_address, to_domain, cc_address,
  cc_domain, recipient_address, recipient_domain, all_recipients_in_domains,
  normalized to lowercase IDNA ASCII with trailing dots stripped),
  subject_contains (Unicode NFC, case-folded), has_attachment, flagged,
  provenance, size_gt/size_lt, and INTERNALDATE time bounds (newer_than,
  older_than, since, before). Predicates within a rule combine via AND;
  rules across a folder combine via OR. Legacy substring keys (from, to,
  to_contains) and regex/body predicates are rejected at load time.

  Covered error layers (per BDD Guidelines §4.5):
    - Core predicate match/no-match (inbound + outbound + Cc + all_recipients) : 14
    - Combination semantics (AND within rule, OR across rules)                 : 2
    - Address/domain boundary & Unicode NFC normalization                      : 3
    - INTERNALDATE used instead of spoofed Date header                         : 1
    - Policy loader rejects unknown and legacy predicates                      : 7
    Total enumerated                                                           : 27  covered by this feature: 27

  Background:
    Given the server clock is pinned to "2026-05-01T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
    And the server is configured with caller "inbox-agent" using policy "inbox-policy"
    And policy "inbox-policy" grants account "gupta-scaratec"
    And policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | whitelist | NONE    |

  Scenario Outline: Predicate <predicate> matches exact target messages and skips non-matches
    Given policy "inbox-policy" sets folder "INBOX" rules to:
      | match               | grant    |
      | <match_expression>  | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from_address            | to_address                               | cc_header            | subject        | size_bytes | date                 |
      | 401 | alice@example.com       | me@scaratec.com                          | legal@ra-buechner.de | Project update | 1024       | 2026-04-20T09:00:00Z |
      | 402 | bob@hornbach.de         | me@scaratec.com                          |                      | Rechnung 42    | 48000      | 2026-04-15T09:00:00Z |
      | 403 | newsletter@shop.example | info@allianz.de, secret@external.example |                      | Grosse Aktion  | 2048       | 2026-01-01T09:00:00Z |
      | 404 | carol@example.com       | me@scaratec.com                          | leak@evil.example    | Mixed Cc       | 1500       | 2026-04-22T09:00:00Z |
    And the message at uid 402 has attachment "inv.pdf" of type "application/pdf" with content byte "A" repeated 512 times
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals <matching_uids>

    Examples:
      | predicate                 | match_expression                                            | matching_uids   |
      | from_address              | from_address=bob@hornbach.de                                | [402]           |
      | from_domain               | from_domain=hornbach.de                                     | [402]           |
      | to_address                | to_address=me@scaratec.com                                  | [401, 402, 404] |
      | to_domain                 | to_domain=allianz.de                                        | [403]           |
      | cc_address                | cc_address=legal@ra-buechner.de                             | [401]           |
      | cc_domain                 | cc_domain=ra-buechner.de                                    | [401]           |
      | recipient_address         | recipient_address=legal@ra-buechner.de                      | [401]           |
      | recipient_domain          | recipient_domain=ra-buechner.de                             | [401]           |
      | all_recipients_in_domains | all_recipients_in_domains=["scaratec.com","ra-buechner.de"] | [401, 402]      |
      | subject_contains          | subject_contains=rechnung                                   | [402]           |
      | has_attachment            | has_attachment=true                                         | [402]           |
      | newer_than                | newer_than=30d                                              | [401, 402, 404] |
      | older_than                | older_than=30d                                              | [403]           |
      | size_gt                   | size_gt=10000                                               | [402]           |

  Scenario: Predicates within one rule are AND-combined
    Given policy "inbox-policy" sets folder "INBOX" rules to:
      | match                                           | grant    |
      | from_domain=hornbach.de AND has_attachment=true | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from_address         | to_address      | subject | date                 |
      | 410 | rechnung@hornbach.de | me@scaratec.com | A       | 2026-04-28T09:00:00Z |
      | 411 | info@hornbach.de     | me@scaratec.com | B       | 2026-04-28T09:00:00Z |
      | 412 | other@example.com    | me@scaratec.com | C       | 2026-04-28T09:00:00Z |
    And the message at uid 410 has attachment "a.pdf" of type "application/pdf" with content byte "X" repeated 64 times
    And the message at uid 412 has attachment "c.pdf" of type "application/pdf" with content byte "Y" repeated 64 times
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals [410]

  Scenario: Multiple rules in one folder are OR-combined
    Given policy "inbox-policy" sets folder "INBOX" rules to:
      | match                   | grant    |
      | from_domain=hornbach.de | ENVELOPE |
      | from_domain=obi.de      | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from_address         | to_address      | subject | date                 |
      | 420 | rechnung@hornbach.de | me@scaratec.com | A       | 2026-04-28T09:00:00Z |
      | 421 | service@obi.de       | me@scaratec.com | B       | 2026-04-28T09:00:00Z |
      | 422 | spam@example.net     | me@scaratec.com | C       | 2026-04-28T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals [420, 421]

  Scenario: Domain predicates enforce exact DNS boundaries and reject display-name or suffix spoofing
    Given policy "inbox-policy" sets folder "INBOX" rules to:
      | match               | grant    |
      | to_domain=allianz.de | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from_address   | to_address                                  | subject | date                 |
      | 430 | me@scaratec.de | LEBENSVERSICHERUNG@ALLIANZ.DE.              | Echt    | 2026-04-28T09:00:00Z |
      | 431 | me@scaratec.de | info@evil-allianz.de                        | Falsch1 | 2026-04-28T09:00:00Z |
      | 432 | me@scaratec.de | info@allianz.de.attacker.example            | Falsch2 | 2026-04-28T09:00:00Z |
      | 433 | me@scaratec.de | "allianz.de" <attacker@unrelated.example>   | Falsch3 | 2026-04-28T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals [430]

  Scenario: from_domain normalizes IDNA punycode and Unicode domains canonically
    Given policy "inbox-policy" sets folder "INBOX" rules to:
      | match                  | grant    |
      | from_domain=müller.de  | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from_address              | to_address      | subject | date                 |
      | 435 | info@xn--mller-kva.de     | me@scaratec.com | Puny    | 2026-04-28T09:00:00Z |
      | 436 | info@mueller.de           | me@scaratec.com | ASCII   | 2026-04-28T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals [435]

  Scenario: subject_contains matches both precomposed NFC and decomposed NFD subjects
    Given policy "inbox-policy" sets folder "INBOX" rules to:
      | match                      | grant    |
      | subject_contains=Rückfrage | ENVELOPE |
    And the folder "INBOX" holds messages:
      | uid | from_address      | to_address      | subject_nfd_escaped    | date                 |
      | 440 | alice@example.com | me@scaratec.com | R\u00fcckfrage NFC     | 2026-04-28T09:00:00Z |
      | 441 | bob@example.com   | me@scaratec.com | Ru\u0308ckfrage NFD    | 2026-04-28T09:00:00Z |
      | 442 | carol@example.com | me@scaratec.com | Angebot                | 2026-04-28T09:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals [440, 441]

  Scenario: Time predicates evaluate server INTERNALDATE and ignore spoofed Date headers in both directions
    Given policy "inbox-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And policy "inbox-policy" sets folder "INBOX" rules to:
      | match          | cap  |
      | older_than=30d | NONE |
    And the folder "INBOX" holds a message with:
      | uid | from_address         | to_address      | subject         | header_date                     | internal_date        |
      | 450 | attacker@example.com | me@scaratec.com | Spoofed Fresh   | Thu, 30 Apr 2026 10:00:00 +0000 | 2025-12-01T10:00:00Z |
      | 451 | honest@example.com   | me@scaratec.com | Spoofed Ancient | Wed, 01 Jan 2020 10:00:00 +0000 | 2026-04-28T10:00:00Z |
    When inbox-agent calls search with account "gupta-scaratec", folder "INBOX", criteria {}, scope "all"
    Then the response field uids equals [451]

  Scenario Outline: Policy loader rejects unknown and retired 2.x predicate keys
    Given the server loads a policy file containing:
      """
      name: inbox-policy
      accounts:
        gupta-scaratec:
          - path: INBOX
            mode: whitelist
            default: NONE
            rules:
              - match: { <invalid_key>: "<value>" }
                grant: ENVELOPE
      """
    Then the server refuses to start
    And the startup error indicates the rule predicate "<invalid_key>" as "not in 3.0 core grammar"

    Examples:
      | invalid_key     | value                |
      | from            | bob@hornbach.de      |
      | to              | me@scaratec.com      |
      | to_contains     | allianz.de           |
      | subject_regex   | ^Rechnung.*$         |
      | header_matches  | X-Spam: *            |
      | body_contains   | dringend             |
      | label           | Rechnungen           |
