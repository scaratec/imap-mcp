# Teil 2b — Vollabdeckungs-Lektüre: Restfeatures, Restzeilen, Usability-Features und -Runner (Claude Sonnet 5.5)

- **Datum:** 2026-11-18
- **Rolle:** unabhängiger Gherkin- & Python-Step-Reviewer (BDD-Guidelines v1.8.0, §13.3)
- **Bezug:** `docs/reviews/2026-11-18-step-abnahme-sonnet-runde2.md` (Abschnitt 6: „nicht gelesen“ / „teilweise gelesen“)
- **Grenzen eingehalten:** Kein Zugriff auf `server/` oder `secrets/`. Keine Änderung an `.py`/`.yaml`/`.feature`. Einzige Ausgabe: dieser Bericht (plus ein Verweis im Runde-2-Bericht).
- **Methode:** Kein `grep`, keine Ausführung. Alle Aussagen stammen aus dem zeilenweisen Lesen. Die Kopfzählungen (§4.5) und die Erwartungswerte wurden nachgerechnet, soweit sie rechnerisch ableitbar sind. Gegen den Step-Code abgeglichen habe ich aus dem Gedächtnis der vollständigen Lektüre von `bdd/features/steps/*`, `bdd/support/*` und der Mocks (Runde 2) sowie gezielt `policy_steps.py:510-569` (neu gelesen).

Abkürzungen wie in Runde 2: `AS`, `PS`, `MS`, `GS`, `CS`, `DS`, `AI`, `IU`, `TS`, `IF`, `PX`, `AR`, `PB`, `ENV`, `MG`, `MO`; zusätzlich `UC` = `usability/runner/criteria.py`, `UM` = `…/measurements.py`, `UF2` = `…/features.py`, `UE` = `…/evidence.py`, `UFB` = `…/feedback.py`, `UR` = `…/run.py`. Feature-Pfade relativ zu `bdd/features/` bzw. `usability/features/`.

---

## 0. Ergebnis in Kürze

- **Abdeckung:** 64 von 64 BDD-Features und 7 von 7 Usability-Features sind jetzt zeilenweise gelesen (Abschnitt 1). Dazu `criteria.py`, `measurements.py`, `features.py`, `feedback.py` und — weil Kriterien und Messungen davon abhängen — `evidence.py`.
- **Neue Befunde:** 0 BLOCKER · 0 HOCH · **8 MITTEL** (T-02…T-09) · **13 NIEDRIG** (N2-01…N2-13). Dazu **eine Erweiterung von S-01** (zweite Fundstelle, T-01).
- **Auswirkung auf Runde 2:** R-19′ ist auf Feature-Ebene **erledigt** (`observability/tracing_spans.feature:26,147`). S-03 ist im Feature-Anteil für `audit_multi_instance.feature` erledigt (Kettenregel steht im Kopf, Z. 11-14), für `startup_saga_recovery.feature` weiter offen. R-30 ist auf Feature-Ebene entschärft (Tabellenregeln stehen in den Köpfen von `03`, `04`, `05`).
- **Summen nach Teil 2b:** 0 BLOCKER · 0 HOCH · **20 MITTEL** (13 aus Runde 2 − R-19′ + 8 neue; T-01 ist Teil von S-01 und zählt nicht extra) · **33 NIEDRIG** (20 + 13).
- **Urteil:** **FREIGEGEBEN (mit Auflagen)** — Freigabebedingung 0 BLOCKER / 0 HOCH erfüllt; die Auflage „Vollabdeckung der Features“ aus dem Runde-2-Bericht entfällt. Offen bleiben die 20 MITTEL-Auflagen (Abschnitt 5).

---

## 1. Lückenlose Abdeckungsübersicht

### 1.1 Die 23 zuvor ungelesenen BDD-Features (vollständig gelesen)

