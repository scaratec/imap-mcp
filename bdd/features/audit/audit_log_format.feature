@test_hooks
Feature: Tamper-evident JSONL audit format, pre-effect write intent, keyed HMAC digests, and test-mode gate

  Every MCP request, policy reload, token event, and saga transition is
  recorded in a daily SHA-256 hash-chained JSONL file (ADR 0048, ADR 0058):
    - Read/meta calls and pre-execution DENY decisions write one record
      with phase="single".
    - Authorized write and artifact tools fsync a phase="intent" record
      before mutating IMAP or disk, followed by phase="outcome" sharing
      the same request_id.
    - JSON-RPC -32602 schema errors emit reason="invalid_arguments", and
      unexpected runtime exceptions emit reason="internal_error" with
      error_class (never the exception message).
    - Hash-chain rule (ADR 0048): record i carries prev_hash = "sha256:" +
      lowercase hex sha256(raw UTF-8 line i-1 including trailing LF), with
      day-0 genesis "sha256:" + 64 zeros and day rollover chaining onto
      eof_day.final_hash.
    - Keyed HMAC digest rule (ADR 0042, ADR 0048):
        key = bytes.fromhex(<64-char hex content of server/provenance_hmac_key>)
        search_query_digest = "hmac-sha256:" + hex(HMAC-SHA256(key, UTF-8(canonical_json(criteria))))
          where canonical_json sorts keys and uses compact (",", ":") separators over criteria only.
        from_domain_digest  = "hmac-sha256:" + hex(HMAC-SHA256(key, UTF-8(lowercase_domain)))
    - Setting any of the 6 test-hook environment variables without
      IMAP_MCP_TEST_MODE=1 refuses server startup fatally.

  Covered error layers (per BDD Guidelines §4.5):
    - Read ALLOW record shape (phase=single, RFC3339 ts, seq, prev_hash) : 1
    - Write tool emits pre-effect phase=intent + post-effect outcome     : 1
    - Opaque <METADATA fetch records audit_detail_reason without leaks   : 1
    - Blacklist cap records keyed from_domain_digest without cleartext   : 1
    - Search logs keyed search_query_digest and suppressed_by_policy     : 1
    - Saga transitions emit ordered records sharing one tx_id            : 1
    - Hash chain detects intermediate tampering and spans UTC day roll   : 2
    - Positive-controlled no-content-leak across body/subject/file/token : 1
    - JSON-RPC -32602 invalid params emits invalid_arguments record      : 1
    - Unexpected runtime exception emits internal_error without message  : 1
    - HTTP auth_failed records http caller_addr and omits wrong token    : 1
    - File modes (0700 dir, 0600 active, 0400 sealed) & SIGKILL restart  : 2
    - Fatal startup refusal for all 6 test hooks without TEST_MODE=1     : 6
    Total enumerated                                                     : 20  covered by this feature: 20

  Background:
    Given the audit log directory is a fresh $TMPDIR/audit
    And the shared fake clock file is set to "2026-04-21T12:00:00Z"
    And secret_store configuration is:
      """
      backend: file_dir
      path: $TMPDIR/secrets
      acknowledge_plaintext: true
      """
    And the file "$TMPDIR/secrets/server/provenance_hmac_key" contains the exact bytes "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    And the IMAP account "gupta-scaratec" exists with folders:
      | folder path      | special use |
      | INBOX            |             |
      | INBOX/Rechnungen |             |
      | Drafts           | \Drafts     |
    And the server is configured with single caller:
      | caller_id     | auth_type     | policy         |
      | invoice-agent | stdio_trusted | invoice-policy |
    And policy "invoice-policy" grants account "gupta-scaratec"
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder           | mode      | default | mark_seen | draft_append | rules                               |
      | gupta-scaratec | INBOX/Rechnungen | whitelist | NONE    | true      | false        | [{from_domain=hornbach.de -> FULL}] |
      | gupta-scaratec | Drafts           | blacklist | FULL    | false     | true         | []                                  |

  Scenario: Read ALLOW call writes a single-phase record with the documented schema
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject       | date                 |
      | 101 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7823 | 2026-04-20T09:00:00Z |
    And the message at uid 101 has plain text body "Rechnungsbetrag 119 EUR"
    When invoice-agent calls fetch_body with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 101
    Then file "2026-04-21.jsonl" contains a JSONL record whose fields equal:
      | field              | value         |
      | phase              | single        |
      | caller_id          | invoice-agent |
      | tool               | fetch_body    |
      | decision           | ALLOW         |
      | reason             | rule_matched  |
      | visibility_granted | FULL          |
      | result             | OK            |
    And that record has field "ts" matching RFC 3339 UTC millisecond format
    And that record has integer field "seq" >= 0
    And that record has field "prev_hash" matching "^sha256:[0-9a-f]{64}$"
    And that record has integer field "latency_ms" >= 0

  Scenario: Write call emits phase=intent before mutation and phase=outcome with matching request_id
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject       | flags | date                 |
      | 105 | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung 7824 | []    | 2026-04-20T09:00:00Z |
    When invoice-agent calls mark_seen with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 105, seen true
    Then file "2026-04-21.jsonl" contains in order for tool "mark_seen":
      | phase   | decision | result  |
      | intent  | ALLOW    | PENDING |
      | outcome | ALLOW    | OK      |
    And both "mark_seen" records carry the same non-empty UUIDv4 "request_id"

  Scenario: Fetching a non-whitelisted message logs audit_detail_reason without exposing sender or subject
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address          | to_address            | subject                   | date                 |
      | 102 | marketing@spammer.com | me@gupta-scaratec.com | [VERTRAULICH] Secret deal | 2026-04-20T09:00:00Z |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 102
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "not_found"
    And file "2026-04-21.jsonl" contains a JSONL record with:
      | field               | value               |
      | tool                | fetch_envelope      |
      | decision            | ALLOW               |
      | result              | ERROR               |
      | error_type          | not_found           |
      | audit_detail_reason | message_not_granted |
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string      |
      | marketing@spammer.com |
      | spammer.com           |
      | [VERTRAULICH]         |
      | Secret deal           |

  Scenario: Capped blacklist fetch logs keyed HMAC from_domain_digest without cleartext domain
    Given policy "invoice-policy" grants folder:
      | folder | mode      | default  | rules                               |
      | INBOX  | blacklist | ENVELOPE | [{from_domain=bank.de -> cap NONE}] |
    And the folder "INBOX" holds messages:
      | uid | from_address    | to_address            | subject     | date                 |
      | 103 | noreply@bank.de | me@gupta-scaratec.com | Kontoauszug | 2026-04-20T09:00:00Z |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX", uid 103
    Then the response field error.type equals "not_found"
    And file "2026-04-21.jsonl" contains a JSONL record with:
      | field               | value                                                                       |
      | tool                | fetch_envelope                                                              |
      | audit_detail_reason | message_capped                                                              |
      | from_domain_digest  | hmac-sha256:852164ec72e10f7535a54042d517b3c983bf8eb620409e750d1660b9a74bf0e0 |
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string |
      | noreply@bank.de  |
      | bank.de          |
      | Kontoauszug      |

  Scenario: Search logs keyed HMAC search_query_digest and suppressed_by_policy without cleartext query terms
    Given the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address          | to_address            | subject               | date                 |
      | 104 | marketing@spammer.com | me@gupta-scaratec.com | Streng vertrauensvoll | 2026-04-20T09:00:00Z |
    When invoice-agent calls search with account "gupta-scaratec", folder "INBOX/Rechnungen", criteria {"subject_contains": "vertrauensvoll"}, scope "all"
    Then file "2026-04-21.jsonl" contains a JSONL record with:
      | field                | value                                                                       |
      | tool                 | search                                                                      |
      | decision             | ALLOW                                                                       |
      | suppressed_by_policy | true                                                                        |
      | search_query_digest  | hmac-sha256:de9d9431acdaccb6d35dc197b042598c4b8971df884d81ad34b67cfc9e60eda9 |
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string |
      | vertrauensvoll   |

  Scenario: Cross-account saga emits ordered saga_transition records sharing a single tx_id
    Given the IMAP account "personal" exists with folders:
      | folder path   |
      | Archiv/Belege |
    And policy "invoice-policy" grants account "personal"
    And policy "invoice-policy" grants the following folder policies:
      | account        | folder           | mode      | default | move_out | accept_incoming | rules                               |
      | gupta-scaratec | INBOX/Rechnungen | whitelist | NONE    | true     | false           | [{from_domain=hornbach.de -> FULL}] |
      | personal       | Archiv/Belege    | whitelist | NONE    | false    | true            | []                                  |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | message_id                 | from_address         | to_address            | subject       | date                 |
      | 106 | <m-106@gupta-scaratec.com> | rechnung@hornbach.de | me@gupta-scaratec.com | Rechnung Saga | 2026-04-20T09:00:00Z |
    When invoice-agent calls move with source {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": 106}, target {"account": "personal", "folder": "Archiv/Belege"}
    Then file "2026-04-21.jsonl" contains 5 records with tool "saga_transition" in exact step order:
      | step    |
      | begin   |
      | fetched |
      | staged  |
      | deleted |
      | commit  |
    And all 5 "saga_transition" records carry the exact tx_id returned by move

  Scenario: Modifying an intermediate JSONL record breaks the hash chain from the next record onward
    When invoice-agent calls list_accounts 5 times
    Then all records of file "2026-04-21.jsonl" form a valid hash chain with consecutive seq numbers starting at 0
    When an external writer alters the "tool" value of record index 2 in "2026-04-21.jsonl"
    Then the offline chain verifier reports records 0 and 1 as valid and record 3 as broken prev_hash

  Scenario: Hash chain spans UTC midnight day rotation via eof_day final_hash
    Given the shared fake clock file is set to "2026-04-21T23:59:59Z"
    And invoice-agent calls list_accounts
    When the shared fake clock file advances to "2026-04-22T00:00:01Z"
    And invoice-agent calls list_accounts
    Then file "2026-04-21.jsonl" ends with a record of tool "eof_day" carrying field final_hash
    And file "2026-04-22.jsonl" begins with seq 0 whose prev_hash equals that final_hash

  Scenario: Audit log omits bodies, subjects, attachment filenames, and active OAuth tokens across verified operations
    Given the server attachment sink directory is a fresh writable directory
    And the mock OAuth2 server is reset and healthy
    And account "gmail-archive" is configured for OAuth2 against the mock OAuth2 server and mock-gmail with scope "https://mail.google.com/" and token_cache "memory_only"
    And the file "$TMPDIR/secrets/accounts/gmail-archive/refresh_token" contains the exact bytes "1//0g-refresh-secret-token-xyz"
    And policy "invoice-policy" grants account "gmail-archive"
    And policy "invoice-policy" grants folder on account "gmail-archive":
      | folder | mode      | default  | rules |
      | INBOX  | blacklist | ENVELOPE | []    |
    And the folder "INBOX/Rechnungen" holds messages:
      | uid | from_address         | to_address            | subject                      | date                 |
      | 108 | rechnung@hornbach.de | me@gupta-scaratec.com | Geheimbetreff Mandant 998877 | 2026-04-20T09:00:00Z |
    And the message at uid 108 has plain text body "-----BEGIN PGP MESSAGE----- geheimer Rumpftext"
    And the message at uid 108 has attachment "Streng_Geheimer_Vertrag.pdf" of type "application/pdf" with content byte "V" repeated 256 times
    When invoice-agent calls list_folders with account "gmail-archive"
    Then the response decision is ALLOW
    When invoice-agent calls fetch_body with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 108
    Then the response decision is ALLOW
    And the response field subject equals "Geheimbetreff Mandant 998877"
    When invoice-agent calls fetch_attachment with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 108, part_id 0
    Then the response decision is ALLOW
    When invoice-agent calls create_draft with account "gupta-scaratec", folder "Drafts", fields:
      """
      {"to": ["partner@example.org"],
       "subject": "Entwurf Geheimprojekt",
       "body_text": "Vertraulicher Entwurfstext"}
      """
    Then the response decision is ALLOW
    And file "2026-04-21.jsonl" contains an outcome record for tool "fetch_attachment" carrying non-empty fields "artifact_id" and "artifact_sha256"
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string               |
      | Geheimbetreff Mandant 998877   |
      | BEGIN PGP MESSAGE              |
      | geheimer Rumpftext             |
      | Streng_Geheimer_Vertrag        |
      | Entwurf Geheimprojekt          |
      | Vertraulicher Entwurfstext     |
      | 1//0g-refresh-secret-token-xyz |
      | saved_to_absolute              |

  Scenario: JSON-RPC -32602 schema validation rejection writes a chained invalid_arguments audit record
    When invoice-agent calls fetch_body with the arguments {"account": "gupta-scaratec", "folder": "INBOX/Rechnungen", "uid": "not-an-int"}
    Then the server responds with JSON-RPC error code -32602
    And file "2026-04-21.jsonl" contains a JSONL record with:
      | field    | value             |
      | decision | DENY              |
      | reason   | invalid_arguments |
      | result   | ERROR             |

  Scenario: Unexpected runtime exception returns ALLOW + ERROR internal_error and audits error_class without leaking message text
    # ADR 0058 & ADR 0062: IMAP_MCP_TEST_INJECT_INTERNAL_ERROR raises RuntimeError(<detail>) inside the named tool.
    Given the server is armed with IMAP_MCP_TEST_INJECT_INTERNAL_ERROR="list_accounts:SENSITIVE-EXCEPTION-DETAIL"
    When invoice-agent calls list_accounts
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "internal_error"
    And the JSON response does NOT contain the literal strings:
      | forbidden_string           |
      | SENSITIVE-EXCEPTION-DETAIL |
    And file "2026-04-21.jsonl" contains a JSONL record with:
      | field       | value          |
      | tool        | list_accounts  |
      | decision    | ALLOW          |
      | reason      | internal_error |
      | result      | ERROR          |
      | error_class | RuntimeError   |
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string           |
      | SENSITIVE-EXCEPTION-DETAIL |

  Scenario: HTTP auth_failed event records http caller_addr and omits wrong bearer token
    Given the file "$TMPDIR/secrets/callers/invoice-agent/token" contains the exact bytes "correct-horse-battery"
    And the server is configured with single caller:
      | caller_id     | auth_type    | token_secret_ref                     | policy         |
      | invoice-agent | shared_token | secret://callers/invoice-agent/token | invoice-policy |
    And the server process is started with transport "http" on 127.0.0.1 and a random port
    When the MCP client performs an Initialize handshake with caller_id "invoice-agent" and bearer token "attacker-bearer-guess"
    Then the handshake fails with error "auth_failed"
    And file "2026-04-21.jsonl" contains a JSONL record with:
      | field    | value       |
      | tool     | auth_failed |
      | decision | DENY        |
      | reason   | auth_failed |
    And that record has field "caller_addr" starting with "http:127.0.0.1:"
    And the audit file does NOT contain any of the literal strings:
      | forbidden_string      |
      | attacker-bearer-guess |
      | correct-horse-battery |

  Scenario: Audit directory and day files enforce 0700 / 0600 / 0400 permissions
    When invoice-agent calls list_accounts
    Then the directory "$TMPDIR/audit" has mode 0700
    And the audit file "2026-04-21.jsonl" has mode 0600
    When the shared fake clock file advances to "2026-04-22T00:00:05Z"
    And invoice-agent calls list_accounts
    Then the audit file "2026-04-21.jsonl" has mode 0400
    And the audit file "2026-04-22.jsonl" has mode 0600

  Scenario: Hard SIGKILL followed by next-day restart seals the previous 0600 day file to 0400 with eof_day
    Given the shared fake clock file is set to "2026-03-15T14:00:00Z"
    And invoice-agent calls list_accounts
    Then the audit file "2026-03-15.jsonl" has mode 0600
    When the server process is killed with SIGKILL
    And the shared fake clock file advances to "2026-03-16T09:00:00Z"
    And the server is restarted and invoice-agent calls list_accounts
    Then file "2026-03-15.jsonl" ends with a record of tool "eof_day" carrying field final_hash
    And the audit file "2026-03-15.jsonl" has mode 0400
    And the audit file "2026-03-16.jsonl" has mode 0600

  Scenario Outline: Setting test hook <hook_var> without IMAP_MCP_TEST_MODE=1 refuses server startup
    When the server process is started without IMAP_MCP_TEST_MODE and with environment <hook_var>="<hook_val>"
    Then the server refuses to start
    And the startup error indicates "IMAP_MCP_TEST_MODE" and "<hook_var>"

    Examples:
      | hook_var                             | hook_val                 |
      | IMAP_MCP_CRASH_AT                    | post_begin               |
      | IMAP_MCP_FAKE_NOW_UTC                | 2020-01-01T00:00:00Z     |
      | IMAP_MCP_FAKE_NOW_FILE               | /tmp/fake_clock          |
      | IMAP_MCP_SAGA_PAUSE_MARKER           | /tmp/pause_marker        |
      | IMAP_MCP_SAGA_RESUME_FILE            | /tmp/resume_file         |
      | IMAP_MCP_TEST_INJECT_INTERNAL_ERROR  | list_accounts:boom       |
