# Abschlussprüfung Runde 3 — Gherkin- & Python-Step-Reviewer (Claude Sonnet 5.5)

- **Datum:** 2026-11-18
- **Maßstab:** `BDD_GUIDELINES_v1.8.0_DE.md` (§13.3 Rollentrennung)
- **Bezug:** `docs/reviews/2026-11-18-step-abnahme-sonnet-runde2.md` (S-01…S-08, R-03′, R-10′, R-16′/R-17′, R-26) und `docs/reviews/2026-11-18-step-abnahme-sonnet-restfeatures.md` (T-01…T-09). Zusammen waren das **20 offene MITTEL-Auflagen**.
- **Grenzen eingehalten:** Kein Zugriff auf `server/` oder `secrets/`. Keine Änderung an `.py`/`.yaml`/`.feature`. Einzige Ausgabe ist dieser Bericht.
- **Methode:** Nur Lesen, kein `grep`, kein Testlauf. Jede Aussage stützt sich auf gelesene Zeilen. Was ich nicht gelesen habe, steht in Abschnitt 4. Ob ein Step im Lauf tatsächlich grün oder rot wird, ist nicht empirisch geprüft.
- Kürzel wie in Runde 2 (`AS` = `assertion_steps.py`, `PS` = `policy_steps.py`, `MS` = `mcp_steps.py`, `CS` = `connection_steps.py`, `GS` = `gmail_steps.py`, `DS` = `deployment_steps.py`, `AI` = `audit_instance_steps.py`, `TS` = `trace_steps.py`, `IF` = `imap_fixture.py`). Zusätzlich: `UC` = `usability/runner/criteria.py`, `UM` = `…/measurements.py`, `UE` = `…/evidence.py`, `UF` = `…/features.py`, `US` = `…/serverconf.py`, `UR` = `…/run.py`.

---

## 0. Ergebnis in Kürze

| Schweregrad | Anzahl | Inhalt |
|---|---|---|
| BLOCKER | 0 | — |
| HOCH | 0 | — |
| **MITTEL** | **2** | **R-10′-Rest** (`UR:71-75`), **T-09-Rest** (`UF:147`, `US:38-80`) |
| NIEDRIG | 13 neue (N3-01…N3-13) | Abschnitt 3.1, dazu der unveränderte Altbestand aus den Vorberichten (3.2) |

**18 von 20 MITTEL-Auflagen sind erledigt.** Bei R-03′ und R-16′/R-17′ blieben nur NIEDRIG-Reste, die ich unten einzeln begründe. Zwei MITTEL sind nicht erledigt.

**Gesamturteil: FREIGEGEBEN (mit Auflagen).** Die Bedingung „ohne Auflagen“ verlangt 0 BLOCKER, 0 HOCH **und** 0 MITTEL. Sie ist wegen der zwei Restpunkte aus Abschnitt 2 nicht erfüllt.

---

## 1. Statusübersicht der 20 Auflagen

Legende: **B** = behoben, **B\*** = behoben mit NIEDRIG-Rest (Abschnitt 3), **T** = teilweise, bleibt MITTEL.

