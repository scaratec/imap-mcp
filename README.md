# imap-mcp

A security-focused [Model Context Protocol](https://modelcontextprotocol.io/) server
that exposes IMAP mailboxes to LLM agents under strict, auditable access control.

## Status

**Design phase.** This repository currently contains architectural decision records
(ADRs) and specifications only. No runnable code yet.

## Scope

`imap-mcp` is intended to sit between one or more IMAP accounts and one or more
LLM agents. It enforces:

- **Per-account, per-folder access control** — every call is checked against a
  declarative policy before it reaches the IMAP layer.
- **Sender-scoped visibility levels** — whitelist or blacklist rules decide
  whether a caller sees a message's full body, only its envelope, only metadata,
  or nothing at all.
- **Transactional cross-account move/copy** — IMAP has no native cross-server
  transaction; this server implements a WAL-backed saga with idempotent retry.
- **OAuth2 (XOAUTH2)** — first-class support for Google, Microsoft, and
  self-hosted providers alongside classic password auth.
- **Append-only audit log** — every decision (allow and deny) is recorded.

See [`docs/adr/`](docs/adr/) for the architectural reasoning behind each of
these choices.

## Why this exists

Giving an LLM raw IMAP access is unsafe: a mailbox contains invoices, contracts,
health records, legal correspondence. Fine-grained, declarative policy with
default-deny semantics is a prerequisite for using such an agent in production.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).
