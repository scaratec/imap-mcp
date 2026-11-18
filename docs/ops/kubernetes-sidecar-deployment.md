# Running imap-mcp 3.0.0 as a Kubernetes sidecar

Operator guide for the deployment topology decided in
[ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md): one
`imap-mcp` sidecar container inside each agent pod, bound to `127.0.0.1`
and serving strictly one configured caller ([ADR-0049](../adr/0049-single-caller-boundary-and-validated-loading.md)).

## 1. Pod topology & trust boundary

```text
Pod: <agent-workload> (replicas: 1, strategy: Recreate, shareProcessNamespace: false)
  ├── container: agent
  │     ro  /sink               emptyDir (agent reads downloaded attachments; cannot create symlinks or files)
  │     env MCP endpoint = http://127.0.0.1:8080/mcp
  │
  └── container: imap-mcp (non-root UID 65532)
        ro  /config             ConfigMap projection (accounts.yaml, single-entry callers.yaml, policies/*.yaml)
        ro  /secrets            Secret projection (caller token, IMAP credential, provenance_hmac_key)
        rw  /state              standalone ReadWriteOnce PVC (audit/, wal.db)
        rw  /sink               emptyDir (written exclusively via ADR 0047 openat + O_NOFOLLOW)
```

Mounting `/sink` `readOnly: true` in the agent container together with
`openat` + `O_CREAT | O_EXCL | O_NOFOLLOW` ([ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md))
eliminates symlink and file-tampering vectors while keeping `/config`,
`/secrets`, and `/state` completely outside the agent's mount namespace
([LIM-0019](../limitations/0019-policy-integrity-requires-out-of-process-trust-boundary.md)).

## 2. Containerfile

```dockerfile
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends gnupg \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "sc-imap-mcp[tracing]==3.0.0"

RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin imapmcp
USER 65532:65532

ENTRYPOINT ["imap-mcp"]
```

## 3. Complete Kubernetes manifest (3.0.0)

### 3.1 ConfigMap

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: imap-mcp-config
data:
  accounts.yaml: |
    accounts:
      - id: company-mail@example.com
        provider: imap-standard
        host: imap.example.com
        port: 993
        user: company-mail@example.com
        auth:
          type: password
          secret_ref: secret://accounts/company-mail/password
        token_cache: memory_only

    secret_store:
      backend: file_dir
      path: /secrets
      acknowledge_plaintext: true

    audit:
      directory: /state/audit
      hot_days: 90
      warm_days: 275
      delete_after_days: 365

    wal:
      path: /state/wal.db

    attachment_sink:
      directory: /sink

  callers.yaml: |
    callers:
      - id: invoice-agent
        policy: invoice-policy
        auth:
          type: shared_token
          token_secret_ref: secret://callers/invoice-agent/token

  invoice-policy.yaml: |
    name: invoice-policy
    accounts:
      company-mail@example.com:
        - path: INBOX/Rechnungen
          mode: whitelist
          default: NONE
          rules:
            - match: { from_domain: hornbach.de }
              grant: FULL
```

### 3.2 Secret & PVC

Project secrets into nested paths (`callers/<id>/token`,
`accounts/<id>/password`, `server/provenance_hmac_key`) with
`defaultMode: 0440` and `fsGroup: 65532`, and allocate a standalone
`ReadWriteOnce` `PersistentVolumeClaim` for `/state` so workload deletion
never destroys the audit chain ([LIM-0020](../limitations/0020-audit-chain-fragments-across-per-agent-instances.md)).

### 3.3 Deployment (`Recreate`, `exec` probe on `/healthz`)

- Set `replicas: 1`, `strategy: { type: Recreate }`, and leave
  `shareProcessNamespace: false`.
- Mount `/sink` with `readOnly: true` on `agent` and read-write on
  `imap-mcp`.
- Because `--host 127.0.0.1` is unreachable from the node kubelet, use an
  in-container `exec` liveness probe querying `http://127.0.0.1:8080/healthz`:
  ```yaml
  livenessProbe:
    exec:
      command:
        - python
        - -c
        - "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"
    initialDelaySeconds: 10
    periodSeconds: 20
    timeoutSeconds: 5
  ```
  `GET /healthz` returns `200 {"status": "ok"}` when healthy and `503
  {"status": "unavailable"}` if `/state/audit` loses writability
  ([ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md)).

## 4. Operational guarantees in 3.0.0

- **Validated startup & atomic projected reload ([ADR-0049](../adr/0049-single-caller-boundary-and-validated-loading.md)):**
  Missing `policies/` files, broken caller->policy->account references,
  multiple callers in `callers.yaml`, or stray `IMAP_MCP_FAKE_NOW_*` /
  `IMAP_MCP_CRASH_AT` variables without `IMAP_MCP_TEST_MODE=1` fail
  startup immediately. `SIGHUP` reads a consistent `..data` generation.
- **Automatic startup saga recovery ([ADR-0046](../adr/0046-content-verified-sagas-and-wal-leases.md)):**
  Container restarts automatically recover non-terminal WAL sagas under
  SQLite leases before serving traffic.
- **Automatic audit retention & safe root hook ([ADR-0048](../adr/0048-pre-effect-audit-and-test-boundary.md)):**
  UTC day sealing automatically gzips warm files and unlinks expired
  archives; `external_root_hook` executes without a shell under a
  scrubbed environment.
- **Out-of-pod OAuth bootstrap ([LIM-0021](../limitations/0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md)):**
  Bootstrap OAuth accounts on an operator workstation with
  `imap-mcp-oauth-bootstrap`, project the refresh token via a Kubernetes
  `Secret`, and set `token_cache: memory_only`.

## 5. References

- [ADR-0046](../adr/0046-content-verified-sagas-and-wal-leases.md) — startup recovery & WAL leases
- [ADR-0047](../adr/0047-per-caller-symlink-safe-attachment-delivery.md) — symlink-safe `/sink` delivery
- [ADR-0048](../adr/0048-pre-effect-audit-and-test-boundary.md) — pre-effect audit & retention
- [ADR-0049](../adr/0049-single-caller-boundary-and-validated-loading.md) — single-caller & ConfigMap generation validation
- [ADR-0050](../adr/0050-secret-stores-and-oauth-posture.md) — secret stores & OAuth posture
- [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md) — sidecar topology & `/healthz`
