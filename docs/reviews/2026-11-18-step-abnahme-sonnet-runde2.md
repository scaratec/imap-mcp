# Step-, Support-, Mock- und Ehrlichkeits-Abnahme `imap-mcp` 3.0.0 — Runde 2 (Claude Sonnet 5.5)

- **Datum:** 2026-11-18
- **Rolle:** unabhängiger Gherkin- & Python-Step-Reviewer (BDD-Guidelines v1.8.0, §13.3)
- **Maßstab:** `/home/randy/Projekte/privat/burn-your-code/BDD_GUIDELINES_v1.8.0_DE.md`
- **Vorgänger:** `docs/reviews/2026-11-17-step-abnahme-sonnet-3.0.md` (R1: 0 BLOCKER · 8 HOCH · 24 MITTEL · 9 NIEDRIG; 14 `UNGEPRÜFT`)
- **Grenzen eingehalten:** Kein Zugriff auf `server/` oder `secrets/`. Keine Änderung an `.py`/`.yaml`/`.feature`. Einzige Ausgabe: dieser Bericht.
- **Methode:** Es stehen weder `grep` noch Ausführung zur Verfügung. Alle Aussagen stammen aus dem zeilenweisen Lesen der unten genannten Dateien; nichts wurde ausgeführt. Was nicht gelesen wurde, ist in Abschnitt 6 ausdrücklich ausgewiesen.

Abkürzungen (wie R1): `AS` = `bdd/features/steps/assertion_steps.py`, `PS` = `…/policy_steps.py`, `MS` = `…/mcp_steps.py`, `GS` = `…/gmail_steps.py`, `CS` = `…/connection_steps.py`, `OS` = `…/oauth_steps.py`, `DS` = `…/deployment_steps.py`, `AI` = `…/audit_instance_steps.py`, `IU` = `…/imap_utf7_steps.py`, `TS` = `…/trace_steps.py`, `IF` = `bdd/support/imap_fixture.py`, `PX` = `…/imap_proxy.py`, `AR` = `…/audit_reader.py`, `PB` = `…/policy_builder.py`, `ENV` = `bdd/features/environment.py`, `MG` = `bdd/mock-gmail/src/mock_gmail/`, `MO` = `bdd/mock-oauth/src/mock_oauth/`, `UF` = `usability/runner/fixtures.py`, `US` = `usability/runner/serverconf.py`. Feature-Pfade relativ zu `bdd/features/`.

---

> **Nachtrag:** Die Abdeckungsauflage dieses Berichts ist durch `docs/reviews/2026-11-18-step-abnahme-sonnet-restfeatures.md` (Teil 2b: 64/64 BDD-Features, 7/7 Usability-Features) erledigt; maßgebliche Summen und Urteil stehen dort (0 BLOCKER · 0 HOCH · 20 MITTEL · 33 NIEDRIG, **FREIGEGEBEN mit Auflagen**).

## 0. Gesamturteil

# FREIGEGEBEN (bedingt) — 0 BLOCKER · 0 HOCH · 13 MITTEL · 20 NIEDRIG

- Alle acht R1-HOCH-Befunde (R-01…R-08) sind in ihrem Kern **behoben**; für R-02 und R-03 verbleiben Reststellen, die ich als MITTEL führe (S-02, R-03′).
- Alle **14 `UNGEPRÜFT`-Altbefunde sind geschlossen** (Abschnitt 5): 12 BEHOBEN, 2 (M-23, M-28) TEILWEISE mit benanntem Rest. **0 `UNGEPRÜFT` verbleiben.**
- Das Ziel „alle 46 restlichen Feature-Dateien gelesen“ ist **nicht erreicht**: 23 von 46 wurden in Runde 2 gelesen (15 vollständig, 8 in den entscheidenden Abschnitten), **23 Features sind weiterhin ungelesen** (Abschnitt 6). Die Freigabe gilt für Step-/Support-/Mock-/Usability-Code vollständig und für die Feature-Ebene nur im Umfang von Abschnitt 6.
- Die Freigabe steht unter den Auflagen aus Abschnitt 3 (insbesondere S-01 Mock-Defekt, S-03 Hohl-Step für die Hash-Kette, S-07 Race im Startup-Recovery-Szenario).

### Statistik R-01…R-41 (Runde 2)

| Status | HOCH (R-01…08) | MITTEL/NIEDRIG (R-09…41) | Σ |
|---|---|---|---|
| BEHOBEN | 6 | 15 | **21** |
| TEILWEISE | 2 (R-02, R-03) | 16 | **18** |
| OFFEN | 0 | 2 (R-26, R-39) | **2** |

### Neue/verbliebene Befunde (Abschnitt 3)

| Schweregrad | Anzahl | IDs |
|---|---|---|
| BLOCKER | 0 | — |
| HOCH | 0 | — |
| MITTEL | 13 | S-01…S-08, R-03′, R-10′, R-16′/R-17′, R-19′, R-26 |
| NIEDRIG | 20 | S-10…S-16, F-01…F-05, R-Reste R-09, R-11, R-25, R-30, R-36, R-37, R-39, R-40 |

---

## 1. Verifikation der acht HOCH-Befunde R-01…R-08