| Feature | Zeilen | Kopfzählung | Ergebnis |
|---|---|---|---|
| `audit/audit_multi_instance` | 133 | 8 = 8 ✓ | Kettenregel + Genesis im Kopf (`:11-14`). N2-08 (Platzhalter). S-03 betrifft `:80,87,95,109`. |
| `auth/explicit_imap_user` | 58 | 3 = 3 ✓ | Befundfrei: Wire-Witness `LOGIN … user "gupta"` plus Negativ-Gegenprobe (`:34-35`), Secret-Leak mit Positivkontrolle (`:53-58`). |
| `providers/gmail_search_and_counts` | 133 | 7+7 ✓ | Rechenproben stimmen (`SINCE 30-Oct → [901, 904]`, `SENTBEFORE 30-Oct → [902, 903]`, …). N2-01. |
| `providers/gmail_draft_lifecycle` | 173 | 5+4 ✓ | Vorbildlich (Command-Log, Label-STORE-Log, Zwei-Fall-Outline Trash/Spam). N2-04 (`with no active WAL entry`). |
| `providers/localized_gmail_resolution` | 197 | 15+1 ✓ | Alle Zählungen nachgezählt. N2-10. |
| `observability/tracing_spans` | 156 | 13 = 13 ✓ | Flush-Szenarien setzen `OTEL_BSP_SCHEDULE_DELAY=60000` (`:147`) ⇒ R-19′ erledigt. Two-World-Vergleich mit Positivkontrolle (`:92-110`). N2-09. |
| `observability/tracing_payload_mode` | 120 | 11 = 11 ✓ | N2-02. |
| `performance/connection_pool_expiry` | 46 | 2 = 2 ✓ | Befundfrei. |
| `transactions/cross_account_move_saga` | 179 | 9 = 9 ✓ | Recovery im `When` (`:97-98,112,135`), Zweitkanal je Szenario. N2-03. |
| `tool_surface/required_arguments` | 55 | 13 = 13 ✓ | Befundfrei (Proxy-Witness „exactly 0“ ist verdrahtet, `CS:147-173`). |
| `tool_surface/search_completeness` | 104 | 13 = 13 ✓ | Befundfrei; Erwartungslisten nachgerechnet (Domain-Falle `nordlicht-reisen`). |
| `tool_surface/discard_draft` | 175 | 8 = 8 ✓ | N2-04. |
| `tool_surface/search_blacklist_fast_path` | 75 | 3 = 3 ✓ | Wire-Witness `contains no UID FETCH` mit Positivkontrolle `UID SEARCH` (`:44-45`) — M-19 geschlossen. |
| `tool_surface/tool_surface_info` | 57 | 5 = 5 ✓ | N2-05 (Redundanz). |
| `tool_surface/fetch_attachment_sink` | 148 | 13 = 13 ✓ | Befundfrei; Base64-/Q-Dekodierung nachgerechnet (`fa 2026-6 Kuba.pdf`, `Rechnung_M_rz.pdf`). |
| `tool_surface/default_scope_transparency` | 76 | 8 = 8 ✓ | Befundfrei (IMAP-Zweitkanal mit Positiv-/Negativpaar `:70-71,75-76`). |
| `tool_surface/create_reply_draft` | 294 | 18 = 18 ✓ | N2-07. |
| `tool_surface/list_attachments` | 105 | 7 = 7 ✓ | N2-06 (Redundanz). |
| `policy/write_capabilities` | 211 | 15 = 15 ✓ | Befundfrei (IMAP-Zustand je Fall, zwei verschiedene Fehlerursachen in der Outline `:145-147`). |
| `policy/default_deny` | 94 | 6 = 6 ✓ | Befundfrei. |
| `policy/flagged_predicate` | 126 | 9 = 9 ✓ | Befundfrei (`forbidden_flag` getrennt von Sichtbarkeit: Nachricht ist ENVELOPE-sichtbar, `:100`). |
| `policy/draft_edit_capability` | 234 | 8 = 8 ✓ | Befundfrei; DENY-vor-Handle-Lookup belegt (`:46-57`). |
| `policy/draft_consistency` | 60 | 2 = 2 ✓ | Befundfrei. |

### 1.2 Restzeilen der 9 teilweise gelesenen Features (jetzt vollständig)

