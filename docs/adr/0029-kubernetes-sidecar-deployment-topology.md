# ADR 0029: Kubernetes Sidecar Deployment Topology

- **Status:** Proposed
- **Date:** 2026-08-25
- **Deciders:** Randy Nel Gupta

## Context

Every deployment mode this project has documented so far assumes the
server and its caller share one host and one OS user: the orchestrator
launches `imap-mcp` as a stdio subprocess, the attachment sink is a
directory both can reach, and the policy file sits in the caller's own
home directory. [LIM-0019] records what that co-location costs — an
agent with a shell can rewrite the policy that constrains it, which is
not a defect in any module but a property of the topology.

A new requirement has arrived: agents run in Kubernetes pods and need
mailbox access under policy. The naive reading is that this is a
transport problem. It is not. The Streamable HTTP transport has existed
since [LIM-0007] was resolved on 2026-04-29 — Starlette plus uvicorn,
one route at `/mcp`, bearer-token caller authentication per [ADR 0015],
`stateless=True` sessions. A caller on another host can already connect.

The real forces are elsewhere, and they pull in opposite directions:

1. **Policy integrity improves with separation.** [LIM-0019] names
   "run the server on a separate host / VM / container whose filesystem
   the agent has no write path to" as the strongest available remedy.
   A container boundary plus a read-only config mount is exactly that.

2. **Attachment delivery breaks with separation.** [ADR 0028] delivers
   attachment bytes by writing a file to the server's filesystem and
   returning the bare filename. [LIM-0014] names "a container without a
   volume mount" as a case where the caller never reads those bytes —
   and the call still reports `result: "OK"`. The failure is silent on
   both sides.

3. **Multi-caller concurrency is unproven.** The HTTP path has been
   exercised by the BDD suite, not by two agents holding different
   policies against one process. Process-wide state exists (the Gmail
   folder-alias cache, the OAuth token cache, one audit hash chain, one
   WAL) and at least one handler resolves a caller-supplied identifier
   without checking the caller against it.

4. **Restart stops being exceptional.** Rollouts, node drains, evictions
   and OOM kills are routine in a cluster. Any state that only survives
   because the process rarely dies stops surviving.

5. **State forbids horizontal scaling.** [ADR 0007] puts the saga WAL in
   a local SQLite file with an explicit single-writer assumption. The
   audit log ([ADR 0021]) is a hash chain reconstructed from the files
   on disk at startup. Two replicas sharing one volume corrupt both.

A decision is needed now because the shape of the deployment determines
which of these are blockers and which are non-issues, and because
publishing a `Dockerfile` without deciding the topology invites the
worst combination — one shared instance, many callers, no shared sink.

## Decision

**We deploy `imap-mcp` as a per-agent sidecar container inside the
agent's own pod, reachable over the pod-local loopback interface, and we
do not offer a shared cluster-wide instance.**

The unit of deployment is one server instance per agent workload, not
one server for the cluster. Concretely:

### 1. Co-location without co-ownership

The server runs as a second container in the agent's pod. The two share
the pod's network namespace and one explicitly declared volume; they
share nothing else. In particular they do not share a filesystem root,
a process namespace, or a writable path to the configuration.

```
Pod: <agent-workload>
  container: agent
    rw  /sink            <- emptyDir, shared
  container: imap-mcp
    ro  /config          <- ConfigMap (accounts.yaml, callers.yaml, policies/)
    ro  /secrets         <- Secret (caller token, IMAP password or refresh token)
    rw  /state           <- PersistentVolumeClaim (audit/, wal.db)
    rw  /sink            <- emptyDir, shared
```

### 2. Transport is HTTP over pod loopback

Containers in a pod share a network namespace, so `127.0.0.1` reaches
the sidecar from the agent container. The server binds its existing
default:

```
imap-mcp --transport http --host 127.0.0.1 --port 8080
```

The port is never published, no `Service` selects it, no `Ingress`
routes to it, and no `NetworkPolicy` is required to contain it — the
loopback interface is not reachable from outside the pod. Binding to
`0.0.0.0` in this topology is a configuration error.

stdio is not available across a container boundary: it requires a
parent-child process relationship and inherited pipes, which two
containers of a pod do not have. HTTP is therefore mandatory here, and
by [ADR 0015] that makes `auth.type: shared_token` mandatory too. The
token is a `Secret`-mounted file referenced from `callers.yaml`.