| ID | Status | Nachweis (Runde 2) |
|---|---|---|
| **R-01** Debug-Log `/tmp/opencode/mcp_err.log` | **BEHOBEN** | `MS:1709-1754` (`_call_update_draft`): kein `open()` mehr im Fehlerpfad; Fehlerbehandlung unverändert funktional, nur ohne Datei-Seiteneffekt. |
| **R-02** stille `lookup.get(key, uid)`-Fallbacks | **TEILWEISE** (Rest → S-02, MITTEL) | Zentrale Funktion `support.imap_fixture.resolve_uid` (`IF:115-162`) ist per Default **strikt** (`AssertionError`, wenn der Hint nicht gesät wurde); Assertion-Steps nutzen sie (`AS:150,168,521,554,570,3554,3572`). **Aber:** `allow_unseeded_probe=True` wird in den When-Pfaden **pauschal** gesetzt (`MS:293-296` `_res_uid`, `MS:724`, `PS:2467`, `CS:208`, `DS:257`) und `AS:569-572`/`AS:336-337` lassen unauflösbare Hints weiterhin unverändert durch. Der Kern der R1-Kritik (Kompensation eines Seeding-/Mapping-Fehlers im **Then**) ist beseitigt; der verbleibende Pfad betrifft Deny-/not_found-Aufrufe. |
| **R-03** Defaults im Seeding, `size_bytes` | **TEILWEISE** (Rest → R-03′, MITTEL) | **Behoben:** `_seed_message` (`PS:757-837`) wirft `AssertionError` bei fehlendem `from`/`from_address`, `to`/`to_address`, `subject`, `date`; `size_bytes` über `exact_size_bytes` mit `RFC822.SIZE`-Selbstprüfung (`PS:980`, `IF:451-491`); `has_attachment true` erzeugt kein `stub.bin` mehr. **Rest:** siehe R-03′ in Abschnitt 3. |
| **R-04** No-op-Givens `is empty` / `contains no message with uid` | **BEHOBEN** | `step_folder_is_empty` (`PS:992-1020`) und `step_folder_has_no_uid` (`PS:1124-1135`) werfen `AssertionError` mit Ist-Liste; `IMAPFixture.folder_uids` wirft bei SELECT-Fehler (`IF:624-633`). |
| **R-05** Crash-Szenarien verifizieren den Absturz nicht | **BEHOBEN** | `MS:709-831`: beide `expecting crash`-Steps verlangen `call_failed` (sonst `AssertionError`) **und** pollen `client.poll_exit()` bis 3 s (sonst `AssertionError`). Rest: der generische `step_caller_calls_move_structured` und `_call_update_draft` behalten `except Exception`, jeweils durch `crash_expected` geschützt (NIEDRIG). |
| **R-06** mock-oauth fabriziert Erfolg bei abgelehntem Refresh | **BEHOBEN** | `MO/server.py:105-124` führt Refresh-Tokens (`register_refresh_token`, `revoke_refresh_token`, `validate_refresh_token`); `372-382` antwortet für unbekannte/widerrufene Tokens `400 invalid_grant`; der Fallback-Block ist weg. Registrierung über `MO/client.py:105-129`, genutzt in `OS:127-137` und `PS:1686-1694`. Regressionstest `MO/tests/test_validation.py:207-220` (`test_unknown_refresh_token_rejected`); Test (d) registriert den Token ausdrücklich (`:171`). |
| **R-07** Usability-Runner: Passwort | **BEHOBEN** | `UF:265-270` (`seed_state`) setzt `state.password = FIXTURE_PASSWORD`; `US:15` führt das Passwort, `US:63` schreibt es ins Secret. Smoke-Test `usability/tests/test_fixtures.py:104-135` (`test_mock_gmail_login_smoke_with_fixture_password`): echter `LOGIN` gegen den gehärteten Mock, erwartet `a01 OK`. |
| **R-08** Absenz-Checks ohne Positivkontrolle / `folder_uids` | **BEHOBEN** (Rest → S-04, MITTEL) | `CS:337-377` (`contains no {verb} command`): mock-gmail-Pfad verlangt nicht-leeres Command-Log, Dovecot-Pfad verlangt laufenden, verdrahteten Proxy (analog `AS:1558-1598`); `AS:3864-3915` und `PS:2755-2797` (`… N messages with keyword/flag`): bei `count == 0` Positivkontrolle „Ordner nicht leer“; `IF:624-633` wirft bei SELECT-Fehler; `GS:491-510` (`_server_command_log`) wirft bei leerem Log. Rest: andere Absenz-Steps (S-04). |

**Ergebnis HOCH:** Alle acht R1-HOCH-Befunde sind in ihrem Kern behoben. Für R-02 und R-03 verbleiben Reststellen, die ich wegen der geschlossenen Hauptwirkung als MITTEL (nicht HOCH) einstufe und in Abschnitt 3 mit Fundstellen führe.

---

## 2. Verifikation R-09…R-41

Legende: **B** = BEHOBEN · **T** = TEILWEISE · **O** = OFFEN · **N** = nicht erneut gelesen (Status aus R1 übernommen; kein Freibrief).