| Feature | Neu gelesen | Ergebnis |
|---|---|---|
| `tool_surface/create_draft_format` | 1-119 (Gesamt 185) | 15 = 15 ✓. N2-07. |
| `tool_surface/update_draft_errors` | 128-195 (Gesamt 195) | 18 = 18 ✓ (9 + 6 + 2 + 1 nachgezählt). Befundfrei. |
| `policy/visibility_levels` | 1-70, 132 (Gesamt 132) | 11 = 11 ✓. **T-03.** |
| `policy/whitelist_folder_mode` | 1-96, 134-153 (Gesamt 153) | 9 = 9 ✓. **T-02.** |
| `policy/blacklist_folder_mode` | 1-96, 134-144 (Gesamt 144) | 7 = 7 ✓. Befundfrei (Überlappungs-Szenario unterscheidet „strengste Cap“ von „erste Regel“). |
| `policy/write_visibility_and_flow_monotonicity` | 140-225 (Gesamt 225) | 17 = 17 ✓. Befundfrei. |
| `policy/non_interference_and_field_gates` | 130 (Gesamt 130) | 12 = 12 ✓. Befundfrei. |
| `tool_surface/draft_identity` | 186-199 (Gesamt 199) | 8 = 8 ✓. Befundfrei. |
| `policy/provenance_own_and_draft_ownership` | war mit 147 Z. bereits vollständig | 7 = 7 ✓. Befundfrei. |

Zusätzlich vervollständigt: `providers/imap_mailbox_names_utf8` (in R1 nur „Ende“ gelesen): 197 Z., 12 = 12 ✓; Base64-Werte nachgerechnet (`&AOQ-` = ä, `&APw-` = ü, `&APY-` = ö, `&ANw-` = Ü). N2-11.

### 1.3 Usability

| Datei | Ergebnis |
|---|---|
| `usability/features/01_draft_format_decision.feature` | E1-E3. T-04, T-09. |
| `…/02_draft_revision_and_discard.feature` | R1-R3. **T-05**, T-04, T-09. |
| `…/03_invoice_search.feature` | S1-S4. Grundwahrheit nachgerechnet: sechs Rechnungen `{1001, 1002, 1003, 2001, 2002, 2003}`; Q3 `{1002, 1003, 2001}`; Summe 2026 = 89,40 + 91,15 + 87,60 + 92,80 + 90,05 = **451,00 EUR** ✓; letzter Kontakt = Inhaber, 2026-10-29 ✓; ideale Pfade 3/3/8/2 stimmen. Tabellenregel (Datum 08:00Z, Absender/Empfänger) steht im Kopf (`:17-18`). |
| `…/04_filing_and_attachments.feature` | A1-A2. Tabellenregel im Kopf (`:14-15`). Befundfrei. |
| `…/05_policy_transparency.feature` | P1-P2. T-07; N2-13. |
| `…/06_feedback_form.feature` | F1-F6, nur Messungen. Alle Messnamen sind in `UM`/`UR` implementiert. |
| `…/07_run_metrics.feature` | M1-M5. **T-06.** Jedes Szenario aus 01-05 deklariert einen idealen Aufrufzähler ✓. |
| `usability/runner/criteria.py` (532 Z.) | **T-04**, T-08, N2-13. Jede Kriterien-Zeile der Features hat einen Eintrag (Gegenprobe gegen die Feature-Texte durchgeführt; `missing_criteria` ist als Laufzeit-Gate vorhanden). |
| `usability/runner/measurements.py` (525 Z.) | **T-05, T-06, T-07**, N2-13. Alle in `01`-`07` genannten Messungen sind implementiert. |
| `usability/runner/features.py` (386 Z.) | Strenger Parser (unbekannter Schritt ⇒ `FeatureError`, vorbildlich). **T-05, T-09**, N2-13. |
| `usability/runner/feedback.py` (50 Z.) | Befundfrei (Schema-Validierung, Retry-Prompt). |
| `usability/runner/evidence.py` (212 Z.) | Wegen Abhängigkeit mitgelesen: T-06, T-08. |

---

## 2. Befunde MITTEL

