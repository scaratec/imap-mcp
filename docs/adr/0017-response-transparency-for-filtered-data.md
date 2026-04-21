# ADR 0017: Response Transparency for Policy-Filtered Data

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

Silent redaction is dangerous when the caller is an LLM. If the
server filters a search result from ten hits down to three without
saying so, the agent's downstream reasoning treats "three" as
complete. It concludes "this vendor only sent three invoices" and
acts on that conclusion. The policy did its job — the data did not
leak — but the caller reached a wrong conclusion from the filtered
view.

Mere transparency is not enough either. Revealing "rule
`from_domain: bank.de` blocked 7 messages" would disclose the policy's
internals, which may themselves be sensitive. The transparency
contract must tell the caller *that* filtering happened and *broadly
why*, without telling it *what was filtered* or the *specific rule*.

Three axes need a consistent design:

- **Counts.** A caller who sees N of M must know M exists.
- **Categorical reasons.** A refusal should state the category
  (folder hidden, sender not whitelisted, ...) without revealing
  rule details.
- **Self-inspection.** A caller should have a first-class way to ask
  "what am I actually allowed to see?" so it does not discover its
  constraints through a sequence of silent refusals.

## Decision

Every MCP response honours a **transparency contract** composed of
three elements: hidden counts, categorical reason codes, and a
`describe_policy` meta-tool.

### 1. Hidden counts

Responses that list or aggregate objects include the count of hidden
peers alongside the visible ones. Names and identifiers of hidden
objects are not disclosed — only the cardinality.

| Tool | Transparency addition |
|------|-----------------------|
| `list_accounts` | `hidden_accounts_count: int` |
| `list_folders`  | `hidden_folders_count: int` (per account) |
| `folder_stats`  | `visible_count`, `hidden_count`, `visibility_level` |
| `search`        | `matched_total`, `matched_visible`, `filtered_out` |

### 2. Categorical reason codes

Every PDP decision carries a reason code drawn from a closed
vocabulary. DENY responses expose the code; redacted ALLOW responses
expose it alongside the partial data.

| Code | Trigger |
|------|---------|
| `rule_matched`            | ALLOW: a sender rule matched and granted the required level |
| `folder_default_applied`  | ALLOW: no sender rule matched but folder default sufficed |
| `folder_hidden`           | DENY: no FolderPolicy for this folder (default NONE) |
| `account_hidden`          | DENY: no AccountPolicy for this account |
| `sender_not_whitelisted`  | DENY in whitelist mode, no rule matched |
| `sender_blacklisted`      | DENY in blacklist mode, rule capped to NONE |
| `visibility_below_<level>`| DENY: matched but granted level is below the tool's minimum |
| `capability_missing`      | DENY: write tool required a capability not granted on the folder |
| `auth_failed`             | DENY: caller authentication could not be resolved |

The vocabulary is closed: new reasons require a new ADR or an
amendment. Policy rule identifiers, match patterns, and folder names
of hidden targets are never included.

### 3. Response field flags on partial content

`fetch_envelope`, `fetch_headers`, `fetch_body`, and `fetch_attachment`
include the fields they could compute and flag those they could not:

```json
{
  "uid": 42,
  "from": "…",
  "subject": "…",
  "body": null,
  "visibility_applied": "ENVELOPE",
  "redacted_fields": ["headers", "body", "attachments"],
  "redaction_reason": "visibility_below_BODY"
}
```

A tool whose minimum level is not met refuses entirely (no partial
payload) with an error response carrying the same `redaction_reason`.

### 4. `describe_policy` meta-tool

Callers invoke `describe_policy()` at session start to understand
their own scope. It returns (schematically):

```json
{
  "caller_id": "invoice-agent",
  "tool_set_version": "1.0",
  "accounts": [
    {
      "id": "gupta-scaratec",
      "semantics": "imap-standard",
      "token_cache": "persist_all",
      "folders_visible": [
        {
          "path": "INBOX/Rechnungen",
          "mode": "whitelist",
          "default_visibility": "NONE",
          "max_visibility": "FULL",
          "capabilities": ["mark_seen", "mark_tagged", "move_out"],
          "sender_rules_count": 3
        }
      ],
      "hidden_folders_count": 4
    }
  ],
  "hidden_accounts_count": 2,
  "tool_set_available": ["list_folders", "search", "..."]
}
```