### 3. One caller per instance

Each instance is configured with exactly one entry in `callers.yaml`.
An operator who needs a second agent deploys a second sidecar with its
own token, its own policy, and its own state volume.

This is the load-bearing part of the decision. It removes the entire
multi-caller concurrency question from the deployment story rather than
answering it: with one configured caller, process-wide caches cannot
serve the wrong identity, and a handler that fails to bind a
caller-supplied identifier to the calling caller has no second caller
to leak to.

### 4. The sink is a shared `emptyDir`, never the state volume

The attachment sink is mounted at the same path in both containers from
one `emptyDir`. This satisfies [ADR 0028]'s implicit precondition — the
caller and the server see the same filesystem at the same path — and
resolves the [LIM-0014] failure mode for this topology.

The sink must not live on the state volume. [LIM-0015] leaves sink
cleanup to the operator, so an unbounded sink on the state volume would
eventually exhaust the space the audit log needs and take the audit
channel down with it. `emptyDir` is the correct medium precisely
because it is discarded with the pod: the sink is scratch space, and
its lifetime should be the pod's.

Because each instance serves one caller, the sink also stops being
shared between callers. The multi-tenant concerns in [LIM-0013] and
[LIM-0016] — one directory for everyone, path disclosed in the tool
description — do not arise: the path is disclosed to the only caller
entitled to the directory's contents.

### 5. Configuration is read-only and reloaded on SIGHUP

`/config` is a ConfigMap projection mounted read-only. The server reads
it at startup and on `SIGHUP` ([ADR 0014]); it never writes there.

Kubernetes updates a projected ConfigMap by writing a new timestamped
directory and atomically re-pointing the `..data` symlink. The server's
reload path re-reads its configured paths, so it observes the new
content — but kubelet does not send a signal. A ConfigMap edit alone
therefore changes nothing in a running process. [ADR 0014] rejected
file-watch reload for V1 and that rejection stands; the operator picks
one of:

- roll the pod (a `Deployment` annotation keyed to the ConfigMap hash),
  which is the recommended default; or
- ~~run a reload sidecar with `shareProcessNamespace: true` that sends
  `SIGHUP` when `..data` changes.~~ **Withdrawn, 2026-08-25** — see the
  amendment below.

> **Amendment, 2026-08-25 (while writing [ops guide]):** the reload
> sidecar option is withdrawn. `shareProcessNamespace: true` makes every
> container's root filesystem reachable through `/proc/<pid>/root`,
> which gives the agent container a read path into the server
> container's `/config`. That is the trust boundary this whole ADR
> exists to establish ([LIM-0019]), traded away for the convenience of
> not restarting a pod. Rolling the pod on a ConfigMap-content hash is
> now the only sanctioned mechanism; `SIGHUP` by `kubectl exec` remains
> available as a manual operator action.
>
> Note the asymmetry this leaves in place: a bad configuration
> delivered by `SIGHUP` is rejected and the previous one stays in
> force, whereas a bad configuration present at *startup* is fatal,
> because there is no prior good state. A rolled pod with a broken
> ConfigMap therefore `CrashLoopBackOff`s. That is the desired
> behaviour — an immediate, visible failure instead of a silent
> divergence — and it is part of why rolling is preferred.

### 6. State is a per-pod `ReadWriteOnce` volume, and replicas are one

`/state` holds the audit directory and `wal.db`. Both are single-writer
by construction. The workload is therefore a `StatefulSet` with one
replica, or a `Deployment` with `replicas: 1`, `strategy: Recreate`, and
a `ReadWriteOnce` claim. `RollingUpdate` is a configuration error: it
briefly runs two processes against one audit chain.

Because each agent pod carries its own state volume, the audit chain is
per-agent rather than per-cluster. That is a change in the shape of the
audit record set, and it is recorded as [LIM-0020].

### 7. A liveness endpoint is added at `/healthz`

The HTTP surface gains exactly one route beside `/mcp`:

- `GET /healthz` returns `200` with body `{"status":"ok"}` once
  configuration has loaded successfully, and `503` with
  `{"status":"unavailable"}` before that or after a failed reload has
  left no usable configuration.