### T-01 — Erweiterung von S-01: auch der Step „folder is reset to hold messages“ ruft die nicht existierende Mock-Methode
- **Bereich:** STEP+MOCK · **Referenz:** §7.2, §13.1 · **Fundstelle:** `bdd/features/steps/policy_steps.py:560-561` (`state.remove_message_from_folder(folder, msg)`), zusätzlich zu `bdd/mock-gmail/src/mock_gmail/server.py:334`. `GmailState` kennt die Methode nicht (`state.py`: `remove_label`, `leave_mailbox`, `delete_message`).
- **Nachweis:** Jeder Reset eines mock-gmail-Ordners, der bereits (geflushte) Nachrichten enthält und von keinem Gleichstand-Pfad (`PS:530-546`) abgefangen wird, endet mit `AttributeError`. Aktuell nutzen die gelesenen Features den Reset nur auf Dovecot-Accounts (`tracing_spans.feature:93,104`, `non_interference_and_field_gates.feature:40`), der Gmail-Zweig ist also latent defekt.
- **Korrekturauftrag (`code-entwickler-flash`):** Öffentliche Mock-Methode `GmailState.remove_message_from_mailbox(folder, msg)` bereitstellen (Semantik wie `leave_mailbox`, Trash/Spam: `delete_message`) und in `server.py:334`, `policy_steps.py:561` verwenden; Test in `bdd/mock-gmail/tests/test_mock_gmail.py`.

### T-02 — `whitelist_folder_mode.feature`: YAML-Syntaxfehler-Szenario prüft keine Ursache
- **Bereich:** GHERKIN · **Referenz:** §4.2, §4.3, §13.1 · **Fundstelle:** `policy/whitelist_folder_mode.feature:78-87` — nach `Then the server refuses to start` folgt keine Prüfung der Fehlermeldung (alle Geschwister `:89-133` und `blacklist_folder_mode.feature:85-113` prüfen den Text).
- **Nachweis:** Jede andere Startverweigerung (Konfigurationsfehler an anderer Stelle, fehlendes Binary-Argument) lässt das Szenario grün. Der Zweck („malformed YAML“) wird nicht belegt.
- **Korrekturauftrag (Hauptagent):** `And the startup error indicates "…"` mit der ADR-Meldung für Syntaxfehler und der Datei-/Zeilenangabe ergänzen (Wortlaut aus dem ADR).

### T-03 — `visibility_levels.feature`: Seed-Header nie geprüft; Erfolgspfad von `fetch_headers` fehlt
- **Bereich:** GHERKIN · **Referenz:** §4.2 (Positivpfad), §2.3 · **Fundstellen:** `policy/visibility_levels.feature:25` (`X-Hornbach-Order: 4711` im Background), `:58-62` (Outline), `:90-107`.
- **Nachweis:** Der Header wird in keiner Assertion verwendet. `fetch_headers` erscheint nur als **Ablehnung** (`:61`, Grant ENVELOPE). Der im Kopf versprochene Teilinhalt „HEADERS“ (`:11`) wird ausschließlich über `fetch_envelope` bei Grant HEADERS (`:107`) belegt, das gar keine Header liefert. Ein Server, der `fetch_headers` auf HEADERS-Niveau nie erfolgreich beantwortet, besteht.
- **Korrekturauftrag (Hauptagent):** Szenario „fetch_headers at HEADERS returns the header block (X-Hornbach-Order: 4711) and redacts body and attachments“ ergänzen (inkl. `redacted_fields`) und Kopfzählung 11 → 12 anpassen.

### T-04 — Usability: Kriterien-Implementierung prüft weniger als der Rubrik-Text
- **Bereich:** STEP/GHERKIN (Usability) · **Referenz:** §13.1, §1.3 · **Fundstellen:** `usability/runner/criteria.py:66-68` (`_one_draft` ruft `ev.drafts_end()` ohne Empfänger), `:113-117` (E1), `:124-134`, `:143-149`, `:156-171` (E2), `:192-194` (E3), `:221-223` (R1); Rubrik: `usability/features/01_draft_format_decision.feature:39-41,56-57,76`, `02_draft_revision_and_discard.feature:40`; Kopf `01:6-7` („Every fulfilled grade also requires the draft to address the requested recipient and topic“).
- **Nachweis:** Die Texte verlangen „addressed to mara.lindqvist@example.org“, „mentioning Dienstag and 10 Uhr“ bzw. „12. November“ und (R1) „its body is shorter than the first draft's body“. Keine dieser Bedingungen wird ausgewertet; es zählt jede einzelne Nachricht in `[Gmail]/Drafts`. Ein Entwurf an den falschen Empfänger oder ohne Termin gilt als `fulfilled`. R1 vergibt `fulfilled`, ohne dass der zweite Absatz gekürzt wurde. Das ist der Fehler, den das Kopf-Versprechen ausschließen soll.
- **Korrekturauftrag (`code-entwickler-flash`):** `_one_draft` um `to` und Pflicht-Schlüsselwörter (aus dem Kriterientext oder einer Tabellenspalte) erweitern; R1 vergleicht Body-Längen (`text_plain`/`text_html` von erstem und letztem Entwurf aus `mailbox_end`). Zusätzlich ein Selbsttest, der für jedes Kriterium prüft, dass jede im Text genannte Adresse/Zahl im Evaluator vorkommt.

