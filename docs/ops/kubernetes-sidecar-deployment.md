# Running imap-mcp as a Kubernetes sidecar

This is the operator's guide to the deployment topology decided in
[ADR-0029](../adr/0029-kubernetes-sidecar-deployment-topology.md): one
`imap-mcp` container inside each agent pod, serving exactly one caller
over the pod's loopback interface.

It is written so that you do not have to re-derive anything. Everything
below was verified against the source at the time of writing; where the
code and an ADR or a docstring disagree, the code is quoted and the
disagreement is called out. Where something does not work yet, it says
so instead of describing an intention.

**Read the two sections "What you need to know first" and "Pitfalls"
before you write a manifest.** Roughly half of the ways this deployment
goes wrong produce a server that starts cleanly, reports no error, and
silently denies every request.

---

## 1. The shape

```
Pod: <your-agent-workload>
  ├── container: agent                    your workload, unchanged
  │     rw  /sink                         emptyDir, shared
  │     env MCP endpoint = http://127.0.0.1:8080/mcp
  │
  └── container: imap-mcp                 this server
        ro  /config                       ConfigMap projection
        ro  /secrets                      Secret projection
        rw  /state                        PersistentVolumeClaim
        rw  /sink                         emptyDir, shared
        env IMAP_MCP_CONFIG_DIR=/config
```

Both containers share the pod's network namespace, so the agent reaches
the server at `127.0.0.1:8080` with no Service, no Ingress and no
NetworkPolicy. They do **not** share a process namespace or a root
filesystem, which is the entire point: the agent cannot rewrite the
policy that constrains it
([LIM-0019](../limitations/0019-policy-integrity-requires-out-of-process-trust-boundary.md)).

They share exactly one directory, `/sink`, because
[ADR-0028](../adr/0028-attachment-file-sink-delivery.md) delivers
attachments as files rather than inline bytes, and without a shared
volume `fetch_attachment` reports success while the agent can never
read the file
([LIM-0014](../limitations/0014-attachment-sink-requires-caller-filesystem-access.md)).

---

## 2. What you need to know first

Six facts that are not guessable from the README and that shape the
manifest:

1. **There is no published container image.** The project releases to
   PyPI as `sc-imap-mcp`; the release workflow has no image build step.
   You build the image yourself — see section 3. ADR-0029 §9 records
   shipping an official image as owed work.

2. **`IMAP_MCP_CONFIG_DIR` is the only way to point at the config.**
   There is no `--config` flag, no XDG support, and no default. If the
   variable is unset or does not name a directory, the process exits
   immediately with a message saying so. That is the one config error
   that is loud.

3. **Paths in the config are taken literally.** Nothing calls
   `expanduser` or `expandvars` anywhere in the codebase. A `~` or a
   `$STATE_DIR` inside `accounts.yaml` becomes a directory with that
   literal name. Write absolute paths.

4. **`--port 0` is unusable here.** The flag's own help text says the
   bound port is printed to stdout. It is not — uvicorn runs at
   `log_level="warning"`, which suppresses the line, and nothing else
   prints it. Always pass an explicit port.

5. **Encryption is selected by port number, not by a setting.** There
   is no `tls:` key. `port: 993` gets an implicit-TLS connection;
   anything else is plaintext IMAP. There is no STARTTLS support and no
   certificate-verification toggle.

6. **The whole `IMAP_MCP_TEST_*` namespace is dangerous in
   production.** `IMAP_MCP_CRASH_AT`, `IMAP_MCP_SAGA_PAUSE_AT`,
   `IMAP_MCP_SAGA_PAUSE_MARKER`, `IMAP_MCP_FAKE_NOW_UTC` and
   `IMAP_MCP_FAKE_NOW_FILE` are read
   unconditionally — they are **not** gated behind
   `IMAP_MCP_TEST_MODE`. `IMAP_MCP_CRASH_AT` kills the process with
   `os._exit(1)` mid-saga. Make sure none of them leaks in from a
   shared ConfigMap or a base image.

---

## 3. The image

Build it yourself. A minimal, working Containerfile:

```dockerfile
FROM python:3.12-slim

# gpg is needed ONLY if you use secret_store.backend: gpg_file.
# /bin/sh is needed ONLY if you configure audit.external_root_hook,
# which is executed with shell=True.
# The slim base already provides /bin/sh; drop the gpg line if you
# use the env_var or file_dir backend.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gnupg \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "sc-imap-mcp==2.0.1"

# Fixed non-root UID so that volume ownership is predictable across
# rebuilds (ADR-0029 §9).
RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin imapmcp
USER 65532:65532

ENTRYPOINT ["imap-mcp"]
```