| # | ID | Status | Nachweis |
|---|---|---|---|
| 1 | S-01 / T-01 | **B** | `state.py:461-479` hat `remove_message_from_folder`: Trash/Spam → `delete_message`; All Mail → Flags und Labels löschen, dann `delete_message`; sonst `leave_mailbox`. `server.py:334` und `PS:537-541` (Reset) verwenden sie. Der Test `test_mock_gmail.py:233-333` deckt CLOSE in INBOX und All Mail sowie alle drei Zweige ab. |
| 2 | S-02 | **B\*** | `IF:115-169`: `resolve_uid` ist strikt. Es wirft, außer der Hint steht in `explicit_unseeded_uids` oder der Ordner hat 0 gesäte Nachrichten. `PS:1086-1111` (`step_folder_has_no_uid`) trägt den Marker ein und prüft gegen mock-gmail bzw. Dovecot. `AS:592-612` (`_resolve_uid_hints`), `AS:359-374` (`src_uid`) und `TS:152-181` werfen bei fehlendem Account/Ordner `AssertionError`. Gherkin: `list_attachments.feature:92` und `create_reply_draft.feature:248` haben die sichtbaren Givens „contains no message with uid 9999/99999“. `tracing_payload_mode.feature:84` löst über die streng aufgelöste UID auf. |
| 3 | S-03 | **B\*** | `AI:292-333` wertet `filename` aus, prüft `seq == Index`, den Genesis-Wert (oder die Kette des Vortags) und `prev_hash` der Vorzeile. `startup_saga_recovery.feature:9-11` nennt Kettenregel und Genesis. |
| 4 | S-04 | **B\*** | `AS:3941-3950` (`response field … does NOT contain`) wirft bei fehlendem Feld und bei Nicht-Container. `AS:2368-2389` (`no resource block`): `MS:182` setzt `last_content_blocks`, der Step wirft sonst. `CS:147-173,231-255` verlangen laufenden, verdrahteten Proxy. `CS:328-338` prüft den Proxy für Dovecot-Accounts. `GS:403-425` verlangt eine nicht leere Ordnerliste. Rest: `AS:2965-2987`. |
| 5 | S-05 | **B** | `CS:400-413` entscheidet nach `resolve_account(...)[0] == "mock-gmail"`. Der Proxy-/Dovecot-Zweig ist erreichbar. |
| 6 | S-06 | **B** | `DS:169-180` wirft ohne Prozess und prüft `proc.poll() is None`. Es ist jetzt ein echter Lebenszeichen-Check. |
| 7 | S-07 | **B** | `startup_saga_recovery.feature:76` lautet `And within 10 seconds the WAL entry … retry_count >= 1`. Der Poll-Step steht in `AS:3527-3548` (Deadline, 0,2-s-Takt, `AssertionError` mit letztem Wert). |
| 8 | S-08 | **B** | `response_transparency.feature:5` nennt „list_folders (sorted lexicographically)“. Die Erwartung `["Archiv", "INBOX/Rechnungen"]` (`:59`) folgt daraus. `:31` schreibt `with provider "imap-standard"` aus. Kopfzählung 2+1+6+1+2+4 = 16 stimmt. |
| 9 | R-03′ | **B\*** | `PS:796-951` (`flush_staged_messages`) wirft bei fehlendem `from`/`to`/`subject`/`date`. `GS:137-151` wirft bei fehlender Pflichtspalte. Rest (NIEDRIG, Begründung unten): Body-/Message-ID-Defaults und die Seeding-Nebenwege. |
| 10 | R-10′ (inkl. R-15) | **T** | Behoben: `MS:119-134` und `MS:647-663` binden TEST_MODE an `is_test_hooks`/`without_test_mode`. Ebenso `PS:1056-1063`, `PS:1884-1900`, `PS:2409-2417`, `PS:3179-3182` und `DS:151-152`. `UR:182-189` setzt TEST_MODE nur noch mit Fake-Uhr und dokumentiert die Ausnahme (ADR 0058/0060). `usability/runner/server.py:57-67` liest Version und Tool-Set über `tool_surface_info`, nicht mehr über die Server-venv. **Offen: `UR:71-75`** (siehe 2.1). |
| 11 | R-16′ / R-17′ | **B\*** | R-17′ behoben: `PS:171-172` wirft bei fehlender `policy`-Spalte. Die `audit`-Steps (`PS:1126-1158`) haben keine Konventions-Caller mehr. `PS:142-145` lehnt Datums-Varianten ab. R-16′ als NIEDRIG: `PS:55-71` registriert nur Accounts, die das Harness-Mapping kennt, und `_start_imap_proxy` wirft bei nicht deklarierten Accounts. |
| 12 | R-26 | **B** | `PS:518-546` setzt Nachrichten und Ordner immer zurück und sät dann neu. Der neue Step `additional messages are seeded into folder` (`PS:549-565`) wird gebraucht und verwendet: `non_interference_and_field_gates.feature:40`, `tracing_spans.feature:104`. Die Resets in `tracing_spans.feature:93` und `:104` haben jeweils die vollständige Tabelle bzw. den Zusatz-Step. |
| 13 | T-02 | **B** | `whitelist_folder_mode.feature:88` lautet `And the startup error indicates "YAML"`. Der Step steht in `AS:3023-3034`. |
| 14 | T-03 | **B** | `visibility_levels.feature:109-122` ist das Positivszenario für `fetch_headers` (Header `X-Hornbach-Order: 4711`, `redacted_fields equals ["body", "attachments"]`, Negativprobe auf den Body). Der Step „contains key … equal to …“ steht in `AS:130-155`. Kopfzählung 6+5+1 = 12 stimmt und ist in `:10-13` angepasst. |
| 15 | T-04 | **B\*** | `UC:66-80` (`_one_draft` mit `to` und Schlüsselwörtern), `UC:125-206` (E1-E3 mit `MARA`/`JONAS` und Schlüsselwörtern), `UC:252-269` (R1 vergleicht Body-Längen), Test `test_mid_findings_t04_t09.py:23-102`. |
| 16 | T-05 | **B\*** | `UF:152-155,274-287` (`_IDEAL_MULTI`, Ablehnung mehrerer Zahlen ohne Variantenzuordnung), `UF:119-127`, `UM:52-53`. Test `:105-121` (`today` = 3, `fixed`/`format-hint` = 2). |
| 17 | T-06 | **B** | `UE:134-146` zählt nur MCP-Tools mit `phase in (None, "single", "outcome")`. `UM:183-184` (`_ok`) verlangt `decision == "ALLOW"` **und** `result == "OK"`. Test `:124-145`. |
| 18 | T-07 | **B\*** | `UM:409-433` erkennt „leer + `policy_may_hide`/`window_and_policy_limited`“ zusätzlich zu DENY. `UC:503-517` und `UC:520-539` ebenso. Test `:148-159`. |
| 19 | T-08 | **B** | `UE:59-63` erkennt nur Mailbibliotheken/-programme und Netzwerkaufrufe auf localhost. Test `:162-181` belegt `grep IMAP` und eine Beispieladresse als unauffällig. |
| 20 | T-09 | **T** | `UF:211-220` lehnt unbekannte Tabellenspalten mit `FeatureError` ab. `US:38-80` gleicht die Variantenpolicy mit der Prosa ab, aber nur teilweise (siehe 2.2). |

