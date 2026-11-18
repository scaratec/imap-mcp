# Roadmap

Index of open debt and operational work items for `imap-mcp`. Each item
links to its authoritative ADR or Limitation Record.

## Current status (2026-11-17)

- **Release:** `sc-imap-mcp` **`3.0.0`** (`tool_set_version: "3.0.0"`,
  27 tools: 13 read, 10 write, 4 meta; [ADR-0040](adr/0040-message-centric-policy-and-gmail-composition.md)
  through [ADR-0064](adr/0064-durable-account-health-and-token-lifecycle.md)).
- **BDD Suite:** 64 `.feature` files under `bdd/features/`, **0 `@pending`
  scenarios**, 100 % green against Dovecot (`imap-a`, `imap-b`),
  `mock-gmail`, and `mock-oauth`.
- **Error-Path Balance:** 323 enumerated SUT cases across L1–L18 (263
  covered by active BDD scenarios, 60 deferred to unit/integration tests,
  **0 gaps**, plus 8 mock-fidelity checks; see
  [`error_path_analysis.md`](error_path_analysis.md)).
- **Spec-Audit:** [Cycle 01](spec-audit/2026-11-18-cycle-01.md) (`PASS` — `0/0/0/0`, commit range `9f174a6..366c013`).

## Limitation Records status (`docs/limitations/`)

| LIM | Status | Intent | Summary |
|---|---|---|---|
| [0001](limitations/0001-reason-code-symmetry-in-bdd.md) | Superseded by [0024](limitations/0024-reason-code-symmetry-in-contract-tests.md) | must-resolve | Replaced by permanent `Mitigated` contract-symmetry record |
| 0002–0009 | Resolved | must-resolve | V1/V2/3.0 paydowns (mocks, MITM proxy, fallback, HTTP, SIGHUP, day-roll retention) |
| [0010](limitations/0010-production-imap-bdd-gaps.md) | Accepted | must-resolve | Implicit TLS (`IMAP4_SSL` port 993) local PKI fixture in `bdd/docker/` |
| [0011](limitations/0011-bulk-move-deferred.md) | Accepted | must-resolve | `bulk_move` deferred until batch transaction semantics are specified |
| [0012](limitations/0012-wire-response-transparency-uneven-across-tools.md) | Withdrawn | must-resolve | Paid down in `3.0.0` via normalized `ALLOW + ERROR` (`ADR 0044`) |
| [0013](limitations/0013-single-attachment-sink-path.md) | Resolved | must-resolve | Paid down in `3.0.0` via per-caller `0700` sink dirs (`ADR 0047`) |
| [0014](limitations/0014-attachment-sink-requires-caller-filesystem-access.md) | Accepted | permanent | File-sink delivery requires shared/local filesystem readability |
| [0015](limitations/0015-attachment-sink-cleanup-out-of-scope.md) | Accepted | permanent | Sink file retention managed by OS / pod `emptyDir` |
| [0016](limitations/0016-attachment-sink-path-disclosed-in-tool-description.md) | Resolved | must-resolve | Paid down in `3.0.0`: `tools/list` omits host paths (`ADR 0047`) |
| [0017](limitations/0017-expired-oauth-token-not-proactively-or-durably-detectable.md) | Withdrawn | must-resolve | Paid down in `3.0.0` via durable `account_health` in `wal.db` (`ADR 0064`) |
| [0018](limitations/0018-oauth-reauth-not-initiable-from-mcp-no-remediation-guidance.md) | Accepted | permanent | OAuth consent stays out-of-band on operator CLI (`ADR 0059`, `ADR 0052`) |
| [0019](limitations/0019-policy-integrity-requires-out-of-process-trust-boundary.md) | Mitigated | permanent | Policy integrity mitigated via sidecar/OS-user boundary (`ADR 0051`) |
| [0020](limitations/0020-audit-chain-fragments-across-per-agent-instances.md) | Accepted | permanent | Per-pod sidecars maintain independent daily hash chains (`ADR 0051`) |
| [0021](limitations/0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md) | Accepted | permanent | Read-only Kubernetes `Secret` pods bootstrap OAuth out-of-band (`ADR 0059`) |
| [0022](limitations/0022-externally-edited-drafts-lose-their-handle.md) | Accepted | permanent | Externally edited drafts fail `X-SC-Provenance` safely (`draft_not_editable`, `ADR 0042`) |
| [0023](limitations/0023-search-performance-large-mailbox.md) | Resolved | must-resolve | Renumbered from duplicate `0011`; batch fetch & pre-filter (`e94f308`) |
| [0024](limitations/0024-reason-code-symmetry-in-contract-tests.md) | Mitigated | permanent | Structural reason-code symmetry bounded by registry, properties & Spec-Audit |

## Closed in 3.0.0 (previously open entries)

