# LIM 0015: Attachment sink cleanup is out of scope

- **Status:** Accepted
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-06-08
- **Date approved:** 2026-11-17
- **Proposed by:** Randy Nel Gupta
- **Approved by:** Randy Nel Gupta
- **Scope:** server/src/imap_mcp/handlers/fetch.py, deployment
- **Related ADRs:** [ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md), [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md)
- **Related Guidelines:** BDD Guidelines §6.2 (housekeeping)

## Resolution intent

`permanent`. Lifecycle cleanup of downloaded attachment artifacts in
`<base_dir>/<caller_id>/` is delegated to the operating environment
(`emptyDir` pod lifecycle in Kubernetes, `systemd-tmpfiles` or `find
-mtime` on workstations) rather than an in-server deletion sweeper.

## Context

Under [ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md),
each `fetch_attachment` call writes a fresh exclusive `0400` file named
`<artifact_id>_<sanitized_base>.<ext>` inside `<base_dir>/<caller_id>/` so
that `O_CREAT | O_EXCL | O_NOFOLLOW` prevents symlink and overwrite races.

## Nature of the weakness

Repeatedly fetching attachments on a long-lived workstation without periodic
directory cleanup accumulates read-only artifact files on disk until free
space runs out (`ENOSPC` -> `sink_not_writable`).

## Why the clean solution is not chosen

An in-server background sweeper unlinking files from `<base_dir>/<caller_id>/`
could delete an attachment while the downstream agent is still reading it
and duplicates standard OS tmpfile management (`systemd-tmpfiles`, pod
`emptyDir` size limits and restart reclamation).

## Mitigations in place

- In Kubernetes sidecar deployments ([ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md)),
  `/sink` is a pod-scoped `emptyDir` isolated from `/state` (`audit/` and
  `wal.db`), so attachment growth can never exhaust audit/WAL storage and
  is reclaimed automatically on pod rollout.
- On `ENOSPC`, `fetch_attachment` unlinks any incomplete file before
  returning `ALLOW + ERROR` (`sink_not_writable`, [ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md)).
- `README.md` documents mtime-based cleanup (`find <sink> -type f -mtime +30 -delete`).

## Residual risk

An unattended workstation sink directory without a cleanup timer can
eventually fill its filesystem partition and return `sink_not_writable`.

## Triggers for revisit

- Operational incidents of `sink_not_writable` (`ENOSPC`) caused by
  uncleaned attachment artifacts.

## References

- [ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md), [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md).
- [LIM-0014](0014-attachment-sink-requires-caller-filesystem-access.md).
