Feature: Audit log format

  Every PDP decision and every saga transition is appended as a JSONL
  record with a SHA-256 hash chain. Daily files rotate at UTC
  midnight; the chain spans the boundary via prev_hash = final_hash
  of the previous file. A strict no-content-leak rule applies.
  See ADR 0021.

  Covered error layers (per BDD Guidelines §4.5):
    - Happy path record shape             : 1
    - ALLOW with reason=rule_matched      : 1
    - DENY record categorical reason      : 1
    - Saga transition records             : 1
    - Hash chain across records           : 1
    - Hash chain across day roll          : 1
    - No-content-leak (body, subject, …)  : 1
    - No-content-leak (DENY sender addr)  : 1
    - Search query hashing                : 1
    - Auth-failed records                 : 1
    - Record permissions (0600)           : 1
    - Stale 0400 file reopened on restart  : 1
    Total enumerated                       : 12   covered by this feature: 12

  Background:
    Given the audit log directory is a fresh $TMPDIR/audit
    And the server is configured with caller "invoice-agent"
    And policy "invoice-policy" grants INBOX/Rechnungen with mode=whitelist, default=NONE, rule from_domain=hornbach.de -> FULL

  Scenario: Each ALLOW call produces a record with the documented field set
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                 | subject          |
      | 101 | rechnung@hornbach.de | Rechnung 7823    |
    When invoice-agent calls fetch_body with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 101
    Then the current day's audit file contains a JSONL record whose fields equal:
      | field              | value                  |
      | caller_id          | invoice-agent          |
      | tool               | fetch_body             |
      | decision           | ALLOW                  |
      | reason             | rule_matched           |
      | visibility_granted | FULL                   |
      | result             | OK                     |
    And the record has a "ts" field matching RFC 3339 UTC to millisecond precision
    And the record has a "seq" field that is a non-negative integer
    And the record has a "prev_hash" field that is "sha256:" followed by 64 lowercase hex characters
    And the record has a "latency_ms" field that is a non-negative integer

  Scenario: A DENY call records the categorical reason without exposing the triggering data
    Given the folder "INBOX/Rechnungen" holds a message with:
      | uid | from                    | subject                      |
      | 102 | marketing@spammer.com   | [VERTRAULICH] Secret deal    |
    When invoice-agent calls fetch_envelope with account "gupta-scaratec", folder "INBOX/Rechnungen", uid 102
    Then the audit file contains a JSONL record with:
      | field     | value                   |
      | decision  | DENY                    |
      | reason    | sender_not_whitelisted  |
    And the record args_summary contains fields {"account", "folder", "uid"}
    And the record does NOT contain the literal string "marketing@spammer.com"
    And the record does NOT contain the literal string "[VERTRAULICH]"
    And the record does NOT contain the literal string "Secret deal"

  Scenario: Saga transitions are recorded as separate events with step names
    Given a cross-account move begins and succeeds
    Then the audit file contains, in this order, at least the records:
      | tool              | step     |
      | saga_transition   | begin    |
      | saga_transition   | fetched  |
      | saga_transition   | staged   |
      | saga_transition   | deleted  |
      | saga_transition   | commit   |
    And all five records share the same tx_id

  Scenario: Hash chain — modifying an intermediate record invalidates every subsequent prev_hash
    Given the audit file already contains 5 records R1..R5 forming a valid chain
    When an external writer replaces R3's "tool" field with a different value
    Then re-computing the hash of R3 produces a value different from R4's prev_hash
    And the offline verifier reports R4 (and later) as tampered
    And the offline verifier reports R1, R2 as unaffected

  Scenario: Hash chain spans day rotation
    Given the audit writer is at 23:59:59 UTC with seq 250 in file "2026-04-21.jsonl"
    When the clock crosses midnight UTC
    Then file "2026-04-21.jsonl" ends with a record of tool "eof_day" carrying field final_hash
    And file "2026-04-22.jsonl" begins with a record whose prev_hash equals that final_hash
    And file "2026-04-22.jsonl" first record has seq 0

  Scenario: The audit log never contains message bodies, subjects, filenames, or OAuth tokens
    Given a sequence of operations over a day creates 20 audit records across ALLOW, DENY, saga, and token_refresh
    When the current audit file is read
    Then the file does NOT contain the literal string "BEGIN PGP" or "-----"
    And the file does NOT contain any access token or refresh token value from the secret store
    And the file does NOT contain any Subject: header from the IMAP test server
    And the file does NOT contain any attachment filename

  Scenario: DENY caused by sender filtering hashes the sender, not cleartext
    When invoice-agent triggers a DENY with reason sender_blacklisted for a message from "noreply@bank.de"
    Then the audit record does NOT contain the literal string "bank.de"
    And the audit record contains a field "from_domain_sha256" equal to the SHA-256 hex digest of "bank.de"

  Scenario: search query text is hashed, not logged in cleartext
    When invoice-agent calls search with criteria {"subject_contains": "vertrauensvoll"}
    Then the audit record does NOT contain the literal string "vertrauensvoll"
    And the audit record contains a field "search_query_digest" equal to the SHA-256 hex digest of the canonicalized JSON criteria

  Scenario: auth_failed events are recorded with caller_addr and no token material
    Given the server is configured with callers:
      | caller_id     | auth_type    | token_secret_ref                     |
      | invoice-agent | shared_token | secret://callers/invoice-agent/token |
    And the secret store contains value "correct-horse-battery" under "callers/invoice-agent/token"
    And the server is started with transport "http" on a random port
    When a client sends an Initialize with caller_id "invoice-agent" and bearer token "wrong-token"
    Then the audit file contains a JSONL record with:
      | field       | value          |
      | tool        | auth_failed    |
      | decision    | DENY           |
      | reason      | auth_failed    |
    And the record does NOT contain the literal string "wrong-token"

  Scenario: Audit files have restrictive filesystem permissions
    Given the audit writer creates the file for today
    Then the current day's audit file has mode 0600
    When the UTC day rolls
    Then the just-closed file has mode 0400
    And the audit directory has mode 0700

  Scenario: Server restart with a stale read-only audit file from a previous run
    Given the server is actively writing to "2026-03-15.jsonl"
    When the server terminates ungracefully
    And the audit file "2026-03-15.jsonl" is set to mode 0400
    And the server is restarted with fake date "2026-03-16"
    And invoice-agent calls list_accounts
    Then file "2026-03-15.jsonl" ends with a record of tool "eof_day" carrying field final_hash
    And the audit file "2026-03-15.jsonl" has mode 0400
    And the audit file "2026-03-16.jsonl" has mode 0600
