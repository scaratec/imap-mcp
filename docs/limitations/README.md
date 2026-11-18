# Limitation Records

A Limitation Record documents an **accepted, bounded weakness** of the
system — a place where the project has consciously decided to live with
less than the BDD Guidelines (v1.8.0) or the [ADRs](../adr/) would
otherwise demand.

This directory is **not** a general-purpose debt log. It is a narrow,
high-friction mechanism with explicit governance.

## Default posture

> **Every problem is solved cleanly per the BDD Guidelines and the
> ADRs. A Limitation Record is the documented exception, never the
> norm.**

## Governance

1. **No self-approval.** Every live `Accepted` or `Mitigated` record must
   be reviewed and explicitly approved by the project owner (`Approved by`
   + `Date approved`).
2. **No silent acceptance.** Shortcuts not recorded here are bugs, not
   limitations.
3. **Every field is filled.** [`TEMPLATE.md`](TEMPLATE.md) defines the
   mandatory metadata and sections (`Scope`, `Resolution commit` for
   `Resolved`, `Withdrawal commit` for `Withdrawn`, etc.).

## Lifecycle

```text
Proposed -> Accepted -> (Mitigated -> Resolved)
                     -> Superseded by LIM-NNNN
         -> Withdrawn (paid down before owner approval)
```

## Numbering & naming

- `NNNN-short-kebab-case-title.md`, numbered sequentially starting at
  `0001`. Numbers are never reused (`0011` is `bulk_move`; the historical
  duplicate search-performance record was renumbered to `0023`).
- `0000` is reserved for this meta-document.

## Index

| # | Title | Status | Intent | Approved by |
|---|---|---|---|---|
| 0001 | [Reason-code symmetry in BDD contract tests](0001-reason-code-symmetry-in-bdd.md) | Superseded by [LIM-0024](0024-reason-code-symmetry-in-contract-tests.md) | must-resolve | Randy Nel Gupta (2026-04-21) |
| 0002 | [Gmail scenarios not runnable against current fixture](0002-gmail-scenarios-not-runnable.md) | Resolved (`fb23b4e`) | must-resolve | Randy Nel Gupta (2026-04-21) |
| 0003 | [OAuth2 scenarios reference a nonexistent mock provider](0003-oauth2-scenarios-not-runnable.md) | Resolved (`fb23b4e`) | must-resolve | Randy Nel Gupta (2026-04-21) |
| 0004 | [IMAP fault injection via in-process env var](0004-imap-fault-injection-via-env-var.md) | Resolved (`a03955a`) | must-resolve | Randy Nel Gupta (2026-04-21) |
| 0005 | [IMAP MITM scenarios deferred](0005-imap-mitm-scenarios-deferred.md) | Resolved (`8695a67`) | must-resolve | Randy Nel Gupta (2026-04-21) |
| 0006 | [5-tuple fallback identity deferred](0006-5-tuple-fallback-deferred.md) | Resolved (`33ade8d`) | must-resolve | Randy Nel Gupta (2026-04-21) |
| 0007 | [HTTP transport deferred](0007-http-transport-deferred.md) | Resolved (`a03955a`) | must-resolve | Randy Nel Gupta (2026-04-21) |
| 0008 | [SIGHUP policy reload deferred](0008-sighup-policy-reload-deferred.md) | Resolved (`fb23b4e`) | must-resolve | Randy Nel Gupta (2026-04-21) |
| 0009 | [Audit day-roll and retention deferred](0009-audit-day-roll-deferred.md) | Resolved (`fb23b4e`) | must-resolve | Randy Nel Gupta (2026-04-21) |
| 0010 | [Production IMAPS TLS handshake not exercised by local BDD stack](0010-production-imap-bdd-gaps.md) | Accepted | must-resolve | Randy Nel Gupta (2026-11-17) |
| 0011 | [bulk_move tool deferred](0011-bulk-move-deferred.md) | Accepted | must-resolve | Randy Nel Gupta (2026-06-05) |
| 0012 | [Wire-level response transparency uneven across tools](0012-wire-response-transparency-uneven-across-tools.md) | Withdrawn (`8324738`) | must-resolve | n/a (paid down in 3.0.0) |
| 0013 | [Single attachment sink path](0013-single-attachment-sink-path.md) | Resolved (`8324738`) | must-resolve | Randy Nel Gupta (2026-11-17) |
| 0014 | [Attachment sink requires caller filesystem access](0014-attachment-sink-requires-caller-filesystem-access.md) | Accepted | permanent | Randy Nel Gupta (2026-11-17) |
| 0015 | [Attachment sink cleanup is out of scope](0015-attachment-sink-cleanup-out-of-scope.md) | Accepted | permanent | Randy Nel Gupta (2026-11-17) |
| 0016 | [Attachment sink path is disclosed in the tool description](0016-attachment-sink-path-disclosed-in-tool-description.md) | Resolved (`8324738`) | must-resolve | Randy Nel Gupta (2026-11-17) |
| 0017 | [Expired OAuth refresh token not proactively or durably detectable](0017-expired-oauth-token-not-proactively-or-durably-detectable.md) | Withdrawn (`8324738`) | must-resolve | n/a (paid down in 3.0.0) |
| 0018 | [OAuth re-authentication cannot be initiated from the MCP surface](0018-oauth-reauth-not-initiable-from-mcp-no-remediation-guidance.md) | Accepted | permanent | Randy Nel Gupta (2026-11-17) |
| 0019 | [Policy integrity requires an out-of-process trust boundary](0019-policy-integrity-requires-out-of-process-trust-boundary.md) | Mitigated | permanent | Randy Nel Gupta (2026-11-17) |
| 0020 | [Audit chain fragments across per-agent sidecar instances](0020-audit-chain-fragments-across-per-agent-instances.md) | Accepted | permanent | Randy Nel Gupta (2026-11-17) |
| 0021 | [OAuth interactive bootstrap and secret-projection writes are out-of-pod](0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md) | Accepted | permanent | Randy Nel Gupta (2026-11-17) |
| 0022 | [Externally edited drafts become non-editable by handle](0022-externally-edited-drafts-lose-their-handle.md) | Accepted | permanent | Randy Nel Gupta (2026-11-17) |
| 0023 | [Search performance on large mailboxes](0023-search-performance-large-mailbox.md) | Resolved (`e94f308`) | must-resolve | Randy Nel Gupta (2026-05-07) |
| 0024 | [Structural reason-code symmetry in black-box contract tests](0024-reason-code-symmetry-in-contract-tests.md) | Mitigated | permanent | Randy Nel Gupta (2026-11-17) |

## Relationship to ADRs

ADRs record *decisions that shape the system*. Limitation Records
document *accepted weaknesses in the chosen direction*. An ADR can
introduce a capability; a Limitation Record only bounds an accepted
exception.