Notes on the dependency set:

- Requires Python ≥ 3.11.
- `uvicorn` and `starlette` are **not** declared as dependencies of
  `sc-imap-mcp` even though the HTTP transport imports them. They
  arrive transitively through the `mcp` package. Pin `mcp` in your
  image if you want that to stay true across rebuilds; a future `mcp`
  that drops them would break `--transport http` at container start,
  not at install time.
- `structlog` is declared but never imported. Logging is stdlib
  `logging` only, and there is no log-level or log-format setting.

---

## 4. A complete manifest

Substitute your own registry, image tag, account and caller names.
This deploys one agent workload with its sidecar.

### 4.1 ConfigMap

ConfigMap keys cannot contain `/`, so the `policies/` subdirectory is
produced by mapping keys to paths with `items`. Paths in `items` may
contain slashes; the kubelet creates the subdirectory.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: imap-mcp-config
data:
  accounts.yaml: |
    accounts:
      - id: company-mail
        provider: imap-standard
        host: imap.example.com
        port: 993                 # 993 => implicit TLS. Any other port
                                  # is plaintext. There is no tls: key.
        user: agent@example.com   # ALWAYS set this. See pitfall P4.
        auth:
          type: password
          secret_ref: secret://accounts/company-mail/password
        token_cache: memory_only

    secret_store:
      backend: file_dir           # see 4.2
      path: /secrets

    audit:
      directory: /state/audit
      hot_days: 90
      warm_days: 275              # accepted but never read; see pitfall P9
      delete_after_days: 365

    wal:
      path: /state/wal.db

    attachment_sink:
      directory: /sink            # NOTE: the key is attachment_sink.directory.
                                  # ADR-0028 calls it "attachment_sink_directory"
                                  # in prose; that spelling is not valid YAML here.

  callers.yaml: |
    callers:
      - id: invoice-agent
        policy: invoice-policy    # must match a policy file's `name:` field,
                                  # NOT a filename. See pitfall P1.
        auth:
          type: shared_token      # stdio_trusted is fatal on HTTP.
          token_secret_ref: secret://callers/invoice-agent/token

  invoice-policy.yaml: |
    name: invoice-policy
    accounts:
      company-mail:
        folders:
          - path: INBOX/Rechnungen
            mode: whitelist
            default: NONE
            rules:
              - from_domain: hornbach.de
                grant: FULL
```

### 4.2 Secret

Use the `file_dir` backend over a projected `Secret`, as ADR-0029 §1
draws it. The `env_var` backend also works and is simpler to wire, but
ADR-0029's Security Implications rank it lower — see 4.2.1 for both the
alternative and the reasons.

The one thing you must get right: `file_dir` turns a reference's
slashes into **real subdirectories**, and the server never creates
them. `secret://callers/invoice-agent/token` is read from
`/secrets/callers/invoice-agent/token`. A flat projection with a key
named `token` is not found. Map every key to its nested path with
`items`, exactly as the ConfigMap does for `policies/`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: imap-mcp-secrets
type: Opaque
stringData:
  caller-token:  "replace-me-with-a-long-random-token"
  imap-password: "replace-me"
```

projected as:

```yaml
        - name: secrets
          secret:
            secretName: imap-mcp-secrets
            items:
              - {key: caller-token,  path: callers/invoice-agent/token}
              - {key: imap-password, path: accounts/company-mail/password}
```

The mapping rule in general is
`secret://<class>/<id>/<field>` → `<path>/<class>/<id>/<field>`.

#### 4.2.1 Alternative: the `env_var` backend

Set `secret_store: {backend: env_var}`, drop the `/secrets` volume, and
add `envFrom: [{secretRef: {name: imap-mcp-secrets}}]` to the server
container. A leftover `path:` is accepted and silently ignored under
`env_var` — the key is part of the model, so `extra="forbid"` does not
catch it and nothing warns that it has no effect.

The reference-to-variable rule, verbatim from the implementation:
strip `secret://`, strip any leading `/`, uppercase, replace `/` with
`__` (two underscores), replace `-` with `_` (one underscore), prepend
`IMAP_MCP_SECRET__`.