| ID | Status | Nachweis / Rest |
|---|---|---|
| R-09 tote No-op-/Zirkelschluss-Steps | **T** (NIEDRIG) | Nicht mehr vorhanden in `AS`, `GS`, `CS`, `MS`, `PS`: Pool-No-ops, Env-Check, `CANONICAL_REASON_CODES`, `step_wal_entry_reaches_status_timed`, `pass`-Step, Marker-Step, `_ = (…)`-Steps, Seed-WAL, `triggers a DENY`, Attachment-Rewrite. Rest: `MS:1273-1286` (`_update_uid_after_rewrite`, ungenutzt), `PS:2284` (`_ = os`), `AS:314` (Zeile ohne Wirkung), `MS:1025-1044` (`step_subsequent_caller_identity_still`, Re-Handshake mit Original-Credentials, ungenutzt — das Feature nutzt jetzt `DS:263-269`). |
| R-10 `IMAP_MCP_TEST_MODE` überall | **T** | Gating umgesetzt: `MS:119-134` (`_ensure_mcp_client`), `AI:50-59`, `DS:151-152`. Rest: erzwungenes/Default-`TEST_MODE=1` an `MS:624,647,671,903,1922`, `PS:1101,1928,2449,3212,3241` und im Usability-Runner `usability/runner/run.py:180` (der **Agent** erhält Test-Tools und Fake-Uhr; keine dokumentierte Ausnahme; `run.py:71-75` hängt zudem `T09:00:00Z` an ein reines Datum). → R-10′. |
| R-11 Ketten-Formel / magisches Datum | **T** (NIEDRIG) | Magisches Datum `2026-04-21` verschwunden (`AS:2977-3002` kennt es nicht mehr). Formel steht in `AR:139` **und** weiter inline in `AS:3385`; `AS:3033` (Archiv) kanonisiert JSON anders als `AS:2991` (Separatoren, S-10). Feature-Köpfe nennen die Kettenregel nicht (§4.1). |
| R-12 `AuditReader.records_today` | **B** | `AR:76-87`: weder Prozess-Env noch Wallclock; optionales `pinned_date`, sonst neueste Tagesdatei. Negativ-Steps lesen über `records()`/`read_file()`. (Name `records_today` bleibt irreführend, NIEDRIG.) |
| R-13 `outcome record … result` OR-Klausel | **B** | `AS:916-933` vergleicht nur `result == result`. |
| R-14 `subprocess.run` ohne Timeout | **B** | `AS` importiert kein `subprocess`; `step_server_started_without_test_mode` (`AS:3333-3355`) nutzt `try_bootstrap_server(…, timeout=3.0)` und wirft bei `timed_out`; `PS:2301-2318` behandelt `timed_out` ebenfalls. |
| R-15 Zugriff auf `server/` | **T** (NIEDRIG) | Der Step mit `server/pyproject.toml` ist weg; `AS:2149-2165` prüft `serverInfo.version` gegen Literal aus dem Gherkin; `TS` führt die Server-venv nicht mehr aus. Rest: Binary-Pfad `ENV:52-57` (über `IMAP_MCP_SERVER_BINARY`, dokumentiert) und `usability/runner/server.py:16,38-45` (Default `server/.venv/bin/imap-mcp`, führt `…/bin/python` der Server-venv aus, um die Paketversion zu lesen). Kein Zugriff auf Produktionsquelltext. |
| R-16 implizite Account-Registrierung | **T** (MITTEL) | `_start_imap_proxy` wirft bei nicht deklariertem Account (`PS:1758-1760`). Aber `_ensure_account_registered` (`PS:55-71`) wird weiter aus Password-/Exists-Steps und `IU:58-65` aufgerufen. |
| R-17 versteckte Policy-/Caller-Defaults | **T** (MITTEL) | Behoben: Datums-Variante von `server clock is pinned` abgelehnt (`PS:142-151`); `acknowledge_plaintext` explizit statt Builder-Default (`PS:43`, `PB:97`). Rest: `PS:171` (`<caller>-policy`), `PS:1166-1171,1186-1191` (`gupta-scaratec`/`invoice-policy`), Fallback-Caller `AI:62`, `CS:735`, `GS:901`. |
| R-18 `total_connections`-Reset / falscher Zähler | **B** | Reset ausschließlich in `MG/state.py:226` (`reset()`, `epoch`) über `ENV:144`; kein `total_connections = 0` im When, kein „IMAP server received at most“-Step mehr in `AS`. (Der Mock-Zähler zählt auch Harness-Verbindungen, NIEDRIG.) |
| R-19 feste Sleeps | **T** (MITTEL → R-19′) | `DS:167-169`-Sleep entfernt; `MS:564-601` pollt statt `sleep(0.4)`, ist aber schwach (R-25). Rest: `CS:720-724` (`sleep(n)`, fachlich benannt). **Unverändert offen:** `TS:27-31,223-224` (`_TEST_BSP_SCHEDULE_DELAY_MS = "1000"`, nur gesetzt, wenn das Szenario nichts vorgibt) — der periodische Batch-Export kann den Shutdown-Flush der Flush-Szenarien ersetzen (R1 M-20). |
| R-20 mock-gmail: Validator und Fidelity | **T** (→ S-01) | `ENV:112,147` verdrahtet `token_validator`; `AUTH=PLAIN` entfernt (`MG/server.py:37-38`); `LIST` wertet Pattern aus (`:245-249`); `UID COPY/MOVE` mit UID-Mengen (`:721,751`); COPY nach All Mail ohne Label (`:732`). **Aber:** `_cmd_close` (`MG/server.py:327-336`) ruft `self._state.remove_message_from_folder(...)`, das in `MG/state.py` **nicht existiert** → S-01. |
| R-21 mock-oauth Nachsicht | **B** | `validate_bearer` ist für nie ausgestellte Tokens `False` (`MO/server.py:147-156`); unbekannter Code ⇒ `400 invalid_grant` (`249-258`); `client_id`/`redirect_uri` werden verglichen (`260-282`, nur wenn auf beiden Seiten gesetzt); `prime_consent` entfernt; Tests laufen ohne Container (Upstream-Timeout 1 s ⇒ nativer Fallback `351-369`). Rest NIEDRIG: Authorize ohne `code_challenge` ⇒ keine PKCE-Prüfung (`284-306`). |
| R-22 Then stößt Recovery an | **B** | Nur noch lesendes Polling (`AS:3424-3446`); `the paused saga is released and finishes` ist ein `When` (`PS:2507-2518`). Rest: `PS:2521-2540` fällt auf „irgendeine WAL-Zeile“ zurück, wenn `tx_id` fehlt (NIEDRIG). |
| R-23 Gmail-Seeding | **T** (MITTEL) | `_uid_base`-Magie weg; Duplikat-Muster beseitigt (`PS:2599-2687` unterscheidet nach Instanz). Rest: `GS:140-142,159` (Default-Absender `test@example.com`, `test@bdd.local`, Betreff `Test`, Body, Datum „jetzt“), `GS:196` (Default-Account `scaratec-gmail`), `GS:198` (Fallback auf irgendeine `_gmail_pending_msg`), `GS:227-231` (Zugriff auf `state._uid_maps` statt `assign_uid`). |
| R-24 WAL-Schema im Given | **B** | `DELETE FROM transactions` im Gmail-Revisionsszenario ist weg. `AS:3494-3502` (`When the WAL retry_count … is set to`) schreibt per `UPDATE` — als `When`, namentlich ausgewiesen, aber weiter schemagekoppelt (NIEDRIG). |
| R-25 Then mit Seiteneffekten | **T** (NIEDRIG) | Die R1-Steps (`list_folders`/`list_accounts` im Then) sind weg. Rest: `GS:560-572,1075-1078` (`flush_staged_messages` in Then-Steps = Seeding im Then). |
| R-26 `folder is reset to hold messages` | **O** (MITTEL) | `PS:544-546` kehrt unverändert ohne Reset zurück, wenn alle Hints schon gesät sind. |
| R-27 eindeutige Betreff-Auflösung | **B** | `IU:127-144`, `AS:2934`, `AS:2068` nutzen `find_unique_uid_by_decoded_subject`; `close_all()` entfernt. Einfach-Treffer-Varianten (`find_uid_by_decoded_subject`: `AS:2504,2523,2865`) bleiben in Flag-/Header-Steps (NIEDRIG). |
| R-28 Fixture: Status/Date-Header | **B** | `Date:`-Header über `email.utils.format_datetime` (`AS:3600-3603,3679`); `IF` in Runde 2 gelesen, keine Statusignoranz mehr notiert. |
| R-29 `deployment_steps` | **T** (MITTEL) | `DS:219-243`: Erwartung und Fehlerfang getrennt (spezifische Exceptions, `AssertionError` außerhalb des `try`). Rest: `DS:169-173` (`no signal is sent`) ist ein **No-op** (S-06). |
| R-30 Usability-Fixture | **T** (NIEDRIG) | `assert` ⇒ `raise AssertionError` (`UF:300-302`); öffentliche API `state.set_next_uid` (`UF:277`). Rest: Defaults `UF:111-115,180-181,229` (Inhaber als Absender/Empfänger, 08:00Z) — dort ist die Feature-Tabelle per Definition das ganze Postfach; NIEDRIG. |
| R-31 `AI` Thread-Join | **B** | `AI:182-184` prüft `is_alive()` ⇒ `AssertionError`. Rest NIEDRIG: Fallback-Caller `AI:62`. |
| R-32 Hilfsmodule tragen Fachwerte | **T** (MITTEL, mit R-03′) | `OS:43,52-53,132`, `DS:62-64,128` (`gupta`, Port 11143, `test123`), `GS:93,656`. Infrastrukturwerte sind nach §13.2 P3 ausgenommen; Token-Texte (`valid-mock-refresh-token`) werden von keiner Assertion gelesen. |
| R-33 nackte `assert` | **B** | Keine nackten `assert` in `AS`, `GS`, `OS`, `CS`, `DS`, `AI`, `IU`, `MS`, `PS`, `TS`, `IF`, `UF`, `MG`, `MO`; `usability/runner/grading.py:95` wirft jetzt `AssertionError`. |
| R-34 Debug-Ausgaben | **B** | Keine `DEBUG`/`print` in `AS`, `GS`, `MS`. |
| R-35 relativer Registry-Pfad | **B** | `AS:4054-4058` (`_registry_path`) über `context.bdd_root` mit `__file__`-Fallback. |
| R-36 `except Exception: pass` | **T** (NIEDRIG) | Eingegrenzt: `CS:414`, `ENV:195-199`. Rest: `AS:488,3317,3323`, `GS:293`, `CS:468,490,…` (Proxy-Konfig-Reads), `IU:44,50`, `PX:317-318`. |
| R-37 typblinde Vergleiche | **T** (NIEDRIG) | `_table_value_matches` (`AS:653-666`) typgetrieben. Rest: `AS:1086` (`str(actual) == …`), `AS:1171`, `OS:347`, `AS:649` (`re.match`), `AS:3882-3883` (Substring in `str(data[0])`). |
| R-38 `X-GM-MSGID` „direct IMAP“ | **B** | `GS:250-300` öffnet eine echte `imaplib`-Verbindung gegen `127.0.0.1:13143`, `UID SEARCH X-GM-MSGID`. Rest NIEDRIG (S-15): Host/Port/User fest (`GS:280-282`). |
| R-39 mUTF-7-Duplikate | **O** (NIEDRIG) | Vier Kopien: `IF:28`, `PX:57`, `MG/state.py:61`, `UF:55`. |
| R-40 LOGIN-Namen-Filter | **T** (NIEDRIG) | `GS:499-510` filtert weiterhin über den LOGIN-Namen, aber ein leeres Ergebnis ist ein lauter Harness-Fehler (Positivkontrolle). |
| R-41 Proxy `close`/Log | **B** | `PX:296` loggt **vor** der Modus-Auswertung ⇒ auch `close` ist im Log; `close_after_forward` (`327-356`) und `delay` (`373-386,446-449`) leiten weiter. Rest NIEDRIG: Log-Schreibfehler nur `logger.warning` (`PX:252-253`). |