---

## 2. Verbleibende MITTEL-Befunde

### 2.1 R-10′-Rest — Fake-Uhr des Usability-Laufs: erfundene Uhrzeit und ungültiger Zeitstempel
- **Bereich:** STEP (Usability) · **Referenz:** §13.2 P3, §1.1 · **Fundstelle:** `usability/runner/run.py:71-75` (`_fake_now`), Quelle `usability/features/03_invoice_search.feature:21`, `04_…:18`, `05_…:20` („the server clock is pinned to "2026-11-02T12:00:00Z"“).
- **Nachweis:** `_fake_now` hängt `T09:00:00Z` an `spec.server_date`, sobald die Matrix-Variable nicht mit `spec.server_date` beginnt. `UF:156` liest die Uhr der Features aber als vollständigen Zeitstempel. Für die Features 03-05 ergibt der Zweig also `2026-11-02T12:00:00ZT09:00:00Z`. Das ist ein ungültiger Wert, den der Server als Startfehler oder gar nicht annimmt. Zusätzlich erfindet der Code eine Uhrzeit, die im Feature nicht steht. Bei gleich lautender Matrix-Konfiguration fällt es nicht auf, deshalb ist der Defekt latent.
- **Korrekturauftrag (`code-entwickler-flash`):** Den Zeitstempel des Features unverändert verwenden. Weicht er von der Matrix-Vorgabe ab, entscheidet das Feature (§1.1), oder der Runner bricht mit `FeatureError` ab. Kein Anhängen einer Uhrzeit. Ein Test in `usability/tests` mit Feature-Uhr „…T12:00:00Z“ und abweichender Matrix-Uhr.