```
secret://callers/invoice-agent/token
  -> IMAP_MCP_SECRET__CALLERS__INVOICE_AGENT__TOKEN

secret://accounts/company-mail/password
  -> IMAP_MCP_SECRET__ACCOUNTS__COMPANY_MAIL__PASSWORD
```

Two reasons ADR-0029 prefers `file_dir`. First, the audit
`external_root_hook` is executed with `shell=True` and inherits the
full process environment, so whatever command you wire there sees every
configured secret. Second, an environment is readable by anyone who can
`kubectl exec` into the container, which is a broader set of principals
than can read a mounted file as the runtime UID. Neither is fatal;
if you have no `external_root_hook` and tight `pods/exec` RBAC, the
ergonomic win is real. See also pitfall P7 for ids that cannot be
expressed as variable names at all.

### 4.3 State volume

A standalone PVC, deliberately **not** a `volumeClaimTemplate`. A
standalone claim survives deletion of the workload, which is what keeps
the audit history from vanishing with a `kubectl delete deployment`
([LIM-0020](../limitations/0020-audit-chain-fragments-across-per-agent-instances.md)).

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: imap-mcp-state-invoice-agent
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 2Gi
```

### 4.4 Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: invoice-agent
spec:
  replicas: 1                     # MUST be 1. See pitfall P5.
  strategy:
    type: Recreate                # MUST be Recreate, not RollingUpdate.
  selector:
    matchLabels: {app: invoice-agent}
  template:
    metadata:
      labels: {app: invoice-agent}
      annotations:
        # Roll the pod when the config changes. This is how a policy
        # change actually reaches the running server — see section 6.
        imap-mcp/config-hash: "REPLACE_WITH_SHA256_OF_CONFIGMAP_DATA"
    spec:
      # Do NOT set shareProcessNamespace: true. See pitfall P3.
      securityContext:
        fsGroup: 65532            # so both containers can use /sink and
                                  # so the PVC is writable by the runtime UID
      containers:
        - name: agent
          image: registry.example.com/your-agent:1.0.0
          env:
            - name: MCP_IMAP_ENDPOINT
              value: "http://127.0.0.1:8080/mcp"
            - name: MCP_IMAP_CALLER_ID
              value: "invoice-agent"
            - name: MCP_IMAP_TOKEN
              valueFrom:
                secretKeyRef:
                  name: imap-mcp-secrets
                  key: caller-token
          volumeMounts:
            - {name: sink, mountPath: /sink}

        - name: imap-mcp
          image: registry.example.com/imap-mcp:2.0.1
          args:
            - "--transport"
            - "http"
            - "--host"
            - "127.0.0.1"         # loopback only; see pitfall P2
            - "--port"
            - "8080"              # never 0; see fact 4
          env:
            - name: IMAP_MCP_CONFIG_DIR
              value: /config
          volumeMounts:
            - {name: config,  mountPath: /config,  readOnly: true}
            - {name: secrets, mountPath: /secrets, readOnly: true}
            - {name: state,   mountPath: /state}
            - {name: sink,    mountPath: /sink}
          securityContext:
            runAsNonRoot: true
            runAsUser: 65532
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: ["ALL"]}
          livenessProbe:
            # An exec probe, NOT httpGet or tcpSocket. The kubelet dials
            # the pod IP, and the server listens on 127.0.0.1 only, so a
            # network probe from the kubelet cannot reach it. See P2.
            exec:
              command:
                - python
                - -c
                - "import socket; socket.create_connection(('127.0.0.1', 8080), 2).close()"
            initialDelaySeconds: 10
            periodSeconds: 20
            timeoutSeconds: 5
          resources:
            requests: {cpu: 50m, memory: 128Mi}
            limits:   {memory: 512Mi}

      volumes:
        - name: config
          configMap:
            name: imap-mcp-config
            items:
              - {key: accounts.yaml,       path: accounts.yaml}
              - {key: callers.yaml,        path: callers.yaml}
              - {key: invoice-policy.yaml, path: policies/invoice-policy.yaml}
        - name: secrets
          secret:
            secretName: imap-mcp-secrets
            defaultMode: 0440         # 0400 would be root-only; see P6
            items:
              - {key: caller-token,  path: callers/invoice-agent/token}
              - {key: imap-password, path: accounts/company-mail/password}
        - name: state
          persistentVolumeClaim:
            claimName: imap-mcp-state-invoice-agent
        - name: sink
          emptyDir: {}
```

---

## 5. Pitfalls

Ordered by how much time they cost when you hit them.

### P1 — A misnamed policy produces a silent, total deny

