Feature: Missing, mistyped, and undeclared arguments are rejected with JSON-RPC -32602 before any IMAP I/O

  ADR 0052 §2 enforces strict inputSchema validation with
  additionalProperties: false across every tool and nested object
  (criteria, source, target, changes, attachments). Missing required
  fields, wrong JSON types (booleans never coerce to integers), and
  unknown or retired 2.x keys ("from", "to", "to_contains") fail with
  JSON-RPC -32602 Invalid params before opening any IMAP connection,
  naming the tool and argument without echoing caller-supplied values.

  Covered error layers (per BDD Guidelines §4.5):
    - Missing required argument (single & multiple)         : 4
    - Missing required member of nested object (source.uid) : 1
    - Wrong JSON type (string/bool for int, int for string, str for bool) : 4
    - Undeclared / retired 2.x extra properties rejected    : 4
    Total enumerated                                        : 13  covered by this feature: 13

  Background:
    Given the server clock is pinned to "2026-10-31T12:00:00Z"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path | special use |
      | INBOX       |             |
      | Drafts      | \Drafts     |
    And the IMAP traffic for "gupta-scaratec" is captured through a proxy
    And the server is configured with caller "mail-agent" using policy "mail-policy"
    And policy "mail-policy" grants account "gupta-scaratec"
    And policy "mail-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | rules |
      | INBOX  | blacklist | FULL    | []    |
    And the folder "INBOX" holds messages:
      | uid | from_address               | to_address            | subject          | date                 |
      | 501 | rechnung@nordlicht.example | me@gupta-scaratec.com | Ihre Rechnung 10 | 2026-10-30T07:00:00Z |

  Scenario Outline: <tool> with <defect> is refused with JSON-RPC -32602 and zero IMAP connections
    When mail-agent calls <tool> with the arguments <arguments>
    Then the server responds with JSON-RPC error code -32602
    And the response error message contains "<message>"
    And the response error message does NOT contain "SECRET-ECHO-PROBE"
    And the IMAP connections opened for account "gupta-scaratec" number exactly 0

    Examples:
      | tool            | defect                       | arguments                                                                                                                         | message                                                                  |
      | list_drafts     | no folder                    | {"account": "gupta-scaratec"}                                                                                                     | list_drafts: missing required argument 'folder'                          |
      | list_folders    | no account                   | {}                                                                                                                                | list_folders: missing required argument 'account'                        |
      | fetch_body      | no uid                       | {"account": "gupta-scaratec", "folder": "INBOX"}                                                                                  | fetch_body: missing required argument 'uid'                              |
      | fetch_body      | neither folder nor uid       | {"account": "gupta-scaratec"}                                                                                                     | fetch_body: missing required arguments 'folder', 'uid'                   |
      | move            | no uid in source             | {"source": {"account": "gupta-scaratec", "folder": "INBOX"}, "target": {"account": "gupta-scaratec", "folder": "Drafts"}}         | move: missing required argument 'source.uid'                             |
      | fetch_body      | uid as a string              | {"account": "gupta-scaratec", "folder": "INBOX", "uid": "SECRET-ECHO-PROBE"}                                                      | fetch_body: argument 'uid' must be an integer, got string                |
      | fetch_body      | uid as a boolean             | {"account": "gupta-scaratec", "folder": "INBOX", "uid": true}                                                                     | fetch_body: argument 'uid' must be an integer, got boolean               |
      | folder_stats    | folder as a number           | {"account": "gupta-scaratec", "folder": 7}                                                                                        | folder_stats: argument 'folder' must be a string, got integer            |
      | search          | has_attachment as a string   | {"account": "gupta-scaratec", "folder": "INBOX", "criteria": {"has_attachment": "yes"}}                                           | search: argument 'criteria.has_attachment' must be a boolean, got string |
      | describe_policy | extra top-level argument     | {"extra_flag": "SECRET-ECHO-PROBE"}                                                                                               | describe_policy: unknown argument 'extra_flag'                           |
      | search          | retired 2.x from key         | {"account": "gupta-scaratec", "folder": "INBOX", "criteria": {"from": "bob@example.com"}}                                         | search: unknown argument 'criteria.from'                                 |
      | search          | retired 2.x to_contains key  | {"account": "gupta-scaratec", "folder": "INBOX", "criteria": {"to_contains": "allianz.de"}}                                       | search: unknown argument 'criteria.to_contains'                          |
      | move            | extra key inside source      | {"source": {"account": "gupta-scaratec", "folder": "INBOX", "uid": 501, "force": true}, "target": {"account": "gupta-scaratec", "folder": "Drafts"}} | move: unknown argument 'source.force' |
