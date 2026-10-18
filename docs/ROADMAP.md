# Roadmap

Index der offenen Schulden. Jeder Eintrag verweist auf das ausführliche
Artefakt (LIM, ADR, Task) — Inhalte werden hier nicht dupliziert.
Reihenfolge ist nach Bearbeitungspriorität sortiert.

## Status (Stand 2026-05-12)

- **BDD-Suite:** 241 passed · 0 failed · 0 skipped.
- **Server-Property-Tests:** 8 passed (`server/tests/policy/`).
- **HEAD:** 0.8.1 + localized Gmail folders + explicit IMAP user +
  flagged predicate.

### LIM-Übersicht

| LIM | Status     | Resolution-Intent | Datum                          |
|-----|------------|-------------------|--------------------------------|
| 0001 | Resolved   | must-resolve      | 2026-05-06                     |
| 0002 | Resolved   | must-resolve      | 2026-05-07                     |
| 0003 | Resolved   | must-resolve      | 2026-05-06                     |
| 0004 | Resolved   | must-resolve      | 2026-04-29                     |
| 0005 | Resolved   | must-resolve      | 2026-04-22                     |
| 0006 | Resolved   | must-resolve      | 2026-04-28                     |
| 0007 | Resolved   | must-resolve      | 2026-04-29                     |
| 0008 | Resolved   | must-resolve      | 2026-05-06                     |
| 0009 | Resolved   | must-resolve      | 2026-05-06                     |
| 0010 | Accepted   | must-resolve      | offen — Production IMAP BDD gaps |
| 0011 | Resolved   | must-resolve      | 2026-05-07                     |
| 0011 | Accepted   | must-resolve      | offen — `bulk_move` (Nummernkollision, siehe LIM-README) |
| 0012 | Proposed   | must-resolve      | offen — Wire-Transparenz ungleich |
| 0013 | Accepted   | permanent         | 2026-06-08                     |
| 0014 | Accepted   | permanent         | 2026-06-08                     |
| 0015 | Accepted   | permanent         | 2026-06-08                     |
| 0016 | Accepted   | permanent         | 2026-06-08                     |
| 0017 | Proposed   | must-resolve      | offen — OAuth-Ablauf nicht erkennbar |
| 0018 | Proposed   | must-resolve      | offen — keine Re-Auth über MCP |
| 0019 | Proposed   | permanent         | offen — Policy-Integrität braucht Trust-Boundary |
| 0020 | Proposed   | permanent         | offen — Audit-Kette fragmentiert je Agent (k8s) |
| 0021 | Proposed   | must-resolve      | offen — OAuth-Bootstrap im Pod unmöglich |
| 0022 | Proposed   | permanent         | offen — extern bearbeitete Entwürfe verlieren den Handle |

### Nächster Block

**Roadmap-Punkt #9 — LIM-0003 Phase 1: Mock-OAuth-Container.**
LIM-0003 muss vor LIM-0002 stehen, weil Gmail-XOAUTH2 den OAuth-Mock
braucht. Konkrete Schritte:

1. `navikt/mock-oauth2-server` Service in
   `bdd/docker/docker-compose.yml` ergänzen, Port-Exposition für die
   Admin-API.
2. `bdd/features/environment.py` `before_all` ergänzen: warten bis
   `/.well-known/openid-configuration` antwortet.
3. Sanity-Check: `curl` gegen die Discovery-URL liefert ein OIDC-
   Doc.

Doku-Pflichtlektüre vor Einstieg:
[LIM-0003](limitations/0003-oauth2-scenarios-not-runnable.md).

### Smoke-Test beim Wieder-Einstieg

```sh
cd /home/randy/Projekte/scaratec/imap-mcp/bdd
.venv/bin/behave --no-color --format=progress features/
# erwartet: 174 passed, 0 failed, 18 skipped

cd /home/randy/Projekte/scaratec/imap-mcp/server
.venv/bin/pytest tests/policy/ -q
# erwartet: 8 passed
```

## Sofort (foundation)

| # | Titel | Verweis | Begründung |
|---|---|---|---|
| ~~1~~ | ~~Spec-Audit-Vorlage anlegen~~ | Task #19, [BDD-Guidelines §13](../../../privat/burn-your-code/BDD_GUIDELINES_v1.8.0_DE.md) | **Erledigt 2026-04-27** — `docs/spec-audit/{README,TEMPLATE}.md`. |
| ~~2~~ | ~~LIM-0001 auflösen — Reason-Code-Symmetrie~~ | [LIM-0001](limitations/0001-reason-code-symmetry-in-bdd.md), Task #20 | **Mitigiert 2026-04-27** — canonical table in ADR-0017 §2.1, `bdd/features/tool_surface/reason_code_contract.feature` (19 Szenarien), `server/tests/policy/test_pdp_properties.py` (8 Properties × 200 Beispiele). Status der LIM: `Mitigated`; offen bleibt nur die Spec-Audit-Beobachtung im Steady State. |

