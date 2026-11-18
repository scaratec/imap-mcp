# Step-, Support-, Mock- und Ehrlichkeits-Abnahme `imap-mcp` 3.0.0 (Claude Sonnet 5.5)

- **Datum:** 2026-11-17
- **Rolle:** unabhängiger Gherkin- & Python-Step-Reviewer (BDD-Guidelines v1.8.0, §13.3)
- **Maßstab:** `/home/randy/Projekte/privat/burn-your-code/BDD_GUIDELINES_v1.8.0_DE.md` (§1–§13 vollständig gelesen)
- **Baseline:** `docs/reviews/2026-11-17-step-audit-sonnet.md` (88 Befunde: B-01…B-05, H-01…H-33, M-01…M-33, N-01…N-17)
- **Grenzen eingehalten:** Kein Zugriff auf `server/` oder `secrets/`. Keine Änderung an `.py`/`.yaml`/`.feature`. Einzige Ausgabe: dieser Bericht.

Abkürzungen für Fundstellen: `AS` = `bdd/features/steps/assertion_steps.py`, `PS` = `…/policy_steps.py`, `MS` = `…/mcp_steps.py`, `GS` = `…/gmail_steps.py`, `CS` = `…/connection_steps.py`, `OS` = `…/oauth_steps.py`, `DS` = `…/deployment_steps.py`, `AI` = `…/audit_instance_steps.py`, `IU` = `…/imap_utf7_steps.py`, `TS` = `…/trace_steps.py`, `IF` = `bdd/support/imap_fixture.py`, `MC` = `…/mcp_client.py`, `PX` = `…/imap_proxy.py`, `AR` = `…/audit_reader.py`, `PB` = `…/policy_builder.py`, `ENV` = `bdd/features/environment.py`, `MG` = `bdd/mock-gmail/src/mock_gmail/`, `MO` = `bdd/mock-oauth/src/mock_oauth/`. Feature-Pfade relativ zu `bdd/features/`.

---

## 0. Gesamturteil

# NACHARBEIT ERFORDERLICH

**0 BLOCKER · 8 HOCH · 24 MITTEL · 9 NIEDRIG** (neue bzw. verbliebene Befunde R-01…R-41, Abschnitt 3).

Die Freigabebedingung (`0 BLOCKER, 0 HOCH`) ist nicht erfüllt. Die Nacharbeit seit der Baseline ist substanziell: Die fünf Baseline-BLOCKER sind **auf Feature-Ebene** beseitigt, die Proxy-/Mock-/Client-Infrastruktur ist deutlich ehrlicher geworden. Der Step-Code selbst wurde dagegen kaum aufgeräumt: Er trägt weiter die Altlasten der Baseline (No-op-Steps, versteckte Defaults, stille UID-Fallbacks, Zirkelschluss-Steps) — überwiegend als totes Gerüst, aber an acht Stellen **live**.

### Abgleich der 88 Altbefunde (Details in Abschnitt 1)

| Status | B | H | M | N | **Σ** |
|---|---|---|---|---|---|
| BEHOBEN (Feature/Code verifiziert) | 4 | 20 | 7 | 2 | **33** |
| ENTFALLEN (Tool/Feature in 3.0 entfernt) | 0 | 3 | 3 | 0 | **6** |
| TEILWEISE (Feature behoben, Step-Rest oder Teilmenge offen) | 1 | 8 | 10 | 5 | **24** |
| OFFEN | 0 | 0 | 4 | 7 | **11** |
| UNGEPRÜFT (zugehöriges Feature nicht gelesen, s. Abschnitt 5) | 0 | 2 | 9 | 3 | **14** |
| **Summe** | 5 | 33 | 33 | 17 | **88** |

### Hauptergebnis in fünf Sätzen

1. Alle Baseline-BLOCKER sind in den Szenarien behoben (echter Taxonomie-Sweep, `tools/list`-Metadatenvergleich, Proxy-Verbindungszähler, proxy-verifizierter Pool-Drain, B-05-Zeile entfernt); ihre No-op-Steps stehen aber **unverändert und wiederverwendbar** in `PS`/`AS`.
2. Persistenz-Prüfungen (§13.2 P1) sind für die Schreib-Tools jetzt überwiegend über IMAP/WAL/Dateisystem/Proxy belegt (`bulk_mark_*`, `move`, `create_draft`/`update_draft`/`discard_draft`, `fetch_attachment` mit sichtbaren Quell-Bytes). Lücke: Abwesenheits-Checks ohne Positivkontrolle (R-08) und nicht verifizierte Crash-Hooks (R-05).
3. Daten-Symmetrie (§13.2 P3) bleibt der größte offene Punkt: Default-Absender/-Empfänger/-Betreff/-Message-ID/-Anhänge im Seeding (R-03) und stille `lookup.get(key, uid)`-Fallbacks (R-02).
4. Die Mocks sind realistisch geworden (mock-gmail: Passwortprüfung, Zustandsautomat, XOAUTH2, `TRYCREATE`; mock-oauth: Priming, Lifetime, PKCE; Proxy: `delay`/`close_after_forward` leiten **weiter**). Rest-Wunschverhalten: mock-oauth fabriziert bei jedem Upstream-Refresh-Fehler eine Erfolgsantwort (R-06).
5. Der Usability-Harness läuft statisch gelesen gegen den gehärteten mock-gmail nicht mehr durch (R-07).

---

## 1. Abgleich aller 88 Altbefunde

Legende: **B** = BEHOBEN · **E** = ENTFALLEN · **T** = TEILWEISE · **O** = OFFEN · **U** = UNGEPRÜFT. „Rest“ verweist auf Abschnitt 3.

### 1.1 BLOCKER

| ID | Status | Nachweis |
|---|---|---|
| B-01 | **B** | `tool_surface/reason_code_contract.feature:227-245`: Scenario Outline mit Proxy, je Tool drei echte Aufrufe (hidden/absent/select-failed), Zweitkanal `none of the captured responses or audit entries … carry any of [...]` (`AS:6220-6240`, Antwort-Historie nicht leer, da vorgelagerte Aufrufe `decision`/`error.type` asserten). Rest: tote Marker-Steps `AS:4199-4251` → R-09. |
| B-02 | **B** | `tool_surface/mcp_tool_discovery.feature:90-130` + `AS:891-952` vergleichen `actual == expected` aus `tools/list`-Metadaten (`_tool_meta`, `AS:880-888`); Meta-Tools prüfen Abwesenheit beider Schlüssel. Rest: alte Mitgliedschafts-Steps `AS:986-1041` → R-09. |
| B-03 | **T** | Feature behoben: `bulk_mark_tagged.feature:26,100` Proxy-Verbindungsprotokoll (`CS:253-265`). Rest: `MS:317-319,1544-1546` setzen `gmail_state.total_connections = 0` weiter im `When`; `AS:4254-4267` (liest Mock-Gmail-Zähler auch für Dovecot, `else`-Zweig toter Code) unverändert → R-18. |
| B-04 | **B** | `policy/policy_reload.feature:91-101`: Proxy, `connections … number exactly 1`, `within 5 seconds … contains a LOGOUT command` (`CS:409-422`), danach `account_hidden`. Rest: No-op-Steps `PS:2390-2398`, `AS:2819-2831` bleiben registriert → R-09. |
| B-05 | **B** | Szenario in `auth/secret_store_backends.feature` enthält keinen „no variable“-Step mehr. Rest: `PS:2861-2873` (Harness-Dict + nachgebaute Server-Namensregel) bleibt → R-09. |

### 1.2 HOCH

