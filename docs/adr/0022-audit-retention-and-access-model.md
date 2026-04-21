# ADR 0022: Audit Retention and Access Model

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

The audit format of [ADR 0021] prescribes *how* records are written.
Three operational questions remain:

- **How long are records kept?** Indefinite retention is a liability
  (the audit log carries personally-identifying information —
  caller IDs, account names, folder paths); zero retention defeats
  forensic usefulness.
- **Who reads the log, and via what mechanism?** An in-band tool
  would turn the audit surface into an attack surface; a
  separately-privileged interface is safer but raises the question
  of what that interface is.
- **Is tamper-evidence local-only, or is integrity anchored off-
  host?** Hash chains are useful but bounded by the attacker's
  access; true tamper-resistance requires an external witness.

## Decision

### Retention

Records progress through three states by age, configurable per
deployment with sensible defaults:

| Stage    | Default | State |
|----------|---------|-------|
| Hot      | 0–90 days | uncompressed JSONL, readable by tools |
| Warm     | 91–365 days | gzipped (`YYYY-MM-DD.jsonl.gz`), still local |
| Expired  | > 365 days | deleted by the audit writer at day-roll |

Retention boundaries are enforced by the audit writer when it
rotates at UTC midnight: files that have crossed `hot_days` are
gzipped and chmod `0400` remains; files that have crossed
`hot_days + warm_days` are unlinked.

Configuration:

```yaml
audit:
  directory: ~/.local/state/imap-mcp/audit
  hot_days: 90
  warm_days: 275            # hot + warm = 365
  delete_after_days: 365
  # optional:
  external_root_hook:
    type: shell
    command: /usr/local/bin/post-audit-root-hash.sh
```

Non-default retention settings are logged at startup (as an
internal `policy_reload`-shaped event) so the operational record
of the server's own settings is itself audited.

### Access model

- **No MCP tool reads the audit log.** Callers cannot, under any
  role, request audit contents through the protocol.
- **Filesystem access** is the primary read path. The log files are
  owned by the server's user and have mode `0400` (closed days) or
  `0600` (current day). Operators tail, `jq`, or aggregate as they
  see fit.
- **Operator CLI** (future, out of V1 scope of this server but
  interface-compatible): a separate binary `imap-mcp-audit` offers
  convenience operations — `tail`, `verify-chain`, `query`, `stats`.
  Not shipped in V1; the filesystem interface suffices.
- **Log-aggregator integration** (Loki, ClickHouse, OpenSearch) is
  accomplished by tailing the JSONL files with standard log
  shippers (Vector, Fluent Bit, Promtail). The server does not
  ship logs; it writes them in a format any shipper can consume.
- **No network listener for the audit stream** in V1.

### Integrity and off-host anchoring

- The hash chain of [ADR 0021] is the local tamper-evidence
  mechanism.
- **Optional external root-hash hook.** At day rotation, the
  server emits the `final_hash` of the closed file to a
  configured external hook (shell command, HTTP endpoint,
  future pub/sub target). The hook's output is not retained by
  the server; it is the operator's responsibility to route it to
  a destination that preserves it (signed Git commit, append-
  only bucket, mail to a compliance address).
- **Integrity verification** is an offline process: `imap-mcp-
  audit verify-chain` (operator CLI, out of V1 scope) reads the
  set of files, walks the chain, confirms `prev_hash` and
  `final_hash` consistency, and compares to the external
  anchors if present.

### Data-protection considerations

- The audit log is a **personal-data asset** in the GDPR sense. It
  carries pseudonymous caller IDs, account identifiers, and
  references to mail metadata owned by a natural person.
- The default 365-day retention is a pragmatic balance between
  forensic usefulness and data-minimization requirements.
  Operators who need longer retention document the justification
  in their own compliance record; operators who need shorter
  retention reduce `warm_days` and/or `delete_after_days`.
- **Subject-access requests** are satisfied by the data controller
  (the operator), not by the server. The log's schema makes it
  straightforward to filter by `args_summary.account` or
  `caller_id`.

## Consequences

### Positive

- **Bounded footprint.** Disk usage is predictable: roughly
  linear in tool-call volume, capped at `delete_after_days`.
- **Zero attack surface over the network.** The audit log has no
  remote read interface. An attacker on the MCP channel cannot
  probe audit state.
- **Aggregator-friendly.** Standard log-shipping tools work
  without modification.
- **Optional off-host tamper resistance.** The hook is a small
  extension point that enables significant integrity guarantees
  when operators invest in them.
- **Compliance-compatible defaults.** 365 days matches typical
  security-forensics retention without violating data-minimization
  norms.

### Negative

- **Operator CLI is future work.** In V1, offline verification of
  the hash chain requires a small script the operator writes (or
  a one-liner with `jq`). Documented.
- **Retention changes require server restart** (or `SIGHUP`).
  Acceptable; retention is not a hot-tuned parameter.
- **No single-audit view across imap-mcp and imap-agent** during
  the transition described in [ADR 0020]. Aggregators resolve this
  by ingesting both.

### Neutral

- Gzipping warm files is a CPU cost at day roll; negligible for
  realistic volumes.

## Security Implications

- **No in-band audit read.** An attacker who controls a caller
  cannot list, read, or probe the audit stream through MCP.
- **Filesystem permission discipline** is relied on. Deployments
  that share the server's user account with other processes
  widen the audit's readable surface. Document and discourage.
- **Retention limits loss surface.** Old logs are deleted; their
  compromise in far-future breaches is impossible if they no
  longer exist.
- **External root-hash hook is optional but important.** Without
  it, an attacker with local-root access can re-hash the chain.
  With it, any such rewrite is detectable off-host. Operators
  handling regulated mail content should enable it.
- **No secret exposure.** The audit log contains no keys,
  tokens, or message content ([ADR 0021]). A full audit dump is
  sensitive metadata, not secret material.
- **GDPR awareness by default.** The 365-day ceiling forces a
  conversation with operators who need longer retention; no
  silent accumulation of multi-year behavioural records.

## Alternatives Considered

- **Indefinite retention.** Rejected on data-minimization grounds.
  Operators who need indefinite retention configure it
  explicitly and own the consequences.
- **Ship a remote audit read API.** Rejected categorically; audit
  is a privileged surface and should not be bridged over the
  network by the server itself. Operators who want remote
  access put a read-only agent on the audit directory.
- **Write to syslog / journald instead of files.** Rejected; no
  per-file rotation, harder to checksum, platform-dependent
  semantics. JSONL files are the simplest portable choice.
- **Required off-host anchoring.** Rejected for V1 as too opinionated
  about operator infrastructure. Available as optional hook; made
  mandatory in a future ADR if the threat model tightens.
- **Operator CLI in V1.** Nice to have, not blocking. The
  interface contract is documented so the CLI can arrive later
  without retrofitting the log.

## References

- [ADR 0020] — `imap-agent` audit is separate and remains so.
- [ADR 0021] — audit log format that this retention model governs.
- GDPR Art. 5(1)(e) — storage limitation principle.

[ADR 0020]: 0020-imap-agent-as-future-client.md
[ADR 0021]: 0021-audit-log-format.md
