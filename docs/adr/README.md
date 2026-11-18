# Architecture Decision Records

This directory contains the architectural decision records (ADRs) for `imap-mcp`.

An ADR captures a single architectural decision, the context that forced it,
and its consequences. ADRs are **immutable once accepted** — a later decision
does not edit an earlier one; it supersedes it and links back.

## Format

All ADRs in this repository follow the format defined in
[`TEMPLATE.md`](TEMPLATE.md). The format is based on
[Michael Nygard's original proposal][nygard], extended with two mandatory
sections:

- **Security Implications** — required because this project's entire purpose is
  access control over sensitive data.
- **Alternatives Considered** — required (at least three alternatives) so any
  reader can see what was rejected and why.

[nygard]: https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions

## Numbering & naming

- ADRs are numbered sequentially, zero-padded to four digits: `0001`, `0002`, …
- File name: `NNNN-short-kebab-case-title.md`
- `0000` is reserved for the meta-ADR that establishes the ADR process itself.
- Numbers are never reused. A deprecated or superseded ADR keeps its number.

## Lifecycle

```text
Proposed  ->  Accepted  ->  (Deprecated | Superseded by ADR-XXXX)
```

Status transitions are recorded by editing the Status line of the superseded
ADR to link its replacement.

## Writing rules

1. **One decision per ADR.** If you are tempted to describe two, write two.
2. **Present tense, active voice.** "We use X" not "X will be used".
3. **Stand on its own.** Link explicitly to anything required.
4. **Keep it short.** Under 500 lines.
5. **No code.** ADRs describe decisions, not implementations.
6. **Write for a public reader.** Avoid internal shorthand or private links.

## Index

| #    | Title | Status |
|------|-------|--------|
| 0000 | [Record Architecture Decisions](0000-record-architecture-decisions.md) | Accepted |
| 0001 | [Default-Deny Hierarchical Policy Model](0001-default-deny-hierarchical-policy.md) | Superseded by [ADR-0040](0040-message-centric-policy-and-gmail-composition.md) |
| 0002 | [Linear Visibility Levels](0002-linear-visibility-levels.md) | Superseded by [ADR-0043](0043-non-interference-transparency-and-search.md) |
| 0003 | [Explicit Whitelist and Blacklist Folder Modes](0003-whitelist-blacklist-folder-modes.md) | Superseded by [ADR-0040](0040-message-centric-policy-and-gmail-composition.md) |
| 0004 | [Sender Rule Matcher Grammar](0004-sender-rule-matcher-grammar.md) | Superseded by [ADR-0041](0041-direction-aware-rule-grammar-and-provenance.md) |
| 0005 | [Per-Folder Write Capabilities](0005-per-folder-write-capabilities.md) | Superseded by [ADR-0045](0045-write-visibility-floors-and-flow-monotonicity.md) |
| 0006 | [Cross-Account Move via Saga, Native MOVE Within Account](0006-cross-account-move-via-saga.md) | Superseded by [ADR-0046](0046-content-verified-sagas-and-wal-leases.md) |
| 0007 | [SQLite as Write-Ahead Log Store](0007-sqlite-as-wal-store.md) | Superseded by [ADR-0046](0046-content-verified-sagas-and-wal-leases.md) |
| 0008 | [Idempotency via Message-ID with Content-Hash Witness](0008-idempotency-via-message-id-and-hash.md) | Superseded by [ADR-0046](0046-content-verified-sagas-and-wal-leases.md) |
| 0009 | [OAuth2 Authorization-Code Flow with Scope Minimization](0009-oauth2-authorization-code-with-scope-minimization.md) | Superseded by [ADR-0059](0059-oauth2-pkce-and-durable-account-health.md) |
| 0010 | [Configurable Token Cache Strategy](0010-configurable-token-cache-strategy.md) | Superseded by [ADR-0064](0064-durable-account-health-and-token-lifecycle.md) |
| 0011 | [Pluggable Secret Store Backend](0011-pluggable-secret-store-backend.md) | Superseded by [ADR-0050](0050-secret-stores-and-oauth-posture.md) |
| 0012 | [Python 3.11+ Runtime and Library Stack](0012-python-runtime-and-library-stack.md) | Superseded by [ADR-0061](0061-python-runtime-and-strict-verification-stack.md) |
| 0013 | [Hybrid IMAP Connection Pool](0013-hybrid-connection-pool.md) | Superseded by [ADR-0055](0055-bounded-per-account-connection-pool.md) |
| 0014 | [Policy as Git-Versioned YAML with SIGHUP Reload](0014-policy-as-git-versioned-yaml.md) | Superseded by [ADR-0049](0049-single-caller-boundary-and-validated-loading.md) |
| 0015 | [Caller Identity and Authentication](0015-caller-identity-and-authentication.md) | Superseded by [ADR-0049](0049-single-caller-boundary-and-validated-loading.md) |
| 0016 | [MCP Tool Set](0016-mcp-tool-set.md) | Superseded by [ADR-0052](0052-tool-surface-3-0-0-and-non-goals.md) |
| 0017 | [Response Transparency for Policy-Filtered Data](0017-response-transparency-for-filtered-data.md) | Superseded by [ADR-0043](0043-non-interference-transparency-and-search.md) |
| 0018 | [Non-Goal Tool Surface](0018-non-goal-tool-surface.md) | Superseded by [ADR-0052](0052-tool-surface-3-0-0-and-non-goals.md) |
| 0019 | [Gmail Label Semantics](0019-gmail-label-semantics.md) | Superseded by [ADR-0040](0040-message-centric-policy-and-gmail-composition.md) |
| 0020 | [imap-agent as a Future Client, not a Component](0020-imap-agent-as-future-client.md) | Superseded by [ADR-0054](0054-generic-mail-mediation-scope-boundary.md) |
| 0021 | [Audit Log Format](0021-audit-log-format.md) | Superseded by [ADR-0048](0048-pre-effect-audit-and-test-boundary.md) |
| 0022 | [Audit Retention and Access Model](0022-audit-retention-and-access-model.md) | Superseded by [ADR-0048](0048-pre-effect-audit-and-test-boundary.md) |
| 0023 | [Test-Only Control Surface](0023-test-only-control-surface.md) | Superseded by [ADR-0058](0058-closed-test-mode-control-boundary.md) |
| 0024 | [Duration Grammar Single Source](0024-duration-grammar-single-source.md) | Superseded by [ADR-0041](0041-direction-aware-rule-grammar-and-provenance.md) |
| 0025 | [Folder-Path Contract and Error Taxonomy](0025-folder-path-contract-and-error-taxonomy.md) | Superseded by [ADR-0056](0056-canonical-folder-resolution-and-code-registry.md) |
| 0026 | [Tool-Surface Consistency](0026-tool-surface-consistency.md) | Superseded by [ADR-0052](0052-tool-surface-3-0-0-and-non-goals.md) |
| 0027 | [Error Envelope and Tool-Surface Versioning](0027-error-envelope-and-tool-surface-versioning.md) | Superseded by [ADR-0044](0044-normalized-envelope-and-code-registry.md) |
| 0028 | [Attachment File-Sink Delivery](0028-attachment-file-sink-delivery.md) | Superseded by [ADR-0047](0047-per-caller-symlink-safe-attachment-delivery.md) |
| 0029 | [Kubernetes Sidecar Deployment Topology](0029-kubernetes-sidecar-deployment-topology.md) | Superseded by [ADR-0051](0051-loopback-kubernetes-sidecar-topology.md) |
| 0030 | [Draft Identity via In-Message Handle and Revision Counter](0030-draft-identity-and-revisions.md) | Superseded by [ADR-0042](0042-cryptographic-and-wal-provenance.md) |
| 0031 | [Draft Revision via the Message-Rewrite Saga](0031-draft-revision-via-rewrite-saga.md) | Superseded by [ADR-0063](0063-structured-own-draft-composition-and-revision.md) |
| 0032 | [One Audit Chain Shared by Local Server Instances](0032-shared-audit-chain-across-local-instances.md) | Superseded by [ADR-0048](0048-pre-effect-audit-and-test-boundary.md) |
| 0033 | [Gmail Special-Use Folders Resolve on Every Path](0033-gmail-special-use-folder-resolution.md) | Superseded by [ADR-0056](0056-canonical-folder-resolution-and-code-registry.md) |
| 0034 | [Discard a Draft by Its Handle](0034-discard-draft-by-handle.md) | Superseded by [ADR-0063](0063-structured-own-draft-composition-and-revision.md) |
| 0035 | [Draft Body Format Is a Deliberate, Equally Easy Choice](0035-draft-body-format-choice.md) | Superseded by [ADR-0063](0063-structured-own-draft-composition-and-revision.md) |
| 0036 | [Search Reports What Its Window Hid and Accepts Calendar Dates](0036-search-window-transparency-and-calendar-dates.md) | Superseded by [ADR-0043](0043-non-interference-transparency-and-search.md) |
| 0037 | [Trace Payload Modes](0037-trace-payload-modes.md) | Superseded by [ADR-0053](0053-non-interfering-telemetry-and-usability-isolation.md) |
| 0038 | [Traces Carry What Usage Efficiency Needs](0038-traces-for-usage-efficiency.md) | Superseded by [ADR-0053](0053-non-interfering-telemetry-and-usability-isolation.md) |
| 0039 | [A Usability Matrix for Agent Behaviour, Apart from BDD](0039-usability-matrix-for-agent-behaviour.md) | Superseded by [ADR-0060](0060-usability-matrix-evaluation-boundaries.md) |
| 0040 | [Message-Centric Policy Evaluation](0040-message-centric-policy-and-gmail-composition.md) | Accepted |
| 0041 | [Direction-Aware Rule Predicate Grammar](0041-direction-aware-rule-grammar-and-provenance.md) | Accepted |
| 0042 | [Cryptographic Draft Provenance](0042-cryptographic-and-wal-provenance.md) | Accepted |
| 0043 | [Non-Interfering Response Projection](0043-non-interference-transparency-and-search.md) | Accepted |
| 0044 | [Normalized Response Envelope](0044-normalized-envelope-and-code-registry.md) | Accepted |
| 0045 | [Per-Message Write Visibility Floors and Flow Monotonicity](0045-write-visibility-floors-and-flow-monotonicity.md) | Accepted |
| 0046 | [Content-Verified Transfer Sagas](0046-content-verified-sagas-and-wal-leases.md) | Accepted |
| 0047 | [Isolated Symlink-Safe Attachment Delivery](0047-per-caller-symlink-safe-attachment-delivery.md) | Accepted |
| 0048 | [Pre-Effect Hash-Chained Audit Trail](0048-pre-effect-audit-and-test-boundary.md) | Accepted |
| 0049 | [Single-Caller Instance Boundary and Validated Configuration](0049-single-caller-boundary-and-validated-loading.md) | Accepted |
| 0050 | [Pluggable Secret Stores](0050-secret-stores-and-oauth-posture.md) | Accepted |
| 0051 | [Loopback Kubernetes Sidecar Topology](0051-loopback-kubernetes-sidecar-topology.md) | Accepted |
| 0052 | [MCP Tool Surface 3.0.0](0052-tool-surface-3-0-0-and-non-goals.md) | Accepted |
| 0053 | [Non-Interfering OpenTelemetry Tracing](0053-non-interfering-telemetry-and-usability-isolation.md) | Accepted |
| 0054 | [Generic Mail-Mediation Scope Boundary](0054-generic-mail-mediation-scope-boundary.md) | Accepted |
| 0055 | [Bounded Per-Account IMAP Connection Pool](0055-bounded-per-account-connection-pool.md) | Accepted |
| 0056 | [Canonical Special-Use Folder Resolution](0056-canonical-folder-resolution-and-code-registry.md) | Accepted |
| 0057 | [Immutable Received Mail](0057-immutable-received-mail-and-structured-drafts.md) | Accepted |
| 0058 | [Closed Test-Mode Control Boundary](0058-closed-test-mode-control-boundary.md) | Accepted |
| 0059 | [OAuth2 PKCE CLI Bootstrap](0059-oauth2-pkce-and-durable-account-health.md) | Accepted |
| 0060 | [Usability Matrix Evaluation Boundaries](0060-usability-matrix-evaluation-boundaries.md) | Accepted |
| 0061 | [Python 3.11+ Runtime and Strict Verification Stack](0061-python-runtime-and-strict-verification-stack.md) | Accepted |
| 0062 | [Closed Reason and Error Code Registry](0062-closed-reason-and-error-code-registry.md) | Accepted |
| 0063 | [Structured Own-Draft Composition and Revision](0063-structured-own-draft-composition-and-revision.md) | Accepted |
| 0064 | [Durable Account Health and Token Lifecycle](0064-durable-account-health-and-token-lifecycle.md) | Accepted |