This is the most expensive mistake available. Three separate ways to
make it, all of which leave a server that starts cleanly, passes its
probe, authenticates the caller, and returns empty results forever:

- The `policies/` directory is not projected at all. The loader guards
  the whole block with `if policies_dir.is_dir():` and skips it
  silently. Zero policies loaded, no warning, no audit record.
- The directory exists but is empty. Identical outcome.
- A caller's `policy:` value names a policy that no file declares. The
  lookup returns empty and the caller sees no accounts at all.

`imap-mcp` has no cross-file validation and logs nothing in any of the
three cases. **After every config change, verify with
`describe_policy`** — see section 8. Note also that the glob is
`*.yaml`; a file named `.yml` is ignored.

### P2 — A network probe cannot reach a loopback listener

The kubelet performs `httpGet` and `tcpSocket` probes by dialling the
**pod IP** from the node. ADR-0029 §2 binds the server to `127.0.0.1`.
Those two facts are incompatible: any `httpGet` or `tcpSocket` probe
against this container fails with connection-refused while the server
is perfectly healthy, and the kubelet restarts it in a loop.

Use an `exec` probe, which runs inside the container's network
namespace. The manifest above does.

This also applies to the `/healthz` endpoint that ADR-0029 §7
specifies: when it exists, it will still not be reachable by an
`httpGet` probe as long as the listener stays on loopback. Use
`exec` + a local HTTP call. The alternative — binding `0.0.0.0` and
adding a NetworkPolicy — is declared a configuration error by
ADR-0029 §2, and for good reason: it exposes the tool surface to the
whole cluster network with a single shared bearer token in front of it.

### P3 — `shareProcessNamespace: true` re-opens LIM-0019

It is tempting: with a shared process namespace a small sidecar can
`kill -HUP` the server when the ConfigMap changes, and you get
signal-based reload without rolling the pod.

Do not do it on a pod whose agent container is the principal the policy
constrains. A shared process namespace makes every container's root
filesystem reachable through `/proc/<pid>/root`. The agent could then
read — and, depending on mount options and UIDs, write — the server's
view of `/config`. That is precisely the trust boundary the whole
topology exists to establish
([LIM-0019](../limitations/0019-policy-integrity-requires-out-of-process-trust-boundary.md)),
and it would be given away for the convenience of not restarting a pod.

Roll the pod on a config-hash annotation instead. It costs one restart
per policy change, and a restart is cheap here.

### P4 — An omitted `user:` silently logs in as the wrong account

If `user` is not set on an account, the login name is derived as
`account.id.split("-", 1)[0]` — the segment before the **first** dash.
An account with `id: company-mail` logs in as `company`. Always set
`user` explicitly.

### P5 — More than one replica corrupts state

The WAL is SQLite with a single-writer assumption
([ADR-0007](../adr/0007-sqlite-as-wal-store.md)) and the audit log is a
hash chain reconstructed by one process from one directory
([ADR-0021](../adr/0021-audit-log-format.md)). Two processes writing
into one `/state` produce a chain that verifies for neither.

`replicas: 1` and `strategy: Recreate` together are what prevent this.
A `RollingUpdate` briefly runs two pods, and with a `ReadWriteOnce`
volume the new pod usually just fails to attach — but on a node where
the old pod is scheduled, it can attach, and then you have two writers.
Treat `RollingUpdate` as a configuration error.

### P6 — A flat Secret projection is never found

The `file_dir` backend resolves `secret://callers/invoice-agent/token`
to `<path>/callers/invoice-agent/token`; the slashes are real
subdirectories and the server never creates them, it only reads. A
Secret projected with a key named `token` at the mount root does not
resolve. Map every key to its nested path with `items` — see 4.2.

Two follow-on traps in the same area:

- **Ownership.** A `Secret` volume is projected as `root:root` with
  mode 0644 by default. With `defaultMode: 0400` and a container
  running as UID 65532, the file becomes unreadable — 0400 grants the
  *owner*, which is root, not you. Either set `fsGroup` (which makes
  the projection group-owned by the fsGroup and readable at 0440) and
  use `defaultMode: 0440`, or leave the mode at the default. The
  manifest in section 4 sets `fsGroup: 65532`; verify with
  `kubectl exec ... -- cat /secrets/callers/<id>/token` before
  concluding a token is wrong.
