# ADR 0056: Canonical Special-Use Folder Resolution

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0025](0025-folder-path-contract-and-error-taxonomy.md) and [ADR 0033](0033-gmail-special-use-folder-resolution.md) addressed Modified UTF-7 wire quoting and localized Gmail special-use folder names (`[Gmail]/Entwürfe`, `[Gmail]/Papierkorb`, `[Gmail]/Alle Nachrichten`), yet two historical issues required a clean replacement:

1. [ADR 0033](0033-gmail-special-use-folder-resolution.md) amended [ADR 0025](0025-folder-path-contract-and-error-taxonomy.md) in place instead of superseding it cleanly.
2. Malformed Modified UTF-7 tokens returned by non-compliant IMAP servers in `LIST` lacked an explicit fallback and audit rule.

## Decision

We translate **all mailbox names between decoded UTF-8 on the MCP/policy/audit/WAL surface and RFC 3501-quoted Modified UTF-7 on the IMAP wire**, and resolve **RFC 6154 special-use folder aliases lazily from the very first call on granted Google accounts**.

### Rules

1. **UTF-8 surface and mUTF-7 wire boundary:** Every MCP argument, response, policy entry, WAL record, and audit entry uses UTF-8 folder paths. Wire IMAP commands encode mailbox names in Modified UTF-7 with RFC 3501 quoting. If an upstream `LIST` entry contains malformed Modified UTF-7, the decoder preserves the raw ASCII token without failing `list_folders` and writes a chained audit entry (`tool: "mailbox_name_decode"`, `result: "OK"`, `reason: "mutf7_decode_fallback"`).
2. **Session-start RFC 6154 alias resolution on Google accounts:** On the first folder-touching call for a granted `google` account, the server queries `LIST` once to map special-use flags (`\All` -> `[Gmail]/All Mail`, `\Drafts` -> `[Gmail]/Drafts`, `\Sent` -> `[Gmail]/Sent Mail`, `\Trash` -> `[Gmail]/Trash`, `\Spam` -> `[Gmail]/Spam`, `\Flagged` -> `[Gmail]/Starred`) to the account's localized wire paths.
3. **Canonical normalization and conflict refusal:** Both canonical (`[Gmail]/Drafts`) and localized (`[Gmail]/Entwürfe`) names are accepted in policies and caller inputs, normalized to canonical paths before policy evaluation, and echoed canonically in all responses. Identical duplicate policy entries collapse cleanly; discrepant entries for the same physical special-use folder fail closed with `DENY / policy_alias_conflict` before mailbox selection.

## Consequences

### Positive

- **Locale-independent policies and first-call reliability.** Canonical `[Gmail]/*` names work from call 1 on any Gmail locale without a warm-up `list_folders` call.
- **Fail-closed alias safety.** Conflicting canonical and localized rules can never bypass restrictions.

### Negative

- Policies containing discrepant duplicate entries for one Gmail special-use folder are denied until consolidated.

### Neutral

- Closed `reason` and `error.type` code sets are governed separately in [ADR 0062](0062-closed-reason-and-error-code-registry.md).

## Security Implications

- **Attack surface.** RFC 3501 quoting and mUTF-7 encoding block wire-command injection via crafted folder names.
- **Trust boundaries.** Rejects conflicting alias rules (`policy_alias_conflict`) instead of picking the wider grant.
- **Data exposure.** Prevents raw mUTF-7 byte sequences from leaking into responses or audit logs.
- **Failure modes.** Malformed upstream mUTF-7 degrades safely without crashing folder discovery.
- **Auditability.** Audit logs record canonical UTF-8 folder names and log `mutf7_decode_fallback` on malformed wire names ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Require `list_folders` before canonical Gmail names resolve.** Rejected: caused production failures whenever agents invoked `create_draft` or `search` first.
- **Reject localized caller inputs and accept canonical names only.** Rejected: breaks callers when users ask about localized folder names (`Entwürfe`, `Papierkorb`).
- **Resolve conflict by taking the intersection of duplicate entries.** Rejected: hides operator configuration mistakes silently instead of surfacing `policy_alias_conflict`.

## References

- Supersedes [ADR 0025](0025-folder-path-contract-and-error-taxonomy.md) and [ADR 0033](0033-gmail-special-use-folder-resolution.md).
- [ADR 0040](0040-message-centric-policy-and-gmail-composition.md), [ADR 0044](0044-normalized-envelope-and-code-registry.md), [ADR 0062](0062-closed-reason-and-error-code-registry.md).
- RFC 3501 §5.1.3, RFC 6154.