| ID | Status | Nachweis |
|---|---|---|
| H-01 | **B** | `tool_surface/bulk_mark_seen.feature:35,45,55,65,74,88-89`: `\Seen` per Mock-State/`direct IMAP FETCH` verifiziert. Rest: „holds 0 messages with flag“ ohne Positivkontrolle → R-08. |
| H-02 | **E** | `add/replace/delete_attachment` (Rewrite empfangener Mails) und Features entfallen in 3.0 (`mcp_tool_discovery.feature:65-89`). Rest: tote Steps `MS:1590-1696` inkl. `_update_uid_after_rewrite` (Server-`new_uid` als Mapping) → R-09. |
| H-03 | **T** | Features behoben: Recovery läuft im `When` (`CS:728-741`, `MS:781-806`), `Then within N seconds the WAL entry for the crashed move has status` liest nur (`AS:5588-5609`). Rest: `AS:1903-1960` (Then ruft `_test_run_recovery`, `except Exception: pass`) bleibt → R-22. |
| H-04 | **T** | `saga_crash_recovery.feature:116-137` nutzt `exact_size_bytes` (Fixture-Verifikation per `RFC822.SIZE`, `IF:397-476`; `AS:5810-5847`, `AS:5889-5910`). Rest: `_body_padded_to` approximiert weiter (`PS:584-596,1018-1019`), Toleranzfenster-Step `AS:3112-3148` → R-03/R-09. |
| H-05 | **B** | Kein WAL-Seeding mehr im Feature; Recovery über echten Crash (`IMAP_MCP_CRASH_AT`). Rest: tote Seed-Steps mit dupliziertem DDL `PS:3187-3384` → R-24. |
| H-06 | **B** | `policy_reload.feature:128-146`: `When the paused saga is released and finishes` (`PS:4255-4267`), `Then the WAL entry for the asynchronous move … committed` + beidseitige IMAP-Suche. Rest: alter Then-Step `AS:3319-3350` (schreibt `.resume` im Then) tot → R-09; Fallback „irgendeine WAL-Zeile“ `PS:4284-4290` wenn `tx_id` fehlt. |
| H-07 | **T** | Recovery-Thens ersetzt durch `search_by_message_id`-Steps. Offen: `intra_account_move.feature:44,88` rufen **live** No-op-Givens → **R-04**; `AS:521-539` (`is unchanged`) schwach; `AS:3185-3193` (`_ = (context, folder)`) tot. |
| H-08 | **B** | `audit/audit_log_format.feature:93-133` mit sichtbaren Policy-/Nachrichtentabellen, explizitem `error.type`/`audit_detail_reason`/HMAC-Literal. Rest: Sonderstep `MS:445-502` tot → R-09. |
| H-09 | **B** | Feature nutzt `calls search with account …, folder …` (`audit_log_format.feature:139`). Rest: Kurzform `MS:1711-1733` (versteckter Account/Ordner) tot → R-09. |
| H-10 | **B** | Digests als **Literal** im Feature (`audit_log_format.feature:128,145`, Regel im Kopf Zeile 14-18); Hook-Test liest `final_hash` aus `eof_day` (`AS:5340-5357`). Rest: Ketten-Formel nur im Step-Code (`AS:5532-5558`, `AI:295`, `AR:146`) → R-11; Zirkelschluss-Step `AS:1640-1664` tot. |
| H-11 | **B** | `audit_log_format.feature:186-224`: reale Werte (Betreff, Anhangsname, Token) mit Positivkontrolle (`fetch_attachment`-Outcome-Record, `AS:5425-5446`); Needles aus Gherkin (`AS:5080-5088`). Rest: alte vakuose Steps `AS:2276-2308` tot → R-09. |
| H-12 | **B** | `audit/audit_retention.feature:34-85`: echte `.gz`-Archive (`AS:5186-5215`), gemerkter SHA-256 (`AS:5218-5237`), `retention_delete` mit `filename`/`age_days 831` (Regel nachgerechnet: 2024-01-12 → 2026-04-22 = 831 Tage), Zustandstabelle mit Existenzprüfung. Rest: `MS:633-640` (`_ = gz_filename`) tot. |
| H-13 | **B** | Fehler/Lifetime/Priming im Mock-Provider (`OS:103-124` → `MO/client.py:33-71`; Feature `oauth2_bootstrap.feature:65,85,120`). Rest: PKCE-Szenario primed `invalid_grant` statt echten Verifier-Mismatch (NIEDRIG, R-33). |
| H-14 | **B** | Zeit über `IMAP_MCP_FAKE_NOW_FILE` (`OS:321-329`), Token-Zähler per Mock-Client (`OS:266-272`), alle Assertions `raise AssertionError`. Rest: nackte `assert` `OS:164,206` → R-33. |
| H-15 | **T** | Feature nutzt `the account entry for … has field "state"` (`OS:332-345`, hart). Rest: `PS:4120-4142` (stilles Durchlaufen, `healthy→active`-Mapping) tot → R-09. |
| H-16 | **T** | Siehe **R-03** (Defaults im Seeding bestehen fort; viele Features liefern jetzt `from_address`/`to_address`/`subject`/`date` explizit). |
| H-17 | **T** | Siehe **R-16/R-17** (implizite Account-Registrierung, Konventions-Policies, versteckte `hornbach.de`-Regeln, Builder-Defaults). |
| H-18 | **E** | `gmail_label_semantics.feature:63-187` seedet per Tabelle (`x_gm_msgid`, UID-Spezifikation sichtbar). Rest: `_uid_base`-Magie `GS:426-499` tot → R-23. |
| H-19 | **B** | Echter Command-Log-Step (`GS:1420-1446`, Feature Zeile 176). Rest: `pass`-Step `GS:624-653` registriert → R-09. |
| H-20 | **T** | `IU:222-244` unverändert (erste Übereinstimmung, `close_all()`), obwohl `find_unique_uid_by_decoded_subject` (`IF:662-673`) existiert; Nutzung in nicht gelesenen Features ungeprüft → R-28. |
| H-21 | **B** | HTTP-Start promotet keine Caller mehr (`MS:537-543`). |
| H-22 | **B** | Passphrase-geschütztes Keypair (`PS:2583-2612`), falsche Passphrase statt gelöschtem Key (`secret_store_backends.feature:126-164`). Rest: alter Step `PS:2702-2826` (löscht Secret-Keys) tot → R-09. |
| H-23 | **B** | Kanonische Tabellen im Gherkin (`reason_code_contract.feature:43-91`) gegen `docs/contract/registry.yaml` (`AS:6184-6266`). Rest: veraltetes `CANONICAL_REASON_CODES` `AS:2014-2041` tot → R-09. |
| H-24 | **B** | `folder_path_contract.feature:31,108-114`: existierender versteckter Ordner wird angelegt, Antworten per `same keys and values except` verglichen (`AS:4854-4865`). |
| H-25 | **E** | Szenario in `non_goal_rejection.feature` entfernt; Rest: `AS:2973-2991` (`_ = …`) tot → R-09. |
| H-26 | **B** | `IF:695-713`: `fetch_flags` wirft bei fehlender UID. Hint-Fallback bleibt → R-02. |
| H-27 | **T** | Features nutzen volle Zeitstempel (`policy_reload.feature:21`); Datums-Variante hängt weiter stillschweigend `T12:00:00Z` an (`PS:173-174`) → R-17. |
| H-28 | **B** | `PX:327-356` (`close_after_forward`: Befehl + Literal werden weitergeleitet, Antwort verworfen), `PX:373-386,444-449` (Delay leitet weiter, verzögert Antwort); Steps `CS:652-695`. Rest: alter `mode close`-Step `PS:1439-1458` verwirft ohne Forward (tot). |
| H-29 | **B** | `fetch_attachment_content.feature:35,49-50`: Quell-Bytes im Gherkin sichtbar, Datei-Bytes direkt dagegen (`AS:3599-3628`), Hash gegen `sha256 hex digest of byte "Q" repeated 4096 times` (`AS:59-65`). Rest: Server-Selbstauskunfts-Steps `AS:1720-1740,4309-4321` (Nutzung in `fetch_attachment_sink.feature` ungeprüft). |
| H-30 | **U** | `inline_attachment_detection.feature` nicht gelesen; Fixture-Builder unverändert (`IF:387-395`, `multipart/mixed`). |
| H-31 | **U** | `connection_reuse.feature` nicht gelesen; `GS:809-839` unverändert (Default-Absender `sender-{i}@example.com`) → R-03. |
| H-32 | **B** | `MG/server.py:148-160` (Passwort), `162-219` (AUTHENTICATE XOAUTH2 + Revocation-Hook), `103-116` (Zustandsautomat, `BAD [CLIENTBUG]`, kein INBOX-Fallback), `308-315,337-345,674-724` (`ALREADYEXISTS`/`TRYCREATE`). Rest: Validator nicht verdrahtet → R-20. |
| H-33 | **B** | `MO/client.py:33-103` + `MO/server.py:52-141,200-293` (Priming, Lifetime, PKCE-S256, Revocation, Bearer-Validierung) + 5 Validierungstests (`MO/tests/test_validation.py`). Rest: R-06, R-21. |

### 1.3 MITTEL

| ID | Status | Nachweis |
|---|---|---|
| M-01 | **U** | Attachment-Rewrite entfallen; `update_draft.feature` nicht gelesen. |
| M-02 | **T** | `bulk_mark_tagged` jetzt über IMAP (`AS:6004-6023`); `AS:4172-4196` unverändert (`no returned message has tag` ohne Mindestanzahl, `tags`/`keywords`-Schema-Raten) → R-08. |
| M-03 | **B** | Eigener SIGKILL-Step `AS:5469-5476`, Feature `audit_log_format.feature:288`. (`MS:809-818` bleibt SIGTERM, aber ungenutzt in dieser Funktion.) |
| M-04 | **T** | Zwei Features laufen produktionsnah (`mcp_tool_discovery.feature:30`, `non_goal_rejection.feature:47`); alle übrigen stdio-Server bekommen `IMAP_MCP_TEST_MODE=1` (`MS:120,858,2428`, `AI:73`) → R-10. |
| M-05 | **T** | Ordner-/Mailbox-Matcher im Proxy (`PX:98-109`, `CS:522-649`); alte verb-only-Steps mit `_ = code`/`_ = folder` `PS:1363-1397,3068-3079` tot → R-09. |
| M-06 | **O** | → **R-02**. |
| M-07 | **T** | Doppelte Muster `GS:809-839` ↔ `PS:4454-4470` bleiben; GS gewinnt und seedet unabhängig vom Account-Typ in `GmailState` → R-23. |
| M-08 | **B** | `AS:549-603` vergleicht exakt (dekodierter Betreff) und zählt; `exact_count`-Pfad. Rest: stilles `return` bei nicht selektierbarem Ordner (`AS:570-572`) → R-08. |
| M-09 | **T** | `OS:74`/`PS:3511` bereinigt; verblieben: `MS:778` (0,4 s nach SIGHUP), `CS:725`, `DS:167-169` → R-19. |
| M-10 | **B** | `MC:251-290`: Queue-Reader + Deadline, `stderr_text` in Fehlermeldung. |
| M-11 | **T** | `AR:117-127,194-211`: defekte Zeilen werden als `is_corrupt` geführt und von `verify_chain` gemeldet. Offen: `AR:77-94` (Prozess-Env/Wallclock) → R-12. |
| M-12 | **O** | `MS:1209-1283` unverändert (`/mcp` ohne Slash, Re-Handshake mit Original-Credentials); Nutzung ungeprüft → R-25. |
| M-13 | **T** | `AS:3684-3700` (Substring), `AS:758-774` (`re.match`), `AS:2531-2543`, `AS:2149-2177` unverändert; `audit_file_missing` jetzt per `contains a record with … missing_filename` (`retention`-Feature:120-124). |
| M-14 | **E** | `list_folders_message_count.feature` entfällt (3.0 streicht `hidden_folders_count`). |
| M-15 | **B** | `IF:662-673` `find_unique_uid_by_decoded_subject`; `AS:5098`. |
| M-16 | **B** | `IF:240-256`: Statusprüfung beim Löschen, Post-Reset-Verifikation (nur leeres `INBOX`). |
| M-17 | **T** | `equals in exact order` existiert (`AS:87-102`); `contains exactly` bleibt reihenfolgeblind (`AS:1794-1798`), Hint-/Pfad-Transformationen `AS:97-99,119-122`. |
| M-18 | **T** | `PS:189-226` (Namens-Substring → Instanz, Spalte `host` ignoriert) unverändert; Nutzung ungeprüft → R-16. |
| M-19 | **O** | `AS:3469-3487` ohne Positivkontrolle → R-08. |
| M-20 | **O** | `TS:32` (`_TEST_BSP_SCHEDULE_DELAY_MS = "1000"`) unverändert → R-19. |
| M-21 | **U** | `connection_pool.feature` nicht gelesen; `CS:385-390` (NOOP-Zähler) neu. |
| M-22 | **B** | `IF:436-450`: Internal Date explizit UTC. |
| M-23 | **U** | nicht gelesen. |
| M-24 | **U** | nicht gelesen. |
| M-25 | **T** | Neue Steps tragen den Account mit (`GS:419-420`); `GS:370,496` mit fixem Account `scaratec-gmail` → R-23. |
| M-26 | **U** | nicht gelesen. |
| M-27 | **U** | nicht gelesen. |
| M-28 | **U** | nicht gelesen. |
| M-29 | **U** | nicht gelesen. |
| M-30 | **U** | nicht gelesen. |
| M-31 | **E** | `localized_gmail_folders.feature` entfällt. |
| M-32 | **E** | `modify_message_capability.feature` entfällt. |
| M-33 | **B** | `MG/server.py:456-462` (Non-PEEK setzt `\Seen`), `894-916` (`_validate_fetch_items`, `BAD` bei Unbekanntem), `973-1000` (ENVELOPE aus RFC822), `114-116` (EXAMINE read-only), `37-38` (nur implementierte Caps), `\bRFC822(?!\.)\b`. |