- **Trailing whitespace.** `file_dir` applies `rstrip("\n")` to the
  file it reads, so trailing newlines are forgiven — but only
  newlines. A trailing space or tab survives, and so does the `\r` of
  a CRLF file. `env_var` strips nothing at all, which means the same
  Secret can yield two different values depending on the backend.
  `echo secret | base64` and `--from-file` against a text file both
  append a newline; `printf '%s'` and `--from-literal` do not. Use
  those two and the asymmetry never bites you.

### P7 — `env_var` only: an id with a dot or an `@` has no valid variable name

Applies to the `env_var` backend only. Its name transformation rewrites
`/` and `-` and nothing else, so an id like `accounting@company.com`
produces
`IMAP_MCP_SECRET__ACCOUNTS__ACCOUNTING@COMPANY.COM__PASSWORD`, which is
not a valid environment variable name and which Kubernetes will not
accept as a Secret key either. `file_dir` has no such restriction —
those characters are legal in a path segment.

Keeping account and caller ids to `[a-z0-9-]` sidesteps this and keeps
the two backends interchangeable. Recommended regardless of backend.

### P8 — An unknown key in any config file kills the process with a traceback

All models use `extra="forbid"` and `strict=True`. A typo'd or
future-dated key raises a `pydantic.ValidationError` that nothing
catches, so the container exits 1 with a Python traceback rather than a
diagnostic. Strict mode also means no type coercion: `port: "993"` as a
string fails where `port: 993` succeeds.

The same applies to a missing `accounts.yaml` or `callers.yaml`
(uncaught `FileNotFoundError`). A missing `secret_store:` block is the
one exception: it produces a clean `SystemExit` with an explanatory
message.

A related trap: every field of `secret_store` has a default, so
`secret_store: {}` passes validation — `backend` defaults to
`file_dir`. Construction then fails with a bare
`ValueError: ... backend requires a 'path'`. Always write `backend`
and, for `file_dir` or `gpg_file`, `path` explicitly.

### P9 — `warm_days` does nothing

`audit.warm_days` is accepted and validated but never read. Retention
gzips at `hot_days` and deletes at `delete_after_days`. A comment in
the source claims deletion happens at `hot_days + warm_days`; the code
uses `delete_after_days`. Set `delete_after_days` to what you actually
mean.

### P10 — The attachment sink is not created for you

Unlike the audit and WAL directories, the sink directory is never
created by the server. An `emptyDir` mount satisfies this because the
kubelet creates the mount point. If you point `attachment_sink` at a
subdirectory of a volume instead, create it first — otherwise
`fetch_attachment` fails per call with `sink_not_writable` while
everything else works.

### P11 — There is no SIGTERM handler

The server installs a handler for SIGHUP only. On SIGTERM the process
takes Python's default; uvicorn does drain HTTP connections, but
nothing in the MCP or saga layer shuts down gracefully.

This is survivable because both durable stores are synchronous: the
audit writer does `write` + `flush` + `fsync` per record, and the WAL
runs in autocommit with `PRAGMA synchronous=FULL`. Nothing is lost.
What you get instead is a saga stuck in a non-terminal state — see P12.

### P12 — A pod killed mid-saga leaves a transaction nothing will finish

There is no WAL scan at startup. `run_pending_recovery` has exactly one
call site in the entire codebase: the test-only `_test_run_recovery`
tool, which requires `IMAP_MCP_TEST_MODE=1`. (A docstring in `saga.py`
claiming recovery runs "during server startup" is stale — no startup
path calls it.)

In production the only thing that advances a stalled cross-account move
is a caller polling `get_transaction_status` for that exact `tx_id`. If
the agent did not persist the `tx_id`, or has moved on, the transaction
stays `staged` indefinitely — and `staged` means the message exists in
**both** mailboxes.

Until ADR-0029 §8 is implemented, treat this as a runbook item:

- Have the agent log every `tx_id` it receives from a `move` across
  accounts.
- After any unplanned restart, check the WAL for non-terminal rows and
  poll `get_transaction_status` for each.
- Prefer intra-account moves where the workflow allows it; those use
  native IMAP MOVE and involve no saga.

### P13 — `get_transaction_status` does not check who is asking

Confirmed in the current code: the handler reads the WAL row by
`tx_id`, never compares the requesting caller against the `caller_id`
stored on that row, and — for a `pending` or `staged` row — calls
`saga.resume()` before returning. `resume` re-resolves credentials from
the row's own accounts, not from the requester.