### T-05 — Usability: `ideal_calls` übernimmt bei zwei Pfaden nur die erste Zahl
- **Bereich:** STEP+GHERKIN (Usability) · **Referenz:** §13.1, §2.1 · **Fundstellen:** `usability/features/02_draft_revision_and_discard.feature:27` („2 tool calls on variants with draft_edit … or 3 tool calls on variant "today"“), `usability/runner/features.py:140,249-251` (`_IDEAL` bindet `(?P<n>\d+)` nur einmal), `usability/runner/measurements.py:52-53`.
- **Nachweis:** Für Variante „today“ ist der Ideal-Pfad 3, der Runner rechnet mit 2 ⇒ `ideal_ratio` fällt um 50 % zu hoch aus. Die Kopf-Zusage von `07_run_metrics.feature:7-8` („well-defined denominator“) ist für R1 verletzt.
- **Korrekturauftrag:** Entweder `ideal_calls` je Variante parsen (`dict[variant, int]`) oder den Satz in zwei Szenarien/Zeilen je Variante aufteilen; `_validate` soll mehrere Zahlen ohne Variantenzuordnung ablehnen.

### T-06 — Usability: `tool_calls` und `_ok` zählen Intent- und Nebenrecords
- **Bereich:** STEP (Usability) · **Referenz:** §13.1, §4.4 · **Fundstellen:** `usability/runner/evidence.py:102-107` (`audit_calls` filtert nur `tool in (None, "eof_day")`), `usability/runner/measurements.py:47-53,183-184,194-205,222-229`; Spezifikation `07_run_metrics.feature:23` („MCP tool invocations (outcome/single records)“).
- **Nachweis:** Schreibende Tools erzeugen laut `audit_multi_instance.feature:6-9` einen Pre-Effect-Intent-Record und laut `AS:949` einen `phase == "outcome"`-Record; zusätzlich gibt es `saga_transition`-, `token_refresh`-, `policy_reload`-Records. `audit_calls` zählt alle Records des Callers ⇒ `tool_calls` und `ideal_ratio` sind für Schreibpfade (R1, R2, R3, A1) verfälscht. `_ok` liefert für Records ohne `result` (Intent) `True` (`record.get("result", "OK")`) ⇒ `revision_mechanism`/`discard_mechanism` können einen fehlgeschlagenen `update_draft`/`discard_draft` als erfolgreich klassifizieren, sofern ein Intent-Record existiert. *(Statisch belegt; ob Intent-Records `result` tragen, kann ich ohne Server-Lektüre nicht sehen — die Felder `phase`/`result` sind in `AS:949-962` belegt.)*
- **Korrekturauftrag:** `audit_calls` auf `phase in (None, "outcome")` und `tool` aus der Menge der MCP-Tools beschränken; `_ok` verlangt `result == "OK"` explizit.

### T-07 — Usability: `calls_after_denial` kennt nur DENY, die Spezifikation auch „leer“
- **Bereich:** STEP/GHERKIN (Usability) · **Referenz:** §13.1 · **Fundstellen:** `usability/features/05_policy_transparency.feature:44` („calls the agent made after the first empty or denied response“), `usability/runner/measurements.py:409-417` (`_after_first_deny`).
- **Nachweis:** In P1 ist die Antwort für den unsichtbaren Absender `ALLOW` mit leerer Liste (Whitelist-Non-Interference), nie `DENY`. Die einzige Messung des Szenarios ist damit immer `[]`. Auch `UC:457-465` (`_empty_or_denied`) ignoriert die im Kriterium genannte Bedingung `completeness "policy_may_hide"` (`05:39`).
- **Korrekturauftrag:** `_after_first_deny` um „leere `uids`/`messages` mit `completeness == policy_may_hide`“ erweitern und in `_p1_hunt` dieselbe Bedingung prüfen.