- It requires no authentication, because a probe has no caller identity
  and kubelet cannot present a bearer token.
- It returns no configuration, no caller list, no account names, no
  version, and no path. The body is the status word and nothing else.
- Every other path continues to return `404`, including every
  `/admin`-shaped path ([ADR 0018]).

This is a deliberate narrowing of the previous invariant "everything
except `/mcp` is 404". The invariant existed to keep an administrative
control surface from appearing by accident. A route that accepts no
input, mutates nothing, and discloses one of two constant strings is
not a control surface, and the alternative — a `tcpSocket` probe —
reports a bound socket as health even when the configuration failed to
load and every tool call would be rejected.

> **Amendment, 2026-08-25 (while writing [ops guide]):** this section
> and §2 were in conflict, and §2 wins. The kubelet performs `httpGet`
> and `tcpSocket` probes by dialling the **pod IP** from the node; §2
> binds the listener to `127.0.0.1`. No kubelet-issued network probe
> can therefore reach this container at all — configured as an
> `httpGet`, the probe fails with connection-refused against a
> perfectly healthy server and the kubelet restarts it in a loop.
>
> `/healthz` is still worth having, because it distinguishes "process
> alive" from "process serving" in a way `tcpSocket` cannot. But the
> probe must be an `exec` probe, which runs inside the container's
> network namespace and can therefore reach loopback. The two
> conceivable alternatives are both rejected: binding `0.0.0.0` is
> declared a configuration error by §2 and would expose the tool
> surface to the cluster network behind a single bearer token, and a
> `hostPort` mapping would do the same with extra steps.
>
> A second point recorded during specification: the ADR describes a
> 503 state, but a configuration error is fatal at startup, so the
> process either serves or does not exist. The unready window exists
> only between the listener binding and the configuration finishing to
> load, and may be empty depending on construction order. If it is,
> `/healthz` is a liveness signal and must not be wired as a readiness
> probe. `bdd/features/deployment/health_probe.feature` is written to
> reveal which it is; the ADR should be settled on this point before
> it moves to Accepted.

### 8. Saga recovery runs at startup

Restart is routine in this topology, so recovery must not depend on an
external trigger. On startup, after configuration loads and before the
transport accepts requests, the server scans the WAL and resumes
non-terminal transactions.