So any authenticated caller holding a `tx_id` can read another caller's
transaction metadata **and** trigger IMAP appends and deletes under the
other caller's accounts. `tx_id` is a 64-bit random hex string, so this
is bearer-token-shaped rather than enumerable, but it is a real
cross-caller authorization gap.

**One caller per instance is what makes this unreachable**, and it is
the reason ADR-0029 §3 is not merely a tidiness preference. If you are
tempted to save resources by pointing several agents at one sidecar
with several entries in `callers.yaml`, this is the defect you are
opting into. Don't, until it is fixed (ROADMAP entry 20).

### P14 — Auth is only applied to the MCP path

The bearer middleware short-circuits on any request path that does not
end in `/mcp`, and everything that is not the `/mcp` mount falls
through to a catch-all that returns an empty 404. That is the intended
shape — `/admin` and friends must 404 — but it means the 404 surface is
unauthenticated. Nothing sensitive is behind it today. Worth knowing
before you add any endpoint.

---

## 6. Changing configuration on a running deployment

The kubelet re-projects a changed ConfigMap into the volume within a
minute or so, atomically: it writes a new timestamped directory,
repoints the `..data` symlink with a single `rename(2)`, and deletes
the old directory. A reader that opens the file fresh always sees one
consistent generation — there is no torn read to defend against.

**The kubelet sends no signal.** The running process keeps enforcing
the configuration it loaded at startup. The re-projected file on disk
and the policy in force are now different things, and nothing reports
the divergence.

Two ways to close that gap:

1. **Roll the pod (recommended).** Put a hash of the ConfigMap data in
   a pod-template annotation, as the manifest above does. Changing the
   ConfigMap changes the hash, which changes the pod template, which
   makes the Deployment replace the pod. The new process loads the new
   config at startup. With `strategy: Recreate` and a `ReadWriteOnce`
   volume this is a brief outage, not a rollover — accept it.

   Tools that do the hashing for you: Kustomize `configMapGenerator`
   (appends a content hash to the ConfigMap name), Helm's
   `checksum/config` annotation idiom, or Flux/Argo equivalents.

2. **Send SIGHUP.** The handler is wired on the HTTP transport as well
   as on stdio, and a failed reload fails closed: the last known good
   configuration stays in force. But delivering the signal from outside
   the container requires either `shareProcessNamespace` (see P3 — do
   not) or `kubectl exec`, which is a manual step that will be
   forgotten.

### Rotating a caller token or an IMAP password

Same mechanism, same caveat: a `Secret` volume is re-projected by the
identical atomic-swap procedure, and the running process keeps using
the value it read at startup until it is signalled or replaced.

Because the agent container also holds the caller token — as an env var
from the same Secret, and env vars are **not** re-projected at all —
rotating it means rolling the pod regardless. Put the Secret's hash in
the same pod-template annotation as the ConfigMap's, or use a generator
that hashes both. Do the two sides in one rollout: a pod where the
server has the new token and the agent still has the old one fails
every handshake with `auth_failed`.

For an IMAP password, which only the server holds, a SIGHUP suffices —
but rolling is simpler and there is no reason to optimise a credential
rotation for uptime.

### Failure behaviour

Note the asymmetry: a bad config delivered by
SIGHUP is rejected and the server keeps running on the old one; a bad
config present at **startup** kills the process, because there is no
prior good state to fall back to. A rolled pod with a broken ConfigMap
therefore CrashLoopBackOffs. That is the correct behaviour, and it is
also why rolling is safer than it sounds: the failure is immediate and
visible, rather than a silent divergence.

---

## 7. Volumes, retention and the audit log

`/state` holds two things that matter for different reasons:

- `wal.db` — operational. Losing it loses in-flight transaction state.
- `audit/` — evidentiary. Losing it loses the record of everything the
  agent ever did, and **nothing detects the loss**.

The second point is the substance of
[LIM-0020](../limitations/0020-audit-chain-fragments-across-per-agent-instances.md).
Each sidecar keeps its own hash chain. Each chain verifies internally.
Nothing binds one chain to another, so deleting one agent's volume
removes its entire history while every surviving chain still verifies
cleanly. An audit review sees a consistent record set and cannot tell
that a whole agent is missing from it.

Consequently:

- **Use a standalone PVC, not `volumeClaimTemplates`.** A standalone
  claim is not garbage-collected when the workload is deleted. If you
  do use a StatefulSet, set
  `persistentVolumeClaimRetentionPolicy: {whenDeleted: Retain,
  whenScaled: Retain}` explicitly — the default reclaims.