---

## 3. Befunde Runde 2 (verbliebene und neue)

### BLOCKER
*Keine.*

### HOCH
*Keine.*

### MITTEL

#### S-01 — mock-gmail: `CLOSE` ruft nicht existierende Methode (Latenter Absturz)
- **Bereich:** MOCK · **Referenz:** §7.2, §13.1 · **Fundstelle:** `bdd/mock-gmail/src/mock_gmail/server.py:334`; Gegenstück `state.py` (kennt `remove_label`, `leave_mailbox`, `delete_message`, aber **kein** `remove_message_from_folder`); Tests `bdd/mock-gmail/tests/test_mock_gmail.py` (kein CLOSE-Test)
- **Nachweis:** Sendet der Server `CLOSE` in einem Postfach, in dem `\Deleted`-markierte Nachrichten stehen (z. B. All Mail, wo `_auto_expunge` das Flag bewusst behält, `server.py:664-666`), löst die Zeile `AttributeError` aus. Der Handler fängt nur `ConnectionResetError`/`BrokenPipeError`/`IncompleteReadError` (`:110`), die Verbindung wird abgebrochen — ein Mock-Defekt, der sich als Server-Fehler tarnt. Die R-20-Zusage „CLOSE-Semantik nach RFC 3501 §6.4.2“ ist damit nicht eingelöst.
- **Korrekturauftrag (`code-entwickler-flash`):** `CLOSE` auf vorhandene Zustandsmethoden abbilden (je Label-Postfach `leave_mailbox`, in Trash/Spam `delete_message`, All Mail: Flag entfernen und Nachricht aus allen Label-Sichten nehmen, wie `UID EXPUNGE` es tut) und einen Test in `test_mock_gmail.py` ergänzen (STORE `\Deleted` in All Mail ⇒ CLOSE ⇒ Nachricht weg, kein Verbindungsabbruch).

#### S-02 — Pauschales `allow_unseeded_probe=True` und Hint-Durchlass (R-02-Rest)
- **Bereich:** STEP · **Referenz:** §1.3, §13.2 P3, §4.1 · **Fundstellen:** `MS:293-296,724`, `PS:2467`, `CS:208`, `DS:257`; `AS:569-572` (`_resolve_uid_hints`: ohne `account`/`folder` bleibt der Hint stehen), `AS:336-337` (`src_uid` ohne Quell-Account/-Ordner), `TS:152-178` (`_resolve_uid_hint`: `except Exception: pass`, danach Auflösung über **alle** Mapping-Schlüssel mit passendem Hint)
- **Nachweis:** Die Opt-in-Semantik ist global statt am Szenario sichtbar. Ein Szenario, das **nicht** `not_found`/Deny erwartet, ist geschützt (der Server meldet dann `not_found`). Ein Szenario, das `not_found`/`DENY` erwartet, besteht auch dann, wenn der Mapping-Eintrag wegen falschen Account-/Ordnerschlüssels fehlt. Die UID-Auflösung in Assertions (`AS:98,120,570`) kompensiert bei fehlendem Kontext weiter still.
- **Korrekturauftrag:** `allow_unseeded_probe` nur über eine sichtbare Step-Variante (`… uid {uid:d} (not seeded)`) setzen; `_resolve_uid_hints` und `AS:336-337` bei fehlendem Kontext `AssertionError` statt Durchlass.

#### S-03 — „form a valid hash chain with consecutive seq numbers starting at 0“ und „offline chain verifier“ prüfen weniger, als der Name sagt
- **Bereich:** GHERKIN+STEP · **Referenz:** §13.1, §4.1, §12 · **Fundstellen:** `AI:289-298` (verwendet von `transactions/startup_saga_recovery.feature:108`), `AS:3368-3394`, `AR:99-148`
- **Nachweis:** `step_all_records_form_valid_hash_chain` ignoriert `filename`, ruft `reader.verify_chain()` über **alle** Dateien und prüft weder die **Seq-Folge** (`0,1,2,…`) noch die Genesis-`prev_hash` (Erwartung beginnt bei `None`, `AR:103,121`). Die Kettenregel (`"sha256:" + sha256(Zeile)`) ist die des Servers (§4.1) und steht in keinem Feature-Kopf. `step_offline_chain_verifier_reports` rechnet die Kette selbst nach und nennt das „offline chain verifier“; ein echter Verifier wird nicht aufgerufen.
- **Korrekturauftrag:** (Step) `filename` auswerten, `seq == index` und Genesis-Wert prüfen; (Feature, Hauptagent) Kettenregel und Genesis-Wert in den Kopf von `audit_log_format.feature`/`startup_saga_recovery.feature` aufnehmen; Step in „the recomputed chain of file … is intact“ umbenennen oder einen echten Verifier-Aufruf (CLI des Produkts) verwenden.

#### S-04 — Weitere Absenz-Steps ohne Positivkontrolle (R-08-Rest)
- **Bereich:** STEP · **Referenz:** §4.3, §4.4 · **Fundstellen:**
  - `AS:2913-2917` (`audit file does NOT contain …`: ohne `*.jsonl` ⇒ `return`)
  - `AS:3854-3859` (`response field … does NOT contain …`: fehlendes Feld ⇒ `return`)
  - `CS:231-243` (`connections … number between`: kein Proxy-Lauf-/Verdrahtungs-Check wie in `CS:147-173`; fehlendes Log ⇒ 0, besteht bei `low = 0`)
  - `CS:316-326` (`contains N commands`: liest nur das Proxy-Log; für mock-gmail-Accounts stets 0 Zeilen)
  - `AS:2329-2336` (`no resource block`: `last_blob` defaultet auf `None`, wird nirgends gesetzt)
  - `GS:397-415` (`does not contain folder`: leere Liste besteht)
- **Korrekturauftrag:** Jeweils wie `AS:1558-1598` erst den beobachtenden Kanal belegen (laufender, verdrahteter Proxy bzw. nicht leere Gegenmenge) und sonst `AssertionError`; `last_blob` aus dem `When` setzen oder den Step entfernen.

#### S-05 — `LOGIN or AUTHENTICATE`-Step: Dovecot-Zweig unerreichbar
- **Bereich:** STEP · **Referenz:** §5.1 · **Fundstelle:** `CS:388-401` — `gmail_state` wird in `ENV:113` immer gesetzt, der `if state is not None`-Zweig greift also **immer**; für einen Dovecot-Account wirft `_server_command_log` („not backed by mock-gmail“). Der Proxy-Zweig (`CS:398-401`) ist toter Code.
- **Korrekturauftrag:** Zweig nach `resolve_account(account_id)[0] == "mock-gmail"` entscheiden (wie `CS:340`).

