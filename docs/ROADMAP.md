# Roadmap

Index der offenen Schulden. Jeder Eintrag verweist auf das ausführliche
Artefakt (LIM, ADR, Task) — Inhalte werden hier nicht dupliziert.
Reihenfolge ist nach Bearbeitungspriorität sortiert.

## Status (Stand 2026-11-03)

- **Release:** `sc-imap-mcp` 2.1.0 (Tool-Set 2.1.0), Tag `v2.1.0`. Auf
  PyPI liegt nur das Wheel; das sdist wurde abgelehnt, der GitHub-Release
  fehlt (Einträge 31, 32).
- **BDD-Suite:** 419 passed · 0 failed · 34 skipped (`@pending`), lokal
  und in CI auf `6cfb6e5`.
- **Server-Property-Tests:** 8 passed (`server/tests/policy/`).
- **Audit:** seit 8e60c98 eine gemeinsame Hash-Kette für alle lokalen
  Instanzen ([ADR-0032](adr/0032-shared-audit-chain-across-local-instances.md)).
  Der Tageswechsel 2026-10-18 → 2026-10-19 wurde im Betrieb geprüft:
  genau ein `eof_day`, Datei versiegelt, Folgetag kettet an `final_hash`.
- **Abhängigkeiten:** `mcp<2` und Build mit `hatchling<1.32` begrenzt
  (Einträge 31, 41).

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

Offen mit dem Owner zu entscheiden: Eintrag 31 (abweichende Umsetzung bestätigen), 49 (`From` bei strukturierten Entwürfen) und 52 (Pool-Standardwerte bei vielen lokalen Instanzen). Danach die Defekte 44 bis 47 und die Einträge 33 bis 39 unter *Audit und Auslieferung*. Der Pilot der Usability-Matrix (50) liegt beim Owner.

### Smoke-Test beim Wieder-Einstieg