- **Wire `audit.external_root_hook`.** It runs at each UTC day roll
  with `%FINAL_HASH%` substituted, and pushing that hash somewhere
  outside the pod gives you an off-volume witness per instance per day.
  It converts "an agent's history vanished silently" into "an agent's
  history vanished and we have the root hashes that prove it existed".
  This is the single most effective mitigation available and it costs
  one line of config. Note it runs with `shell=True` and a 5-second
  timeout, and failures are swallowed — so test the command by hand.
- **Ship the JSONL files.** ADR-0022 deliberately keeps shipping out of
  the server; use Vector, Fluent Bit or Promtail against `/state/audit`
  as a second sidecar or a node-level agent. Note the files are mode
  0600 and the directory 0700, owned by the runtime UID — the shipper
  must run as that UID or share its group.
- **Reconciling chains across agents is an aggregator problem**, not
  something the server can do. Since each instance serves exactly one
  caller, `caller_id` and chain identity coincide, which makes shipped
  records unambiguously attributable once merged.

---

## 8. Pre-flight checklist

Run through this before declaring a deployment done. Items marked ⚠
are the ones that fail silently.

**Manifest**

- [ ] `IMAP_MCP_CONFIG_DIR` is set and matches the ConfigMap mount path
- [ ] `--port` is an explicit number, not `0`
- [ ] `--host` is `127.0.0.1`, and the liveness probe is `exec`, not
      `httpGet`/`tcpSocket`
- [ ] `replicas: 1` and `strategy: Recreate`
- [ ] `shareProcessNamespace` is absent or `false`
- [ ] `/config` and `/secrets` are both mounted `readOnly: true`
- [ ] the sink volume is mounted into **both** containers at the same
      path, and that path matches `attachment_sink.directory`
- [ ] `fsGroup` is set, or both containers run as the same UID
- [ ] no `IMAP_MCP_TEST_*`, `IMAP_MCP_CRASH_AT` or
      `IMAP_MCP_SAGA_PAUSE_*` variable is present in the pod's
      environment
- [ ] a config-hash annotation exists on the pod template

**Configuration**

- [ ] ⚠ every `policies/*.yaml` key is listed in the ConfigMap volume's
      `items` with a `policies/` path prefix
- [ ] ⚠ every caller's `policy:` value equals some policy file's
      internal `name:` field
- [ ] every account sets `user:` explicitly
- [ ] every account's `port:` is `993` unless you intend plaintext
- [ ] `secret_store:` is present, and `path:` is set if the backend is
      `file_dir`
- [ ] every `secret_ref` and `token_secret_ref` resolves — for
      `file_dir`, an `items[].path` produces the nested
      `<class>/<id>/<field>` path; for `env_var`, the transformed
      variable name exists in the Secret
- [ ] the projected secret files are readable by the runtime UID
      (`defaultMode` and `fsGroup` agree)
- [ ] `wal.path` and `audit.directory` are absolute and under `/state`
- [ ] no `~` and no `$VAR` appears in any path
- [ ] `delete_after_days` is the retention you actually want

**After rollout**

- [ ] the container reached `Running` and did not restart
- [ ] ⚠ `describe_policy` from the agent returns the accounts and
      folders you expect — **this is the only check that catches a
      silently policy-less server**
- [ ] `list_folders` returns the expected folders
- [ ] `get_caller_identity` returns the expected `caller_id`
- [ ] `fetch_attachment` on a known message writes a file the agent
      container can read at the sink path
- [ ] `/state/audit` contains a JSONL file with records for the calls
      you just made
- [ ] if wired, `external_root_hook` fired — verify after the first UTC
      day roll

---

## 9. What does not work in a pod

Recorded so nobody rediscovers it:

- **OAuth-backed accounts cannot be bootstrapped or repaired in the
  cluster.**
  ([LIM-0021](../limitations/0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md))
  The bootstrap CLI needs a browser and a TTY, and no implemented
  secret backend is both writable and Kubernetes-idiomatic. Mint the
  refresh token on a workstation, inject it as a Secret, and use
  `token_cache: memory_only`. Before deploying an OAuth account,
  determine whether the provider rotates refresh tokens — if it does,
  the injected token may be superseded at the first refresh and the
  pod will fail on its next restart with no in-cluster remedy.
  Password and app-password accounts are unaffected.

- **`persist_all` token caching.** It writes beneath the secret-store
  path and only for the `file_dir` backend. A Secret projection is a
  read-only tmpfs, so the write fails.

