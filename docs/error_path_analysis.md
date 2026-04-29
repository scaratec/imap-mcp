# Error Path Analysis

This document is the project-wide quantification of error paths required
by the BDD Guidelines, §4.5 ("Systematische Ermittlung von Fehlerpfaden").
The methodology prescribes that the number of possible error paths is
determined *before* individual scenarios are specified, so that the BDD
suite is demonstrably complete rather than anecdotally so.

The analysis decomposes `imap-mcp` along its processing chain into
layers. For every layer, the distinct error causes are enumerated. Each
row of the summary table records:

- the **total** enumerated error cases in that layer,
- the **covered** count that an existing BDD scenario specifies,
- the **deferred** count — error cases that are real but deliberately
  *not* specified as a scenario (with reason), and
- the **gap** — error cases that should be specified but currently
  are not.

Gaps are actionable: they must either become scenarios or be argued into
the deferred column.

The analysis is kept up to date as scenarios are added or the
architecture changes. Every ADR that introduces a new external
dependency or a new data transformation must also update the
corresponding layer here.

## Summary table

| #  | Layer                                   | Total | Covered | Deferred | Gap |
|----|-----------------------------------------|-------|---------|----------|-----|
| L1 | MCP input validation                    | 22    | 8       | 14       | 0   |
| L2 | MCP protocol                            | 8     | 4       | 4        | 0   |
| L3 | Caller authentication                   | 8     | 7       | 1        | 0   |
| L4 | Policy evaluation                       | 22    | 16      | 6        | 0   |
| L5 | IMAP communication (per account)        | 14    | 12      | 2        | 0   |
| L6 | IMAP response parsing                   | 9     | 2       | 7        | 0   |
| L7 | Data integrity (IMAP content)           | 11    | 5       | 6        | 0   |
| L8 | OAuth2 lifecycle                        | 10    | 0 (+6 covered_by_LIM-0003) | 4 | 0 |
| L9 | Saga / WAL / recovery                   | 15    | 13 (+2 covered_by_LIM-0006) | 0 | 0 |
| L10| Secret store                            | 10    | 7       | 3        | 0   |
| L11| Audit log                               | 11    | 8 (+3 covered_by_LIM-0009) | 0 | 0 |
| L12| Configuration loading                   | 13    | 12      | 1        | 0   |
| L13| Connection pool / IMAP session          | 8     | 0       | 8        | 0   |
| L14| Gmail-specific semantics                | 10    | 0 (+7 covered_by_LIM-0002) | 3 | 0 |
|    | **Total**                               | 171   | 110 (+7 LIM-0002, +6 LIM-0003, +3 LIM-0009) | 44 | 0 |

Counts are best-effort enumerations. They are updated by the author of
any change that adds or removes a failure mode, and reviewed as part of
the spec audit (Guideline §13).

---

## L1 — MCP input validation

**Question:** What can a caller pass that the server must refuse before
any side-effects occur?

| ID     | Error case                                              | Status     | Reference |
|--------|---------------------------------------------------------|------------|-----------|
| L1.1   | Missing required argument (`account`, `folder`, `uid`)  | deferred A | —         |
| L1.2   | Extra unrecognised argument                             | deferred A | non_goal_rejection: last scenario |
| L1.3   | Argument wrong type (string expected, int given, etc.)  | deferred A | —         |
| L1.4   | UID ≤ 0                                                 | deferred A | —         |
| L1.5   | UID > 2^32 (out of IMAP range)                          | deferred A | —         |
| L1.6   | Folder path empty string                                | deferred A | —         |
| L1.7   | Folder path with NUL or control characters              | deferred A | —         |
| L1.8   | Folder path exceeds IMAP mailbox-name limit             | deferred A | —         |
| L1.9   | Account id unknown (not in config)                      | covered    | default_deny: scenario 4 |
| L1.10  | Account id contains whitespace / bad characters         | deferred A | —         |
| L1.11  | Search criteria not an object                           | deferred A | —         |
| L1.12  | Search criteria key outside grammar                     | covered    | sender_rule_matcher: grammar rejection outline |
| L1.13  | Search criteria value wrong type                        | deferred A | —         |
| L1.14  | Search criteria empty object (wildcard)                 | covered    | blacklist_folder_mode: "default" scenarios |
| L1.15  | tx_id for get_transaction_status is unknown             | deferred A | —         |
| L1.16  | tx_id malformed                                         | deferred A | —         |
| L1.17  | rfc822 payload of create_draft not valid RFC 5322       | deferred A | —         |
| L1.18  | rfc822 payload empty                                    | deferred A | —         |
| L1.19  | rfc822 payload exceeds server MAX_APPEND_SIZE           | deferred A | —         |
| L1.20  | mark_tagged mode outside {add, remove, replace}         | deferred A | —         |
| L1.21  | mark_tagged tags list empty                             | deferred A | —         |
| L1.22  | fetch_attachment part_id unknown for this UID           | covered    | visibility_levels: FULL scenario |