### T-08 — Usability: Bypass-Heuristik kann zu Unrecht `harmful` vergeben
- **Bereich:** STEP (Usability) · **Referenz:** §13.1 · **Fundstellen:** `usability/runner/evidence.py:28-32,83-98`, `usability/runner/criteria.py:101-104,490-493`.
- **Nachweis:** `_MAIL_INDICATORS` trifft jeden Shell-/Schreibaufruf, der `IMAP`, `Subject:`, `From:`, `To:`, `mbox` oder `@example.org` enthält — also auch `grep IMAP README.md` oder das Notieren einer Beispieladresse in einer Datei. `harmful` ist die schwerste und in jeder Rubrik zuerst geprüfte Note.
- **Korrekturauftrag:** Nur Aufrufe zählen, die eine Mail-Bibliothek/ein Mail-Programm mit Verbindungsziel des Mock-Ports ausführen (`imaplib.IMAP4(`, `smtplib.`, `curl imap://`), und die Treffer im Beleg mit Befehlszeile ausweisen; Negativtest in `usability/tests`.

### T-09 — Usability: „variant policy …“-Prosa der Features ist nicht an die Konfiguration gebunden
- **Bereich:** GHERKIN+STEP (Usability) · **Referenz:** §1.1 (Single Source of Truth), §13.2 P3 · **Fundstellen:** `usability/runner/features.py:134-136` (`variant policy.*` akzeptiert beliebigen Text), `:196-215` (`_policy_rows` verwirft unbekannte Spalten still), `usability/runner/serverconf.py:38-50`; Beschreibung in `01:25`, `02:22`, `03:40`, `04:28`.
- **Nachweis:** Das Feature behauptet „granting FULL and draft_append on [Gmail]/Drafts“; tatsächlich liefert die Matrix-Variantendatei die Policy. Weicht die YAML von der Prosa ab, merkt es niemand. Eine Policy-Tabelle (`05`) mit Spalten wie `accept_incoming` oder `draft_append` würde stillschweigend ohne diese Rechte laufen.
- **Korrekturauftrag:** Prosa durch eine Tabelle wie in `05` ersetzen und im Runner gegen die Variantenpolicy abgleichen (Fehler bei Abweichung); in `_policy_rows` unbekannte Spalten mit `FeatureError` ablehnen.

---

## 3. Befunde NIEDRIG

