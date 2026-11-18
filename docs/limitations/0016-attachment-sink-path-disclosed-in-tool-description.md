# LIM 0016: Attachment sink path is disclosed in the tool description

- **Status:** Resolved
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-06-08
- **Date approved:** 2026-11-17 (reclassified from permanent and paid down in 3.0.0)
- **Date resolved:** 2026-11-17
- **Proposed by:** Randy Nel Gupta
- **Approved by:** Randy Nel Gupta
- **Resolution commit:** 8324738, 366c013
- **Scope:** server/src/imap_mcp/dispatch.py, server/src/imap_mcp/handlers/fetch.py
- **Related ADRs:** [ADR-0028](../adr/0028-attachment-file-sink-delivery.md), [ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md)
- **Related Guidelines:** BDD Guidelines §1.2 (observable behaviour)

## Resolution intent

`must-resolve` (reclassified 2026-11-17 by owner from `permanent` and paid down in `3.0.0`). The original static-tool-description rationale was superseded by [ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md), which omits host paths from `tools/list` and returns `saved_path` solely inside `FULL`-authorized `fetch_attachment` responses.

## Context

[ADR 0028] specifies that the configured `attachment_sink_directory`
is rendered into the `fetch_attachment` tool description, which
is returned to every caller on every `list_tools` call. The
agent needs the path to read the files; per-call repetition
in the response was rejected on token-budget grounds (one
absolute path multiplied across a bulk-style fetch loop is
real waste). The description is the alternative.

Every caller that has the right to call `list_tools` therefore
sees the operator's chosen sink directory path. In a
single-caller, single-tenant deployment that is the same
information the caller would have figured out anyway. In a
multi-caller deployment it is filesystem-layout information
about the operator's host that every caller learns regardless
of whether that caller will ever invoke `fetch_attachment`.

## Nature of the weakness

The disclosed string is the operator's filesystem path —
something like `/home/operator/imap-attachments/` or
`/var/lib/imap-mcp/sink/`. From this an attacker who
compromises any one caller learns:

- The OS account the server is running as (paths under
  `/home/X/` reveal user X).
- A small piece of the operator's filesystem layout.
- A directory that is, by construction, world-readable or
  at least caller-readable, and that holds whatever
  attachments any caller has fetched.

The disclosure is one-way: the server tells the caller, the
caller cannot influence what the server tells. There is no
attack vector that uses the disclosure to *change* server
behaviour. The risk is purely informational.

## Why the clean solution is not chosen

Hiding the path while keeping the file-sink delivery model
requires one of:

- **Per-call path in response** — costs tokens linearly in
  bulk workflows (the exact concern [ADR 0028] §4 cites).
- **Out-of-band path discovery channel** — invents an MCP
  extension or a separate config-fetch tool. Adds complexity
  for an informational disclosure most deployments do not
  consider sensitive.
- **Per-caller virtualized paths** — server presents a fake
  path like `/imap-sink/` in the description and maps it
  internally to the real one. Now there are two paths in
  every audit record, two ways to express "where did this
  file go", and no mechanism for the caller to actually
  read the file because the fake path is not real.

The information value of the path is low for any operator
who has chosen a sink that is not also a sensitive directory.
The cost of any hiding mechanism is high. The slim choice is
disclosure plus an operator-side guidance note.

## Mitigations in place

- Operators who consider the path itself sensitive can place
  the sink in a directory whose name is not informative
  (`/srv/imap-sink/`, not `/home/finance-team/secrets/`).
- The path is the only disclosure: contents are visible only
  to processes with filesystem permission. The disclosure
  does not grant read access; it grants knowledge of where
  to look if access already exists.

## Residual risk

Prior to `3.0.0`, callers without `FULL` visibility learned the operator's configured host sink directory path from `tools/list`. Eliminated once `ADR-0047` removed the path from `tools/list`.

## Triggers for revisit

- Any compliance review forbidding host filesystem path disclosure prior to per-message `FULL` authorization (triggered and resolved on 2026-11-17).

## Resolution note

Resolved in `3.0.0` by [ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md): `list_tools` publishes a static contract description without any host filesystem path, and `saved_path` is returned solely inside the `fetch_attachment` response after `FULL` visibility authorization has succeeded.

## References

- [ADR-0028](../adr/0028-attachment-file-sink-delivery.md), [ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md).
- [LIM-0013](0013-single-attachment-sink-path.md),
  [LIM-0014](0014-attachment-sink-requires-caller-filesystem-access.md),
  [LIM-0015](0015-attachment-sink-cleanup-out-of-scope.md)
  — sibling sink-related LIMs.