- **#20 (`get_transaction_status` caller check & side-effects):** Closed in [ADR-0046](adr/0046-content-verified-sagas-and-wal-leases.md) and [ADR-0049](adr/0049-single-caller-boundary-and-validated-loading.md) (single-caller startup check + read-only `caller_id`-bound status).
- **#22 (Startup saga recovery):** Closed in [ADR-0046](adr/0046-content-verified-sagas-and-wal-leases.md) (`startup_saga_recovery.feature` active).
- **#23 (`GET /healthz`):** Closed in [ADR-0051](adr/0051-loopback-kubernetes-sidecar-topology.md) (`health_probe.feature` active, including `503` on unwritable audit dir #37).
- **#24 (Projected ConfigMap & Secret reload):** Closed in [ADR-0049](adr/0049-single-caller-boundary-and-validated-loading.md) (`projected_config_reload.feature` active).
- **#26 (HTTP `caller_addr`):** Closed in [ADR-0048](adr/0048-pre-effect-audit-and-test-boundary.md) (`http:<ip>:<port>`).
- **#28 (`expanduser` on config paths):** Closed in [ADR-0049](adr/0049-single-caller-boundary-and-validated-loading.md).
- **#29 (Cross-file `policy:` reference validation):** Closed in [ADR-0049](adr/0049-single-caller-boundary-and-validated-loading.md).
- **#30 (`/healthz` loopback + `503` state):** Closed in [ADR-0051](adr/0051-loopback-kubernetes-sidecar-topology.md).
- **#33 (Pre-effect `intent` audit before writes):** Closed in [ADR-0048](adr/0048-pre-effect-audit-and-test-boundary.md).
- **#34 (Audit `-32602` & unexpected exceptions):** Closed in [ADR-0048](adr/0048-pre-effect-audit-and-test-boundary.md).
- **#35 (Automatic audit retention on UTC day roll):** Closed in [ADR-0048](adr/0048-pre-effect-audit-and-test-boundary.md).
- **#38 (Proposed ADRs 0029–0032):** Superseded by Accepted [ADR-0042](adr/0042-cryptographic-and-wal-provenance.md), [ADR-0048](adr/0048-pre-effect-audit-and-test-boundary.md), [ADR-0051](adr/0051-loopback-kubernetes-sidecar-topology.md), and [ADR-0063](adr/0063-structured-own-draft-composition-and-revision.md).
- **#44 (Single reactive XOAUTH2 refresh retry):** Closed in [ADR-0064](adr/0064-durable-account-health-and-token-lifecycle.md).
- **#45 (Paragraph-preserving HTML-to-text conversion):** Closed in [ADR-0063](adr/0063-structured-own-draft-composition-and-revision.md).
- **#46 (`folder_absent` across all scopes on `search`/`list_messages`):** Closed in [ADR-0044](adr/0044-normalized-envelope-and-code-registry.md).
- **#47 (`list_labels` UTF-8 decoding):** Closed in [ADR-0040](adr/0040-message-centric-policy-and-gmail-composition.md) / [ADR-0056](adr/0056-canonical-folder-resolution-and-code-registry.md).
- **#49 (Structured `create_draft` `From:` header):** Closed in [ADR-0063](adr/0063-structured-own-draft-composition-and-revision.md).
- **#52 (Multi-instance connection pool defaults):** Closed in [ADR-0055](adr/0055-bounded-per-account-connection-pool.md) (`max_size = 2`, `idle_ttl = 120s`).

## Remaining open items

| # | Title | Reference | Kind |
|---|---|---|---|
| 1 | Local TLS (`IMAP4_SSL` port 993) PKI fixture in `bdd/docker/` | [LIM-0010](limitations/0010-production-imap-bdd-gaps.md) | `must-resolve` debt |
| 2 | `bulk_move` tool specification and implementation | [LIM-0011](limitations/0011-bulk-move-deferred.md) | `must-resolve` debt |
| 3 | Official OCI container image build in release workflow | [ADR-0051](adr/0051-loopback-kubernetes-sidecar-topology.md) | Delivery |
| 4 | Port to `mcp` SDK 2.x (currently pinned `< 2`) & lockfile CI pinning | [ADR-0061](adr/0061-python-runtime-and-strict-verification-stack.md) | Maintenance |
| 5 | Execute full 120-run agent usability matrix pilot on `3.0.0` | `usability/matrix.yaml`, [ADR-0060](adr/0060-usability-matrix-evaluation-boundaries.md) | Owner-triggered |

## Smoke-test commands

```sh
# 1. Start Docker fixtures (Dovecot imap-a, imap-b + mock-oauth)
cd bdd
docker compose -f docker/docker-compose.yml up -d

# 2. Run full BDD suite (zero skipped, zero failed)
.venv/bin/behave --no-color --format=progress features/

# 3. Run server unit, contract-registry, and property tests + strict typing + lint
cd ../server
.venv/bin/pytest -q
.venv/bin/mypy --strict src/imap_mcp
.venv/bin/ruff check src tests

# 4. Run usability runner deterministic self-tests
cd ..
bdd/.venv/bin/python -m pytest -q usability/tests
```