`saga_crash_recovery.feature` already narrates this ("the server
re-scans the WAL on startup and resumes non-terminal transactions") but
every scenario drives recovery through an explicit step, so the startup
path itself is unverified. The specification gap is closed by
`transactions/startup_saga_recovery.feature`.

### 9. Distribution

The project publishes a container image alongside the existing PyPI
package. The image contains the `gpg` binary, because the `gpg_file`
secret-store backend shells out to it and a missing binary is a startup
failure. The image runs as a non-root user with a fixed UID so that the
state volume's ownership is predictable.

## Consequences

### Positive

- **[LIM-0019] is mitigated by construction.** The agent has no write
  path to `/config`: it is a different container's mount, and it is
  read-only even within that container. The incident of 2026-06-22 — an
  agent rewriting its own policy to `default: FULL` through its shell —
  is not reproducible in this topology.
- **[LIM-0014] is resolved for this topology.** Server and caller share
  `/sink` at one path by explicit declaration, and the declaration is
  in the same manifest as both containers, so the coupling is visible
  where it is created.
- **Multi-caller defects cannot be reached.** One configured caller
  means process-wide caches have exactly one identity to serve.
- **Blast radius is per-agent.** A compromised or wedged instance
  affects one workload. Credentials, policy, audit chain and sink are
  all scoped to it.
- **No network exposure to reason about.** Loopback-only means no
  `Service`, no `NetworkPolicy`, no TLS termination, and no bearer
  token traversing the pod network.
- **`Secret`-mounted credentials replace plaintext in a home
  directory.** The `file_dir` backend reads a read-only tmpfs
  projection instead of a file in the operator's `$HOME`.
- **Per-caller sink isolation.** Attachments fetched by one agent are
  not readable by another agent's process.

### Negative

- **One instance per agent multiplies IMAP connections.** There is no
  connection pool today: [ADR 0013] is Accepted but unimplemented, and
  every tool call performs a full TCP, TLS, LOGIN and LOGOUT cycle. N
  sidecars against one Gmail account contend for that provider's
  simultaneous-connection limit, and each pays roughly a second of
  connect cost per call. This is an existing defect that the topology
  amplifies; it is a paydown of [ADR 0013], not a limitation to accept.
- **One state volume per agent pod.** Storage cost scales with the
  number of agents, and the audit record set fragments into one chain
  per agent. See [LIM-0020].
- **Sink bytes die with the pod.** `emptyDir` is discarded on pod
  deletion. An agent that fetches an attachment and is evicted before
  reading it loses the bytes and must re-fetch. This is correct
  behaviour for scratch space but is a change from the local
  deployment, where the sink outlives any single agent run.
- **A ConfigMap edit does not reload the server.** Operators must wire
  a rollout trigger or a signalling sidecar; nothing about the edit
  alone signals failure, so a stale policy stays in force silently.
- **OAuth bootstrap cannot run in the pod.** See [LIM-0021].
- **A projection mistake yields a silent, total deny.** *(Added
  2026-08-25 while writing [ops guide].)* Three distinct
  misconfigurations — `policies/` not projected, projected but empty,
  or a caller naming a policy no file declares — each produce a server
  that starts cleanly, passes its probe, authenticates the caller and
  then returns nothing, with no warning and no audit record. The
  policy loader guards the whole directory with `if
  policies_dir.is_dir():` and skips it silently, and there is no
  cross-file validation between `callers.yaml` and `policies/`. This
  is the most likely way a real deployment goes wrong, it is peculiar
  to this topology because projection is where the mistake is made,
  and this ADR does not address it. A startup check — every caller's
  policy resolves, or refuse to start — would be consistent with how
  the server already treats `stdio_trusted` on HTTP and should be
  decided before this ADR is Accepted. Until then the operator guide
  makes `describe_policy` a mandatory post-rollout step.
- **Two deployment stories to document and support.** Local stdio
  remains the ergonomic default for desktop orchestrators; this ADR
  adds a second shape with different failure modes.

### Neutral

- The Streamable HTTP transport is used unchanged; this ADR adds no
  transport code. `stateless=True` means no session affinity question
  arises, which is moot at one replica but keeps the option open.
- The existing `--host` default of `127.0.0.1` is already correct for
  this topology. No new default is introduced.
- ~~The choice of `StatefulSet` versus `Deployment` with `Recreate` is
  left to the operator; both satisfy the single-writer requirement.~~
  **Amended, 2026-08-25 (while writing [ops guide]):** both satisfy the
  single-writer requirement, but they differ on a point this ADR cares
  about, so the choice is not neutral. A `StatefulSet`'s
  `volumeClaimTemplates` bind the claim's lifetime to the workload and
  the default `persistentVolumeClaimRetentionPolicy` reclaims it, so
  `kubectl delete statefulset` silently destroys an agent's entire
  audit chain — exactly the loss [LIM-0020] says nothing detects. A
  `Deployment` with `replicas: 1`, `strategy: Recreate` and a
  *standalone* `PersistentVolumeClaim` leaves the claim behind. That is
  the recommended shape. Operators who prefer a `StatefulSet` must set
  `persistentVolumeClaimRetentionPolicy: {whenDeleted: Retain,
  whenScaled: Retain}` explicitly.

## Security Implications

- **Attack surface.** One new route, `GET /healthz`, unauthenticated. It
  accepts no parameters, reads no configuration at request time, and
  emits one of two constant JSON bodies. It is reachable only from
  within the pod, because the listener is bound to loopback. The
  disclosure it permits is "a server process is up and has a loaded
  configuration", which an in-pod caller learns anyway from its first
  tool call.
- **Trust boundaries — moved, and this is the point.** The boundary
  between the agent and the policy that constrains it moves from "same
  user, same filesystem, enforced by convention" to "different
  container, read-only mount, enforced by the kubelet". The agent
  retains write access to exactly one server-visible path, `/sink`,
  where it can create and delete files. It cannot influence what the
  server writes there: [ADR 0028] keeps the directory operator-chosen
  and the filename server-derived.
- **Residual trust in the pod spec.** Whoever can edit the pod
  specification can mount a writable ConfigMap, add `/config` to the
  agent container, or set `shareProcessNamespace: true` and reach the
  server process directly. The trust boundary is therefore only as
  strong as the cluster's RBAC on the workload's own manifest. This
  moves the integrity question from filesystem permissions to Kubernetes
  RBAC — a better place for it, but not an absolute one.
- **Data exposure.** Attachment bytes land on an `emptyDir`, which is
  node-local storage backed by the node's filesystem or memory.
  Anything with node-level access — a privileged pod, a node operator,
  a node-level backup — can read them, and they are not encrypted at
  rest unless the node's storage is. This is the same exposure class as
  the local deployment's sink directory, relocated to the node.
- **Credential handling.** Secrets arrive as a read-only projection.
  The `env_var` backend remains available and remains the weaker
  choice, though the original reason no longer applies. *(Amended,
  2026-08-25:* the signalling sidecar with `shareProcessNamespace:
  true` is withdrawn — see §5 — so `/proc/<pid>/environ` is no longer
  readable from the agent container. Two reasons survive: the audit
  `external_root_hook` is executed with `shell=True` and inherits the
  full process environment, so every configured secret is visible to
  whatever command an operator wires there; and an environment is
  readable by anything that can `kubectl exec` into the container,
  which is a broader set of principals than can read a mounted file
  as the runtime UID. `file_dir` over a `Secret` projection, as drawn
  in §1, stays the recommendation. The cost is that the projection
  must map each key to its nested `<class>/<id>/<field>` path with
  `items[].path`, because the backend turns the reference's slashes
  into real subdirectories and never creates them.*)
- **Failure modes.** A missing or unwritable `/sink` mount surfaces as
  `sink_not_writable` on `list_tools` and on every `fetch_attachment`
  ([ADR 0028] §3), so a manifest that forgets the shared volume in the
  *server* container is caught. A manifest that forgets it in the
  *agent* container is not caught by anything: the server writes
  successfully and the agent cannot read. That half of [LIM-0014]
  survives this ADR.
- **Auditability.** Every call remains audited with the resolved
  `caller_id` ([ADR 0021]). Two known gaps are unchanged and are worse
  in a networked topology than they were on stdio: the `caller_addr`
  field is emitted as `stdio:pid=<pid>` even on HTTP, and the OAuth
  token-refresh path writes audit records outside the hash chain.
  Neither is introduced here; both are owed fixes.
- **What this ADR does not fix.** It removes the *exposure* of the
  multi-caller defects by removing multi-caller deployments; it does
  not repair them. An operator who ignores §3 and configures two
  callers on one instance re-enables them, and nothing in the server
  refuses that configuration.

## Alternatives Considered

- **A shared cluster-wide `imap-mcp` Service.** One instance, many
  agent pods as HTTP callers, each with its own token and policy. This
  is the shape the README already advertises ("for multi-agent
  setups"). Rejected for now on three counts: it requires the
  multi-caller authorization defects to be fixed first and proven by
  scenarios that do not exist; it has no answer to [LIM-0014] short of
  a `ReadWriteMany` volume mounted at an identical path in every agent
  pod, which recreates the shared-sink problems of [LIM-0013] and
  [LIM-0016] across tenants; and [ADR 0007] caps it at one replica
  anyway, so it buys no scalability — only a single point of failure
  for every agent at once. It remains the right long-term shape once
  those are addressed, and would supersede this ADR.
- **Both containers in one image, server as a stdio subprocess.** The
  agent launches `imap-mcp --transport stdio` itself, exactly as on a
  laptop. Rejected: it puts the agent and the server under one OS
  principal again, which is the precise condition [LIM-0019] describes.
  A read-only ConfigMap mount would still prevent the policy rewrite,
  but the agent could replace the server's Python environment or its
  entry point, and the boundary would rest on nothing but the mount
  flag.
- **Unix domain socket on the shared volume instead of loopback HTTP.**
  Marginally tighter than loopback: filesystem permissions on the
  socket become an additional gate. Rejected for V1 because the server
  exposes no `--uds` option, uvicorn's support for it would need a CLI
  surface and scenarios, and loopback inside a pod network namespace is
  already unreachable from outside. Worth revisiting if a future
  deployment shares a network namespace more widely.
- **`tcpSocket` probe instead of `/healthz`.** No new route, no change
  to the 404 invariant. Rejected: a bound socket proves the listener
  started, not that configuration loaded. The failure this probe most
  needs to catch — a malformed policy on cold startup, which [LIM-0019]
  documents as taking down the whole tool surface — leaves the socket
  bound and the server useless.
- **`/healthz` behind bearer authentication.** Rejected: kubelet
  presents no credentials, and giving the probe a token would put a
  caller token in the pod spec.
- **A readiness endpoint that reports IMAP reachability.** Rejected:
  it would make the pod's readiness depend on a third party's
  availability, so a Gmail outage would cause a restart loop that
  cannot help. IMAP reachability belongs in alerting, not in a probe.
- **`hostPath` or a `PersistentVolumeClaim` for the sink.** Rejected:
  [LIM-0015] leaves cleanup to the operator, so a durable sink grows
  without bound. Tying its lifetime to the pod is the only bound
  available without building retention the server has decided not to
  own.
- **Sink and state on one volume.** Rejected: an unbounded sink would
  exhaust the audit log's space, converting a disk-usage problem into
  loss of the audit channel.

## References

- [ADR 0007] — SQLite WAL, single-writer; the origin of `replicas: 1`.
- [ADR 0013] — connection pool; Accepted, unimplemented, and amplified
  by one instance per agent.
- [ADR 0014] — policy as Git-versioned YAML with `SIGHUP` reload;
  file-watch reload rejected.
- [ADR 0015] — caller identity; `shared_token` is mandatory on HTTP and
  `stdio_trusted` on HTTP is a fatal startup error.
- [ADR 0018] — non-goal tool surface; the `/admin` 404 invariant this
  ADR narrows.
- [ADR 0021] — audit log format; the hash chain that forbids two
  writers.
- [ADR 0028] — attachment file sink; the delivery model whose
  precondition this topology satisfies.
- [LIM-0007] — HTTP transport; Resolved 2026-04-29.
- [LIM-0013] — single sink path.
- [LIM-0014] — sink requires caller filesystem access; resolved for
  this topology by the shared `emptyDir`.
- [LIM-0015] — sink cleanup out of scope; the reason the sink is
  ephemeral here.
- [LIM-0016] — sink path disclosed in the tool description.
- [LIM-0019] — policy integrity requires an out-of-process trust
  boundary; this ADR is the deployment mode its "Triggers for revisit"
  section anticipates.
- [LIM-0020] — audit chain fragments across per-agent instances.
- [LIM-0021] — OAuth bootstrap and refresh-token persistence are not
  possible in-pod.
- [ops guide] — the operator's guide that implements this ADR:
  Containerfile, complete manifest, configuration reference, pitfalls,
  pre-flight checklist and day-2 procedures. The four dated amendments
  above were all discovered while writing it.
- Scenarios: `bdd/features/deployment/health_probe.feature`,
  `bdd/features/deployment/projected_config_reload.feature`,
  `bdd/features/transactions/startup_saga_recovery.feature`.
- Kubernetes ConfigMap projection and atomic `..data` symlink swap:
  <https://kubernetes.io/docs/concepts/configuration/configmap/>

[ADR 0007]: 0007-sqlite-as-wal-store.md
[ADR 0013]: 0013-hybrid-connection-pool.md
[ADR 0014]: 0014-policy-as-git-versioned-yaml.md
[ADR 0015]: 0015-caller-identity-and-authentication.md
[ADR 0018]: 0018-non-goal-tool-surface.md
[ADR 0021]: 0021-audit-log-format.md
[ADR 0028]: 0028-attachment-file-sink-delivery.md
[LIM-0007]: ../limitations/0007-http-transport-deferred.md
[LIM-0013]: ../limitations/0013-single-attachment-sink-path.md
[LIM-0014]: ../limitations/0014-attachment-sink-requires-caller-filesystem-access.md
[LIM-0015]: ../limitations/0015-attachment-sink-cleanup-out-of-scope.md
[LIM-0016]: ../limitations/0016-attachment-sink-path-disclosed-in-tool-description.md
[LIM-0019]: ../limitations/0019-policy-integrity-requires-out-of-process-trust-boundary.md
[LIM-0020]: ../limitations/0020-audit-chain-fragments-across-per-agent-instances.md
[LIM-0021]: ../limitations/0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md
[ops guide]: ../ops/kubernetes-sidecar-deployment.md