`describe_policy` shows the caller's *own* profile only; it never
leaks the shape of other callers' policies. It names visible folders
and summarizes rule counts, but does not include the rules
themselves.

### 5. Auditability of transparency

Each redacted response emits an audit record ([ADR 0021]) recording
the reason code. Counts of hidden peers are not logged (they change
with the target mailbox's state and are not security-relevant on
their own), but reason codes are.

## Consequences

### Positive

- **LLM callers reason correctly.** The agent knows the scope of
  what it does not know.
- **Prompt-engineered strategies become feasible.** Agent authors
  can instruct "if `hidden_count > 0`, reply 'this may be
  incomplete' rather than asserting totals".
- **No silent leaks via counts.** The reason code vocabulary is
  closed and vetted; it communicates categorical information only.
- **Self-inspection is a first-class feature.** `describe_policy`
  means an agent never has to probe the policy through refusals.

### Negative

- **Response schemas are bigger.** Every reply carries transparency
  fields, even when nothing was filtered. Acceptable; the payload
  remains small relative to message bodies.
- **Operators must design reason codes deliberately.** Adding a new
  DENY category is an ADR-sized decision.
- **Hidden counts can themselves leak structural information.** An
  attacker who can issue queries and observe `hidden_count` deltas
  could infer bulk mailbox structure over time. This is tolerable
  for the primary threat model (compromised agent, not malicious
  remote user with interactive query budget); documented.

### Neutral

- `describe_policy` is the only meta-tool that must be callable
  without any visibility grants — it is the foundation the caller
  uses to know what else it can do.

## Security Implications

- **Minimum-information principle.** Callers learn that filtering
  happened and *why categorically* — enough to avoid false
  conclusions, not enough to reconstruct the policy.
- **No rule identifiers exposed.** A bug that leaked rule IDs could
  let an attacker probe policy structure. The contract prohibits
  their inclusion in any response field.
- **No names of hidden objects.** Hidden folders and accounts are
  counted, not named. An LLM aware of "there are 4 folders I cannot
  see" cannot name them and cannot target further probes at them.
- **Reason codes are category-only.** `sender_blacklisted` tells
  the caller its sender is on a blacklist, but not which rule or
  what pattern matched. An attacker probing which senders are
  blacklisted learns only that their specific choice is — which
  they already know, since they chose it.
- **Auditability closes a review gap.** Every DENY has a reason
  code in the audit log; the operator can cross-reference without
  relying on the caller's self-report.

## Alternatives Considered

- **Full silence (no transparency fields).** Rejected; produces
  confident hallucinations in LLM callers.
- **Full disclosure (show matched rule id, matched pattern).**
  Rejected; leaks policy internals that may themselves be sensitive.
- **Transparency only on DENY, not on partial ALLOW.** Rejected;
  partial-response `redacted_fields` flag is the exact mechanism by
  which an LLM learns "the body was not served" and can respond
  accordingly.
- **Disclose hidden item names, not just counts.** Rejected; folder
  or account names can themselves be information ("Banking", "Legal
  Disputes"), and disclosure undermines default-deny.
- **Present transparency only via `describe_policy`, not per-
  response.** Rejected; callers frequently discover constraints only
  in context of a specific message or folder, which
  `describe_policy` alone cannot anticipate.

## References

- [ADR 0001] — policy hierarchy that produces decisions.
- [ADR 0002] — visibility levels cited in `visibility_applied`.
- [ADR 0003] — whitelist / blacklist reasons.
- [ADR 0005] — capabilities, and the `capability_missing` reason.
- [ADR 0016] — the tool surface that emits transparency fields.
- [ADR 0021] — audit format that includes reason codes.

[ADR 0001]: 0001-default-deny-hierarchical-policy.md
[ADR 0002]: 0002-linear-visibility-levels.md
[ADR 0003]: 0003-whitelist-blacklist-folder-modes.md
[ADR 0005]: 0005-per-folder-write-capabilities.md
[ADR 0016]: 0016-mcp-tool-set.md
[ADR 0021]: 0021-audit-log-format.md