### 1.4 NIEDRIG

| ID | Status | Nachweis |
|---|---|---|
| N-01 | **O** | Nackte `assert`: `IF:464`, `GS:846-848`, `OS:164,206`, `usability/runner/fixtures.py:300`, `usability/runner/grading.py:95` → R-33. |
| N-02 | **O** | `AS:3452` liest `server/pyproject.toml`; `TS:193-215`/`usability/runner/server.py:38-45` führen die Server-venv aus → R-15. |
| N-03 | **B** | `TS:446-455,464-471`: Negativ-Witness verlangt nicht-leere Span-Menge. |
| N-04 | **O** | `AI:196-200` `join(timeout=60)` ohne `is_alive()`. |
| N-05 | **U** | Feature-Inkonsistenzen: betroffene Features nicht (vollständig) gelesen. |
| N-06 | **O** | `IF:27-60` (zweiter mUTF-7-Encoder), `IF:78-98` (Aliase). |
| N-07 | **O** | `AS:1368,1463` `str(rec.get(k)) == v`. |
| N-08 | **T** | `ENV:230-237` meldet stderr; `ENV:211` `except OSError: pass`, `PX:317-318`. |
| N-09 | **B** | `DS:161-164` `the test runner is not executing as root` (Nutzung in den Rechte-Features ungeprüft). |
| N-10 | **O** | `GS:359-364,416-418`, `PS:1100-1102`, `usability/runner/fixtures.py:274` (`_uid_counters`, `_uid_maps`). |
| N-11 | **T** | `PX` Modus `close` ohne Log (unverändert, `PX:320-325`). |
| N-12 | **T** | `GS:531-561` „direct IMAP SEARCH … X-GM-MSGID“ liest `GmailState` statt IMAP; Docstring-nach-Code `IF:311-312`. |
| N-13 | **U** | nicht gelesen. |
| N-14 | **U** | nicht gelesen. |
| N-15 | **O** | `GS:1091,1313` filtert Server-Verkehr über LOGIN-Namen. |
| N-16 | **T** | `MS:2436-2440` behandelt `timed_out`; `PS:3987-3993,4053-4062` nicht. |
| N-17 | **T** | `MG/server.py:221-231` (LIST ignoriert Pattern), `state.py:453` (UIDVALIDITY konstant); `MO/tests` benötigt Container. |

---

## 2. Prüfmuster-Auswertung (§13.2) und positive Befunde

### P1 — Persistence-Validierung je schreibendem Tool

| Tool | Zweitkanal | Urteil |
|---|---|---|
| `mark_seen` | `IF.fetch_flags` (eigene imaplib-Verbindung; wirft bei fehlender UID) | ok (Hint-Fallback → R-02) |
| `bulk_mark_seen` | Mock-State/`direct IMAP FETCH` (`GS:1199-1214`), Zähler Mock-Log | ok; Abwesenheits-Zähler ohne Positivkontrolle → R-08 |
| `mark_tagged` / `bulk_mark_tagged` | `AS:6004-6023` (IMAP FETCH FLAGS), Proxy-Verbindungszähler | ok → R-08 |
| `move` (intra) | `AS:274-280` IMAP-SEARCH, Proxy-Command-Log `AS:2860-2906` | ok |
| `move` (cross-account) | WAL (`WALReader`), beidseitige IMAP-SEARCH, Mock-/Proxy-Log | ok; Crash-Voraussetzung nicht verifiziert → R-05 |
| `copy` | Schreib-Step `MS:426-442`; Zweitkanal-Steps wie `move` | ok (Feature nicht gelesen) |
| `create_draft` / `create_reply_draft` | `AS:4545-4560`, `AS:4597-4616`, `GS:1279-1304`, `mime_witness_steps` (eigener FETCH + stdlib-Parse) | vorbildlich |
| `update_draft` / `discard_draft` | Header-Occurrence-Count, `exactly one message with headers`, Remember/Differs (`AS:4545-4661`), WAL | vorbildlich |
| `fetch_attachment` | Sink-Datei **direkt** gegen Gherkin-Bytes (`AS:3599-3628`), Mode/Dir-Mode, Datei-Anzahl | ok |

### Positive Muster (zur Nachahmung)

- `PX:327-356,373-386`: `close_after_forward` und `delay` leiten den Befehl inkl. Literal **weiter** und manipulieren nur die Antwort — realistisches Fehlerverhalten (§7.2).
- `MC:251-290`: Queue-basierter Reader mit Deadline; Timeouts enthalten Server-stderr.
- `IF:245-256`: Post-Reset-Verifikation; `IF:397-476`: exakt dimensionierte Nachrichten mit `RFC822.SIZE`-Selbstprüfung.
- `AS:2909-2949` (`contains no SEARCH command`): Absenz-Witness verlangt laufenden, verdrahteten Proxy — Vorlage für R-08.
- `CS:157-183`, `CS:27-59`: fehlender Kanal = Harness-Fehler statt leerem Log.
- `AS:59-65` (`sha256 hex digest of byte "Q" repeated N times`): Ableitungsregel im Gherkin sichtbar, Step rechnet nur nach Gherkin-Vorgabe.
- `MG/server.py`: Zustandsautomat, `BAD [CLIENTBUG]`, `NO [TRYCREATE]`, Pre-/Post-Auth-Capabilities, Label-STORE-Log mit eigenem Parse-Ergebnis.
- `MO/server.py:219-246` + 5 Validierungstests: PKCE-S256 mit echter Verifier-Prüfung, primed Fehler, Lifetime, Revocation.
- `AS:5340-5357`, `audit_log_format.feature:128,145`: HMAC-Digests als Literal im Feature mit dokumentierter Regel im Kopf (vermeidet §4.1-Zirkelschluss).

### Isolation (§6.1)

Kein Import und kein `open()` aus `server/` im `bdd/**`-/`usability/**`-Code, **mit** den Ausnahmen `AS:3452` (liest `server/pyproject.toml`) sowie dem Ausführen der Server-venv (`TS:198-215`, `usability/runner/server.py:38-45`, Binary `ENV:52-57`) → R-15.

---

## 3. Befunde (nach Schweregrad)

### BLOCKER

*Keine.* Kein live ausgeführter Step in den gelesenen Features kann konstruktionsbedingt nicht scheitern (alle Baseline-BLOCKER-Muster sind aus den Szenarien verschwunden).

### HOCH

#### R-01 — Debug-Log nach `/tmp/opencode/mcp_err.log` im Fehlerpfad von `update_draft`
- **Schweregrad:** HOCH · **Bereich:** STEP · **Referenz:** §6.1, §5.3, §4.4
- **Fundstellen:** `MS:2249-2250`, `MS:2257-2258`
- **Nachweis:** `_call_update_draft` schreibt in **jedem** `MCPRPCError`- und Exception-Pfad per `open("/tmp/opencode/mcp_err.log", "a")` in ein hart codiertes Verzeichnis außerhalb des Scratch-Verzeichnisses. Existiert `/tmp/opencode/` nicht (CI, frischer Host), löst der Debug-Write `FileNotFoundError` aus, überdeckt die eigentlich erwartete Protokollantwort (`-32602`, `draft_revision_conflict` etc.) und lässt die Negativszenarien in `update_draft_errors.feature`/`update_draft.feature` aus Gründen scheitern, die mit dem Systemverhalten nichts zu tun haben. Außerdem leckt die Datei Testdaten über Läufe hinweg (Isolation §6.1).
- **Korrekturauftrag (`code-entwickler-flash`):** Beide `with open(...)`-Blöcke ersatzlos entfernen. Bei Bedarf Diagnose in die `AssertionError`-Meldung bzw. `context.scratch_dir` schreiben.

