Feature: On a Gmail account, date windows, attachments and folder counts are reported as Gmail reports them

  ADR 0017, 0019, 0026 §5, 0036.

  The agent usability smoke test ran the invoice tasks against
  mock-gmail and got answers no real Gmail account gives: a search with
  `before` returned messages after the date, the default window
  reported every match as lying before it, no invoice had an attachment
  and every folder held 0 messages. The server was not at fault. The
  mock ignored the IMAP SEARCH keys BEFORE and ON, rendered every
  message as a single text/plain part in BODYSTRUCTURE, whatever MIME it
  stored, and did not implement STATUS, so list_folders read no count.
  The usability grades of the invoice and filing tasks and every BDD
  scenario on a Gmail account rested on that (Guideline §7.2).

  Rules used for the numbers below:
    - The server date is 2026-11-02; the recent window therefore starts
      2026-10-26 (ADR 0026 §5).
    - since is inclusive, before is exclusive; both are calendar dates
      in IMAP SEARCH SINCE / BEFORE semantics (RFC 3501 §6.4.4): they
      compare the internal date, the SENT* keys the Date header,
      disregarding time and timezone.
    - A message is seeded by IMAP APPEND with its Date header as the
      internal date, as on the Dovecot fixture.
    - Gmail renders a multipart message as such in BODYSTRUCTURE, with
      the Content-Disposition of each part (RFC 3501 §7.4.2);
      has_attachment is true for a message with a part disposed as
      attachment.
    - A folder's message count is what Gmail reports for the mailbox
      (STATUS MESSAGES, EXAMINE EXISTS).

  What is at stake. An agent asked for the invoices of September is
  handed October's too; one asked which invoices carry a PDF is told
  none do; one deciding where to look is told every folder is empty.
  The usability grades of those tasks measure the mock, not the agent.

  Independent-channel rule. The first scenarios pin the mock with
  direct IMAP commands from the harness's own connection, compared
  against the seeded table; the tool scenarios read the server's
  response, and every expected uid set and count is derivable from the
  table and the rules above.

  Covered error layers (per BDD Guidelines §4.5):
    - Mock fidelity: UID SEARCH date keys (BEFORE, ON, SINCE, SENTBEFORE, SENTON) : 5
    - Mock fidelity: BODYSTRUCTURE of a message with a PDF attachment              : 1
    - Mock fidelity: STATUS reports the number of messages                         : 1
    - search with a calendar window (since + before, before only)                 : 2
    - Default window reports the matches before it                                : 1
    - has_attachment in search criteria and in list_messages                      : 2
    - list_folders and folder_stats report the folder's message count             : 2
    Total enumerated                                                              : 14  covered by this feature: 14

  Background:
    Given the server date is pinned to "2026-11-02"
    And the IMAP account "scaratec-gmail" exists with provider "google" and folders:
      | folder path      |
      | INBOX            |
      | [Gmail]/All Mail |
    And the server is configured with caller "invoice-agent" using policy "invoice-policy"
    And policy "invoice-policy" grants account "scaratec-gmail"
    And policy "invoice-policy" folder defaults for "INBOX" are:
      | mode      | default  |
      | blacklist | ENVELOPE |
    And the folder "scaratec-gmail:INBOX" holds messages:
      | uid | from                           | subject               | date                 |
      | 901 | rechnung@nordlicht.example.org | Ihre Rechnung 2026-10 | 2026-10-30T07:00:00Z |
    And the message has attachment "rechnung-2026-10.pdf" of type "application/pdf" with size 4096 bytes
    And the folder "scaratec-gmail:INBOX" holds messages:
      | uid | from                           | subject               | date                 |
      | 902 | rechnung@nordlicht.example.org | Ihre Rechnung 2026-09 | 2026-09-29T07:00:00Z |
    And the message has attachment "rechnung-2026-09.pdf" of type "application/pdf" with size 2048 bytes
    And the folder "scaratec-gmail:INBOX" holds messages:
      | uid | from                              | subject                    | date                 |
      | 903 | buchhaltung@nordlicht.example.org | Zahlungserinnerung 2026-08 | 2026-08-28T07:00:00Z |
      | 904 | mara.lindqvist@example.org        | Angebot                    | 2026-10-31T07:00:00Z |

  # --- Mock fidelity ---------------------------------------------------

  Scenario Outline: The mock answers UID SEARCH <key> on the internal date or the Date header as RFC 3501 says
    When a direct IMAP command is issued in "INBOX" on "scaratec-gmail":
      """
      UID SEARCH <key> <day>
      """
    Then the direct IMAP command is answered with status "OK"
    And the direct IMAP SEARCH answer lists exactly the messages <messages>

    Examples:
      | key        | day         | messages        |
      | BEFORE     | 01-Sep-2026 | [903]           |
      | ON         | 29-Sep-2026 | [902]           |
      | SINCE      | 30-Oct-2026 | [901, 904]      |
      | SENTBEFORE | 30-Oct-2026 | [902, 903]      |
      | SENTON     | 31-Oct-2026 | [904]           |

  Scenario: The mock renders a message with a PDF attachment as multipart/mixed in BODYSTRUCTURE
    Then a direct IMAP FETCH of the BODYSTRUCTURE of message 901 in "scaratec-gmail:INBOX" shows a multipart/mixed body with the parts:
      | part | type            | disposition | filename             |
      | 1    | text/plain      |             |                      |
      | 2    | application/pdf | attachment  | rechnung-2026-10.pdf |

  Scenario: The mock reports the number of messages in a mailbox through STATUS
    Then a direct IMAP STATUS of "INBOX" on "scaratec-gmail" reports MESSAGES 4

  # --- search with a calendar window and the default window -----------

  Scenario Outline: A calendar window on a Gmail account selects exactly the messages inside it
    When invoice-agent calls search with account "scaratec-gmail", folder "INBOX", criteria <criteria>
    Then the response decision is ALLOW
    And the response uids equals <uids>
    And the response field applied_scope equals "explicit_window"

    Examples:
      | criteria                                                                                | uids  |
      | {"from_domain": "nordlicht.example.org", "since": "2026-09-01", "before": "2026-10-01"} | [902] |
      | {"from_domain": "nordlicht.example.org", "before": "2026-09-01"}                        | [903] |

  Scenario: The default window on a Gmail account reports how many matches lie before it
    When invoice-agent calls search with account "scaratec-gmail", folder "INBOX", criteria {"from_domain": "nordlicht.example.org"}
    Then the response decision is ALLOW
    And the response uids equals [901]
    And the response field applied_scope equals "recent_7d"
    And the response field outside_scope_total equals 2

  # --- attachments -----------------------------------------------------

  Scenario: has_attachment on a Gmail account selects the invoices that carry a PDF
    When invoice-agent calls search with account "scaratec-gmail", folder "INBOX", criteria {"has_attachment": true}, scope "all"
    Then the response decision is ALLOW
    And the response uids equals [901, 902]

  Scenario: list_messages on a Gmail account tells which messages carry an attachment
    When invoice-agent calls list_messages with account "scaratec-gmail", folder "INBOX", criteria {}, scope "all"
    Then the response contains 4 messages
    And the response field messages has exactly one entry matching:
      | field          | value                 |
      | subject        | Ihre Rechnung 2026-10 |
      | has_attachment | true                  |
    And the response field messages has exactly one entry matching:
      | field          | value                 |
      | subject        | Ihre Rechnung 2026-09 |
      | has_attachment | true                  |
    And the response field messages has exactly one entry matching:
      | field          | value                      |
      | subject        | Zahlungserinnerung 2026-08 |
      | has_attachment | false                      |
    And the response field messages has exactly one entry matching:
      | field          | value   |
      | subject        | Angebot |
      | has_attachment | false   |

  # --- folder counts ---------------------------------------------------

  Scenario: list_folders on a Gmail account reports the message count of a folder
    When invoice-agent calls list_folders with account "scaratec-gmail"
    Then the folder entry "INBOX" has message_count 4

  Scenario: folder_stats on a Gmail account reports the message count of a folder
    When invoice-agent calls folder_stats with account "scaratec-gmail", folder "INBOX"
    Then the response decision is ALLOW
    And the response field visible_count equals 4
