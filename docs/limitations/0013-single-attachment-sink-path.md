# LIM 0013: Single attachment sink path

- **Status:** Accepted
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-06-08
- **Date approved:** 2026-06-08
- **Proposed by:** Randy Nel Gupta
- **Approved by:** Randy Nel Gupta
- **Related ADRs:** [ADR-0028](../adr/0028-attachment-file-sink-delivery.md)
- **Related Guidelines:** none specific

## Resolution intent

`permanent`. This is a tool-surface slimness decision the project
holds indefinitely. Multi-sink support would multiply tool
arguments, multiply audit fields, and re-introduce the caller-
chosen-path attack vector that the single-sink design takes off
the table. There is no future state in which "multiple sink
directories" becomes the right answer for this server.

## Context

[ADR 0028] introduces an attachment file sink: `fetch_attachment`
writes the decoded bytes to a server-side directory and returns
the filename. The configuration entry `attachment_sink_directory`
holds exactly one absolute path.

The shape was deliberate. A multi-sink design — per account, per
caller, per folder, per content type — was on the table and
rejected. This LIM records the rejection so future contributors
do not re-litigate it as an obvious convenience.

## Nature of the weakness

Operators with use cases like "write invoice attachments to
`/accounting/`, write everything else to `/inbox/`" or "give
each caller its own sink subtree" cannot express that in the
server config. They have one directory; every successful
`fetch_attachment` lands there regardless of which caller,
which account, or which content type produced it.

Mixed-tenancy deployments cannot use directory permissions to
isolate caller A's attachments from caller B's. If both callers
are authorized to invoke `fetch_attachment`, both see whatever
either of them wrote to the shared directory.

## Why the clean solution is not chosen

A multi-sink configuration must answer four design questions
that this project chooses not to answer:

1. **How does the caller select a sink at call time?** A tool
   argument re-introduces the caller-chosen-path attack vector
   the single-sink design eliminates. An implicit selection
   (e.g. by `content_type`) embeds policy in code, not config.
2. **How does the operator express "all PDFs to X, all images
   to Y"?** Any expressive syntax (`{content_type_pattern,
   target}` lists; routing DSLs) is a small policy language
   inside the server config, with its own validation and
   audit footprint.
3. **How does the audit log reason about a per-caller subtree?**
   The audit record already names the absolute path written.
   Per-caller subtrees mean the operator must correlate
   filesystem paths back to caller identity to answer "who
   pulled what".
4. **What changes for the agent's tool-description-based
   discovery?** The current design names the one path in the
   tool description. Multi-sink would either expose every
   destination (information leak about server layout) or
   none (and the agent could not know where its files went).

Each of these is solvable. None of them is worth the surface
growth for an IMAP-mcp project whose core mandate is
auditable access to mail. The slim surface is a feature, not
an accident.

## Mitigations in place

- Operators who need routing can layer a downstream rule on the
  sink directory: a `inotify`-based mover, a cron sweeper, a
  systemd path unit. The server writes one place; the operator
  routes from there.
- Per-caller isolation can be approximated by running one server
  process per caller, each with its own
  `attachment_sink_directory`. The MCP transport already
  supports per-caller server processes (stdio is one-process-per-
  client by construction).
- For deployments where strict per-caller isolation matters
  more than configuration economy, deploy as in the previous
  point or wrap the server in a per-caller container. The
  filesystem isolation then comes from the operator's
  container runtime, not from the server's config schema.

## Residual risk

A single-tenant operator with mixed content types accumulates
all attachment types in one directory. A multi-tenant operator
who configures one server for both callers exposes attachments
across the caller boundary at the filesystem layer. The worst
case is the multi-tenant misconfiguration: a finance caller
pulls a confidential PDF, a marketing caller has read access
to the same directory and can list and read it. Both callers
were authorized at the MCP layer; the filesystem flattens
them onto the same shelf.

## Triggers for revisit

- A second user request for per-caller or per-content-type
  routing arrives with concrete details. (One request is
  documented today, not enough.)
- A real-world multi-tenant deployment of this server is
  attempted, the operator hits the shared-directory
  exposure described above, and the per-process workaround
  proves operationally infeasible.
- A future MCP transport gains a session-scoped resource
  channel that obviates the file sink entirely, in which case
  this whole ADR/LIM cluster gets reconsidered.

## References

- [ADR-0028](../adr/0028-attachment-file-sink-delivery.md) —
  attachment file sink; this LIM bounds it.
- [LIM-0014](0014-attachment-sink-requires-caller-filesystem-access.md)
  — the related "agent needs filesystem access" constraint.
- [LIM-0016](0016-attachment-sink-path-disclosed-in-tool-description.md)
  — the related path-disclosure constraint.