#### S-06 — `When no signal is sent to the server` ist ein No-op
- **Bereich:** STEP · **Referenz:** §13.1, Prüfauftrag „niemals leere/No-op-Steps“ · **Fundstelle:** `DS:169-173` — liest `context.mcp_server_proc`/`context.startup_proc`; das erste Attribut wird nirgends gesetzt, `startup_proc` ist ein Bootstrap-Ergebnis. Der Step prüft praktisch nichts (Verwendung: `deployment/projected_config_reload.feature:43`).
- **Korrekturauftrag:** Step auf den Lebenszeichen-Check des `MCPHttpClient._proc` (`poll() is None`) stützen oder entfernen.

#### S-07 — `startup_saga_recovery.feature:73-74`: Erwartung ohne Warten (Race)
- **Bereich:** GHERKIN+STEP · **Referenz:** §4.4 · **Fundstellen:** `transactions/startup_saga_recovery.feature:71-74`, `AS:3464-3476`
- **Nachweis:** `retry_count >= 1` wird unmittelbar nach `list_folders` gelesen. Die Recovery des Servers läuft asynchron beim Start und hat evtl. noch keinen Versuch unternommen; das Szenario kann falsch-rot sein. Alle Geschwister-Szenarien nutzen `within 10 seconds`.
- **Korrekturauftrag (Hauptagent):** Zeile 73-74 als `Then within 10 seconds the WAL entry for the crashed move has retry_count >= 1` (neuer Poll-Step) formulieren.

#### R-03′ — Versteckte Fachdefaults im Rest des Seedings (R-03/R-17/R-23/R-32-Rest)
- **Bereich:** STEP · **Referenz:** §13.2 P3 · **Fundstellen:** `PS:856-857,881,889` (`flush_staged_messages`: Absender/Empfänger `{user}@bdd.local`, Body `Body for {subject}`, Message-ID `<scenario-{uid}@bdd.local>`, relevant vor allem für `_stage_handle_drafts`, `PS:597-637`), `PS:1025-1061` (`repeated letter body`: `sender@example.com`, `Message {uid}`, `2026-10-30T07:00:00Z`), `PS:2599-2687` (`Bulk message n`, `{user}@{account}.example`, UID-Start 9000), `GS:140-142,159,196,198`, `PS:171,1166-1171,1186-1191`
- **Nachweis:** Die Pflichtspalten-Härtung gilt für `_seed_message`; die genannten Nebenwege erzeugen weiter Fachwerte, die in Assertions (Absender-Regeln, Provenance „own“, Cc-Ableitung) relevant sein können. Das Feature zeigt sie nicht.
- **Korrekturauftrag:** Die Nebenwege durch `_seed_message` leiten (Pflichtspalten) oder bei fehlenden Angaben `AssertionError`; Bulk-Steps tragen Betreff-Präfix und Empfänger als Parameter.

#### R-26 — `folder is reset to hold messages` überspringt das Reset (unverändert offen)
- **Fundstelle:** `PS:544-546`. Korrektur wie R1.

#### R-16′ / R-17′ — implizite Account-Registrierung und Konventions-Policies (Rest)
- **Fundstellen:** `PS:55-71` (aufgerufen u. a. aus `IU:58-65`), `PS:171`, `PS:1166-1171`, `PS:1186-1191`. Korrektur wie R1: nur in den dafür benannten Givens registrieren; sonst `AssertionError`.

#### S-08 — `response_transparency.feature`: Sortierregel und Provider-Default nicht sichtbar
- **Bereich:** GHERKIN · **Referenz:** §2.2, §13.2 P2/P3 · **Fundstellen:** `tool_surface/response_transparency.feature:58` (`folders equals ["Archiv", "INBOX/Rechnungen"]` — `AS:106-127` vergleicht reihenfolgesensitiv; die Tabelle des Background nennt die umgekehrte Reihenfolge, die Sortierregel steht nirgends), `:121` (`semantics … "imap-standard"` — der Account entsteht über `exists with folders` ohne Provider; `imap-standard` stammt aus `PB:25`)
- **Korrekturauftrag (Hauptagent):** Sortierregel (alphabetisch/aufsteigend) in den Kopf aufnehmen oder `equals in exact order` mit Regel zitieren; im Background `with provider "imap-standard"` ausschreiben.

#### R-10′ — Test-Modus im Usability-Lauf und in Rest-Steps
- **Bereich:** STEP · **Referenz:** §5.3, §7.2 · **Fundstellen:** `usability/runner/run.py:180` (zusätzlich `:71-75`), `MS:624,647,671,903,1922`, `PS:1101,1928,2449,3212,3241`
- **Korrekturauftrag:** Im Usability-Runner `IMAP_MCP_TEST_MODE` nur setzen, wenn die Fake-Uhr verlangt ist, und die Ausnahme am Aufruf dokumentieren; in den Rest-Steps nach dem Muster `MS:119-134` an `is_test_hooks` binden.

#### R-19′ — Flush-Witness kann durch periodischen Export bestehen (R1 M-20, unverändert)
- **Bereich:** STEP · **Referenz:** §4.4, §4.3 · **Fundstellen:** `TS:27-31,223-224`; betrifft `observability/tracing_spans.feature` (nicht gelesen)
- **Korrekturauftrag:** Für die Flush-Szenarien `OTEL_BSP_SCHEDULE_DELAY` deutlich über die Beendigungsdauer setzen (z. B. 60 000 ms), damit nur der Shutdown-Flush liefern kann.

### Feature-Ebene (NIEDRIG, an den Hauptagenten)

- **F-01:** `deployment/health_probe.feature:70,128` — `the audit log contains 0 records …` ohne Positivkontrolle, dass der Audit-Schreiber überhaupt arbeitet (z. B. vorab ein authentifizierter Aufruf mit ≥ 1 Record).
- **F-02 (M-23-Rest):** `tool_surface/search_criteria_and_pagination.feature:7-9,49` — der Kopf verspricht „pad IMAP SINCE/BEFORE by 1 day“; der Wire-Witness belegt nur `contains a UID SEARCH command`, nicht die Argumente. Titel auf das Beobachtbare reduzieren oder Argumente im Proxy-Log prüfen (§1.2).
- **F-03:** `tool_surface/create_draft_error_visibility.feature:120` — `When 3 seconds elapse …` ist ein fester Sleep (`CS:720-724`); besser Poll auf IMAP-Zustand mit Deadline.
- **F-04:** `policy/sender_rule_matcher.feature:96` — dass `LEBENSVERSICHERUNG@ALLIANZ.DE.` (Großschreibung, FQDN-Punkt) auf `allianz.de` passt, folgt aus ADR 0041, steht aber nicht im Kopf (§2.2).
- **F-05:** `policy/write_visibility_and_flow_monotonicity.feature:42-53` vs. `:55-65` — COUNT ⇒ `ALLOW + ERROR not_found`, METADATA ⇒ `DENY visibility_below_ENVELOPE`; der Unterschied ist im Kopf nicht erklärt.

### NIEDRIG