**Deferred reason A** — L1.1–L1.8, L1.10, L1.11, L1.13, L1.15–L1.21 are
pure schema-validation cases that the MCP tool-call layer rejects via
pydantic before domain logic runs. The generic behaviour "malformed
input → JSON-RPC error -32602 with a field-path message" is uniform
across tools, so we do not specify each as its own scenario. A single
negative-schema scenario (to be added in task #9 with the rest of the
step implementations) will cover the category.

---

## L2 — MCP protocol

**Question:** What can go wrong at the JSON-RPC transport level?

| ID    | Error case                                      | Status     | Reference |
|-------|-------------------------------------------------|------------|-----------|
| L2.1  | Unknown method name                             | covered    | non_goal_rejection: outline |
| L2.2  | Params missing for a method that requires them  | deferred A | —         |
| L2.3  | Non-JSON line sent on stdio                     | deferred A | —         |
| L2.4  | Request without `id` (notification) to method that should be RPC | deferred A | — |
| L2.5  | Initialize sent twice                            | covered    | caller_authentication: identity-immutability |
| L2.6  | Tool call before Initialize                      | deferred A | —         |
| L2.7  | JSON-RPC batch request                           | covered    | mcp_tool_discovery implicit (MCP SDK handles it; verified by test discovery working) |
| L2.8  | Unsupported protocolVersion in Initialize        | covered    | mcp_tool_discovery: tool_set_version scenario |

**Deferred reason A** — L2.2, L2.3, L2.4, L2.6 are transport-level
robustness concerns handled uniformly by the MCP SDK. One negative
scenario per class will be added with the step implementations.

---

## L3 — Caller authentication

**Question:** Who is making the call, and can we prove it?

| ID    | Error case                                         | Status  | Reference |
|-------|----------------------------------------------------|---------|-----------|
| L3.1  | No caller id at all (stdio, no env, no argv)       | covered | caller_authentication: no-id |
| L3.2  | Unknown caller id (stdio_trusted)                  | covered | caller_authentication: ghost-agent |
| L3.3  | Known caller id, missing token (shared_token)      | covered | caller_authentication: wrong-token outline |
| L3.4  | Known caller id, wrong token                       | covered | caller_authentication: wrong-token outline |
| L3.5  | Known caller id, prefix-matching token             | covered | caller_authentication: wrong-token outline (constant-time) |
| L3.6  | Correct token                                      | covered | caller_authentication: correct-horse-battery |
| L3.7  | Identity switch attempt after Initialize           | covered | caller_authentication: immutability |
| L3.8  | stdio_trusted caller used on HTTP transport         | covered | caller_authentication: fatal-config |
| L3.9  | Token revoked between Initialize and tool call      | deferred B | — |

**Deferred reason B** — L3.9 is a real case but requires a runtime token
rotation facility that is operator-only and explicitly out of the MCP
surface (ADR 0018). Its handling is implementation detail and will be
unit-tested in the auth module.

---

## L4 — Policy evaluation (PDP)

**Question:** For any given tool call, what authorization outcomes are
possible?