- **The connection pool.** [ADR-0013](../adr/0013-hybrid-connection-pool.md)
  is Accepted but not implemented. With N sidecars you get N
  independent connection sets against your IMAP provider's per-account
  connection limit. Gmail allows 15 simultaneous IMAP connections per
  account; plan the agent count against that, not against pod capacity.

- **`caller_addr` in the audit log.** It is hardcoded to
  `stdio:pid=<pid>` even on the HTTP transport, so the field carries no
  information about where a request came from. Do not build a review
  process on it.

- **Metrics and structured logs.** There is no metrics endpoint, no
  readiness endpoint, no log-level setting and no `--version` flag.
  Observability is the audit JSONL plus whatever uvicorn emits at
  warning level.

---

## 10. Open points against ADR-0029

Four conflicts and omissions surfaced while writing this guide. All
four are now recorded as dated amendments in ADR-0029 itself, since it
is still `Proposed`; they are listed here so an operator sees in one
place where the guide departs from the ADR's original text and why.

1. **§7 (`/healthz`) versus §2 (loopback binding) — amended.** The ADR
   specified an HTTP health endpoint for a container that, by the same
   ADR, no kubelet-issued network probe can reach. §2 wins; the probe
   must be `exec`-based. See P2.

2. **§5's reload sidecar — withdrawn.** The ADR offered a sidecar with
   `shareProcessNamespace: true` as a way to deliver `SIGHUP`. It
   would have handed the agent container a read path into the server's
   filesystem via `/proc/<pid>/root`. Rolling the pod on a config hash
   is now the only sanctioned mechanism. See P3.

3. **`StatefulSet` versus `Deployment` — no longer neutral.** The ADR
   left the choice to the operator. A `StatefulSet`'s default
   `persistentVolumeClaimRetentionPolicy` reclaims the volume, which
   destroys an agent's audit chain on `kubectl delete statefulset` —
   the loss LIM-0020 says nothing detects. A `Deployment` plus a
   standalone PVC is the recommended shape. See section 7.

4. **Nothing detects a policy-less start — added as a Negative
   consequence.** Three distinct misconfigurations produce a running
   server that denies everything without a warning (P1). It is the
   most likely way a real deployment goes wrong and the ADR had no
   position on it.

Genuinely still open, for whoever moves the ADR to `Accepted`:

- **Is §7's 503 state reachable at all?** A configuration error is
  fatal at startup, so the process either serves or does not exist.
  The unready window exists only between the listener binding and the
  configuration finishing to load, and may be empty depending on
  construction order. If it is empty, `/healthz` is a liveness signal
  and wiring it as a readiness probe would mislead.
  `bdd/features/deployment/health_probe.feature` is written to reveal
  which it is.
- **Should a caller whose policy does not resolve be fatal at
  startup?** It would be consistent with how the server already treats
  `stdio_trusted` on HTTP, and it would cost nothing. Until it is
  decided, `describe_policy` after every rollout is the only defence,
  and this guide makes it mandatory (section 8).

---

## 11. References

- [ADR-0029](../adr/0029-kubernetes-sidecar-deployment-topology.md) —
  the topology this guide operates.
- [ADR-0015](../adr/0015-caller-identity-and-authentication.md) —
  `shared_token` is mandatory on HTTP.
- [ADR-0011](../adr/0011-pluggable-secret-store-backend.md) — secret
  backends. Both `file_dir` and `env_var` are usable here; ADR-0029
  prefers `file_dir` over a projected `Secret`.
- [ADR-0028](../adr/0028-attachment-file-sink-delivery.md) — why a
  shared `/sink` volume is required.
- [ADR-0022](../adr/0022-audit-retention-and-access-model.md) —
  retention, `external_root_hook`, log shipping.
- [ADR-0007](../adr/0007-sqlite-as-wal-store.md) — the single-writer
  constraint behind `replicas: 1`.
- [LIM-0014](../limitations/0014-attachment-sink-requires-caller-filesystem-access.md),
  [LIM-0019](../limitations/0019-policy-integrity-requires-out-of-process-trust-boundary.md),
  [LIM-0020](../limitations/0020-audit-chain-fragments-across-per-agent-instances.md),
  [LIM-0021](../limitations/0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md)
  — the four limitation records this deployment mode touches.
- [ROADMAP](../ROADMAP.md), section "Cluster-Betrieb" — the owed work.