### 2.2 T-09-Rest — „variant policy …“-Prosa nur teilweise an die Variantenpolicy gebunden
- **Bereich:** GHERKIN+STEP (Usability) · **Referenz:** §1.1, §13.2 P3 · **Fundstellen:** `usability/runner/serverconf.py:38-80` (`_verify_variant_policy_description`), `usability/runner/features.py:147` (`variant policy.*` nimmt beliebigen Text), `usability/features/02_draft_revision_and_discard.feature:22`, `03_invoice_search.feature:40`, Test `usability/tests/test_mid_findings_t04_t09.py:196-222`.
- **Nachweis:** Der Abgleich sucht feste Teilstrings im kleingeschriebenen Prosa-Text und ignoriert alle anderen Aussagen ohne Fehler:
  - `01:25` (`draft_append on "[Gmail]/Drafts"`) und `04:28` (`move_out on "INBOX"`, `accept_incoming on "INBOX/Rechnungseingänge"`) sind gebunden.
  - `02:22` schreibt `FULL + draft_append + draft_edit + move_out on "[Gmail]/Drafts" and accept_incoming on "[Gmail]/Trash"`. Dort passt kein Teilstring auf die `draft_append`-/`move_out`-/`accept_incoming`-Prüfung. Nur `draft_edit` wird verglichen (`US:47-58`), `move_out` und Trash-`accept_incoming` bleiben ungeprüft.
  - `03:40` („blacklist FULL on "INBOX", "INBOX/Rechnungseingänge", and "[Gmail]/Sent Mail"“) wird gar nicht geprüft, auch `mode` und `default` nicht.
  - Der Test nutzt eine erfundene Beschreibung („variant policy granting FULL and draft_append on "[Gmail]/Drafts"“), die nicht dem Wortlaut der Features 02 und 03 entspricht. Der Test belegt deshalb nur die beiden Fälle, die ohnehin stimmen.
  - Weicht die Matrix-YAML von der Prosa ab, merkt es der Runner in diesen Fällen weiter nicht. Das war der Kern von T-09.
- **Korrekturauftrag:** Hauptagent: Policy-Beschreibung in 02 und 03 (wie in 05) als Tabelle schreiben, nicht als Prosa. `code-entwickler-flash`: Tabelle mit der Variantenpolicy vergleichen (Ordner, `mode`, `default`, Capabilities), bei jeder Abweichung `ValueError`, unbekannte Prosa-Aussagen ablehnen statt ignorieren. Test gegen die echten Feature-Texte (`FEATURES` aus `samples`), nicht gegen erfundene.

---

## 3. NIEDRIG-Befunde (kein Hindernis für die Freigabe)

Ich begründe die Herabstufung jeweils. Die wichtigste Regel: ein Rest ist NIEDRIG, wenn er nur laut scheitert (falsch-rot) oder von keinem gelesenen Szenario behauptet wird.

### 3.1 Reste aus den 20 Auflagen

