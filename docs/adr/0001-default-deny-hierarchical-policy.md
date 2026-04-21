# ADR 0001: Default-Deny Hierarchical Policy Model

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

`imap-mcp` mediates LLM access to IMAP mailboxes. A mailbox is not a uniform
resource: within a single account, folders hold invoices, private correspondence,
medical records, banking notifications, and casual chatter side by side. Within
a single folder, some senders are fully trustworthy to expose to an agent and
others must remain invisible.

Two structural questions must be answered before any other policy feature can
be specified:

1. **What is the default** when no rule matches a given request?
2. **At what level(s) of granularity** can a policy be expressed?

The answers shape every later design choice: how configuration is written, how
decisions are audited, and how safely the system degrades when a rule is
missing, malformed, or ambiguous.

The threat model drives the answers. An LLM agent is not a traditional
authenticated user: it may be manipulated by prompt injection, it may compose
requests the operator did not foresee, and it lacks reliable judgment about
what it should not ask for. A policy model that leaks on absence of a rule is
unsafe for this class of caller.

## Decision

We adopt a **three-level hierarchical policy model with default-deny at every
level**. Every incoming tool call is evaluated by a Policy Decision Point (PDP)
before any IMAP operation is performed.

The hierarchy is:

```
AccountPolicy        (may this caller see this account at all?)
  └── FolderPolicy   (may this caller see this folder of that account?)
        └── SenderRule[]  (which messages within that folder, at what level?)
```

Evaluation rules:

1. **Deny is the default at every level.** A caller with no matching
   `AccountPolicy` sees zero accounts. A caller with access to an account but
   no `FolderPolicy` for a given folder sees that folder as non-existent. A
   folder with `FolderPolicy` but no matching `SenderRule` for a given message
   treats that message according to the folder's declared default — which is
   itself either deny (whitelist mode) or a bounded non-zero level (blacklist
   mode). See [ADR 0003] for that distinction.
2. **Lower levels cannot widen higher ones.** A `FolderPolicy` cannot grant
   access to an account the caller's `AccountPolicy` denies. A `SenderRule`
   cannot grant a visibility level the `FolderPolicy` does not permit.
3. **Every PDP evaluation produces an auditable decision record**, whether it
   allows or denies. See [ADR 0007] for the audit log format.
4. **Policy evaluation is synchronous and happens in-process before the IMAP
   call is made.** The IMAP layer has no knowledge of callers or policy.

The visibility levels available at each rule — from `NONE` through `FULL` — are
defined in [ADR 0002]. The whitelist-vs-blacklist semantics of the default
within a folder are defined in [ADR 0003]. This ADR fixes only the
*hierarchical structure* and the *default-deny principle*.

[ADR 0002]: 0002-folder-scoped-visibility-levels.md
[ADR 0003]: 0003-whitelist-blacklist-folder-modes.md
[ADR 0007]: 0007-audit-log-format.md

## Consequences

### Positive

- **Fail-safe by construction.** A missing, misnamed, or broken rule causes
  *less* access, never more. This is the correct failure direction for a
  security-enforcement component.
- **Locality of reasoning.** Each level can be understood and reviewed in
  isolation. An operator asking "can the invoice agent read folder X?" needs
  to read at most three rules.
- **Composable with per-caller identity.** Different callers (different LLM
  agents) can be granted different policy sets over the same accounts without
  duplicating configuration.
- **Auditability is cheap.** Because every decision flows through one PDP,
  logging is a single cross-cutting concern rather than a per-tool feature.

### Negative

- **New folders require explicit onboarding.** A folder created on the IMAP
  server is invisible to all callers until a `FolderPolicy` is added. This is
  intentional but operationally real: bulk folder creation on the server side
  will not be automatically usable.
- **Policy authoring is the critical path.** Mistakes in the policy file are
  the most likely cause of "why can't my agent see X?" incidents. Good
  tooling (validation, dry-run, diff) is required and is future work.

### Neutral

- The PDP decides *whether* and *at what level* a message may be seen.
  Actually *applying* that level — redacting fields, omitting attachments — is
  a separate component (the Redaction Layer). This separation is a deliberate
  choice, not a side effect of this ADR.

## Security Implications

- **Attack surface.** The hierarchy moves all authorization logic into one
  component (the PDP). That component becomes a high-value target for bugs; it
  must be minimal, heavily tested, and free of I/O. The upside is that no
  other layer needs to make authorization decisions, eliminating scattered
  checks where mistakes typically live.
- **Trust boundaries.** The MCP-facing boundary is where caller identity is
  established; the PDP boundary is where the caller's rights are resolved;
  the IMAP boundary is a pure mechanism layer that trusts its inputs. These
  three boundaries are distinct and documented.
- **Data exposure.** Default-deny guarantees that an un-enumerated mailbox,
  folder, or sender is invisible. The opposite (default-allow) would mean
  that every newly-created folder on the server is immediately LLM-readable
  until someone remembers to restrict it — unacceptable for a mailbox that
  may hold contracts or medical data.
- **Failure modes.** A malformed policy file should fail closed: refuse to
  start rather than start with degraded enforcement. An unreachable PDP
  during a hot-reload should freeze evaluation rather than bypass it. These
  behaviours are normative, not best-effort.
- **Auditability.** Because the PDP sees every request, the audit log can
  record a full decision trail including the matching rule. A missing rule
  produces a `DENY` record with reason `no_matching_rule`, which is itself a
  signal: repeated such denials from the same caller indicate either a
  misconfigured policy or a caller probing beyond its scope.
- **Prompt-injection resistance.** An LLM cannot escalate its own access by
  composing a cleverer request: the policy is bound to the caller identity,
  not to the content of the request.

## Alternatives Considered

- **Default-allow with explicit denies.** Rejected. The entire purpose of the
  server is to constrain a caller that cannot be trusted to self-limit;
  requiring an exhaustive deny list for each new folder or sender inverts the
  burden of proof and is guaranteed to leak over time.
- **Flat RBAC without hierarchy** (a role maps directly to a set of messages).
  Rejected as insufficiently expressive: the natural units of access — accounts
  and folders — disappear, and every new folder requires touching every role.
- **Per-message ACLs.** Rejected as unmanageable. A mailbox contains thousands
  of messages, and the ones that need the same treatment almost always share
  a folder or a sender. ACLs reconstruct by brute force what the hierarchy
  expresses natively.
- **No in-process policy; enforcement in the caller.** Rejected. The caller is
  an LLM or an LLM-driven agent. Asking it to enforce its own access controls
  defeats the point of having an MCP server as a trust boundary.
- **Two-level hierarchy (account + folder only)**, with sender filtering
  deferred to Redaction. Rejected because sender rules frequently need to
  decide *whether a message is visible at all*, not merely what to redact.
  That belongs in the PDP, not in Redaction.

## References

- Saltzer & Schroeder, *The Protection of Information in Computer Systems*
  (1975), principle of fail-safe defaults.
- [RFC 4314](https://www.rfc-editor.org/rfc/rfc4314) — IMAP ACL extension,
  reviewed and found to be too coarse for the per-sender granularity this
  system requires.
- [OASIS XACML 3.0](https://docs.oasis-open.org/xacml/3.0/xacml-3.0-core-spec-os-en.html)
  — the PDP/PEP terminology is borrowed from XACML; the policy language here
  is deliberately much narrower.