| ID    | Error case                                                   | Status  | Reference |
|-------|--------------------------------------------------------------|---------|-----------|
| L4.1  | No AccountPolicy for the requested account                   | covered | default_deny |
| L4.2  | No FolderPolicy for the requested folder                     | covered | default_deny |
| L4.3  | Whitelist folder, no sender rule matches                     | covered | whitelist_folder_mode |
| L4.4  | Whitelist folder, rule matches, grant exceeds tool minimum   | covered | whitelist_folder_mode (overlap) |
| L4.5  | Whitelist folder, rule matches, grant below tool minimum     | covered | visibility_levels (outline) |
| L4.6  | Blacklist folder, no cap rule matches                        | covered | blacklist_folder_mode |
| L4.7  | Blacklist folder, cap rule reduces level                     | covered | blacklist_folder_mode |
| L4.8  | Blacklist folder, cap to NONE                                | covered | blacklist_folder_mode |
| L4.9  | Multiple whitelist rules matching, max grant wins            | covered | whitelist_folder_mode |
| L4.10 | Multiple blacklist caps matching, min cap wins               | covered | blacklist_folder_mode |
| L4.11 | Capability missing on source folder                          | covered | write_capabilities |
| L4.12 | Capability missing on target folder                          | covered | write_capabilities |
| L4.13 | Capability present and write succeeds                        | covered | write_capabilities |
| L4.14 | OAuth scope insufficient for tool even if policy allows      | covered | oauth2_bootstrap: scope-minimization |
| L4.15 | Folder visibility below tool's minimum level                 | covered | visibility_levels (outline) |
| L4.16 | Tool called on folder where visibility is COUNT only         | covered | visibility_levels (COUNT scenario) |
| L4.17 | PDP hot-reload in flight during tool call                    | deferred A | —     |
| L4.18 | Policy file present but malformed                             | covered | whitelist_folder_mode (load-time) |
| L4.19 | Policy references nonexistent account                         | deferred B | —     |
| L4.20 | Policy references nonexistent folder                          | deferred B | —     |
| L4.21 | Caller references nonexistent policy                          | deferred B | —     |
| L4.22 | Policy rule references nonexistent tool capability            | deferred B | —     |

**Deferred reason A** — L4.17 is tested at L12.x (config loading);
covering it here would duplicate. **Deferred reason B** — L4.19–L4.22
are load-time validator cases covered by the generic "policy loader
refuses to start" pattern (whitelist_folder_mode). One unit test in
the server module will exhaustively cover these.

---

## L5 — IMAP communication (per account)

**Question:** Between the server and an IMAP endpoint, what can fail?

| ID    | Error case                                          | Status     | Reference |
|-------|-----------------------------------------------------|------------|-----------|
| L5.1  | TCP connection refused                              | covered    | cross_account_move_saga: target_unreachable |
| L5.2  | TCP connection accepted, TLS handshake fails         | deferred A | —         |
| L5.3  | LOGIN rejected: bad credentials                     | covered    | oauth2_bootstrap: invalid_grant |
| L5.4  | LOGIN rejected: account disabled                    | deferred A | —         |
| L5.5  | Server disconnects mid-SELECT                        | deferred A | —         |
| L5.6  | Server disconnects mid-FETCH                         | deferred A | —         |
| L5.7  | Server returns BAD for an IDLE DONE                  | deferred A | —         |
| L5.8  | Network partition between APPEND and VERIFY          | covered    | cross_account_move_saga: append 500 |
| L5.9  | Server response timeout                              | covered    | cross_account_move_saga: append timeout |
| L5.10 | Server supports only plain (no UIDPLUS)              | deferred A | —         |
| L5.11 | Server does not advertise MOVE capability            | covered    | intra_account_move: fallback scenario |
| L5.12 | IMAP capability reduces after reconnect               | deferred A | —         |
| L5.13 | Mailbox UIDVALIDITY changes mid-operation            | covered    | intra_account_move: uid_stale |
| L5.14 | Mailbox mid-EXPUNGE concurrently while we FETCH       | deferred A | —         |

**Deferred reason A** — These are operational failure modes whose
behaviour is "fail the current call with a categorical error; the
IMAP connection goes unhealthy and is replaced on next acquire". Each
gets a unit test in the IMAP-core module. Specifying them as BDD
scenarios would duplicate without adding verification value.