```sh
cd /home/randy/Projekte/scaratec/imap-mcp/bdd
.venv/bin/behave --no-color --format=progress features/
# erwartet: 525 passed, 0 failed, 34 skipped
# Hat semantic-release die Version angehoben, vorher die Metadaten der
# Entwicklungsumgebung auffrischen, sonst schlägt in
# mcp_tool_discovery.feature der Versionsvergleich fehl:
#   cd ../server && .venv/bin/pip install --no-deps -e .

cd /home/randy/Projekte/scaratec/imap-mcp/server
.venv/bin/pytest -q
# erwartet: 410 passed

cd /home/randy/Projekte/scaratec/imap-mcp
bdd/.venv/bin/python -m pytest -q usability/tests
# erwartet: 62 passed (Selbsttests des Usability-Runners, ohne Agenten)
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
| ~~21~~ | ~~Verbindungspool aus ADR-0013 ist Accepted, aber nicht implementiert~~ (**erledigt 2.3.0**, `server/src/imap_mcp/pool.py`, `bdd/features/performance/connection_pool*.feature`) | [ADR-0013](adr/0013-hybrid-connection-pool.md) | **Defekt** — Kapazitätsgrenze je Provider bei N Sidecars |
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

## Audit und Auslieferung (aufgenommen 2026-10-18)

Anlass war der Ausfall vom 2026-10-16: Nach dem Tageswechsel schlug jeder
Tool-Aufruf mit `[Errno 13] Permission denied` auf der versiegelten
Audit-Datei des Vortags fehl, bis der Prozess neu gestartet wurde.
Ursache waren mehrere lokale Server-Instanzen (ein stdio-Server je
MCP-Client-Sitzung), die sich gegenseitig Dateien versiegelten. Behoben in
8e60c98, Regressionstest `bdd/features/audit/audit_multi_instance.feature`,
Entscheidung [ADR-0032](adr/0032-shared-audit-chain-across-local-instances.md).
Die Einträge unten sind dabei aufgefallen und nicht Teil des Fixes.

| #  | Titel | Verweis | Art / Stand |
|----|---|---|---|
| ~~31~~ | ~~`readme = "../README.md"` liegt außerhalb von `server/`: hatchling ≥ 1.32 bricht den Build ab, das sdist enthält `../README.md` und wird von PyPI mit 400 abgelehnt~~ | `server/pyproject.toml`, 37941fe | **Erledigt 2026-11-03, abweichend von der Entscheidung vom 2026-10-19:** statt einer Kopie `server/README.md` mit Gleichheitsprüfung liefert `server/hatch_build.py` die Haupt-README als `readme` (im Repository aus `../README.md`, im sdist aus der dort als `PYPI_README.md` eingebundenen Kopie); kein Duplikat, das gepflegt werden muss. hatchling-Begrenzung aufgehoben. Ursache des 400 war der sdist-Eintrag `../README.md`. sdist von 2.1.1 nachgereicht; für 2.1.0 lehnt PyPI neue Dateien ab (Release älter als 14 Tage). Owner bitte bestätigen oder Rückbau auf die entschiedene Variante anordnen |
| ~~32~~ | ~~GitHub-Release v2.1.0 fehlt, weil der Release-Job am sdist abbrach~~ | Tag `v2.1.0` | **Erledigt 2026-11-03** wie entschieden: Release mit Text aus dem CHANGELOG und aus dem Tag gebauten Artefakten; v2.1.1 über den neuen `workflow_dispatch`-Nachtrag (97394bd) einschließlich sdist auf PyPI |
| 33 | Audit-Eintrag entsteht erst nach dem Handler: scheitert das Schreiben, ist die IMAP-Aktion schon ausgeführt, aber nicht protokolliert; ein erneuter Versuch kann doppelt anlegen | `server/src/imap_mcp/dispatch.py` (`_audit_tool_call`), [ADR-0021](adr/0021-audit-log-format.md) | **Defekt** — **entschieden 2026-10-19:** schreibende Tools bekommen vorab einen `intent`-Eintrag; ist das Audit nicht schreibbar, wird nichts ausgeführt. Braucht ADR (Erweiterung von ADR-0021) und Feature File; lesende Tools bleiben bei einem Eintrag danach |
| 34 | Wirft ein Handler eine unerwartete Exception, entsteht kein Audit-Eintrag | `dispatch.py` | **Defekt** — Entscheidung offen. Empfehlung: immer protokollieren mit `result: ERROR`, `reason: internal_error` und dem Exception-Typ, ohne Fehlertext (kann Inhalte enthalten) |
| 35 | Aufbewahrung läuft im Betrieb nie: `AuditWriter.rotate()` wird nur vom Test-Tool `_test_run_audit_rotation` aufgerufen; nichts wird komprimiert oder gelöscht | `server/src/imap_mcp/audit.py`, [ADR-0022](adr/0022-audit-retention-and-access-model.md) | **Defekt** — Entscheidung offen, wann die Aufbewahrung läuft (z. B. beim Versiegeln eines Tages unter der Sperre aus ADR-0032) |
| 36 | Altbestand vor 8e60c98: wiederholte `eof_day`-Einträge in versiegelten Dateien (bis über 4000 je Datei), Kettenbrüche durch parallele Instanzen (zuletzt in `2026-10-18.jsonl` vor seq 14) und Einträge ohne Kettenfelder aus dem alten OAuth-Pfad (`<tag>.jsonl` mit nur `token_refresh`, Rechte 0664, nie versiegelt) | ADR-0032 *Neutral* | Entscheidung offen. Empfehlung: Bestand nicht umschreiben, als LIM dokumentieren und einen künftigen Verifier diese Muster tolerieren lassen |
| 37 | Kein Status-Signal, ob das Audit schreibbar ist; der Client sieht einen gesunden Server, bis ein Aufruf scheitert | Bugreport 2026-10-17, Punkt 4 | Feature — Entscheidung offen (Feld in `tool_surface_info` oder `describe_policy`, oder eigenes Tool) |
| 38 | ADR-0032 steht auf *Proposed* | [ADR-0032](adr/0032-shared-audit-chain-across-local-instances.md) | Owner-Entscheidung offen |
| 39 | Während des Ausfalls (2026-10-16 ca. 21:50 bis Neustart) können `create_draft`-Aufrufe Entwürfe angelegt und trotzdem einen Fehler gemeldet haben; Wiederholungen ergeben dann Duplikate | Folge von Eintrag 33 | Betrieb — offen: `[Gmail]/Drafts` der betroffenen Konten lesend auf Duplikate prüfen |
| ~~40~~ | ~~Das Gmail-App-Passwort des Mock-Testkontos lag bis cfcff4f unverschlüsselt im öffentlichen Repository und bleibt in der Historie lesbar~~ | `bdd/mock-gmail/credentials/app-password.secret` | **Erledigt 2026-11-01** — App-Passwort „imap-traces“ beim Provider gelöscht; laut Google zuletzt am 2026-05-06 verwendet, also vor dem Klartext-Commit fb23b4e (2026-05-07). Datei entfernt, Historie nicht umgeschrieben. Pre-commit-Hook `git-crypt status -e` bricht Commits mit unverschlüsselten Dateien unter git-crypt-Filter ab |
| 41 | Server läuft nicht mit dem MCP-SDK 2.x (u. a. `McpError` → `MCPError`); derzeit `mcp>=1.0.0,<2` | 6cfb6e5 | Wartung — Portierung auf mcp 2 als eigene Arbeit |
| 42 | CI installiert Abhängigkeiten ohne Versionsstand; ein neues Release eines Pakets (hier mcp 2.0) ließ nahezu alle BDD-Szenarien mit Fehler enden, ohne dass sich Code geändert hatte | `.github/workflows/bdd.yml`, `ci.yml` | Wartung — Entscheidung offen (Constraints- oder Lock-Datei für CI, getrennt von den Bereichen im Paket) |
| 43 | GitHub Actions: `actions/checkout@v4` und `actions/setup-python@v5` laufen auf Node 20 (veraltet); `ubuntu-latest` wechselt ab 2026-10-19 auf Ubuntu 26 | Annotationen der Läufe vom 2026-10-18 | Wartung — Aktionen anheben, Release-Workflow auf dem neuen Image prüfen |

## Agenten-Nutzbarkeit (aufgenommen 2026-11-03)

Anlass waren Rückmeldungen der Agenten: Entwürfe ließen sich nicht ändern
oder löschen, Suchen endeten still nach sieben Tagen, HTML-Mails waren
mühsam. Behoben in 2.2.0 und 2.2.1 (ADR-0033 bis ADR-0038); gemessen wird
das Verhalten der Agenten künftig mit der Usability-Matrix (ADR-0039,
`usability/`). Bei der Abnahme gegen das echte Gmail nach 2.2.0 fielen
falsch maskierte System-Labels und eine fehlende mUTF-7-Kodierung beim
Löschen der alten Entwurfsfassung auf; beides deckte der Mock nicht ab
(`bdd/features/providers/gmail_draft_lifecycle.feature`).

| #  | Titel | Verweis | Art / Stand |
|----|---|---|---|
| 44 | Sporadisch „IMAP AUTHENTICATE failed:“ mit leerer Meldung, beobachtet 2026-11-01 (2.1.0) und 2026-11-03 (2.2.0), vermutlich direkt nach einer Token-Erneuerung; ein zweiter Versuch gelingt | `imap_core._authenticate_imap` | **Defekt** — offen: Fehlertext von Gmail mitliefern, einmaliger Wiederholungsversuch mit frischem Token |
| 45 | Aus HTML abgeleiteter Klartext verliert Absätze (`<p>A</p><p>B</p>` → `AB`); betrifft `fetch_body` und die Textalternative von `create_draft`/`create_reply_draft` | `imap_core._strip_html`, ADR-0035 | **Defekt** — offen: Blockelemente als Zeilenumbrüche; ändert die Ausgabe von `fetch_body` (Feature File anpassen) |
| 46 | `search`/`list_messages` mit `scope: "all"` oder Kalenderfenster auf einem nicht vorhandenen Ordner liefern eine leere Liste statt `folder_absent`; nur das 7-Tage-Fenster meldet es | `handlers/search.py`, ADR-0025 | **Defekt** — offen |
| 47 | `list_labels` gibt Label-Namen mUTF-7-kodiert zurück | `imap_core.gmail_list_labels` | Defekt (Anzeige) — offen |
| 48 | Gmail-Annahmen des Mocks ohne Abnahme am echten Konto: Entwurf in ein eigenes Label verschieben, Verhalten bei Auto-Expunge-Einstellung „archivieren“, `\Inbox` nach `\Trash`, All Mail ohne Spam | `bdd/mock-gmail/README.md` | Prüfen — bei der nächsten Abnahme mit Probe-Nachrichten verifizieren |
| 49 | Strukturierte `create_draft`-Entwürfe haben keinen `From`-Header; Mail-Clients setzen ihn aus der gewählten Identität | ADR-0035 | Entscheidung offen: Konto-Identität wie bei `create_reply_draft` verwenden? |
| 50 | Pilot der Usability-Matrix (120 Läufe) | `usability/matrix.yaml` | Owner startet bewusst (Kosten, Kontingente) |
| 51 | Traces vom Desktop per OTLP an Grafana Tempo (talos-3l), Payload-Modus `full` | Trello „Meta Projects“ → ToDo | Eigenes Arbeitspaket, wartet auf Abschluss der laufenden Grafana-Umbauten |
| 52 | Pool-Standardwerte aus ADR-0013 (max. 4 Verbindungen, 10 min Leerlauf je Instanz) gehen von einem Prozess aus; lokal läuft je Agent-Sitzung eine Instanz (ADR-0032), bei ~15 Sitzungen droht Gmails Grenze von etwa 15 gleichzeitigen Verbindungen je Konto | `server/src/imap_mcp/pool.py`, ADR-0013 | Entscheidung offen: Standardwerte senken oder Grenze instanzübergreifend durchsetzen. Lokal bis dahin `IMAP_MCP_POOL_MAX_SIZE=2`, `IMAP_MCP_POOL_IDLE_TTL=60` in den Agent-Konfigurationen |

## Querschnitt

- **Error-Path-Analysis-Bilanz** ([error_path_analysis.md](error_path_analysis.md)) wird mit jedem Eintrag oben aktualisiert. Layer L13 (Connection pool) ist seit 2.3.0 überwiegend abgedeckt; Verbindungszahlen sind über den Mock und den IMAP-Proxy beobachtbar. Offen bleiben L13.4 (Acquire-Timeout, Antwortvertrag ungeklärt), L13.7 (Drain bei Token-Erneuerung, Mock kann kein XOAUTH2) und L13.8 (keine IDLE-Nutzer).
- **Spec-Audit-Zyklen** sammeln sich unter `docs/spec-audit/<datum>-cycle-N.md`. Jeder Zyklus prüft die drei Prüfmuster aus BDD-Guidelines §13.2 gegen alle nicht-pending Szenarien.

## Prozess

Diese Datei wird in derselben Änderung aktualisiert, die einen Eintrag schließt oder hinzufügt. Bei Konflikt mit einer LIM gewinnt die LIM (sie ist die ausführliche Form).
