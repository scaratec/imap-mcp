# LIM 0015: Attachment sink cleanup is out of scope

- **Status:** Accepted
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-06-08
- **Date approved:** 2026-06-08
- **Proposed by:** Randy Nel Gupta
- **Approved by:** Randy Nel Gupta
- **Related ADRs:** [ADR-0028](../adr/0028-attachment-file-sink-delivery.md)

## Resolution intent

`permanent`. Lifecycle management of files the server wrote into
an operator-controlled directory is an operator-side concern.
Embedding retention logic in the server would require a
retention policy schema, a clock-driven sweep, and an audit
story for "the server deleted a file you might still want" —
all complexity that operators already have well-understood
tools for (cron, systemd timers, find -mtime, tmpfiles.d).

## Context

[ADR 0028] specifies that `fetch_attachment` writes decoded
attachment bytes to a configured directory on the server's
filesystem. The server returns the filename and considers the
operation complete. The file persists on disk indefinitely
unless the operator removes it.

Re-fetching the same attachment produces the same filename (the
hash component is `md5(bytes)`, idempotent), so a re-fetch
overwrites in place rather than accumulating duplicates. Every
*distinct* attachment a caller ever fetches leaves a file
behind.

## Nature of the weakness

A busy caller can grow the sink directory monotonically. There
is no server-side retention, no expiration timestamp on files,
no on-startup sweep that removes anything older than N days,
no warning when free space drops below a threshold. The
server's only interaction with sink contents is "write a new
file" — it does not list, does not stat for cleanup, does not
delete.

Operators who never clean up will eventually fill the
filesystem. The server's first noticeable response to that is
`ENOSPC` on the next `fetch_attachment`, surfaced as
`sink_not_writable` with the errno in `error.detail`.

## Why the clean solution is not chosen

A retention policy embedded in the server must answer:

- **Time-based, size-based, or count-based?** Each is a
  different config schema, each has edge cases.
- **What is the audit story when the server deletes a file?**
  The original `fetch_attachment` call is in the audit log
  saying "this file was created at /path/X". A later silent
  deletion creates a gap a forensic reviewer must reconcile
  by reading retention config alongside the audit log.
- **What happens during a deletion mid-read?** If the caller
  is reading a file and the retention sweep removes it, the
  caller sees a partial read. The server-side coordination
  to prevent that is open-file-handle counting, which the
  server does not otherwise track.
- **Who is authorized to override?** Retention "for this file
  only" is the kind of escape hatch every retention design
  inevitably gets pulled toward, and every escape hatch is a
  policy-bypass primitive.

None of these is unsolvable; all of them are out of scope for
an IMAP-mcp project whose mandate is attachment delivery, not
attachment lifecycle.

## Mitigations in place

- Re-fetch is idempotent. A caller that re-pulls the same
  attachment does not double-write the disk. The growth
  pattern is "one file per distinct attachment ever
  fetched", not "one file per fetch call".
- The audit log records the absolute path of every file
  written. An operator can drive a cleanup script from the
  audit log itself: parse `saved_to_absolute`, filter by
  age, `unlink`.
- Standard OS tooling does the job well: `find <sink>
  -mtime +30 -delete` in cron; `systemd-tmpfiles` with a
  `D!` line; a `tmpfiles.d` snippet shipped alongside the
  server package as a sample operator artefact (TODO,
  separate piece of work).
- `sink_not_writable` (ENOSPC) is a clear, structured
  diagnostic; an operator who is paged by it can immediately
  reconcile against the file count in the sink directory.

## Residual risk

An unattended deployment fills its sink filesystem and
`fetch_attachment` starts failing for every call. The failure
mode is loud (every call returns `sink_not_writable`), the
remediation is mechanical (delete old files), but the
downtime window between "disk full" and "operator notices" is
the operator's responsibility to bound through monitoring.

A worse case is a deployment where the sink is on the same
filesystem as the server's WAL, audit log, or other state. A
full sink can prevent the server from writing its audit log,
which is a correctness issue beyond `fetch_attachment`. The
recommended deployment in the README will say "give the sink
its own filesystem or quota" — but that recommendation does
not enforce itself.

## Triggers for revisit

- A real-world deployment hits a disk-full incident that the
  out-of-band cron approach did not catch in time, and the
  operator argues that server-side retention would have
  helped.
- The project gains a need for in-flight file lifecycle for a
  different reason (per-call temp files, per-session
  scratch space) and a unified retention story becomes
  worth the complexity.

## References

- [ADR-0028](../adr/0028-attachment-file-sink-delivery.md) —
  attachment file sink; this LIM names the lifecycle gap.
- [LIM-0013](0013-single-attachment-sink-path.md),
  [LIM-0014](0014-attachment-sink-requires-caller-filesystem-access.md),
  [LIM-0016](0016-attachment-sink-path-disclosed-in-tool-description.md)
  — sibling sink-related LIMs.