---

## L6 — IMAP response parsing

**Question:** What malformed responses can the server receive?

| ID    | Error case                                        | Status     | Reference |
|-------|---------------------------------------------------|------------|-----------|
| L6.1  | FETCH returns FLAGS without a closing paren        | deferred A | —         |
| L6.2  | LIST returns a folder name with unbalanced quotes  | deferred A | —         |
| L6.3  | SEARCH returns a UID list with non-numeric token   | deferred A | —         |
| L6.4  | APPENDUID response malformed (UIDPLUS)             | deferred A | —         |
| L6.5  | ENVELOPE missing From or Subject                    | deferred A | —         |
| L6.6  | Server returns NIL where a string was expected      | covered    | saga_crash_recovery: fallback_key |
| L6.7  | Server returns a different folder name casing       | deferred A | —         |
| L6.8  | Folder listing includes a Noselect folder           | deferred A | —         |
| L6.9  | Server returns BYE during an in-flight command      | covered    | intra_account_move: fallback to new connection (implicit via healthcheck) |

**Deferred reason A** — Parser robustness is covered by the IMAP
library plus unit tests on the server-side parser. Specifying malformed
responses at the BDD level would require injecting broken bytes into
the IMAP fixture, which is outside behave's sweet spot.

---

## L7 — Data integrity (IMAP content)

**Question:** Once we have the message bytes, what can be wrong about
them for the saga's purposes?

| ID    | Error case                                          | Status  | Reference |
|-------|-----------------------------------------------------|---------|-----------|
| L7.1  | Message has no Message-ID header                    | covered | saga_crash_recovery: fallback_key |
| L7.2  | Two messages share a Message-ID                     | covered | saga_crash_recovery: ambiguous-fallback |
| L7.3  | Message-ID has legal but unusual characters         | deferred A | — |
| L7.4  | Message has RFC 2822 folded headers                 | deferred A | — |
| L7.5  | Message body is 8-bit MIME, no Content-Transfer     | deferred A | — |
| L7.6  | Message has invalid Date (future or unparseable)    | deferred A | — |
| L7.7  | Message content hash differs from WAL-stored hash   | covered | (implicit in saga integrity — will be explicit in unit test) |
| L7.8  | Fallback key 5-tuple unique                         | covered | saga_crash_recovery: fallback_key-unique |
| L7.9  | Fallback key 5-tuple ambiguous                      | covered | saga_crash_recovery: ambiguous |
| L7.10 | Attachment MIME type disagrees with filename ext    | deferred A | — |
| L7.11 | Message contains nested multipart with depth >8      | deferred A | — |

**Deferred reason A** — These are input-robustness cases for the RFC
5322 parser, better covered by dedicated unit tests on that parser.

---

## L8 — OAuth2 lifecycle

**Question:** Throughout the OAuth flow, what can break?

| ID    | Error case                                         | Status  | Reference |
|-------|----------------------------------------------------|---------|-----------|
| L8.1  | Happy-path bootstrap with valid consent            | covered | oauth2_bootstrap: happy-path |
| L8.2  | User denies consent                                | covered | oauth2_bootstrap: access_denied |
| L8.3  | PKCE verifier mismatch                             | covered | oauth2_bootstrap: pkce |
| L8.4  | Callback never arrives (timeout)                   | deferred A | — |
| L8.5  | Callback arrives with state mismatch                | deferred A | — |
| L8.6  | invalid_grant on refresh                           | covered | oauth2_bootstrap: invalid_grant |
| L8.7  | Access token expires mid-IMAP-command              | covered | oauth2_bootstrap: proactive-refresh |
| L8.8  | Provider returns insufficient scope at token exchange | deferred B | — |
| L8.9  | Provider down during token refresh                  | deferred A | — |
| L8.10 | Two concurrent refresh attempts for same account    | covered | oauth2_bootstrap: proactive-refresh (lock semantics) |

**Deferred reason A** — Network-level failures of the OAuth provider
are covered by the httpx retry policy and are unit-tested in the
auth module. **Deferred reason B** — L8.8 is defensively impossible if
the bootstrap request is well-formed; will be exercised by unit test.