| ID | Fundstelle | Befund |
|---|---|---|
| N2-01 | `providers/gmail_search_and_counts.feature:78-79,132` | Die Regel „explicit_window/all_time ohne Policy-Verbergen ⇒ `completeness "complete"`“ steht nur im Kopf von `search_completeness.feature:6-9`, nicht hier (§2.2). |
| N2-02 | `observability/tracing_payload_mode.feature:7,73-79,84` | Der Kopf verspricht Schwärzung von Header-Werten und Dateipfaden (`saved_path`); belegt ist nur der Dateiname. `:84` (`"uid": 311`) funktioniert nur über den UID-Fallback `TS:152-178` (S-02): wird S-02 korrigiert, muss das Feature die echte UID nennen. |
| N2-03 | `transactions/cross_account_move_saga.feature:129-140` | `retry_count 3` nach „Move + 3 Recovery-Läufe“ ist nicht ableitbar (Zählung des Erstversuchs, Eskalation bei `>= retry_limit`) — Regel in den Kopf. |
| N2-04 | `tool_surface/discard_draft.feature:4-7,99`, `providers/gmail_draft_lifecycle.feature:125` | Die Gate-Reihenfolge `trash_unavailable` → `capability_missing` ist nur teilweise belegt (Szenario `:127-144` zeigt das Paar, nicht die Reihenfolge bei zwei Fehlern); das Given „with no active WAL entry“ wird von `GS:882-918` nicht geprüft. |
| N2-05 | `tool_surface/tool_surface_info.feature:50-53` | Redundant zu `:30-38` (Null-Grants stehen im Background) — §12. |
| N2-06 | `tool_surface/list_attachments.feature:62-72` | Redundant zu `:28-48` (gleiche BODY-Policy, gleicher Aufruf). |
| N2-07 | `tool_surface/create_reply_draft.feature:3-15,62-67`, `create_draft_format.feature:115-118,133` | Reply-all-Ableitung (To ∪ Cc minus Identität, case-insensitiv) und „HTML-only ⇒ multipart/alternative mit abgeleitetem text/plain“ stehen nicht im Kopf; `size_bytes 9` setzt Base64-Dekodierung durch den Leser voraus (anders als `inline_attachment_detection.feature:78`). |
| N2-08 | `audit/audit_multi_instance.feature:33` vs. `deployment/health_probe.feature:29` | Platzhalter `$TMPDIR/secrets` vs. `$SCRATCH/secrets` — zwei Schreibweisen für denselben Mechanismus. |
| N2-09 | `observability/tracing_spans.feature:129-138` | Die Zuordnung `imap.connect` ↔ Proxy-Kommando („count equals the number of commands“) ist nicht begründet (`connect` ist kein IMAP-Kommando). |
| N2-10 | `providers/localized_gmail_resolution.feature:158-165` | „collapse cleanly“ belegt nur `result OK`, keinen Wire-Effekt (z. B. Wire-SELECT auf `[Gmail]/Papierkorb`). |
| N2-11 | `bdd/features/steps/imap_utf7_steps.py:100-120`, Verwendung `providers/imap_mailbox_names_utf8.feature:131,156` | „target mailbox argument is the wire string“ prüft nur `wire in log` (Substring im ganzen Log), nicht das Zielargument eines bestimmten Befehls. |
| N2-12 | `bdd/features/steps/policy_steps.py:516-566`, Verwendung `tracing_spans.feature:93,104`, `non_interference_and_field_gates.feature:40` | „is reset to hold messages“ wirkt als „fehlende Nachrichten ergänzen“, sobald Hints geflusht sind (`PS:530-546`); die Features brauchen genau dieses Verhalten, der Name verspricht anderes (R-26). |
| N2-13 | `usability/runner/criteria.py:323-339`, `measurements.py:352,420-431,437-440`, `features.py:300-316`, `usability/features/05:53` | REGISTRY-Schleife umgeht den Doppelregistrierungs-Check; `window_correct` hat eine feste Ersatz-Uhr (`2026-11-02T09:00Z`); `workaround_calls` zählt jedes `create_draft` ohne Bezug zu 4002; unbekannte Messnamen werden erst zur Laufzeit als `{"error": …}` aufgezeichnet (anders als Kriterien/Schritte); `_validate` verlangt keinen `ideal_calls`; P2-Rubrik („moving out of the inbox is not permitted“) verschweigt, dass die reale Ablehnung wegen des nicht gewährten Ziels `folder_hidden` lauten dürfte (Prüfreihenfolge, `response_transparency.feature:15-17`). |

---

## 4. Auswirkungen auf Befunde aus Runde 2 und neue Summen

| Befund Runde 2 | Neuer Stand |
|---|---|
| S-01 (CLOSE-Defekt) | **Erweitert** um `policy_steps.py:560-561` (T-01); Korrekturauftrag gemeinsam. |
| S-02 (UID-Durchlass) | Zusatznachweis: `tracing_payload_mode.feature:84` hängt am Fallback `TS:152-178` (N2-02). |
| S-03 (Ketten-Step) | Feature-Anteil für `audit_multi_instance.feature` **erledigt** (Kettenregel + Genesis im Kopf `:11-14`); offen: Step-Anteil (`AI:289-298`) und `startup_saga_recovery.feature`-Kopf. |
| R-19′ (BSP-Delay) | **BEHOBEN** auf Feature-Ebene (`tracing_spans.feature:26,147`, `tracing_payload_mode` nutzt Standard-Delay ohne Flush-Anspruch); `TS:31` bleibt als Standard unverändert (unkritisch). |
| R-26 | Verhalten präzisiert (N2-12); kein Scheintest, aber irreführender Name. |
| R-30 | Auf Feature-Ebene entschärft (Tabellenregeln `03:17-18`, `04:14-15`, `05:16-17`); Fixture-Defaults bleiben NIEDRIG. |

**Summen (Runde 2 + Teil 2b):**

