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
  access control over sensitive data. An ADR that does not discuss its security
  impact is incomplete.
- **Alternatives Considered** — required so a future reader (or a public
  contributor) can see what was rejected and why, without having to reconstruct
  it from discussion.

[nygard]: https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions

## Numbering & naming

- ADRs are numbered sequentially, zero-padded to four digits: `0001`, `0002`, …
- File name: `NNNN-short-kebab-case-title.md`
- `0000` is reserved for the meta-ADR that establishes the ADR process itself.
- Numbers are never reused. A deprecated or superseded ADR keeps its number.

## Lifecycle

```
Proposed  →  Accepted  →  (Deprecated | Superseded by ADR-XXXX)
```

- **Proposed** — authored, under discussion, not yet binding.
- **Accepted** — merged to `main`. Binding until deprecated or superseded.
- **Deprecated** — decision no longer applies, but no replacement is in force.
- **Superseded** — replaced by a named later ADR. Both remain in the repository.

Status transitions are themselves recorded: when an ADR moves to Superseded,
edit its Status line and link the replacement. Do not delete the file.

## Writing rules

1. **One decision per ADR.** If you are tempted to describe two, write two.
2. **Present tense, active voice.** "We use X" not "X will be used".
3. **Stand on its own.** A reader should not need prior ADRs to understand
   this one — link explicitly to anything required.
4. **Keep it short.** Under 500 lines. If it grows larger, split it or move
   detail into a linked specification document.
5. **No code.** ADRs describe decisions, not implementations. Pseudo-code for
   illustration is fine; real code belongs in the implementation.
6. **Write for a public reader.** This repository may become public. Avoid
   internal shorthand, named individuals, or private links.

## Index

| #    | Title                                   | Status   |
|------|-----------------------------------------|----------|
| 0000 | [Record Architecture Decisions](0000-record-architecture-decisions.md) | Accepted |
| 0001 | [Default-Deny Hierarchical Policy Model](0001-default-deny-hierarchical-policy.md) | Accepted |
| 0002 | [Linear Visibility Levels](0002-linear-visibility-levels.md) | Accepted |
| 0003 | [Explicit Whitelist and Blacklist Folder Modes](0003-whitelist-blacklist-folder-modes.md) | Accepted |
| 0004 | [Sender Rule Matcher Grammar](0004-sender-rule-matcher-grammar.md) | Accepted |
| 0005 | [Per-Folder Write Capabilities](0005-per-folder-write-capabilities.md) | Accepted |
| 0006 | [Cross-Account Move via Saga, Native MOVE Within Account](0006-cross-account-move-via-saga.md) | Accepted |
| 0007 | [SQLite as Write-Ahead Log Store](0007-sqlite-as-wal-store.md) | Accepted |
| 0008 | [Idempotency via Message-ID with Content-Hash Witness](0008-idempotency-via-message-id-and-hash.md) | Accepted |
| 0009 | [OAuth2 Authorization-Code Flow with Per-Account Scope Minimization](0009-oauth2-authorization-code-with-scope-minimization.md) | Accepted |
| 0010 | [Configurable Token Cache Strategy](0010-configurable-token-cache-strategy.md) | Accepted |
| 0011 | [Pluggable Secret Store Backend](0011-pluggable-secret-store-backend.md) | Accepted |
| 0012 | [Python 3.11+ Runtime and Library Stack](0012-python-runtime-and-library-stack.md) | Accepted |
| 0013 | [Hybrid IMAP Connection Pool](0013-hybrid-connection-pool.md) | Accepted |
| 0014 | [Policy as Git-Versioned YAML with SIGHUP Reload](0014-policy-as-git-versioned-yaml.md) | Accepted |
| 0015 | [Caller Identity and Authentication](0015-caller-identity-and-authentication.md) | Accepted |
| 0016 | [MCP Tool Set](0016-mcp-tool-set.md) | Accepted |
| 0017 | [Response Transparency for Policy-Filtered Data](0017-response-transparency-for-filtered-data.md) | Accepted |
| 0018 | [Non-Goal Tool Surface](0018-non-goal-tool-surface.md) | Accepted |
| 0019 | [Gmail Label Semantics](0019-gmail-label-semantics.md) | Accepted |
| 0020 | [imap-agent as a Future Client, not a Component](0020-imap-agent-as-future-client.md) | Accepted |
| 0021 | [Audit Log Format](0021-audit-log-format.md) | Accepted |
| 0022 | [Audit Retention and Access Model](0022-audit-retention-and-access-model.md) | Accepted |