- **S-10:** `AS:2991` (`separators=(",", ":")`) vs. `AS:3033,3042` (Python-Default) kanonisieren die Historiendateien unterschiedlich; der Hash der Archivvariante ist nicht mit der Plain-Variante vergleichbar. Hidden Defaults der Seed-Records (`invoice-agent`, `list_accounts`, `ALLOW`) in `AS:2980-2990,3022-3032`.
- **S-11:** `OS:172-180` (`proc.stdout.readline()` ohne Timeout), `OS:183,216` (`communicate(timeout=5.0)` ohne Kill bei `TimeoutExpired`).
- **S-12:** `AS:2346-2369` (`no file was written outside the sink directory`) prüft nur `*.pdf` im Eltern-Verzeichnis und zwei feste Pfade.
- **S-13:** `MO/server.py:388` liest `refresh_tokens` ohne Lock; Upstream-Pfad erzeugt fehlendes `refresh_token` selbst (`:340-341`).
- **S-14:** `bdd/mock-gmail/tests/test_mock_gmail.py` (230 Z.) deckt Login/AUTHENTICATE/Zustandsautomat/Postfachexistenz/FETCH ab; `search.py` (323 Z.), `mime.py` (143 Z.), STORE/COPY/MOVE/EXPUNGE/CLOSE sind **ungetestet** (hätte S-01 aufgedeckt).
- **S-15:** `GS:280-282` (`127.0.0.1:13143`, `test@scaratec.com`), `GS:93,656` (Passwort `test`).
- **S-16:** `AS:1509-1555` (`the IMAP command log … contains in order`): Token-weises Substring-Matching über das **ganze** Log — `["STORE", "\Deleted"]` kann aus zwei verschiedenen Kommandozeilen zusammengesetzt werden; die Kette `SELECT "INBOX" … SELECT "[Gmail]/Drafts" … SELECT "INBOX"` (`connection_pool.feature:76-80`) ist damit weich belegt.

---

## 4. Nachtrag: Usability-Runner, `trace_steps`, Mocks

| Datei | Ergebnis |
|---|---|
| `usability/runner/serverconf.py` | R-07 behoben (`FIXTURE_PASSWORD` geschrieben, Konfiguration konsistent: `acknowledge_plaintext` explizit, `stdio_trusted` für den Probe-Caller). |
| `usability/runner/fixtures.py` | `seed_state` setzt das Passwort (`:265-270`), nutzt die öffentliche `set_next_uid`; `assert` durch `raise AssertionError` ersetzt (`:300-302`). Rest NIEDRIG (R-30, R-39). |
| `usability/runner/mock.py` | Sauber (eigener Event-Loop je Lauf, `call()` für konsistente Zustandslesung, Timeouts). |
| `usability/runner/run.py` | R-10′ (`:180`), Datumsanhang `T09:00:00Z` (`:71-75`). Positiv: Isolationsprüfung „Transcript zeigt MCP-Aufrufe, die der Server nie auditiert hat“ (`:219-222`), Infrastrukturfehler werden `not-assessable` statt Pass (`:307-308`). |
| `usability/runner/server.py` | R-15 (Rest, `:16,38-45`); `except Exception` in `:68-69` verschluckt Fehler bei `tool_surface_info` (optional, NIEDRIG). |
| `usability/runner/grading.py` (Z. 75-114) | R-33 behoben (`:95`). Bewertungslogik selbst (Rubriken, Judge) nicht Gegenstand dieser Abnahme. |
| `usability/tests/test_fixtures.py` | Smoke-Test R-07 vorhanden; Determinismus-Tests (`PYTHONHASHSEED`-Subprozess) sind aussagekräftig. |
| `bdd/features/steps/trace_steps.py` (593 Z.) | Weiterhin vorbildlich: Sink-Polling mit Deadline (`_poll`), Absenz-Witness verlangt nicht leeren Sink (`:430-440`), `_every_span` verlangt Spans (`:449-451`), Zählvergleich Spans ↔ Proxy-Log-Zeilen im Aufrufzeitraum (`:564-590`). Reste: R-19′ (BSP-Delay), S-02 (`_resolve_uid_hint`), `_every_span(…, 0, …)`-Witnesses pollen nicht (NIEDRIG, R1 N-03). |
| `bdd/mock-gmail/src/mock_gmail/search.py` (323 Z.) | **Befundfrei** gegenüber RFC 3501 §6.4.4: Tokenizer mit Klammern/Quoting/Escapes, BAD bei unbekannten Schlüsseln, INTERNALDATE vs. Date-Header korrekt getrennt, `UID`-/Seq-Mengen mit `*`, Gmail-Schlüssel. Ungetestet (S-14). |
| `bdd/mock-gmail/src/mock_gmail/mime.py` (143 Z.) | **Befundfrei**: BODYSTRUCTURE mit Disposition je Part, Großschreibung wie Gmail, message/rfc822 verschachtelt. Ungetestet (S-14). |
| `bdd/mock-gmail/src/mock_gmail/state.py`, `server.py` | S-01 (CLOSE-Defekt). Positiv: Zustandsautomat, `TRYCREATE`/`ALREADYEXISTS`, Pre-/Post-Auth-CAPABILITY, Label-STORE-Log mit eigenem Parse-Ergebnis, `epoch`-Isolation zwischen Szenarien, `close_all_connections` mit Wartebedingung. |
| `bdd/mock-gmail/tests/test_mock_gmail.py` (230 Z.) | Vier Tests mit echtem TCP-Verkehr; S-14. |
| `bdd/mock-oauth/src/mock_oauth/server.py`, `client.py`, `tests/test_validation.py` | R-06/R-21 behoben (Abschnitt 1/2). |
| `bdd/support/audit_reader.py`, `policy_builder.py`, `wal_reader.py`, `imap_proxy.py`, `features/environment.py` | Gelesen; Befunde siehe R-11, R-12, R-17, R-24, R-36, R-39, R-41. `wal_reader.py` ist read-only (`mode=ro`) und unabhängig von der Server-Selbstauskunft. |

---

## 5. Schließung der 14 `UNGEPRÜFT`-Altbefunde

