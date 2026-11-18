# ADR 0054: Generic Mail-Mediation Scope Boundary

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

`imap-mcp` operates alongside downstream workflow agents (such as invoice-processing or legal-correspondence assistants) that classify documents, run OCR or LLM extraction, and write business records into external databases. Embedding domain-specific extraction or workflow state into the mail-mediation server would widen its attack surface, import complex document parsers into the credential boundary, and compromise its reusability. [ADR 0020](0020-imap-agent-as-future-client.md) established this separation for V1 but contained private repository URLs and omitted the attachment-sink and audit persistence paths.

## Decision

We keep `imap-mcp` **strictly domain-agnostic**: all business classification, OCR, document extraction, and workflow orchestration live in external MCP clients, while `imap-mcp` persists only read-only policy/account configuration, secret-store credentials, `wal.db` (sagas, provenance, account health), `audit/*.jsonl`, and per-caller read-only attachment artifacts.

### Scope boundary

1. **No domain-specific tools or document parsers.** `imap-mcp` exposes no invoice, accounting, calendar, or CRM tools, and runs no LLM, OCR, PDF, or office-document rendering libraries inside the server process.
2. **No business workflow database.** `wal.db` stores exclusively saga transactions ([ADR 0046](0046-content-verified-sagas-and-wal-leases.md)), draft provenance ([ADR 0042](0042-cryptographic-and-wal-provenance.md)), and durable `needs_rebootstrap` health ([ADR 0059](0059-oauth2-pkce-and-durable-account-health.md)).
3. **Downstream pipelines are untrusted MCP callers.** Automated agents connect over stdio or loopback HTTP subject to the exact same PDP, non-interference, flow-monotonicity, and audit rules as interactive chat clients.

## Consequences

### Positive

- **Minimal dependency and memory-safety surface.** No PDF, image, or office codecs execute inside the credential-holding mediator.
- **Domain-independent reuse.** One server binary serves accounting, legal, engineering, and personal triage agents solely via distinct policies.

### Negative

- Downstream clients parse downloaded files from their isolated read-only attachment directory ([ADR 0047](0047-per-caller-symlink-safe-attachment-delivery.md)) in their own process.

### Neutral

- Shared Python packaging conventions ([ADR 0061](0061-python-runtime-and-strict-verification-stack.md)) require zero cross-package imports between `imap-mcp` and downstream clients.

## Security Implications

- **Attack surface.** Excludes complex document parsers from the process holding IMAP credentials and `provenance_hmac_key`.
- **Trust boundaries.** Keeps downstream extraction engines outside the mailbox authorization boundary.
- **Data exposure.** Enumerates every on-disk persistence path (`config`, `secrets`, `wal.db`, `audit/`, `attachment_sink/<caller_id>/`) for uniform encryption and backup.
- **Failure modes.** Compromising or crashing a downstream OCR/LLM worker cannot expose IMAP credentials or bypass folder/message policies.
- **Auditability.** All mail accesses by automated pipelines are recorded in `imap-mcp`'s hash-chained audit trail ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Embed PDF/invoice text extraction inside `fetch_attachment`.** Rejected: couples domain schemas to the mediator and brings high-risk parsers into the credential process.
- **Optional in-process OCR plugin behind a feature flag.** Rejected: optional native parser dependencies still expand supply-chain and container image exposure.
- **Share a PostgreSQL database with downstream agent workflows.** Rejected: creates cross-service schema coupling and breaks single-caller sidecar isolation ([ADR 0051](0051-loopback-kubernetes-sidecar-topology.md)).

## References

- Supersedes [ADR 0020](0020-imap-agent-as-future-client.md).
- [ADR 0047](0047-per-caller-symlink-safe-attachment-delivery.md) — per-caller attachment delivery.
- [ADR 0048](0048-pre-effect-audit-and-test-boundary.md) — audit persistence.
- [ADR 0061](0061-python-runtime-and-strict-verification-stack.md) — Python runtime stack.