## Kurzfristig (Doku-Lücken)

| # | Titel | Verweis | Status |
|---|---|---|---|
| ~~3~~ | ~~ADR für Test-Only-Mechanismen~~ | [ADR-0023](adr/0023-test-only-control-surface.md) | **Erledigt 2026-04-27** — `IMAP_MCP_CRASH_AT`, `_test_run_recovery`, Underscore-Prefix-Konvention für private Response-Felder dokumentiert. |
| ~~4~~ | ~~`server/tests/` einführen~~ | `server/tests/policy/` | **Erledigt 2026-04-27** als Teil der LIM-0001-Mitigation 6. |
| ~~5~~ | ~~Stale Task-Einträge schließen~~ | Tasks #15, #16 | **Erledigt 2026-04-27** — beide jetzt explizit unter LIM-0007 referenziert; `secret_store_backends.feature` umgetagged von LIM-0003 → LIM-0007. |

## Mittelfristig (Mock-Subprojekte als LIM-Paydown)

| # | Titel | Verweis | Status |
|---|---|---|---|
| 6 | Mock-Gmail Phase 1 — Command-Trace-Erhebung | [LIM-0002](limitations/0002-gmail-scenarios-not-runnable.md), Task #21 | `bdd/mock-gmail/` existiert leer. |
| 7 | Mock-Gmail Phase 2 — IMAP-Mock | LIM-0002, Task #22 | depends on #6 |
| 8 | Mock-Gmail Phase 3 — Validierung + Container | LIM-0002, Task #23 | depends on #7; löst LIM-0002 |
| 9 | Mock-OAuth Phase 1 — navikt-Container | [LIM-0003](limitations/0003-oauth2-scenarios-not-runnable.md), Task #24 | `bdd/mock-oauth/` existiert leer. |
| 10 | Mock-OAuth Phase 2 — Python-Wrapper | LIM-0003, Task #25 | depends on #9 |
| 11 | Mock-OAuth Phase 3 — Validierung mit google-auth | LIM-0003, Task #26 | depends on #10 |
| 12 | Mock-OAuth Phase 4 — Dovecot XOAUTH2 verdrahten | LIM-0003, Task #27 | depends on #11; löst LIM-0003 |

## Langfristig (Phasen aus dem Plan, jeweils mit LIM-Deckung)

| # | Titel | Verweis | LIM |
|---|---|---|---|
| ~~13~~ | ~~Phase D — HTTP transport + shared_token~~ | Plan Phase D | **Resolved 2026-04-29** — Streamable-HTTP + shared_token-Bearer-Auth landeten in Phase D; die residualen 12 Szenarien wurden in Phase D-Rest geschlossen. LIM-0007 Resolved. |
| ~~14~~ | ~~Phase E — SIGHUP policy reload~~ | Plan Phase E | **Mitigiert 2026-04-28** — 5/7 Szenarien grün; OAuth-Scope-Szenario unter LIM-0003, in-flight-Saga unter LIM-0008 (Saga-Pause-Mechanismus fehlt). |
| ~~15~~ | ~~Phase F-Rest — Audit day-roll & retention~~ | Plan Phase F | **Mitigiert 2026-04-28** — Rotation/gzip/Retention/eof_day grün; external-hook + manual-deletion-detection bleiben unter LIM-0009. |
| ~~16~~ | ~~Phase B-Rest — 5-Tupel-Fallback identity~~ | Plan Phase B | **Resolved 2026-04-28** — beide Szenarien grün; LIM-0006 geschlossen. |
| ~~17~~ | ~~Phase C-Rest — UIDVALIDITY + CAPABILITY-Strip via MITM~~ | Plan Phase C-Rest | **Resolved 2026-04-22** — `bdd/support/imap_proxy.py` + Server-UIDVALIDITY-Check via NOOP; LIM-0005 geschlossen. |
| ~~18~~ | ~~Phase R — Fault-Injection auf MITM-Proxy migrieren~~ | Plan Phase R | **Resolved 2026-04-29** — `fault_injection.py` entfernt, alle 5 Modi wire-level via `imap_proxy.py`; LIM-0004 geschlossen. |
| ~~19~~ | ~~Phase D-Rest — LIM-0007 HTTP-Transport-Restarbeiten~~ | Plan Phase D-Rest | **Resolved 2026-04-29** — alle 12 Restszenarien grün; env_var + gpg_file Secret-Stores, stdio Initialize-Failure-Pfad, HTTP-Identity-Immutability, oauth-bootstrap-Stub; LIM-0007 geschlossen. |

## Cluster-Betrieb (ADR-0029, aufgenommen 2026-08-25)