#### R-02 — Stille `lookup.get(key, uid)`-Fallbacks (UID-Hint wird zur echten UID)
- **Schweregrad:** HOCH · **Bereich:** STEP · **Referenz:** §1.3, §13.2 P3, §4.1
- **Fundstellen (Auswahl):** `MS:299,356,403,440,497,926,1325,1449,1471,1498,1524,1582,1607,1639,1668,1939,1963,1993,2023,2054,2085`; `CS:231`; `DS:246`; `AS:150,175,620,662,3564-3568,5716,5735`; `PS:1579,1641,1647,1730,1807,2383,4215`; `IU:97-111`; `TS:153-169`
- **Nachweis:** Fehlt ein Mapping-Eintrag, wird stillschweigend der im Szenario genannte Hint als echte UID an den Server geschickt bzw. in der Assertion verwendet. Ein fehlgeschlagenes Seeding, ein vertauschter Ordner oder ein Account-Schlüsselfehler (z. B. `GS:370,496` mit fixem Account `scaratec-gmail`) wird so nicht zum Fehler, sondern kompensiert. `AS:3564-3568`/`IU:100-103`/`PS:2383` iterieren zusätzlich über **alle** Schlüssel und nehmen den ersten Treffer mit passendem Hint (Account/Ordner ignoriert).
- **Korrekturauftrag:** Zentrale Funktion `resolve_uid(context, account, folder, hint, *, deny_path=False)`: fehlender Eintrag ⇒ `AssertionError("No message seeded as <hint> in <account>:<folder>")`, außer Szenarien mit ausdrücklichem Deny-Pfad (UID existiert absichtlich nicht; dann explizites Opt-in, z. B. `uid 9999` im Gherkin ohne Seed ⇒ Step-Variante `… uid {uid:d} (not seeded)`). Alle oben genannten `lookup.get(…, uid)` darauf umstellen; `AS:3564-3568`, `IU:97-111`, `PS:2383` nur mit (Account, Ordner)-Schlüssel.

#### R-03 — Versteckte fachliche Defaults und Näherungen im Seeding (§13.2 P3)
- **Schweregrad:** HOCH · **Bereich:** GHERKIN+STEP · **Referenz:** §13.2 P3, §1.3, §2.1
- **Fundstellen:**
  - `PS:994-995` (`from`/`to` ⇒ `"{user}@bdd.local"`), `PS:936` (Betreff `"Test"`), `PS:1021` (Body `"Body for {subject}"`), `PS:1024` (`has_attachment true` ⇒ unsichtbares `stub.bin`, 16 Byte), `PS:1033` (Message-ID `<scenario-{uid}@bdd.local>`), `PS:744-785` (`_stage_handle_drafts`: `from`/`to`/`date` leer ⇒ Default)
  - `PS:4413-4451,4454-4470,4589-4595` (`_stage_n_simple_messages`: Absender `bulk@example.com`, Betreff `Bulk seed #n`, `to` leer, UIDs ab 9000)
  - `GS:51-70` (`to_addr="test@bdd.local"`, Datum „jetzt“), `GS:253-254` (`test@example.com`), `GS:450-463` (`rechnung@hornbach.de` für `canonical_all_mail_uid`), `GS:719-733` (`trash@example.com`), `GS:820-838` (`sender-{i}@example.com`, Datum „jetzt“), `GS:894-898` (`To: {user}@bdd.local`, Message-ID)
  - `AS:5758-5768,5796-5806` (`To: me@example.com`, festes Datum `Wed, 06 May 2026 09:00:00 +0000`)
  - `PS:584-596,1018-1019` (`size_bytes`-Spalte wird nur **angenähert**, `_body_padded_to`)
- **Nachweis:** Fehlt eine Spalte, erzeugt der Step fachliche Werte, die später für Assertions relevant sind: Sender-Regeln (`from_domain`), Cc-/Reply-All-Ableitung (To ∪ Cc minus Identität), zeitfenster-/`since`-Kriterien (Datum „jetzt“), Größenschwellen. Das Feature-File zeigt diese Werte nicht (§2.1/§13.2 P3). Die baseline-geforderte „harte `AssertionError` bei fehlenden Pflichtspalten“ ist nicht umgesetzt.
- **Korrekturauftrag:** Für alle Seeding-Steps Pflichtspalten definieren (`from_address`/`from`, `to_address`/`to`, `subject`, `date`; `message_id` nur bei Reply-/Saga-Szenarien) und bei Fehlen `AssertionError("Seeding table column '<name>' required")`. `has_attachment true` ersetzen durch das vorhandene `the message has attachment "<name>" of type … with size N bytes` (`PS:3754-3771`) bzw. den Step mit sichtbaren Bytes. `size_bytes`-Spalte: exakte Größe über `IMAPFixture.seed_message(exact_size_bytes=…)` (existiert, `IF:397-476`) statt `_body_padded_to`. Bulk-Steps: Absender/Betreff/`to` als Parameter ohne Default (`holds N messages from "<addr>"`), die Variante ohne Absender entfernen. Mock-Gmail-Seeding (`GS:809-839`, `GS:894-898`) auf dieselben Pflichtfelder zwingen.

#### R-04 — Live-No-op-Givens `is empty` und `contains no message with uid`
- **Schweregrad:** HOCH · **Bereich:** STEP · **Referenz:** §13.1, §5.1, §4.3
- **Fundstellen:** `PS:1133-1162` (`step_folder_is_empty`: Docstring „assert (and enforce) emptiness“, Code `pass`, berechnetes `uids` wird nie geprüft), `PS:1510-1515` (`step_folder_has_no_uid`: `_ = (context, folder, uid)`); Verwendung: `transactions/intra_account_move.feature:44` (`And the IMAP folder "Archiv/Rechnungen-2026" is empty`, erbt den Step-Typ Given), `intra_account_move.feature:88` (`the folder "INBOX/Rechnungen" contains no message with uid 9999`)
- **Nachweis:** Beide Vorbedingungen sind im Szenario sichtbar, werden aber nie geprüft; sie stützen die Erwartungen `not_found` (UID 9999) und die spätere Leerprüfung des Zielordners. Die Zusicherung „enforce emptiness“ im Docstring ist unwahr.
- **Korrekturauftrag:** Beide Steps als echte Assertions implementieren (`folder_uids == []` bzw. `9999 not in folder_uids`, `AssertionError` mit Ist-Liste) oder die Zeilen im Feature streichen. Gleiche Behandlung für tote Geschwister `AS:3185-3193`, `AS:2819-2831`, `PS:2390-2398`, `GS:624-653` (siehe R-09).

#### R-05 — Crash-Szenarien verifizieren den Absturz nicht
- **Schweregrad:** HOCH · **Bereich:** STEP · **Referenz:** §4.3, §13.1, §5.1
- **Fundstellen:** `MS:915-949` (`invokes move expecting crash`: `except Exception: pass`), `MS:952-997` (`invokes update_draft expecting crash`: ebenso), `MS:2256-2269`; genutzt in `transactions/saga_crash_recovery.feature:47,59,71,84,97,109,121,133`, `draft_revision_recovery.feature:46,71`
- **Nachweis:** Der Step erwartet einen Serverabsturz (`IMAP_MCP_CRASH_AT`), prüft ihn aber nie: weder wird verlangt, dass der Aufruf fehlschlug, noch dass der Prozess tot ist. Löst der Hook nicht aus (Hook umbenannt, nicht gelesen, TEST_MODE fehlt), läuft der Move normal durch — und die folgenden Erwartungen (`committed`, Quelle leer, Ziel genau eine Nachricht, `staged_rfc822 is null`) sind für fünf der acht Szenarien **trivial erfüllt**. Der Test belegt dann nur den Erfolgspfad, nicht die Recovery.
- **Korrekturauftrag:** Im Step nach dem Aufruf `assert/raise`: Aufruf hat `MCPClientError`/Broken-Pipe geliefert **und** `client.poll_exit()` ist nicht `None` (ggf. mit kurzem Polling-Timeout); sonst `AssertionError("Crash hook <at> did not terminate the server")`. `except Exception` auf `(MCPClientError, BrokenPipeError, OSError)` eingrenzen.

#### R-06 — mock-oauth fabriziert Erfolg, wenn der Upstream einen Refresh ablehnt
- **Schweregrad:** HOCH · **Bereich:** MOCK · **Referenz:** §7.2, §13.2 P1
- **Fundstellen:** `MO/server.py:281-291`; Verwendung der Platzhalter-Refresh-Tokens `OS:132` (`valid-mock-refresh-token`), `PS:4114,4176` (`mock_refresh_token`), `MO/tests/test_validation.py:194-203`
- **Nachweis:** Antwortet der Upstream auf `grant_type=refresh_token` nicht mit 200 (z. B. unbekannter/widerrufener Refresh-Token ⇒ `invalid_grant`), erzeugt der Mock stattdessen **selbst** ein `200`-Token (`mock-access-token-<ms>`). Ein echter Provider lehnt unbekannte Refresh-Tokens ab; der Server-Pfad „Refresh schlägt fehl ⇒ `needs_rebootstrap`“ ist daher nur über Priming erreichbar, nicht über die tatsächliche Tokenvergabe. Test (d) in `test_validation.py` räumt das ein (Kommentar „Upstream … accepts dummy_rt or returns upstream response“) und prüft nur, dass die *geprimte* Meldung ausbleibt.
- **Korrekturauftrag:** Refresh-Tokens im Mock führen (ausgestellte Tokens aus Code-Exchange merken, `revoke_refresh_token` ergänzen) und unbekannte/widerrufene mit `400 invalid_grant` beantworten; Fallback-Block `281-291` entfernen. Platzhalter-Tokens der Harness (`OS:132`, `PS:4114,4176`) über einen Mock-Admin-Endpunkt „issue refresh token“ registrieren. Test (d) um einen unbekannten Token ⇒ 400 erweitern.

