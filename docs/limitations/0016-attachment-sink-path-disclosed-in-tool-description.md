# LIM 0016: Attachment sink path is disclosed in the tool description

- **Status:** Accepted
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-06-08
- **Date approved:** 2026-06-08
- **Proposed by:** Randy Nel Gupta
- **Approved by:** Randy Nel Gupta
- **Related ADRs:** [ADR-0028](../adr/0028-attachment-file-sink-delivery.md)

## Resolution intent

`permanent`. The disclosure is structural: the agent must know
where to read files; the tool description is the one place the
MCP protocol gives us to put static, per-tool documentation.
Any design that hides the path either hides it from the agent
too (defeating the sink's purpose) or invents a separate
out-of-band discovery channel (re-creating the problem the
file sink solves).

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
- README guidance (TODO) will recommend choosing a sink
  path that does not encode sensitive identifiers in the
  directory name.

## Residual risk

A multi-tenant deployment where caller A is trusted with
attachment data and caller B is not — but both call
`list_tools` to discover their available tools — leaks the
sink path to caller B. Caller B cannot read the files
(filesystem permissions), but learns that caller A's files
are at a knowable location. If caller B later gains
filesystem access by a separate vulnerability, the path is
already known and the lateral move is one `ls` command away.

The worst plausible scenario is a misconfigured multi-caller
deployment combined with a separate filesystem-read
vulnerability in caller B. Neither alone is sufficient; both
together would be.

## Triggers for revisit

- An operator scenario where the sink-path information itself
  is rated as sensitive enough to justify a hiding mechanism
  (e.g. a compliance audit that explicitly prohibits
  filesystem-layout disclosure across tenants).
- An MCP protocol extension that adds caller-scoped tool
  metadata, so the description can vary per caller, at which
  point the path could be omitted from descriptions for
  callers that do not have `fetch_attachment` granted by
  policy.

## References

- [ADR-0028](../adr/0028-attachment-file-sink-delivery.md) —
  attachment file sink; this LIM names the disclosure trade-
  off the ADR makes.
- [LIM-0013](0013-single-attachment-sink-path.md),
  [LIM-0014](0014-attachment-sink-requires-caller-filesystem-access.md),
  [LIM-0015](0015-attachment-sink-cleanup-out-of-scope.md)
  — sibling sink-related LIMs.
