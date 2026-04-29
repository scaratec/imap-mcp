# Roadmap

Index der offenen Schulden. Jeder Eintrag verweist auf das ausführliche
Artefakt (LIM, ADR, Task) — Inhalte werden hier nicht dupliziert.
Reihenfolge ist nach Bearbeitungspriorität sortiert.

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
| 13 | Phase D — HTTP transport + shared_token | Plan `noble-prancing-glacier.md` Phase D | [LIM-0007](limitations/0007-http-transport-deferred.md) |
| ~~14~~ | ~~Phase E — SIGHUP policy reload~~ | Plan Phase E | **Mitigiert 2026-04-28** — 5/7 Szenarien grün; OAuth-Scope-Szenario unter LIM-0003, in-flight-Saga unter LIM-0008 (Saga-Pause-Mechanismus fehlt). |
| ~~15~~ | ~~Phase F-Rest — Audit day-roll & retention~~ | Plan Phase F | **Mitigiert 2026-04-28** — Rotation/gzip/Retention/eof_day grün; external-hook + manual-deletion-detection bleiben unter LIM-0009. |
| ~~16~~ | ~~Phase B-Rest — 5-Tupel-Fallback identity~~ | Plan Phase B | **Resolved 2026-04-28** — beide Szenarien grün; LIM-0006 geschlossen. |
| ~~17~~ | ~~Phase C-Rest — UIDVALIDITY + CAPABILITY-Strip via MITM~~ | Plan Phase C-Rest | **Resolved 2026-04-22** — `bdd/support/imap_proxy.py` + Server-UIDVALIDITY-Check via NOOP; LIM-0005 geschlossen. |
| ~~18~~ | ~~Phase R — Fault-Injection auf MITM-Proxy migrieren~~ | Plan Phase R | **Resolved 2026-04-29** — `fault_injection.py` entfernt, alle 5 Modi wire-level via `imap_proxy.py`; LIM-0004 geschlossen. |

## Querschnitt

- **Error-Path-Analysis-Bilanz** ([error_path_analysis.md](error_path_analysis.md)) wird mit jedem Eintrag oben aktualisiert. Layer L13 (Connection pool) bleibt absichtlich `deferred B` (kein extern beobachtbares Verhalten).
- **Spec-Audit-Zyklen** sammeln sich unter `docs/spec-audit/<datum>-cycle-N.md`. Jeder Zyklus prüft die drei Prüfmuster aus BDD-Guidelines §13.2 gegen alle nicht-pending Szenarien.

## Prozess

Diese Datei wird in derselben Änderung aktualisiert, die einen Eintrag schließt oder hinzufügt. Bei Konflikt mit einer LIM gewinnt die LIM (sie ist die ausführliche Form).