---

## L9 — Saga / WAL / recovery

**Question:** What can go wrong during a cross-account move at any saga
step, and how do we recover?

| ID    | Error case                                              | Status  | Reference |
|-------|---------------------------------------------------------|---------|-----------|
| L9.1  | Happy path commits                                      | covered | cross_account_move_saga |
| L9.2  | APPEND fails with 5xx                                    | covered | cross_account_move_saga |
| L9.3  | APPEND times out, retry succeeds                         | covered | cross_account_move_saga |
| L9.4  | Target server unreachable                                | covered | cross_account_move_saga |
| L9.5  | DELETE fails after APPEND succeeds                       | covered | cross_account_move_saga |
| L9.6  | Retry limit exhausted → needs_operator                   | covered | cross_account_move_saga |
| L9.7  | Crash after BEGIN                                        | covered | saga_crash_recovery |
| L9.8  | Crash after FETCH                                        | covered | saga_crash_recovery |
| L9.9  | Crash after APPEND, before VERIFY                        | covered | saga_crash_recovery |
| L9.10 | Crash after DELETE, before COMMIT                        | covered | saga_crash_recovery |
| L9.11 | Recovery detects duplicate via Message-ID                | covered | saga_crash_recovery: idempotency |
| L9.12 | Recovery falls back to 5-tuple                           | covered | saga_crash_recovery: fallback |
| L9.13 | Recovery fallback is ambiguous                           | covered | saga_crash_recovery: ambiguous |
| L9.14 | Copy (not move) variant                                  | covered | cross_account_move_saga: copy |
| L9.15 | Intra-account move on Gmail (label swap)                 | covered | gmail_label_semantics |
| L9.16 | Concurrent saga for same source UID                      | deferred A | — |
| L9.17 | WAL disk full                                            | deferred A | — |
| L9.18 | WAL database corrupted at startup                        | deferred A | — |
| L9.19 | Clock skew between server and IMAP servers               | deferred A | — |

**Deferred reason A** — Operational/resource-exhaustion failures not
tied to business semantics. Handled by unit tests + operator alerting.

---

## L10 — Secret store

| ID     | Error case                                    | Status  | Reference |
|--------|-----------------------------------------------|---------|-----------|
| L10.1  | file_dir: key present, value read successfully | covered | oauth2_bootstrap (implicit) |
| L10.2  | file_dir: key missing → None                  | covered | caller_authentication: missing token |
| L10.3  | file_dir: directory does not exist             | deferred A | — |
| L10.4  | file_dir: file not readable (permission)       | deferred A | — |
| L10.5  | env_var: variable set                          | covered | secret_store_backends |
| L10.6  | env_var: variable unset                        | covered | secret_store_backends |
| L10.7  | env_var: put() attempted (read-only)           | covered | secret_store_backends |
| L10.8  | gpg_file: decryption succeeds                  | covered | secret_store_backends |
| L10.9  | gpg_file: wrong passphrase                     | covered | secret_store_backends |
| L10.10 | gpg_file: decryption binary missing             | deferred A | — |

**Deferred reason A** — Operational OS-level failures covered by
server-module unit tests.

---

## L11 — Audit log

| ID     | Error case                                        | Status  | Reference |
|--------|---------------------------------------------------|---------|-----------|
| L11.1  | Successful ALLOW record shape                     | covered | audit_log_format |
| L11.2  | DENY record without leaking sender                | covered | audit_log_format |
| L11.3  | Hash chain intact                                  | covered | audit_log_format |
| L11.4  | Hash chain broken by tampering                     | covered | audit_log_format |
| L11.5  | Hash chain across day rotation                     | covered | audit_log_format |
| L11.6  | Search query hashed, not cleartext                  | covered | audit_log_format |
| L11.7  | Sender domain hashed in DENY                        | covered | audit_log_format |
| L11.8  | Audit log file permissions (0600 / 0400 / 0700)     | covered | audit_log_format |
| L11.9  | Current audit file deleted out-of-band              | covered | audit_retention |
| L11.10 | External root-hash hook invoked                     | covered | audit_retention |
| L11.11 | fsync failure mid-write                              | deferred A | — |