[ADR-0029](adr/0029-kubernetes-sidecar-deployment-topology.md) legt die
Topologie fest: ein `imap-mcp`-Sidecar je Agent-Pod, genau ein Caller je
Instanz, HTTP über den Pod-Loopback. Betriebsanleitung:
[`ops/kubernetes-sidecar-deployment.md`](ops/kubernetes-sidecar-deployment.md).

Der Transport ist **nicht** die offene Arbeit — er existiert seit
LIM-0007 (Resolved). Offen sind die folgenden Punkte. Die als **Defekt**
markierten Zeilen sind keine akzeptierten Schwächen, sondern Fehler, und
bekommen deshalb bewusst keine LIM — eine Limitation Record ist nicht
dafür da, eine saubere Lösung zu ersetzen, die nur aufwendig ist:

| #  | Titel | Verweis | Art |
|----|---|---|---|
| 20 | `get_transaction_status` prüft den Caller nicht gegen den im WAL hinterlegten `caller_id` | ADR-0029 §3, Security Implications, [Betriebsanleitung P13](ops/kubernetes-sidecar-deployment.md) | **Defekt** — Autorisierungslücke, im Code verifiziert; siehe Einschränkung unter der Tabelle |
| 21 | Verbindungspool aus ADR-0013 ist Accepted, aber nicht implementiert | [ADR-0013](adr/0013-hybrid-connection-pool.md) | **Defekt** — Kapazitätsgrenze je Provider bei N Sidecars |
| 22 | Saga-Recovery beim Start | ADR-0029 §8, `bdd/features/transactions/startup_saga_recovery.feature` | Feature — Neustarts sind im Cluster Routine |
| 23 | `GET /healthz` | ADR-0029 §7, `bdd/features/deployment/health_probe.feature` | Feature |
| 24 | Projizierte Config + SIGHUP | ADR-0029 §5, `bdd/features/deployment/projected_config_reload.feature` | Feature |
| 25 | Container-Image neben dem PyPI-Paket (inkl. `gpg`, fester Nicht-Root-UID) | ADR-0029 §9 | Auslieferung |
| 26 | `caller_addr` ist auf HTTP hartkodiert `stdio:pid=<pid>` | ADR-0029 Security Implications | **Defekt** — Audit-Feld ohne Aussagewert |
| 27 | ~~Zwei Audit-Schreibpfade umgehen die Hash-Kette~~ | ADR-0029 Security Implications, [ADR-0021](adr/0021-audit-log-format.md), [ADR-0032](adr/0032-shared-audit-chain-across-local-instances.md) | **Behoben** in 8e60c98 — `token_refresh` und `oauth_bootstrap` schreiben über den AuditWriter |
| 28 | Kein `expanduser` auf konfigurierten Pfaden | ADR-0029 §1 | Stolperfalle im Manifest |
| 29 | Keine Startprüfung „jede `policy:`-Referenz eines Callers löst auf" | ADR-0029 Consequences (Negative), [Betriebsanleitung P1](ops/kubernetes-sidecar-deployment.md) | **Defekt** — fehlendes/leeres `policies/` oder ein unbekannter Policy-Name ergibt einen scheinbar gesunden Server, der alles verweigert; keine Warnung, kein Audit-Eintrag |
| 30 | `/healthz` ist mit der Loopback-Bindung aus ADR-0029 §2 für kubelet-Proben nicht erreichbar | ADR-0029 §7 (Amendment 2026-08-25) | Entscheidung offen — bis dahin nur `exec`-Probe; außerdem ist zu klären, ob der 503-Zustand überhaupt beobachtbar ist |

Einschränkung zu Eintrag 20: die Kompensation „ein Caller je Instanz"
trägt auf stdio, aber der HTTP-Transport ist von sich aus mehr-Caller-fähig
— er authentifiziert gegen eine gemeinsame `callers.yaml` und schreibt in
ein gemeinsames WAL. Für jede HTTP-Instanz mit mehr als einem Caller in
`callers.yaml` ist die Lücke erreichbar, und der Server weist eine solche
Konfiguration nicht zurück. ADR-0029 §3 ist deshalb keine Ordnungsfrage,
sondern die einzige Absicherung.

## Querschnitt

- **Error-Path-Analysis-Bilanz** ([error_path_analysis.md](error_path_analysis.md)) wird mit jedem Eintrag oben aktualisiert. Layer L13 (Connection pool) bleibt absichtlich `deferred B` (kein extern beobachtbares Verhalten).
- **Spec-Audit-Zyklen** sammeln sich unter `docs/spec-audit/<datum>-cycle-N.md`. Jeder Zyklus prüft die drei Prüfmuster aus BDD-Guidelines §13.2 gegen alle nicht-pending Szenarien.

## Prozess

Diese Datei wird in derselben Änderung aktualisiert, die einen Eintrag schließt oder hinzufügt. Bei Konflikt mit einer LIM gewinnt die LIM (sie ist die ausführliche Form).
