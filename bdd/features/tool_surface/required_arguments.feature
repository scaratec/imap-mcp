Feature: A call with a missing or mistyped argument is refused as invalid params before any IMAP command

  Every tool publishes a JSON-Schema inputSchema in list_tools: the
  arguments it requires and the JSON type of each one. An agent that
  leaves out a required argument, or passes one with the wrong JSON
  type, made a malformed call. The server refuses it at the protocol
  level with JSON-RPC -32602 (invalid params), the code it already uses
  for the other schema rejections (ADR 0024, 0026 §1, 0031), and the
  message names the tool and the argument, so the agent can correct the
  call without guessing.

  Found by the agent usability smoke test: list_drafts without folder
  and list_folders without account were answered with JSON-RPC error
  code 0 whose message was only the key name ('folder', 'account'). The
  agent could not tell what it had done wrong.

  Rules used below:
    - required and type are read from the tool's inputSchema; nested
      objects (move/copy source and target, criteria) are checked the
      same way, the path written with dots (source.uid).
    - JSON integer is a number without fraction; true and false are
      booleans, never integers (JSON Schema).
    - The value an agent passed is not echoed in the message, only its
      JSON type.
    - Extra arguments the schema does not know are not part of this
      rule (non_goal_rejection: describe_policy ignores them).

  What is at stake. An agent that cannot read why a call failed retries
  blindly or gives up; a handler that runs on half its arguments could
  reach the mailbox with a value nobody chose.

  Independent-channel rule. The JSON-RPC error is read from the wire by
  the test client. That no handler ran is witnessed by the logging IMAP
  proxy in front of the account: it accepted no connection, so no IMAP
  command reached the mailbox.

  Covered error layers (per BDD Guidelines §4.5):
    - Missing required argument (one)                       : 3 (list_drafts folder, list_folders account, fetch_body uid)
    - Missing required arguments (several)                  : 1
    - Missing required member of a nested object            : 1 (move source.uid)
    - Required argument with the wrong JSON type            : 3 (uid string, uid boolean, folder number)
    - Optional argument with the wrong JSON type            : 1 (criteria.has_attachment)
    Total enumerated                                        : 9   covered by this feature: 9

  Background:
    Given the IMAP account "gupta-scaratec" exists with folders:
      | folder path |
      | INBOX       |
      | Drafts      |
    And the server is configured with caller "mail-agent" using policy "mail-policy"
    And policy "mail-policy" grants account "gupta-scaratec"
    And policy "mail-policy" folder defaults for "INBOX" are:
      | mode      | default |
      | blacklist | FULL    |
    And the folder "INBOX" holds messages:
      | uid | from                       | subject           | date                 |
      | 501 | rechnung@nordlicht.example | Ihre Rechnung 10  | 2026-10-30T07:00:00Z |

  Scenario Outline: <tool> with <defect> is refused with -32602 naming the argument
    When mail-agent calls <tool> with the arguments <arguments>
    Then the server responds with JSON-RPC error code -32602
    And the response error message contains "<message>"
    And the IMAP connections opened for account "gupta-scaratec" number exactly 0

    Examples:
      | tool          | defect                       | arguments                                                                                           | message                                                    |
      | list_drafts   | no folder                    | {"account": "gupta-scaratec"}                                                                       | list_drafts: missing required argument 'folder'            |
      | list_folders  | no account                   | {}                                                                                                  | list_folders: missing required argument 'account'          |
      | fetch_body    | no uid                       | {"account": "gupta-scaratec", "folder": "INBOX"}                                                    | fetch_body: missing required argument 'uid'                |
      | fetch_body    | neither folder nor uid       | {"account": "gupta-scaratec"}                                                                       | fetch_body: missing required arguments 'folder', 'uid'     |
      | move          | no uid in source             | {"source": {"account": "gupta-scaratec", "folder": "INBOX"}, "target": {"account": "gupta-scaratec", "folder": "Drafts"}} | move: missing required argument 'source.uid' |
      | fetch_body    | uid as a string              | {"account": "gupta-scaratec", "folder": "INBOX", "uid": "abc"}                                      | fetch_body: argument 'uid' must be an integer, got string  |
      | fetch_body    | uid as a boolean             | {"account": "gupta-scaratec", "folder": "INBOX", "uid": true}                                       | fetch_body: argument 'uid' must be an integer, got boolean |
      | folder_stats  | folder as a number           | {"account": "gupta-scaratec", "folder": 7}                                                          | folder_stats: argument 'folder' must be a string, got integer |
      | search        | has_attachment as a string   | {"account": "gupta-scaratec", "folder": "INBOX", "criteria": {"has_attachment": "yes"}}             | search: argument 'criteria.has_attachment' must be a boolean, got string |