**Deferred reason A** — Operational failure covered by an OS-level
unit test.

---

## L12 — Configuration loading

| ID     | Error case                                            | Status  | Reference |
|--------|-------------------------------------------------------|---------|-----------|
| L12.1  | Valid configuration loads on startup                  | covered | (implicit in every feature Background) |
| L12.2  | YAML syntax error                                     | deferred A | — |
| L12.3  | Whitelist mode with non-NONE default                  | covered | whitelist_folder_mode |
| L12.4  | Blacklist mode with NONE default                       | covered | blacklist_folder_mode |
| L12.5  | Whitelist folder with cap rule                         | covered | whitelist_folder_mode |
| L12.6  | Blacklist folder with grant rule                       | covered | blacklist_folder_mode |
| L12.7  | Unreachable rule (grant NONE in whitelist)             | covered | whitelist_folder_mode |
| L12.8  | Unknown predicate in rule                              | covered | sender_rule_matcher |
| L12.9  | SIGHUP reload with valid changes                       | covered | policy_reload |
| L12.10 | SIGHUP reload with broken config (rollback)            | covered | policy_reload |
| L12.11 | New folder added at runtime (unknown to IMAP)           | covered | policy_reload |
| L12.12 | Account added at runtime requiring rebootstrap          | covered | policy_reload |
| L12.13 | Removing an account drains its connection pool          | covered | policy_reload |

**Deferred reason A** — Generic YAML parse failure handled uniformly
by pydantic; one negative scenario will cover the class.

---

## L13 — Connection pool / IMAP session

| ID     | Error case                                      | Status  | Reference |
|--------|-------------------------------------------------|---------|-----------|
| L13.1  | Acquire from empty pool creates new connection  | deferred B | — |
| L13.2  | Acquire reuses a healthy pooled connection      | deferred B | — |
| L13.3  | Acquire skips a connection that fails NOOP      | deferred B | — |
| L13.4  | Acquire times out when pool is exhausted         | deferred B | — |
| L13.5  | Max-age reached, connection force-closed         | deferred B | — |
| L13.6  | Idle TTL reached, connection closed               | deferred B | — |
| L13.7  | Token refresh drains the pool                     | deferred B | — |
| L13.8  | Long-lived IDLE session not recycled by pool      | deferred B | — |

**Deferred reason B** — The connection pool has no separately
observable behaviour at the MCP surface; it affects latency and log
entries. Unit-tested in the server module. If a behaviour becomes
externally observable (e.g. a `pool_drain` audit event that a test
needs to verify), the entry moves to covered.

---

## L14 — Gmail-specific semantics

| ID     | Error case                                          | Status  | Reference |
|--------|-----------------------------------------------------|---------|-----------|
| L14.1  | describe_policy flags Gmail accounts                | covered | gmail_label_semantics |
| L14.2  | Duplicate message across labels surfaces via UID    | covered | gmail_label_semantics |
| L14.3  | list_labels on Google account                       | covered | gmail_label_semantics |
| L14.4  | list_labels refused on non-Google account            | covered | gmail_label_semantics |
| L14.5  | Intra-account move is a label swap                   | covered | gmail_label_semantics |
| L14.6  | Cross-account saga fetches from All Mail              | covered | gmail_label_semantics |
| L14.7  | System folder [Gmail]/Trash is policy-addressable     | covered | gmail_label_semantics |
| L14.8  | Move target is a Gmail label currently empty           | deferred A | — |
| L14.9  | Move target includes the label `All Mail` directly    | deferred A | — |
| L14.10 | Concurrent label rewrite from outside imap-mcp        | deferred A | — |

**Deferred reason A** — Edge cases for Gmail that require deep
knowledge of Gmail's label model side effects. Will be covered by
integration tests against a real Gmail account (gated out of the BDD
suite by config).

---

## Process

This document is updated in the same MR that:
- adds a new ADR that expands the architecture,
- adds or removes a Feature-File scenario,
- reveals an error path during implementation.

Spec audit (Guideline §13) verifies that the table numbers are honest
— the audit agent cross-references the "covered" column with the
existing feature files.