| Altbefund | Neuer Status | Nachweis (Datei:Zeile) |
|---|---|---|
| **H-30** Apple-Mail-Struktur wird nicht erzeugt | **BEHOBEN** | `tool_surface/inline_attachment_detection.feature:25-57` legt die **echte** verschachtelte Struktur als DocString ab (`multipart/alternative` → `text/plain` + `multipart/mixed` → `text/html` + `application/pdf; Content-Disposition: inline`); drittes Szenario `:80-106` (Name ohne Disposition ⇒ Anhang, Inline-Text ohne Dateiname ⇒ ausgeschlossen); Inhalt wird gegen **sichtbare** Bytes geprüft (`:78`: `exact ASCII content "%PDF-1.4\n"`, `JVBERi0xLjQK` ⇒ 9 Byte, `:69,76`). Kopf 3 = 3 Szenarien ✓. |
| **H-31** `connection_reuse`: Szenario besteht für einen Server, der nichts tut | **BEHOBEN** | `performance/connection_reuse.feature:27,35`: Absender sichtbar (`billing@example.com`); Ergebnismenge geprüft (`:30-31,38-39`: `contains 5 messages`, `total_visible 20`, `uids has length 20`); Titel und Schranke stimmen (`at most 2` ↔ `between 1 and 2`, `:32,40`). |
| **M-01** WAL-„committed“ nur über Selbstauskunft | **BEHOBEN** | `tool_surface/update_draft.feature:265-269` prüft die WAL-Zeile per `the WAL transactions table has an entry with:` (SQLite, `AS:301-363`) mit `tx_id`, `status committed`, `staged_rfc822 (null)`; `get_transaction_status` ist nur Zusatz (`:270-271`). `add/replace/delete_attachment` sind entfallen. Kopf 13 = 13 Szenarien ✓. |
| **M-21** Pool-Szenarien belegen ihre Regel nicht | **BEHOBEN** (Rest S-16) | `performance/connection_pool.feature:51` (`at least 4 NOOP commands`, `CS:380-385`), `:94` (`peak concurrent connections equalled 2`, exakt), `:76-80` (SELECT-Reihenfolge), `:88` (`exactly 2` Verbindungen nach Drop), `:100` (`pool_exhausted`), `:106` (`ends with a LOGOUT`). Kopf 7 = 7 ✓. Rest: Token-Substring-Matching (`AS:1509-1555`). |
| **M-23** „IMAP pre-filter/fast-path“ nur im Titel | **TEILWEISE** (→ F-02) | `tool_surface/list_messages.feature` behauptet im Kopf nichts Unbeobachtbares mehr (Fast-Path-Szenario entfallen). `search_criteria_and_pagination.feature:49` liefert einen Wire-Witness (`contains a UID SEARCH command`), aber nur das Verb; der Kopf (`:7-9`) verspricht die 1-Tag-Polsterung von SINCE/BEFORE ohne Argument-Prüfung. |
| **M-24** Ordnungsvertrag unsichtbar | **BEHOBEN** | Kopf nennt die Regel („ordered by ascending UID“): `list_messages.feature:1-9`, `search_criteria_and_pagination.feature:10`; Assertion `equals in exact order` (`:48,72,…`, `AS:88-103`); `list_messages.feature:74-90` trennt UID- von Datumsreihenfolge (Daten absteigend, UIDs aufsteigend, Seite `[402, 403]`). |
| **M-26** HTML-Fallback: Präferenz nur positiv | **BEHOBEN** | `tool_surface/fetch_body_html_fallback.feature:53-54`: `text_body equals "Plaintext wins"` und `does NOT contain "HTML loses"`; Szenario 1 `:40-43` mit Negativ-Pins. Kopf 2 = 2 ✓. |
| **M-27** Reply-Drafts: Persistenz/Struktur | **BEHOBEN** | `tool_surface/draft_identity.feature:122` (`carries 1 header X-SC-Draft-Id, 1 header X-SC-Draft-Rev, and 1 header X-SC-Provenance` per IMAP); `tool_surface/create_draft_format.feature:174-185` (multipart/alternative, `<blockquote`, zitierter Text, `<script>` entfernt — direkter IMAP-FETCH). |
| **M-28** schwache Negativ-/Wertprüfungen | **TEILWEISE** (Rest nicht gelesen) | (a) `policy/visibility_levels.feature:72-132`: METADATA-Szenario mit konkreten Nullfeldern und Literal-Leak-Liste, FULL mit exakten Bytes/Hash (`:131-132`). (c) `response_transparency.feature:36-37,49-53`: der versteckte Account `osthues-mail` existiert und wird geprüft. (b)/(d) `whitelist_folder_mode.feature:95-134`, `blacklist_folder_mode.feature:95-134`: Loader-Szenarien mit Fehlertext, Audit-Szenario mit `audit_detail_reason`-Tabelle; die Zeilen 1-94 bzw. ab 135 beider Dateien sowie `visibility_levels.feature:1-69,133-ff` sind **nicht gelesen**. |
| **M-29** NFC-Normalisierung unausgeübt | **BEHOBEN** | `policy/sender_rule_matcher.feature:114-124`: Spalte `subject_nfd_escaped` mit `R\u00fcckfrage NFC` **und** `Ru\u0308ckfrage NFD`, erwartet `[440, 441]`. Kopf 27 = 27 ✓ (Zeilen nachgerechnet, u. a. `all_recipients_in_domains` ⇒ `[401, 402]`). |
| **M-30** Erwartung hängt an unsichtbarer Prüfreihenfolge/INBOX | **BEHOBEN** | `tool_surface/response_transparency.feature:15-17` benennt die Auswertungsreihenfolge (Quell-Gate → Ziel-Gate → Quell-Capability → Ziel-Capability); Zeilen `:97-99` belegen sie (`folder_hidden`, `folder_hidden`, `capability_missing`). `list_folders_message_count.feature` entfällt (3.0 ohne `hidden_folders_count`). Kopf 16 = 16 ✓. Rest: S-08. |
| **N-05** Feature-Inkonsistenzen | **BEHOBEN** | `message_rewrite_recovery.feature` entfällt; `caller_authentication.feature:10-16` zählt 12 = 12 (Timing-Szenario gestrichen, Immutable-Szenario `:123-133` jetzt mit `DS:263-269` ohne Re-Handshake); `startup_saga_recovery.feature`, `health_probe.feature`, `projected_config_reload.feature` sind nicht mehr `@pending`, Steps liegen in `MS:624-671`, `DS:41-631`, `AI`, `CS`. Köpfe 9 = 9, 14 = 14, 8 = 8 ✓. Hinweis: das Vorhandensein einzelner Givens (`WAL retry_limit`, `WAL database file is removed`) wurde nicht Step für Step abgeglichen. |
| **N-13** Kopf-/Titel-/Zählabweichungen | **BEHOBEN** | `connection_reuse.feature` (Titel = Wert), `create_draft_error_visibility.feature:4-12,66,84,100` (Kopf nennt `error.detail`, Szenarien prüfen es; 5 = 5), `search_criteria_and_pagination.feature:14-21` (34 = 34 nachgezählt), `localized_gmail_folders.feature` entfällt. |
| **N-14** Teilweise Assertions | **BEHOBEN** | `tool_surface/fetch_body_attachment_metadata.feature:35-42` (beide Anhänge vollständig); `tool_surface/update_draft_errors.feature:51-58` (Outline mit `IMAP command log … is cleared` + `contains 0 commands`, Proxy in `:31`). Rest: `CS:316-326` liest nur das Proxy-Log (S-04). |

**Ergebnis:** 12 BEHOBEN · 2 TEILWEISE (M-23, M-28) · **0 UNGEPRÜFT**.

---

## 6. Prüfumfang und Einschränkungen

### Gelesen (Runde 2, vollständig, zeilenweise)

