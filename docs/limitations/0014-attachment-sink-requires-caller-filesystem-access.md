# LIM 0014: Attachment sink requires caller filesystem access

- **Status:** Accepted
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-06-08
- **Date approved:** 2026-11-17
- **Proposed by:** Randy Nel Gupta
- **Approved by:** Randy Nel Gupta
- **Scope:** server/src/imap_mcp/handlers/fetch.py, deployment
- **Related ADRs:** [ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md), [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md)
- **Related Guidelines:** BDD Guidelines §4.3 (persistence validation), §6.1 (isolation)

## Resolution intent

`permanent`. File-sink delivery ([ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md))
writes decoded attachment bytes to `<base_dir>/<caller_id>/<artifact_id>_<name>.<ext>`
(`0400`) and returns `saved_to` and `saved_path`. The MCP caller must
share read access to that filesystem path (local workstation or shared
read-only pod `emptyDir` mount under [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md));
remote network-separated callers without a shared mount cannot read the
resulting file.

## Context

Inline base64 attachment payloads overflow LLM context windows on real-world
PDFs and archives. [ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md)
delivers attachments via a local/shared filesystem directory instead.

## Nature of the weakness

If an operator deploys `imap-mcp` on host A and connects an MCP client
running on host B with no shared filesystem at `saved_path`,
`fetch_attachment` succeeds on host A (`ALLOW + OK`) while the client on
host B cannot open `saved_path`.

## Why the clean solution is not chosen

Both supported deployment topologies — local workstation `stdio` and
co-located Kubernetes pod sidecar ([ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md),
`127.0.0.1` loopback + shared `/sink` `emptyDir`) — guarantee co-located
filesystem visibility by construction. Adding an authenticated HTTP blob
streaming server would widen network attack surface for an unsupported
remote-client topology.

## Mitigations in place

- `fetch_attachment` returns the canonical `saved_path` and relative
  `saved_to` to `FULL`-authorized callers ([ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md)).
- Loopback HTTP binding (`127.0.0.1`, [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md))
  prevents remote hosts from connecting by default.
- `docs/ops/kubernetes-sidecar-deployment.md` documents the `readOnly: true`
  `/sink` `emptyDir` mount on the agent container.

## Residual risk

A Kubernetes manifest that omits the `volumeMounts` entry for `/sink` on
the `agent` container while mounting `/sink` on `imap-mcp` causes the
agent to get `ENOENT` when opening `saved_path`.

## Triggers for revisit

- Any future ADR authorizing non-loopback remote MCP clients over the
  network.

## References

- [ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md), [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md).
- [LIM-0015](0015-attachment-sink-cleanup-out-of-scope.md).