| ID | Fundstelle | Befund und Begründung |
|---|---|---|
| N3-01 | `IF:115-169`, `MS:295-298`, `PS:2434`, `CS:208`, `AS:558-560` | `allow_unseeded_probe` wird weiter übergeben, im Funktionsrumpf aber ignoriert. Irreführend, ohne Wirkung. Zusätzlich lässt `resolve_uid` bei Ordnern ohne gesäte Nachrichten den Hint durch. Das betrifft nur Ordner ohne Seed, dort ist kein Mapping möglich. Aufräumen. |
| N3-02 | `AS:2965-2987` | `audit file does NOT contain …` besteht, wenn das Audit-Verzeichnis existiert, aber leer ist (kein `*.jsonl`, keine Dateien). Ist mindestens eine Datei da, wirft der Step bei leerem Text. Rest: leeres Verzeichnis ist ein Pass. Die Szenarien, die ich gelesen habe (z. B. `blacklist_folder_mode.feature:134-144`), haben davor eine Positivkontrolle im Gherkin. Positivkontrolle im Step ergänzen. |
| N3-03 | `AS:2835-2859` | `caller sink subdirectory … contains 0 files` und `sink base directory … contains 0 files` bestehen bei fehlendem Verzeichnis. Das ist bei „nichts gespeichert“ fachlich plausibel, aber ohne Beleg, dass der Sink erreichbar war. |
| N3-04 | `AS:3431-3457`, `AS:3040-3065`, `AS:3079-3108` | `offline chain verifier reports …` rechnet die Kette selbst nach und heißt trotzdem „offline chain verifier“. Die Regel steht jetzt im Feature-Kopf (§4.1 erfüllt). Namens- und Serialisierungsunterschiede (`separators` bei `:3054` vs. Archiv `:3096`) bleiben. |
| N3-05 | `PS:843,851,987-1023,2566-2654`, `PS:3059-3066,3099-3110`, `GS:162` | Seeding-Nebenwege erzeugen weiter Fachwerte: Body `Body for {subject}`, Message-ID `<scenario-{uid}@bdd.local>`, Wiederholungsbody mit Absender `sender@example.com`, Betreff `Message {uid}`, Datum `2026-10-30T07:00:00Z`, Bulk-Betreff `Bulk message n`. Dazu: gefälschte Eingangsmail mit Absender `attacker@external.example` und fest verdrahtete Betreffs „Harmlose Anfrage“/„Nicht aus INBOX verwerfbar“ im Step-Code. In keinem Szenario, das ich gelesen habe, wird einer dieser Werte als Erwartungswert behauptet. Fehlt ein Betreff, scheitert der Step laut (`AssertionError`), nicht still. Offen bleibt: der versteckte Absender des Wiederholungsbodys könnte bei sender-bezogenen Policies die Sichtbarkeit beeinflussen (`tracing_payload_mode.feature:91`). Das habe ich nicht gegengeprüft. Spalten verlangen statt Defaults. |
| N3-06 | `PS:86,98`, `PS:62`, `PS:175` | `step_secret_store_holds_password` registriert einen Account bei Bedarf selbst (Passwort `test`, Host/Port aus dem Harness-Mapping). Fehlt die Spalte `auth_type`, wird `stdio_trusted` angenommen (im Docstring `PS:159-166` steht noch die alte Aussage, dass `policy` abgeleitet werde). Beides sind Infrastrukturwerte, die laut §13.2 P3 ausgenommen sind. |
| N3-07 | `MS:923,1906` | Dort löst jede `IMAP_MCP_*`-Variable TEST_MODE aus, auch reine Betreiber-Variablen wie `IMAP_MCP_TRACE_PAYLOADS` oder `IMAP_MCP_POOL_*`. In `_ensure_mcp_client` (`MS:120-129`) und `MS:650-659` ist die Liste enger. Vereinheitlichen. |
| N3-08 | `UC:261-262` | R1: Ist der erste Entwurf-Body nicht ermittelbar, gilt `shorter = True`. Das sollte „nicht bewertbar“ sein. Die Beweisquellen (Span, Transkript, Mailbox) decken den Normalfall. |
| N3-09 | `UC:514-517` | `_empty_or_denied` liefert am Ende für **jede** leere Liste `True`. Damit ist die Bedingung `completeness == policy_may_hide` im Zweig davor wirkungslos, und `UM:409-421` ist strenger als `UC`. Im Feature P1 ist die Policy ein Whitelist-Ordner, dort ist es egal. Vereinheitlichen. |
| N3-10 | `UM:424-433` | `_after_first_deny` koppelt `audit[i]` und `spans[i]` über den Index. Fehlt ein Span oder ist die Reihenfolge anders, verrutscht die Zuordnung. |
| N3-11 | `UF:119-127` | `get_ideal_calls` gibt für eine unbekannte Variante still den `draft_edit`-Wert zurück. `_validate` verlangt weiter keinen `ideal_calls` (N2-13). |
| N3-12 | `usability/runner/server.py:64` | `except Exception: tool_set = None` schluckt Fehler beim Lesen von `tool_surface_info`. Danach greift der Fallback `server_info`. |
| N3-13 | `GS:205-206` | Doppelte `raise`-Zeile (tote Zeile). Außerdem `GS:162` Body `Body for {subject}` (siehe N3-05). |

### 3.2 Altbestand, in dieser Runde nicht erneut geprüft oder unverändert