#### R-07 — Usability-Harness läuft gegen den gehärteten mock-gmail nicht mehr (Login scheitert)
- **Schweregrad:** HOCH · **Bereich:** STEP/MOCK (Usability-Harness) · **Referenz:** §7.2, §5.3
- **Fundstellen:** `usability/runner/serverconf.py:15` (`FIXTURE_PASSWORD = "fixture-only"  # mock-gmail accepts any password`), `serverconf.py:63`, `usability/runner/run.py:157-160`, `usability/runner/fixtures.py:265-267`; Mock: `MG/server.py:148-160`, `MG/state.py:165`
- **Nachweis (statisch):** `GmailState.password` ist `"test"`, `LOGIN` mit abweichendem Passwort wird mit `NO [AUTHENTICATIONFAILED]` beantwortet. Der Usability-Runner legt `GmailState()` an, `seed_state` ruft `state.reset()` (setzt das Passwort nicht), und schreibt `fixture-only` ins Secret. Jeder Server-Login im Usability-Lauf scheitert; der Kommentar „mock-gmail accepts any password“ ist seit der Mock-Härtung falsch. Ergebnisverzeichnisse (`usability/results/20261103…`) stammen aus der Zeit vor der Härtung. (Nicht ausgeführt; aus dem Lesen von Mock und Runner belegt.)
- **Korrekturauftrag:** `state.password = FIXTURE_PASSWORD` im Runner setzen (oder `FIXTURE_PASSWORD = "test"`), Kommentar korrigieren, Regressionstest im Usability-Smoke-Test (`usability/tests`), der einen Login gegen den Mock durchführt.

#### R-08 — Abwesenheits-Checks ohne Positivkontrolle (live)
- **Schweregrad:** HOCH · **Bereich:** STEP · **Referenz:** §4.3, §4.4, §13.2 P1
- **Fundstellen:**
  - `CS:359-382` (`contains no {verb} command`, Dovecot-Pfad): kehrt bei fehlendem/leerem Proxy-Log **stillschweigend** zurück (`CS:372-376`); genutzt in `saga_crash_recovery.feature:75,101`
  - `AS:6004-6045` und `PS:4556-4586` (`contains N messages with keyword/flag`, `holds N messages with flag`): bei `N = 0` besteht der Step auch, wenn der Ordner leer oder nicht selektierbar ist (`IF:572-577`: `folder_uids` liefert `[]` bei SELECT-Fehler); genutzt in `bulk_mark_tagged.feature:48,58,70,80,90`, `bulk_mark_seen.feature:45,55,65`
  - `AS:3469-3487` (`audit log does not contain any entry with tool`), `AS:4187-4196` (`no returned message has tag`, keine Mindestanzahl), `AS:1608-1619` (`records_today` ⇒ ggf. leere Menge, siehe R-12), `AS:570-572` (`return` bei nicht selektierbarem Ordner im `does not contain`-Pfad)
- **Nachweis:** Die Checks sind dort sinnvoll, wo vorher etwas Positives belegt ist; als Steps erzwingen sie es nicht. Ein Server, der die Nachrichten löscht, den Ordner nicht öffnet oder gar keine IMAP-Verbindung aufbaut, besteht „0 Nachrichten mit Keyword“, „kein APPEND“, „kein Audit-Eintrag“.
- **Korrekturauftrag:** (a) `CS:359-382` an das Muster `AS:2909-2949` angleichen: läuft der Proxy, ist er verdrahtet, ist das Log nicht leer — sonst Harness-`AssertionError`. (b) `…contains/holds N … with flag/keyword` bei `N = 0` zuerst die Gesamtzahl der Ordner-UIDs `> 0` (oder ein im Gherkin genannter Erwartungswert) verlangen; `IMAPFixture.folder_uids` bei SELECT-Fehler `RuntimeError` statt `[]`. (c) `AS:3469-3487`, `AS:4187-4196` Mindestanzahl Records/Messages `> 0` vorab prüfen.

### MITTEL

#### R-09 — Tote, aber registrierte No-op-/Zirkelschluss-Steps (latente Scheintests)
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §13.1, §5.1, §4.1
- **Fundstellen:** `PS:2390-2398`, `AS:2819-2831` (Pool-Drain `_ = …`), `PS:2861-2873` (Env-Check gegen Harness-Dict + Server-Namensregel), `AS:3185-3193`, `AS:2973-2991` (`_ = (caller_id, other_caller)`), `GS:624-653` (`pass`), `PS:4120-4142` (`healthy`-Mapping, stilles Durchlaufen), `AS:4199-4251` (Marker-Step `armed` + Scan), `AS:2276-2308` (vakuose „no token/Subject/filename“), `AS:1640-1664` (SHA über Kanon-JSON), `AS:1720-1740` (Hash aus `size_bytes` der Antwort), `AS:2014-2041` (`CANONICAL_REASON_CODES`, veraltet), `AS:986-1041` (Mitgliedschafts-„Metadaten“), `AS:3112-3148` (Größenfenster +4096), `AS:3319-3350` (Then schreibt `.resume`), `MS:445-502`, `MS:1711-1733`, `MS:633-640`, `MS:1590-1696`, `PS:3187-3384` (WAL-Seed mit Schema-Duplikat), `PS:2702-2826` (löscht Secret-Keys statt Passphrase), `PS:1363-1397,3068-3079` (`_ = code`, `_ = folder`), `PS:1439-1458` (`mode close` ohne Forward), `PS:2829-2858` (`if not captured: return`)
- **Nachweis:** Von den gelesenen Features nutzt keines diese Steps mehr (verifiziert an `policy_reload`, `secret_store_backends`, `saga_crash_recovery`, `reason_code_contract`, `audit_log_format`, `audit_retention`, `mcp_tool_discovery`, `gmail_label_semantics`, `non_goal_rejection`, `bulk_mark_*`). Sie bleiben aber wiederverwendbar; ein Autor, der den Satz aus der Baseline zitiert, bekommt wieder einen Scheintest. Für nicht gelesene Features (Abschnitt 5) ist die Nutzung **nicht ausgeschlossen**.
- **Korrekturauftrag:** Steps löschen. Falls ein Step in einem nicht gelesenen Feature benutzt wird: als echte Assertion implementieren oder Zeile im Feature streichen. Als CI-Gate: Skript, das jeden `@given/@when/@then`-Step ohne `raise`/Vergleich oder mit `pass`/`_ = (…)` meldet (siehe Baseline §7).

#### R-10 — Test-Modus wird für alle stdio-Server gesetzt
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §5.3, §7.2
- **Fundstellen:** `MS:120,858,2428`, `AI:73`, `PS:1360,1467,3092,5074,5103`, `usability/runner/run.py:180`
- **Nachweis:** `_ensure_mcp_client` und Geschwister setzen `IMAP_MCP_TEST_MODE=1` unabhängig von `@test_hooks` (`ENV:159`, `context.is_test_hooks` wird nur für den HTTP-Start gelesen, `MS:541`). Nur zwei Features fahren produktionsnah (`mcp_tool_discovery.feature:30`, `non_goal_rejection.feature:47`). Im Usability-Lauf bekommt der **Agent** einen Server mit Test-Tools und Fake-Uhr.
- **Korrekturauftrag:** `IMAP_MCP_TEST_MODE` nur setzen, wenn `context.is_test_hooks` (Tag) oder ein expliziter Hook-Step es verlangt; Usability-Runner ohne Test-Modus (Fake-Uhr über separaten Mechanismus oder dokumentierte Ausnahme).

#### R-11 — Ketten-Formel, Schema und magisches Datum im Glue
- **Schweregrad:** MITTEL · **Bereich:** GHERKIN+STEP · **Referenz:** §4.1, §1.3
- **Fundstellen:** `AS:5532-5558` (`offline chain verifier` rechnet `"sha256:" + sha256(prev_line)` nach), `AI:295-300`, `AR:146`, `AS:5141-5172,5188-5215` (`_create_historical_sealed_file` baut Records mit eigenem Schema und Hash; `is_today = (date_str == "2026-04-21")`, Record-Inhalt `reason: authenticated`, `ts`-Format)
- **Nachweis:** Die Hash-Kettenregel steht in drei Python-Orten, aber nicht in den Feature-Köpfen (`audit_log_format.feature:14-18` dokumentiert nur die HMAC-Regel). `_create_historical_sealed_file` verhält sich anders, wenn die Dateiname das Datum `2026-04-21` trägt (aktive Tagesdatei 0600 statt versiegelt 0400) — eine unsichtbare Kopplung an die Uhrzeit eines Szenarios (`audit_retention.feature:80`, Outline-Zeile 1).
- **Korrekturauftrag:** Kettenregel (Prev-Hash = `sha256:` + SHA-256 der vorherigen Zeile inkl. Newline) in den Feature-Kopf aufnehmen und in `AuditReader` kapseln; Seed-Dateien als sichtbare Tabellenwerte (`sealed`/`active`) statt über ein Datums-Literal im Step.