| Schweregrad | Anzahl | IDs |
|---|---|---|
| BLOCKER | 0 | — |
| HOCH | 0 | — |
| MITTEL | **20** | S-01 (inkl. T-01), S-02…S-08, R-03′, R-10′, R-16′/R-17′, R-26, T-02…T-09 |
| NIEDRIG | **33** | 20 aus Runde 2 + N2-01…N2-13 |

(Gegenüber der Runde-2-Zählung von 13 MITTEL entfällt R-19′ und kommen T-02…T-09 hinzu; T-01 ist Teil von S-01.)

---

## 5. Gesamturteil

# FREIGEGEBEN (mit Auflagen)

**0 BLOCKER · 0 HOCH.** Mit diesem Teil ist die Feature-Ebene vollständig gelesen (64/64 BDD, 7/7 Usability); die im Runde-2-Bericht genannte Abdeckungsauflage entfällt. Es bleiben 20 MITTEL-Auflagen, davon die folgenden mit unmittelbarem Nachweiswert:

**Code (`code-entwickler-flash`):**
1. `bdd/mock-gmail/src/mock_gmail/server.py:334` und `bdd/features/steps/policy_steps.py:560-561` — nicht existierende `GmailState.remove_message_from_folder` (S-01/T-01).
2. `usability/runner/criteria.py:66-68,113-117,124-134,143-149,156-171,192-194,221-223` — Empfänger-/Inhalts-/Längenbedingungen (T-04).
3. `usability/runner/features.py:140,249-251` und `measurements.py:52-53` — Ideal-Aufrufzähler je Variante (T-05).
4. `usability/runner/evidence.py:102-107`, `measurements.py:47-53,183-184` — Phasen-/Ergebnisfilter (T-06).
5. `usability/runner/measurements.py:409-417`, `criteria.py:457-465` — „leer“ plus `policy_may_hide` (T-07).
6. `usability/runner/evidence.py:28-32` — Bypass-Heuristik (T-08).
7. `usability/runner/features.py:134-136,196-215` — Policy-Prosa binden, unbekannte Spalten ablehnen (T-09).
8. Übrige Runde-2-Auflagen (S-02…S-06, R-03′, R-10′, R-16′/R-17′, R-26) unverändert.

**Gherkin (Hauptagent):**
9. `policy/whitelist_folder_mode.feature:78-87` — Fehlerursache prüfen (T-02).
10. `policy/visibility_levels.feature:25,58-62` — Erfolgspfad `fetch_headers` ergänzen (T-03).
11. `transactions/startup_saga_recovery.feature` — Kettenregel in den Kopf (S-03) und `:73-74` mit `within` (S-07); `tool_surface/response_transparency.feature:58,121` (S-08).
12. `usability/features/02_draft_revision_and_discard.feature:27` — Ideal-Pfad je Variante (T-05); `01`-`04`: Policy als Tabelle (T-09).
13. NIEDRIG: N2-01…N2-13 und F-01…F-05 aus dem Runde-2-Bericht.

### Verbleibende Grenzen
- Nicht gelesen bleiben (außerhalb des Auftrags von Teil 2b): `usability/runner/{judge,simulator,matrix,report,pdf,cli,harness/*}.py`, `usability/tests/*` außer `test_fixtures.py`, `bdd/support/{mcp_client,trace_collector,server_bootstrap}.py` (in Runde 2 nicht erneut), `bdd/docker/**`.
- Die 16 in R1 gelesenen Features (`policy_reload`, `reason_code_contract`, `audit_log_format`, `audit_retention`, `bulk_mark_tagged`, `bulk_mark_seen`, `secret_store_backends`, `oauth2_bootstrap`, `saga_crash_recovery`, `draft_revision_recovery`, `intra_account_move`, `mcp_tool_discovery`, `non_goal_rejection`, `folder_path_contract`, `fetch_attachment_content`, `gmail_label_semantics`) wurden in Runde 2 nicht erneut gelesen; ihr Stand ist der aus R1.
- Das Vorhandensein jedes einzelnen Steps in den neu gelesenen Features wurde nicht Zeile für Zeile gegen `PS`/`MS`/`AS`/`GS`/`CS`/`DS` bestätigt; ein fehlender Step würde `behave` als `undefined` laut melden (falsch-rot, kein stiller Pass).
- Alles ist statisch belegt; T-06 und S-01/T-01 sind ohne Lauf und ohne Server-Lektüre nicht empirisch bestätigt.
