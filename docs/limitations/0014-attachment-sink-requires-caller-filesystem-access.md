# LIM 0014: Attachment sink requires caller filesystem access

- **Status:** Accepted
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-06-08
- **Date approved:** 2026-06-08
- **Proposed by:** Randy Nel Gupta
- **Approved by:** Randy Nel Gupta
- **Related ADRs:** [ADR-0028](../adr/0028-attachment-file-sink-delivery.md)

## Resolution intent

`permanent`. The constraint is inherent to the file-sink
delivery model: server writes a file; agent reads a file. If
the two cannot reach a common filesystem, the model does not
apply. This is not something `imap-mcp` can fix from inside —
it is a property of the deployment topology.

## Context

[ADR 0028] specifies that `fetch_attachment` writes the decoded
bytes to a directory on the server's filesystem and returns the
filename. The agent (the MCP caller) then reads the file
directly. The contract implicitly requires that the server and
the caller process see the same filesystem at the same path.

In a typical local deployment — stdio transport, server and
agent on the same host as the same user, or as different users
with a shared writable directory — this is trivially true. In
other deployments it is not.

## Nature of the weakness

`fetch_attachment` returns a filename without checking whether
the calling agent has any way to read it. The server cannot
discover that the agent is

- a remote MCP client over HTTP transport on a different host,
- a container without a volume mount that bridges to the sink
  directory,
- a user account that has no read permission on the directory
  the server (running as a different user) wrote into.

In each case the `fetch_attachment` call appears to succeed
(`result: "OK"`, `saved_to: "filename.pdf"`), but the agent
cannot open the file. The failure surfaces on the agent side
as `FileNotFoundError` or `PermissionError`, not as anything
the server can diagnose or audit.

## Why the clean solution is not chosen

Detecting from the server that the caller cannot read the
written file requires either

- a probe channel through the MCP transport that asks the
  caller to read back a test byte (extends every MCP client
  with a new capability the protocol does not currently
  provide), or
- per-deployment configuration that names the caller's
  filesystem context and lets the server reason about access
  (operator must catalog every caller's filesystem view, on
  every host, with every container layer).

Both options push complexity into territory the server
deliberately stays out of. The simpler stance: document the
constraint, surface it to the operator at deployment time, and
let the operator verify that server and agent share the path.

## Mitigations in place

- The configured sink path is named explicitly in the
  `fetch_attachment` tool description (see [LIM-0016]). An
  operator setting up a new deployment will see the path and
  can verify reachability from the agent before issuing the
  first real `fetch_attachment`.
- The sink health check ([ADR 0028] §3) tells the agent the
  path is writable from the server's perspective on every
  `list_tools` and every `fetch_attachment` call. This
  catches the half of the problem the server can see.
- Recommended deployment shape in `README` (TODO): server and
  agent run as the same OS user on the same host, or with a
  shared volume mount, or with explicit ACLs that grant the
  agent's identity read access to the sink directory.

## Residual risk

A multi-host deployment — server on a remote machine, agent on
the user's laptop — silently produces files the agent will
never open. The agent reports success to the user; the user
expects an attachment that is not there. The failure mode is
invisible to the server's audit log because the read never
reaches the server. Operator must diagnose by inspecting
agent-side logs (FileNotFoundError) and reconciling against
the sink directory contents.

Worst-realistic case: an operator deploys `imap-mcp` as a
hosted service for an agent on a different machine, configures
a sink directory the remote agent has no way to reach, and
the deployment appears to work until someone actually tries to
read an attachment.

## Triggers for revisit

- A real-world incident report where this constraint is the
  documented root cause of an operator-facing problem.
- A future MCP transport extension that provides a portable
  bytes-back channel (file-resource API with native read
  semantics across host boundaries).
- Adoption of a deployment pattern where remote-host operation
  becomes the common case rather than the exception.

## References

- [ADR-0028](../adr/0028-attachment-file-sink-delivery.md) —
  attachment file sink; this LIM names a precondition the ADR
  cannot itself enforce.
- [LIM-0013](0013-single-attachment-sink-path.md) — the
  related single-path constraint.
- [LIM-0016](0016-attachment-sink-path-disclosed-in-tool-description.md)
  — the related path-disclosure constraint.