#### R-12 — `AuditReader.records_today` nimmt Prozess-Env / Wallclock / „neueste Datei“
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §4.1, §4.4, §5.3
- **Fundstellen:** `AR:77-94`; Nutzung `AS:1267,1287,1307,1324,1342,1364,1462,1506,1614,1632,2051,2495,…`, `GS`/`PS`-Aufrufe
- **Nachweis:** Die Methode liest `IMAP_MCP_FAKE_NOW_UTC` aus `os.environ` des **Harness**, nicht aus `context.mcp_extra_env`/Uhrdatei; fällt sonst auf „neueste Datei“ bzw. Wallclock zurück. Bei mehreren Tagesdateien (Tagwechsel-Szenarien) liefert sie nur die neueste; Negativ-Steps darauf (`AS:1608-1619`) laufen auf leerer oder unvollständiger Menge.
- **Korrekturauftrag:** `records_today` entfernen oder auf `context`-Uhr (`FAKE_NOW_FILE`/`FAKE_NOW_UTC` aus `mcp_extra_env`) umstellen; Negativ-Steps lesen alle Dateien (`reader.records()`) mit Positivkontrolle `len(records) > 0`.

#### R-13 — `outcome record … result` akzeptiert jedes Ergebnis bei `ALLOW`
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §13.1, §5.1
- **Fundstellen:** `AS:1187` (`r.get("result") == result or (result == "OK" and r.get("decision") == "ALLOW")`)
- **Nachweis:** Erwartet das Szenario `result "OK"`, besteht jeder Record mit `decision ALLOW` — auch `result "ERROR"` (z. B. IMAP-Fehler nach ALLOW). Die benannte Feldgröße kann nicht scheitern.
- **Korrekturauftrag:** Nur `r.get("result") == result` vergleichen.

#### R-14 — `subprocess.run` ohne Timeout/Stdin im „refuses to start“-Step
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §4.4
- **Fundstellen:** `AS:5501-5519`; genutzt in `audit_log_format.feature:295-307` (6 Beispiele)
- **Nachweis:** Hält der Server den Test-Hook-Start nicht fatal an (genau der Fehlerfall), läuft er mit geerbtem Stdin endlos; kein `timeout`, kein `input`.
- **Korrekturauftrag:** `support.server_bootstrap.try_bootstrap_server` verwenden (Timeout, `timed_out` ⇒ `AssertionError`) statt rohem `subprocess.run`.

#### R-15 — Zugriff auf `server/` (Isolation §6.1)
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §6.1, §13.3
- **Fundstellen:** `AS:3447-3466` (`Path(__file__).parents[3] / "server" / "pyproject.toml"` gelesen), `TS:193-215` (führt `server/.venv/bin/python` aus), `usability/runner/server.py:38-45` (ebenso), `ENV:49-57` (Binary-Pfad)
- **Nachweis:** Der Step `the server info version matches the installed sc-imap-mcp package version` liest Produktionsquellcode (`pyproject.toml`). `features/tool_surface/mcp_tool_discovery.feature:132-143` fordert bereits die Literalversion `3.0.0` im Gherkin; der Pyproject-Abgleich ist redundant und verschiebt Wahrheit in den Produktions-Tree.
- **Korrekturauftrag:** Step `AS:3447-3466` entfernen (Literalversion im Feature genügt). `_installed_server_version`/`server_info()` über `pip show`-äquivalente Ausgabe oder `serverInfo` belegen, nicht über Pfad in `server/`; Binary-Pfad nur über `IMAP_MCP_SERVER_BINARY`.

#### R-16 — Implizite Account-Registrierung und Namens-Mapping
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §13.2 P3, §1.3
- **Fundstellen:** `PS:53-69` (`_ensure_account_registered`, Fixture-Passwort, Host/Port), aufgerufen u. a. aus `PS:84,96,141,1283,1321,1674,1769,1877,1897,2441,3417`, `IU:56-63`, `_start_imap_proxy` (`PS:2909`); `PS:189-226` (Instanz über Namens-Substring `gmail`/`gupta`/`osthues`, Spalte `host` ignoriert, `auth_type` aus Spaltenanwesenheit); `IF:78-98` (Aliase `gmail-ronly`, `ext-user`, `g-work`… teilen Mailboxen mit anderen IDs)
- **Nachweis:** Ein Szenario, das einen Account nur erwähnt, bekommt ihn unsichtbar angelegt (Instanz, Port, Passwort, Secret-Ref), und `capture_witnessed_command_logs` (`PS:3036-3060`) registriert ihn bei jedem Command-Log-Witness. Der Abgleich Szenario↔Konfiguration wird nicht mehr durch fehlende Deklaration scheitern.
- **Korrekturauftrag:** Registrierung nur in den dafür benannten Given-Steps (`the IMAP account … exists …`); in Hilfs-Steps `AssertionError`, wenn der Account nicht deklariert ist. `PS:189-226`: Spalte `host` als Alias gegen `IMAP_INSTANCES` auflösen oder entfernen; Instanzwahl explizit (Spalte `instance`).

#### R-17 — Versteckte Policy-/Caller-/Builder-Defaults
- **Schweregrad:** MITTEL · **Bereich:** GHERKIN+STEP · **Referenz:** §13.2 P3, §1.3, §5.1
- **Fundstellen:** `PS:3450-3485` (`minimal configuration`: Regel `from_domain=hornbach.de → ENVELOPE`, `move_out/mark_seen/mark_tagged = True`, Policy `invoice-policy`), `PS:1208-1247` (`mark_tagged=true on`: heimliche `hornbach.de → FULL`-Regel), `PS:243` (fehlende `policy`-Spalte ⇒ `<caller>-policy`), `PS:3515-3528` (Konventionsname `<prefix>-agent → <prefix>-policy`), `PS:1876-1882,1896-1902` (`invoice-policy`/`invoice-agent`), `PS:3300ff`-Aufrufer `PS:1283-1294` (`audit-hook-policy`), `PS:173-176` (Datum ohne Uhrzeit ⇒ `T12:00:00Z`), `PB:25-27,36,97` (Default-Host/Port/Provider/`token_cache = memory_only`, `acknowledge_plaintext = True`), `MS:2426` (`bootstrap-probe` als Fallback-Caller), `CS:737`/`MS:886,1499` (`invoice-agent`/`draft-agent` als Fallback-Caller)
- **Nachweis:** Ergebnisse (ALLOW/DENY, Sichtbarkeit, Startverweigerung) hängen an Policy-/Caller-Daten, die im Szenario fehlen. `PB:97` injiziert `acknowledge_plaintext: true` — ein sicherheitsrelevantes Opt-in — als Default.
- **Korrekturauftrag:** Steps mit Defaults entfernen oder bei fehlenden Angaben `AssertionError`; `PB`-Defaults auf `None` und im Step explizit setzen; die Datums-Variante von `server clock is pinned` entfernen (nur volle Zeitstempel, dann ist die Referenzzeit für Sub-Tages-Fenster sichtbar, §2.2).

#### R-18 — Versteckte Zähler-Manipulation und falscher „IMAP server“-Zähler
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §13.2 P1/P2, §4.1, §5.3
- **Fundstellen:** `MS:317-319`, `MS:1544-1546` (`gmail_state.total_connections = 0` im `When`), `AS:4254-4267` (`IMAP server received at most`: nutzt `gmail_state.total_connections`, immer 0 bei Dovecot), `GS:842-848` (nackte `assert`)
- **Nachweis:** Die Verbindungs-Absicherung der Bulk-Szenarien läuft inzwischen über Proxy/Mock-Zähler (verifiziert), aber der Reset im `When` ist eine versteckte Manipulation des Zeugenzählers, und der generische „IMAP server received …“-Step liest für Dovecot-Accounts den Mock-Zähler (Wert 0 ⇒ stets wahr).
- **Korrekturauftrag:** Reset-Zeilen aus `MS` streichen (der Reset geschieht in `before_scenario`); `AS:4254-4267` löschen oder wie `CS:157-183` an Proxy koppeln; nackte `assert` in `GS:846` durch `raise AssertionError`.

#### R-19 — Feste Sleeps und Flush-Witness-Verzögerung
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §4.4
- **Fundstellen:** `MS:778` (`sleep(0.4)` nach SIGHUP), `CS:721-725` (`sleep(n)` „… elapse for the delayed upstream APPEND“), `DS:167-169` (`sleep(0.05)`), `TS:28-32` (BSP-Delay 1000 ms, Flush-Szenarien erlauben 5 s ⇒ periodischer Export kann den Shutdown-Flush ersetzen, Baseline M-20)
- **Korrekturauftrag:** Auf Bedingung pollen (Audit-Record `policy_reload`, Upstream-Zustand per Mock-/Proxy-Log nach Weiterleitung, Prozess-Exit). Für die beiden Flush-Szenarien `OTEL_BSP_SCHEDULE_DELAY` deutlich über die Beendigungsdauer setzen.

#### R-20 — mock-gmail: XOAUTH2-Validator nicht verdrahtet; kleine Protokollabweichungen
- **Schweregrad:** MITTEL · **Bereich:** MOCK · **Referenz:** §7.2
- **Fundstellen:** `ENV:95-112` (kein `gmail_state.token_validator`), `MG/state.py:168`, `MG/server.py:208-215`; `MG/server.py:37` (`AUTH=PLAIN` beworben, `AUTHENTICATE PLAIN` ⇒ `NO` in `167-169`), `221-231` (LIST ignoriert Referenz/Pattern), `674-724` (`UID COPY`/`UID MOVE` nur eine einzelne UID), `693` (COPY nach All Mail fügt den Ordnernamen als Label hinzu), `296-298` (CLOSE expungiert `\Deleted` nicht)
- **Nachweis:** Die Zusage „Bearer gegen Mock-OAuth validieren“ (Baseline H-32 a) ist nicht eingelöst: Jeder nichtleere Bearer gilt, außer er wurde über den Test-Hook einmalig widerrufen. Ein abgelaufener oder erfundener Token bleibt auf dem Mock grün.
- **Korrekturauftrag:** `GmailState.token_validator` in `ENV.before_all` mit `mock_oauth_client.validate_bearer` verdrahten (und `MO.validate_bearer` für **unbekannte** Tokens `False` liefern, siehe R-21); `AUTH=PLAIN` nicht bewerben oder implementieren; `UID COPY/MOVE` mit UID-Mengen; CLOSE-Semantik nach RFC 3501 §6.4.2.