- **R-09-Reste:** `AS:342` (`getattr(context, "message_uids", {})` ohne Wirkung). Nicht neu gelesen: `MS:1273-1286` (`_update_uid_after_rewrite`), `PS:2284` (`_ = os`), `MS:1025-1044`.
- **Mock-gmail:** `_cmd_expunge`/`_uid_expunge` für All Mail (`server.py:355-373`, `684-710`) löschen die Nachricht weiter nicht, `CLOSE` jetzt schon. Diese Abweichung habe ich nicht erneut gelesen.
- **PS:** `step_wal_async_move_status` (`PS:2488-2507`) fällt weiter auf „irgendeine WAL-Zeile“ zurück, wenn `last_response` keine `tx_id` hat.
- **N2-01…N2-13, F-01…F-05, S-10…S-16** aus den Vorberichten bleiben offen, siehe dort.

---

## 4. Nicht gelesen oder nicht geprüft

- **Server-Seite:** `server/` und `secrets/` sind gesperrt und blieben ungelesen. Alle Aussagen zum Servicevertrag stützen sich auf Features und ADR-Zitate in den Features.
- **Dateien, die ich in dieser Runde nur teilweise gelesen habe:** `AS` Zeilen 4001-Ende, `CS` Zeilen 1-119 und 450-Ende, `GS` Zeilen 1-99, 250-379 und 500-Ende, `DS` Zeilen 1-119 und 240-Ende, `MS` die nicht genannten Bereiche, `PS` Zeilen 3189-Ende, `TS` Zeilen 1-129 und 220-Ende, `IF` Zeilen 1-94 und 225-Ende, `usability/runner/run.py` Zeilen 1-49 und 200-Ende, `usability/runner/matrix.py` (nie gelesen, daher kenne ich die Matrix-Vorgabe für `IMAP_MCP_FAKE_NOW_UTC` nicht).
- **Ausführung:** Kein Lauf von `behave` oder `pytest`. Ob die Tests aus `test_mid_findings_t04_t09.py` und `test_mock_gmail.py` grün sind, weiß ich nicht. Ihr Inhalt belegt nur, dass die Prüfungen vorgesehen sind.
- **Abdeckung der Features:** Die in Teil 2b vollständig gelesenen Features habe ich in dieser Runde nur an den Stellen erneut gelesen, die zu den 20 Auflagen gehören (Abschnitt 1).
- **Strikte `resolve_uid`:** Szenarien, die eine ungesäte UID in einem Ordner mit gesäten Nachrichten ansprechen, brauchen jetzt das Given „contains no message with uid N“. Ich habe nur die zwei genannten Fälle gegengeprüft. Ein weiteres Szenario ohne dieses Given würde laut scheitern (falsch-rot), nicht still bestehen.

---

## 5. Gesamturteil

# FREIGEGEBEN (mit Auflagen)

**0 BLOCKER · 0 HOCH · 2 MITTEL.** Die Freigabe ohne Auflagen setzt 0 MITTEL voraus. Die zwei offenen Punkte sind klein und gut abgegrenzt.

**Auflagen an `code-entwickler-flash`:**
1. `usability/runner/run.py:71-75` — Zeitstempel des Features verwenden, keine Uhrzeit erfinden, kein Anhängen an einen vollständigen Zeitstempel (2.1).
2. `usability/runner/serverconf.py:38-80` und `usability/runner/features.py:147` — Variantenpolicy vollständig gegen die Beschreibung abgleichen, unbekannte Aussagen ablehnen, Test gegen die echten Feature-Texte (2.2).

**Auflage an den Hauptagenten (Gherkin):**
3. `usability/features/02_draft_revision_and_discard.feature:22` und `03_invoice_search.feature:40` — Variantenpolicy als Tabelle statt Prosa formulieren (2.2).

**Optional (NIEDRIG):** N3-01…N3-13 und die Altbestände aus 3.2. Besonders sinnvoll: N3-02 (Positivkontrolle im Step), N3-05 (Pflichtspalten statt Defaults) und N3-09 (einheitliche Leer-/Deny-Erkennung).

Sobald die Punkte 1 bis 3 erledigt sind, lautet das Urteil nach erneuter Sichtprüfung dieser drei Stellen **FREIGEGEBEN ohne Auflagen**.