- `bdd/features/environment.py` (305 Z.); `bdd/support/`: `imap_fixture.py` (855), `audit_reader.py` (211), `policy_builder.py` (303), `wal_reader.py` (103), `imap_proxy.py` (543)
- `bdd/features/steps/`: `mcp_steps.py` (1981), `policy_steps.py` (3344), `assertion_steps.py` (4144), `gmail_steps.py` (1093), `connection_steps.py` (783), `oauth_steps.py` (351), `deployment_steps.py` (631), `audit_instance_steps.py` (298), `imap_utf7_steps.py` (153), `mime_witness_steps.py` (168), `trace_steps.py` (593)
- Mocks: `mock-gmail/src/mock_gmail/{server,state,search,mime}.py`, `mock-gmail/tests/test_mock_gmail.py` (Z. 1-230), `mock-oauth/src/mock_oauth/{server,client}.py`, `mock-oauth/tests/test_validation.py`
- Usability: `runner/{serverconf,fixtures,mock,server,run}.py`, `runner/grading.py` (Z. 75-114), `tests/test_fixtures.py`
- Features **vollständig**: `inline_attachment_detection`, `connection_reuse`, `fetch_body_html_fallback`, `health_probe`, `projected_config_reload`, `startup_saga_recovery`, `caller_authentication`, `connection_pool`, `list_messages`, `fetch_body_attachment_metadata`, `response_transparency`, `sender_rule_matcher`, `update_draft`, `search_criteria_and_pagination`, `create_draft_error_visibility`
- Features **teilweise**: `draft_identity` (Z. 1-190), `create_draft_format` (Z. 120-185), `update_draft_errors` (Z. 1-135), `visibility_levels` (Z. 70-132), `whitelist_folder_mode` (Z. 95-134), `blacklist_folder_mode` (Z. 95-134), `provenance_own_and_draft_ownership` (Z. 1-147), `write_visibility_and_flow_monotonicity` (Z. 1-140), `non_interference_and_field_gates` (Z. 1-130)
- Aus R1 weiterhin gültig (18 Features): `policy_reload`, `reason_code_contract`, `audit_log_format`, `audit_retention`, `bulk_mark_tagged`, `bulk_mark_seen`, `secret_store_backends`, `oauth2_bootstrap`, `saga_crash_recovery`, `draft_revision_recovery`, `intra_account_move`, `mcp_tool_discovery`, `non_goal_rejection`, `folder_path_contract`, `fetch_attachment_content`, `gmail_label_semantics`, `update_draft_errors`, `imap_mailbox_names_utf8`

### Nicht gelesen (23 von 64 Features)

`audit/audit_multi_instance`, `auth/explicit_imap_user`, `providers/gmail_search_and_counts`, `providers/gmail_draft_lifecycle`, `providers/localized_gmail_resolution`, `observability/tracing_spans`, `observability/tracing_payload_mode`, `performance/connection_pool_expiry`, `transactions/cross_account_move_saga`, `tool_surface/required_arguments`, `tool_surface/search_completeness`, `tool_surface/discard_draft`, `tool_surface/search_blacklist_fast_path`, `tool_surface/tool_surface_info`, `tool_surface/fetch_attachment_sink`, `tool_surface/default_scope_transparency`, `tool_surface/create_reply_draft`, `tool_surface/list_attachments`, `policy/write_capabilities`, `policy/default_deny`, `policy/flagged_predicate`, `policy/draft_edit_capability`, `policy/draft_consistency`.

Außerdem nicht gelesen: Rest der teilweise gelesenen Features (s. o.), `bdd/support/{mcp_client,trace_collector,server_bootstrap}.py` (in Runde 2 nicht erneut), `usability/runner/{criteria,measurements,judge,evidence,features,feedback,simulator,matrix,report,pdf,cli,harness/*}.py`, `usability/features/*.feature`, `usability/tests/` außer `test_fixtures.py`, `mock-gmail/__main__.py`, `bdd/docker/**`.

### Grenzen der Aussagekraft

- Kein `grep`, keine Ausführung: Alle Befunde stammen aus dem Lesen; insbesondere S-01 ist **statisch** belegt (Methode fehlt in `state.py`), nicht durch einen Lauf.
- Das Vorhandensein jedes in den neu gelesenen Features verwendeten Steps (z. B. `the human mail client sends the draft …`, `an external client copies the draft message …`, `the WAL retry_limit is configured …`, `a second server instance is started for caller …`) habe ich **nicht** einzeln gegen `PS`/`MS`/`AS` abgeglichen; ein fehlender Step würde behave beim Lauf als `undefined` melden (kein stiller Pass).
- Die Rechenproben der Features (Kopfzählungen, Erwartungswerte) sind für die in Abschnitt 5/6 als „vollständig“ genannten Features nachgerechnet; für teilweise gelesene nur im gelesenen Ausschnitt.
- `usability/runner/*` außerhalb der genannten Dateien und `usability/features/*` blieben ungelesen; R-07/R-10/R-15/R-30/R-33 sind für die gelesenen Dateien belegt.

---

## 7. Gesamturteil

# FREIGEGEBEN (bedingt)

**0 BLOCKER · 0 HOCH** — die Freigabebedingung ist erfüllt. Die Freigabe ist **bedingt**, weil (1) das Vollabdeckungsziel für die Feature-Ebene verfehlt wurde (23 Features ungelesen, Abschnitt 6) und (2) 13 MITTEL-Befunde offen sind. Sie decken sich nicht mit einer Freigabe für die ungelesenen Features.

### Auflagen mit exakter Fundstelle

**Code (`code-entwickler-flash`):**

1. **S-01** `bdd/mock-gmail/src/mock_gmail/server.py:334` — nicht existierende `state.remove_message_from_folder`; Test in `bdd/mock-gmail/tests/test_mock_gmail.py` ergänzen.
2. **S-02** `bdd/features/steps/mcp_steps.py:293-296,724`, `policy_steps.py:2467`, `connection_steps.py:208`, `deployment_steps.py:257`, `assertion_steps.py:336-337,569-572`, `trace_steps.py:152-178` — Hint-Durchlass nur über sichtbare Step-Variante.
3. **S-03** `audit_instance_steps.py:289-298`, `assertion_steps.py:3368-3394` — `filename`, Seq-Folge, Genesis prüfen; Step-Namen angleichen.
4. **S-04** `assertion_steps.py:2329-2336,2913-2917,3854-3859`, `connection_steps.py:231-243,316-326`, `gmail_steps.py:397-415` — Positivkontrolle vor Absenz.
5. **S-05** `connection_steps.py:388-401` — Zweig nach Instanztyp.
6. **S-06** `deployment_steps.py:169-173` — No-op ersetzen.
7. **R-03′** `policy_steps.py:856-857,881,889,1025-1061,2599-2687`, `gmail_steps.py:140-142,159,196,198` — Pflichtspalten statt Fachdefaults.
8. **R-10′** `usability/runner/run.py:180`, `mcp_steps.py:624,647,671,903,1922`, `policy_steps.py:1101,1928,2449,3212,3241`.
9. **R-16′/R-17′** `policy_steps.py:55-71,171,1166-1171,1186-1191`, `imap_utf7_steps.py:58-65`.
10. **R-19′** `trace_steps.py:27-31,223-224`.
11. **R-26** `policy_steps.py:544-546`.

**Gherkin (Hauptagent):**

12. **S-07** `transactions/startup_saga_recovery.feature:73-74` — `within 10 seconds` für `retry_count >= 1`.
13. **S-08** `tool_surface/response_transparency.feature:58,121` — Sortierregel, Provider ausschreiben.
14. **S-03 (Feature-Anteil)** Kettenregel in die Köpfe von `audit/audit_log_format.feature` und `transactions/startup_saga_recovery.feature` aufnehmen.
15. **F-01…F-05** (NIEDRIG) siehe Abschnitt 3.

**Nächste Runde:** die 23 ungelesenen Features (Abschnitt 6) gegen die Steps lesen; danach Neubewertung der Freigabe ohne „bedingt“.