#### R-21 — mock-oauth: weitere Nachsichtigkeiten
- **Schweregrad:** MITTEL · **Bereich:** MOCK · **Referenz:** §7.2, §13.1
- **Fundstellen:** `MO/server.py:121-141` (`validate_bearer` ⇒ `True` für nie ausgestellte Tokens), `224` (`if code_info and code_info.get("challenge")`: unbekannter Code oder Authorize ohne PKCE ⇒ keine Verifier-Prüfung), `78-98` (`redirect_uri`/`client_id` gespeichert, beim Exchange nicht verglichen), `MO/client.py:105-107` (`prime_consent` ist `_ = (issuer_id, subject)`), `MO/tests/test_validation.py` (benötigt Container `127.0.0.1:19080`)
- **Korrekturauftrag:** Bearer nur gültig, wenn ausgestellt und nicht widerrufen/abgelaufen; Token-Exchange mit unbekanntem Code ⇒ `400 invalid_grant`; `redirect_uri`/`client_id` vergleichen; `prime_consent` entfernen oder implementieren; Tests ohne Container (Upstream-Stub) oder klare CI-Voraussetzung.

#### R-22 — Then-Step `reaches status … within` stößt Recovery erneut an
- **Schweregrad:** MITTEL (latent; aktive Features nutzen die Variante `crashed move`) · **Bereich:** STEP · **Referenz:** §1.3, §5.1, §4.4
- **Fundstellen:** `AS:1903-1960` (`_test_run_recovery` in jeder Polling-Iteration, `except Exception: pass`)
- **Nachweis:** Das Muster ist die Baseline-H-03-Konstruktion; es verdeckt „läuft nur einmal“-Verträge.
- **Korrekturauftrag:** Step löschen oder auf reines WAL-Polling reduzieren (wie `AS:5588-5609`).

#### R-23 — Gmail-Seeding: fixe Konvention, fixer Account-Schlüssel, Schattenmuster
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §13.2 P2/P3, §5.2
- **Fundstellen:** `GS:477-481,496` (`_uid_base = {"INBOX": 505, "Rechnungen": 510, "Hornbach": 520}`), `GS:370,496` (`("scaratec-gmail", …)`), `GS:809-839` ↔ `PS:4454-4470` (identisches Muster; `GS` gewinnt alphabetisch und seedet **immer** in `GmailState`, auch für Dovecot-Accounts)
- **Korrekturauftrag:** `_uid_base`-Konvention löschen (Features liefern UIDs bereits in Tabellen/`with UID …`); Account aus dem Step übernehmen; Duplikat entfernen oder Account-Typ unterscheiden.

#### R-24 — Kopplung an das WAL-Schema im Given (Gmail-Revisionsszenario)
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §13.2 P3, §1.3, §4.1
- **Fundstellen:** `GS:1517-1524` (`DELETE FROM transactions WHERE status IN ('pending','staged')`, `except sqlite3.OperationalError: pass`), `PS:3254-3283,3329-3359` (duplizierte DDL, tot, siehe R-09), `GS:1499` (Fallback-Caller `draft-agent`)
- **Nachweis:** Das Given manipuliert die Server-WAL über Status-Namen des Server-Schemas und schluckt Schema-Fehler; ändert sich das Schema, läuft der Test ohne die gewünschte Vorbedingung weiter.
- **Korrekturauftrag:** Zustand ohne aktiven WAL-Eintrag über einen Crash-Hook (`IMAP_MCP_CRASH_AT`) herstellen statt über direkte SQL-Löschung; `OperationalError` nicht schlucken.

#### R-25 — `Then`-Steps mit Seiteneffekten und Re-Authentisierung
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §1.3, §5.1, §4.3
- **Fundstellen:** `AS:1233-1250` (`new IMAP connection attempts … are refused`: ruft `list_folders` im Then auf), `AS:3279-3293` (`server … logs a critical error`: ruft `list_accounts`, Magic-String `Active audit file disappeared`), `AS:2351-2365` (`subsequent get_caller_identity returns`: ruft das Tool im Then), `MS:1264-1283` (`still returns`: Re-Handshake mit **Original-Credentials**, `/mcp` ohne Slash in `MS:1230-1232`)
- **Nachweis:** Die Aktion im Then erzeugt neue Audit-/IMAP-Ereignisse und verändert, was später beobachtet wird (Heisenberg-Test). Der Re-Handshake mit Originaldaten kann die behauptete Session-Unveränderlichkeit nicht widerlegen.
- **Korrekturauftrag:** Aufrufe in `When` verschieben, `Then` nur lesen; Immutable-Identity-Szenario auf den tatsächlichen Vertrag (jeder Request authentifiziert separat) umformulieren oder Session-ID-Verhalten testen.

#### R-26 — `folder is reset to hold messages` überspringt das Reset stillschweigend
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §1.3, §6.2
- **Fundstellen:** `PS:653-687`
- **Nachweis:** Enthält die Tabelle nur bereits früher gesäte Hints, wird weder geleert noch neu gesät — der Step „resettet“ dann nichts (`return` Zeile 679). Flags/Zustände aus vorherigen Aufrufen bleiben erhalten.
- **Korrekturauftrag:** Immer leeren und alles neu seeden oder den Step in „adds new messages“ umbenennen.

#### R-27 — Feste UID-/Subject-Auflösung in nicht eindeutigen Steps (H-20-Rest)
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §4.3, §13.2 P1
- **Fundstellen:** `IU:222-244` (`exactly one result` prüft nur `uid is not None`, ruft `close_all()`), `IU:97-111` (Hint-Mapping über alle Schlüssel), `AS:6064-6149` (erste Übereinstimmung statt Eindeutigkeit), `IF:496` (`_ = (subject, sender)` — `_lookup_uid_by_subject_and_from` ignoriert beide Parameter und liefert die höchste UID)
- **Korrekturauftrag:** `find_unique_uid_by_decoded_subject` verwenden; `IF:485-502` entweder echte Filterung oder Parameter entfernen.

#### R-28 — Seeding-Fixture: Dateinamens- und Statusannahmen
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §4.3, §5.1
- **Fundstellen:** `IF:277-287` (`create_folder` ignoriert `CREATE`-Status), `IF:455` (`conn.create` vor APPEND ohne Status), `IF:311-312` (Docstring nach Code), `AS:5810-5847` (Date-Header als rohe ISO-Zeichenkette `Date: 2026-04-01T10:00:00Z` — kein RFC-5322-Datum; SENTON-/Fallback-Verhalten hängt vom Server-Parser ab)
- **Korrekturauftrag:** `CREATE`-Status prüfen (`[ALREADYEXISTS]` toleriert); Date-Header per `email.utils.format_datetime` formatieren.

#### R-29 — `deployment_steps`: Erwartung und Fehlerfang kollidieren
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §5.3, §4.4
- **Fundstellen:** `DS:228-233` (`raise AssertionError("Handshake unexpectedly succeeded")` im selben `try`, das `except Exception` fängt; besteht, wenn der erwartete Fehltext zufällig im eigenen Meldungstext steht), `DS:167-169` (`no signal is sent` ist `sleep(0.05)`)
- **Korrekturauftrag:** `try/except` auf `MCPRPCError`/`MCPClientError` eingrenzen, `AssertionError` außerhalb des `try` auslösen.

#### R-30 — Usability-Harness: Defaults im Fixture-Builder und private Mock-Zugriffe
- **Schweregrad:** MITTEL · **Bereich:** STEP (Usability) · **Referenz:** §13.2 P3, §5.3
- **Fundstellen:** `usability/runner/fixtures.py:180-181` (`sender`/`recipient` ⇒ Inhaber), `115` (Tag ohne Uhrzeit ⇒ 08:00:00Z), `134-139,229` (Body/Anrede aus Absendername abgeleitet), `274-275` (`state._uid_counters`), `300` (nackte `assert`)
- **Nachweis:** Die Features führen das Postfach als „ganze“ Tabelle; fehlende Spalten erzeugen unsichtbare Werte, auf denen Rubrik-Kriterien (Fristen, Anreden) beruhen können.
- **Korrekturauftrag:** Pflichtspalten; `assert` ⇒ `AssertionError`; öffentliche `GmailState`-API für UID-Seeding ergänzen.

#### R-31 — `AI`-Mehrinstanzen-Steps: Thread-Join ohne Lebendigkeitsprüfung, fixer Caller
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §4.4, §13.2 P3
- **Fundstellen:** `AI:196-200` (`thread.join(timeout=60)` ohne `is_alive()`; hängende Threads bleiben unbemerkt), `AI:77` (fester Caller `invoice-agent`), `AI:73` (TEST_MODE)
- **Korrekturauftrag:** Nach `join` `is_alive()` ⇒ `AssertionError`; Caller aus dem Szenario.

#### R-32 — Hilfsmodule tragen Fachwerte
- **Schweregrad:** MITTEL · **Bereich:** STEP · **Referenz:** §13.2 P3, §1.3
- **Fundstellen:** `OS:43,132` (`test@scaratec.com`, `valid-mock-refresh-token`), `OS:41-99` (Host/Port/User fix, Spalten des Szenarios ignoriert), `PS:4079,4114,4176`, `GS:1241` (Passwörter), `AS:5748-5768` (Mail-Skelett)
- **Nachweis:** Zugangs-/Infrastrukturwerte sind nach §13.2 P3 ausgenommen, die Mail-Skelette (R-03) und Token-Texte nicht, soweit Assertions sie lesen.
- **Korrekturauftrag:** Mit R-03 zusammenführen.

### NIEDRIG

- **R-33 — Nackte `assert` (unter `python -O` wirkungslos):** `IF:464`, `GS:846-848`, `OS:164,206`, `usability/runner/fixtures.py:300`, `usability/runner/grading.py:95` ⇒ `raise AssertionError`. (Baseline N-01.)
- **R-34 — Debug-Ausgaben:** `AS:1490-1491` (`DEBUG MISMATCH` nach stderr), `GS:666-673` (`WAL DEBUG` + direkter `sqlite3`-Zugriff beim Fehlerpfad), `MS:566` (`print("DEBUG: …")`) entfernen oder in die `AssertionError`-Meldung verschieben.
- **R-35 — Registry-Pfad relativ zum Arbeitsverzeichnis:** `AS:6189,6202,6213,6250` (`Path("docs/contract/registry.yaml")`) ⇒ `context.bdd_root.parent / "docs" / …`; sonst nur aus dem Repo-Root lauffähig.
- **R-36 — Einzelne `except Exception: pass`/Toleranzen:** `CS:419-420` (maskiert Harness-Fehler im Polling), `CS:707-708`, `ENV:211`, `PX:317-318`, `PX:241-242` (Konfigurationsfehler ⇒ stilles Fortfahren mit alter Config) ⇒ Fehler loggen oder `AssertionError`.
- **R-37 — Typblinde Vergleiche:** `AS:1368,1463` (`str(rec.get(k)) == v`: `None`↔`"None"`, `True`↔`"True"`), `AS:3684-3700` (Substring statt Muster), `AS:758-774` (`re.match` statt vollständiger Muster).
- **R-38 — `GS:531-561` „direct IMAP SEARCH on … X-GM-MSGID“ liest `GmailState` statt der IMAP-Leitung** (Name ≠ Kanal): umbenennen oder über `_harness_conn` per `UID SEARCH X-GM-MSGID` laufen lassen.
- **R-39 — Doppelter mUTF-7-Encoder und Aliase** (`IF:27-60`, `ProxyEncoder PX:57-80`, `usability/runner/fixtures.py:55-73`): drei Kopien; Zugriff auf `GmailState._uid_maps` (`GS:359-364,416-418`, Baseline N-06/N-10).
- **R-40 — Harness-Unterscheidung über LOGIN-Namen** (`GS:1091,1313`): bricht, sobald ein Account `user: test` verwendet (Baseline N-15).
- **R-41 — `PX:320-325` Modus `close` ohne Log-Eintrag; `PX:244-253` Log-Fehler nur als Warnung** (Baseline N-11).

---

## 4. Statistik der neuen/verbliebenen Befunde

| Schweregrad | Anzahl | IDs |
|---|---|---|
| BLOCKER | 0 | — |
| HOCH | 8 | R-01…R-08 |
| MITTEL | 24 | R-09…R-32 |
| NIEDRIG | 9 | R-33…R-41 |

Verteilung nach Prüfmuster (Mehrfachzuordnung): P1 Persistence 5 · P2 Herkunft 3 · P3 Daten-Symmetrie 9 · §1.3/§5.1 Glue-Logik 9 · §4.1 Zirkelschluss 3 · §4.4/§5.3 Assertions/Sleeps 8 · §7.2 Mock-Ehrlichkeit 5 · §6.1 Isolation 2.

---

## 5. Prüfumfang, Methode, Einschränkungen (Ehrlichkeit über die Abdeckung)

**Gelesen (vollständig, zeilenweise):**
- `bdd/features/environment.py`
- `bdd/support/`: `imap_fixture.py`, `mcp_client.py`, `imap_proxy.py`, `policy_builder.py`, `trace_collector.py`, `audit_reader.py`, `wal_reader.py`, `server_bootstrap.py`
- `bdd/features/steps/`: `assertion_steps.py` (6266 Z.), `policy_steps.py` (5204 Z.), `mcp_steps.py` (2487 Z.), `gmail_steps.py` (1700 Z.), `connection_steps.py` (784), `deployment_steps.py` (620), `trace_steps.py` (616), `oauth_steps.py` (345), `audit_instance_steps.py` (386), `imap_utf7_steps.py` (253), `mime_witness_steps.py` (168)
- Mocks: `mock-gmail/src/mock_gmail/server.py`, `state.py`, `__main__.py`; `mock-oauth/src/mock_oauth/server.py`, `client.py`, `tests/test_validation.py`; Anfang von `mock-gmail/tests/test_mock_gmail.py` (Z. 1-120)
- Usability: `runner/fixtures.py`, `serverconf.py`, `server.py`, `mock.py`, `grading.py`, `run.py` (Z. 1-260)
- Features (gegen Steps abgeglichen, 18 von 64): `policy/policy_reload`, `tool_surface/reason_code_contract`, `audit/audit_log_format`, `audit/audit_retention`, `tool_surface/bulk_mark_tagged`, `tool_surface/bulk_mark_seen`, `auth/secret_store_backends`, `auth/oauth2_bootstrap`, `transactions/saga_crash_recovery`, `transactions/draft_revision_recovery`, `transactions/intra_account_move`, `tool_surface/mcp_tool_discovery`, `tool_surface/non_goal_rejection`, `tool_surface/folder_path_contract`, `tool_surface/fetch_attachment_content`, `providers/gmail_label_semantics`, `tool_surface/update_draft_errors` (Z. 120-195), `providers/imap_mailbox_names_utf8` (Ende)

**Nicht gelesen:** `mock-gmail/src/mock_gmail/search.py`, `mime.py`, restliche `mock-gmail/tests`; `mock-oauth` (`__init__`, `config`, `README`, `pyproject`); Usability: `runner/{criteria,measurements,judge,evidence,features,feedback,simulator,matrix,report,pdf,cli,__main__}.py`, `runner/harness/*`, `usability/features/*.feature`, `usability/tests/*`, Rest von `run.py`; **46 von 64 BDD-Features** (u. a. `connection_pool*`, `connection_reuse`, `search_*`, `list_messages`, `visibility_levels`, `update_draft`, `create_*_draft`, `inline_attachment_detection`, `fetch_attachment_sink`, `gmail_search_and_counts`, `gmail_draft_lifecycle`, `localized_gmail_resolution`, `sender_rule_matcher`, `write_*`, `non_interference…`, `provenance…`, `startup_saga_recovery`, `health_probe`, `projected_config_reload`, `audit_multi_instance`, `caller_authentication`).

**Folgen für die Aussagekraft:**
- 14 Altbefunde stehen auf **UNGEPRÜFT**, weil das zugehörige Feature nicht gelesen wurde; der Step-Code dazu ist unverändert (soweit zutreffend, ist das in der Tabelle vermerkt).
- „Tot/ungenutzt“ in R-09 bezieht sich auf die 18 gelesenen Features. Für die übrigen kann die Nutzung eines alten Steps nicht ausgeschlossen werden; die Korrekturanweisung („löschen oder echt implementieren“) bleibt in beiden Fällen gleich.
- Es standen weder Suche (`grep`) noch Ausführung zur Verfügung: alle Fundstellen stammen aus dem Lesen, nichts wurde gelaufen. R-07 ist deshalb **statisch belegt**, nicht empirisch.
- `bdd/docker/**` und `docs/**` waren nicht Prüfgegenstand (außer `docs/contract/registry.yaml`-Bezug über `AS:6184-6266`).

---

## 6. Priorisierte Korrekturaufträge

**An `code-entwickler-flash` (Freigabe-relevant, HOCH):**
1. R-01 Debug-Log entfernen (`MS:2249-2258`).
2. R-05 Crash-Verifikation in den beiden `expecting crash`-Steps.
3. R-02 `resolve_uid` mit `AssertionError` statt `get(key, uid)`.
4. R-03 Pflichtspalten im Seeding, Defaults streichen, exakte Größen.
5. R-04 `is empty`/`no message with uid` als echte Assertions oder Feature-Zeile streichen.
6. R-08 Positivkontrolle in Absenz-Checks, `folder_uids` bei SELECT-Fehler werfen.
7. R-06 mock-oauth: Refresh-Token-Verwaltung, Fallback `server.py:281-291` entfernen.
8. R-07 Usability-Runner: Passwort des mock-gmail setzen, Regressionstest.

**An den Hauptagenten (Gherkin):** R-11 (Ketten-Formel in den Feature-Kopf), R-17 (volle Zeitstempel in `server clock is pinned`, explizite Policy-Tabellen statt Konventionen), R-25 (Aktionen vom `Then` ins `When`, Identity-Immutable-Szenario auf den tatsächlichen HTTP-Vertrag), R-03 (Pflichtspalten in den Seeding-Tabellen aller Features, die sie bisher weglassen — bei Wegfall des Defaults werden Features mit fehlenden Spalten hart scheitern; vor der Umstellung Features nach Spalten durchsehen).

**Gesamturteil:** **NACHARBEIT ERFORDERLICH** (0 BLOCKER, 8 HOCH).

*Ende des Berichts. Relevanz und Korrekturbedarf jedes Befunds entscheidet gemäß §13.3/§13.4 der Mensch.*
