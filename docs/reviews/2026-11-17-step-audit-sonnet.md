# Step-Audit `bdd/` — Prüfbericht (Claude Sonnet 5.5)

- **Datum:** 2026-11-17
- **Rolle:** unabhängiger Python-Step-Prüfer (BDD-Guidelines v1.8.0, §13.3)
- **Maßstab:** `/home/randy/Projekte/privat/burn-your-code/BDD_GUIDELINES_v1.8.0_DE.md` (vollständig gelesen)
- **Grenzen eingehalten:** Kein Zugriff auf `server/`. Keine Änderung an `.py`- oder `.feature`-Dateien. Einzige Ausgabe: dieser Bericht.

---

## 0. Umfang, Methode, Abkürzungen

### Geprüfte Dateien (vollständig gelesen)

| Gruppe | Dateien | Zeilen |
|---|---|---|
| Hooks | `bdd/features/environment.py` | 269 |
| Step-Module | `assertion_steps.py` (4113), `policy_steps.py` (3880), `mcp_steps.py` (2081), `gmail_steps.py` (1414), `trace_steps.py` (543), `oauth_steps.py` (312), `audit_instance_steps.py` (286), `connection_steps.py` (248), `imap_utf7_steps.py` (233), `mime_witness_steps.py` (148) | ≈ 13 260 |
| Support | `bdd/support/imap_fixture.py`, `mcp_client.py`, `imap_proxy.py`, `policy_builder.py`, `trace_collector.py`, `audit_reader.py`, `wal_reader.py`, `server_bootstrap.py` | ≈ 2 500 |
| Mock | `bdd/mock-gmail/src/mock_gmail/{state,server,search,mime,__main__,__init__}.py` (≈ 1 700 Z.), `bdd/mock-oauth/**` (`client.py`, `__init__.py`, `tests/test_validation.py`, `config/oauth-mock.yaml`, `README.md`, `pyproject.toml`), `bdd/support/__init__.py` — `state.py` in Teil 1, der Rest in der Ergänzungsprüfung (Abschnitt 9) | ≈ 2 000 |

### Gegengelesene Feature-Dateien (68 von 68: 37 in den Abschnitten 3–8, die übrigen 31 in Abschnitt 9)

> **Korrektur zur ersten Fassung:** Dort war von „62“ Feature-Dateien die Rede; tatsächlich liegen 68 unter `bdd/features/**` (31 `tool_surface`, 11 `policy`, 6 `providers`, 6 `transactions`, 4 `auth`, 3 `audit`, 3 `performance`, 2 `deployment`, 2 `observability`). Offen waren daher nicht 20, sondern 31 Dateien. Sie sind jetzt alle gelesen.

`audit/audit_log_format`, `audit/audit_retention`, `audit/audit_multi_instance`, `policy/policy_reload`, `policy/write_capabilities`, `auth/oauth2_bootstrap`, `auth/caller_authentication`, `auth/secret_store_backends`, `auth/explicit_imap_user`, `tool_surface/mcp_tool_discovery`, `…/tool_surface_info`, `…/non_goal_rejection`, `…/reason_code_contract`, `…/folder_path_contract`, `…/search_blacklist_fast_path`, `…/fetch_attachment_content`, `…/fetch_attachment_sink`, `…/bulk_mark_seen`, `…/bulk_mark_tagged`, `…/add_attachment`, `…/replace_attachment`, `…/delete_attachment`, `…/update_draft`, `…/discard_draft`, `…/create_reply_draft`, `…/list_folders_message_count`, `transactions/saga_crash_recovery`, `…/startup_saga_recovery`, `…/cross_account_move_saga`, `…/intra_account_move`, `…/message_rewrite_recovery`, `…/draft_revision_recovery`, `providers/gmail_label_semantics`, `providers/imap_mailbox_names_utf8`, `observability/tracing_payload_mode`, `deployment/health_probe`, `deployment/projected_config_reload`.

Die übrigen 31 Feature-Dateien (u. a. `tracing_spans`, `connection_pool*`, restliche `policy/*`, `providers/*`, `tool_surface/*`) sind einzeln in **Abschnitt 9** abgehandelt; dort steht je Datei entweder ein Befund oder die ausdrückliche Bestätigung der Befundfreiheit.

### Abkürzungen für Fundstellen

`AS` = `bdd/features/steps/assertion_steps.py`, `PS` = `…/policy_steps.py`, `MS` = `…/mcp_steps.py`, `GS` = `…/gmail_steps.py`, `OS` = `…/oauth_steps.py`, `IU` = `…/imap_utf7_steps.py`, `AI` = `…/audit_instance_steps.py`, `TS` = `…/trace_steps.py`, `IF` = `bdd/support/imap_fixture.py`, `MC` = `bdd/support/mcp_client.py`, `AR` = `bdd/support/audit_reader.py`, `PB` = `bdd/support/policy_builder.py`, `PX` = `bdd/support/imap_proxy.py`, `ENV` = `bdd/features/environment.py`.
Feature-Pfade sind relativ zu `bdd/features/`.

### Schweregrade

- **BLOCKER** — der Step/das Szenario kann gemäß Code nicht scheitern oder prüft nicht das, was sein Name/Szenario behauptet; die ausgewiesene Spezifikation ist damit **nicht validiert** (§13.1 „Stub mit Statuscode“-Muster).
- **HOCH** — wesentliche Ehrlichkeitslücke gegen §13.2 Prüfung 1/2/3 oder §1.3/§4.1 mit konkretem Szenario-Bezug.
- **MITTEL** — Schwächung, latentes Vakuum oder Regelverstoß ohne aktuell nachgewiesene Fehlannahme.
- **NIEDRIG** — Hygiene, Wartbarkeit, Dokumentation.

---

## 1. Statistik

| Schweregrad | Abschnitte 3–6 (Teil 1) | Abschnitt 9 (Ergänzung) | **Gesamt** |
|---|---|---|---|
| BLOCKER | 5 | 0 | **5** |
| HOCH | 26 | 7 (H-27…H-33) | **33** |
| MITTEL | 19 | 14 (M-20…M-33) | **33** |
| NIEDRIG | 12 | 5 (N-13…N-17) | **17** |
| **Gesamt** | 62 | 26 | **88** |

Geprüft: 68 von 68 Feature-Dateien, 10 Step-Module, `environment.py`, 9 Support-Module, 6 mock-gmail-Module, mock-oauth-Subprojekt.

Verteilung nach Prüfmuster (Mehrfachzuordnung möglich): §13.2 P1 (Persistence) 22 · §13.2 P2 (Herkunft) 13 · §13.2 P3 (Daten-Symmetrie) 16 · §1.3/§5.1 (versteckte Logik/Mutation) 22 · §4.1 (Zirkelschluss) 5 · §4.4/§5.2/§5.3 (Assertions/Sleeps/Struktur) 20 · §7.2 (Mock-Fidelity, neu ausgewiesen) 8.

### Hauptbefunde in einem Satz

1. Fünf Steps/Szenarien sind **strukturell vakuos** (B-01…B-05): Metadaten-Steps prüfen keine Metadaten, ein „Sweep“-Given tut nichts, der „IMAP connections“-Zähler liest den Mock-Gmail-Zähler in Dovecot-Szenarien, Pool-Drain ist ein No-op, die Env-Var-Absenz prüft das Harness-eigene Dict.
2. **Schreibende Tools** `bulk_mark_seen`, `add/replace/delete_attachment` und die Rewrite-Flag-Erhaltung werden **nicht** über einen zweiten Kanal geprüft, obwohl die Feature-Header genau das versprechen (§13.2 P1).
3. **Recovery-Then-Steps lösen selbst Recovery aus** (`_test_run_recovery` in Polling-Schleife) und maskieren damit die Spezifikation „runs once“.
4. **Versteckte Fixture-Daten** (Default-Absender/Empfänger, Stub-Anhänge, Hornbach-Regeln, `invoice-policy`, magische UIDs 505/510/520, Ersatzwerte für `size`/`sha256`) beeinflussen Assertions, ohne im Szenario zu stehen (§13.2 P3).
5. Mehrere „Fehler-Injektions“-Givens bilden das Feature **nicht** ab (OAuth-Fehler und PKCE-Tamper werden im Server-Testmodus statt im Mock-Provider erzeugt; `wrong passphrase` testet fehlenden Secret-Key; `delay` leitet nie weiter).
6. (Ergänzung, Abschnitt 9) Die Mocks sind an mehreren Stellen **nachsichtiger als die echten Systeme** (mock-gmail: beliebiges LOGIN, kein XOAUTH2, implizites Anlegen von Postfächern, kein Zustandsautomat; mock-oauth: reine Stubs ohne die im README versprochene Steuer-API). Zusätzlich hängen mehrere erwartete Werte an unsichtbaren Konstanten (`T12:00:00` bei „server date is pinned“) und an Quell-Bytes, die nie mit dem Ergebnis verglichen werden (Sink-Datei).

---

## 2. Positive Befunde (Muster zur Nachahmung)

- `mime_witness_steps.py` (148 Z.): unabhängiger IMAP-FETCH + stdlib-`email`-Parse, kein Zugriff auf das Tool-Response, Fehler laut (`TS`-ähnliche Disziplin).
- `trace_steps.py` + `trace_collector.py`: echter OTLP-Sink als unabhängiger Kanal, Polling mit Deadline, Negativ-Witness verlangt nicht-leere Span-Menge (`TS:386`).
- `AS:3779-4113` (Draft-Handle-Steps) und `AS:3169-3290` lesen den Zielzustand per eigener `imaplib`-Verbindung; `update_draft.feature`/`discard_draft.feature` erfüllen §13.2 P1 vorbildlich (Header-Occurrence-Count, `exactly one message with headers`, Remember/Differs).
- `PS:104-129` `_assert_special_use_as_declared`: erkennt Harness-Lücken statt still abzuweichen.
- `WALReader` ist read-only (`mode=ro`, `wal_reader.py:56-60`) und unabhängig vom Server-Selbstauskunft-Tool.
- `connection_steps.imap_command_lines` (`connection_steps.py:25-57`): fehlender Kanal wird als Harness-Fehler gemeldet, nie als leeres Log.

---

## 3. BLOCKER

### B-01 — Folder-Taxonomie-Sweep: Given tut nichts, Then-Scan hat nichts zu scannen
- **Schweregrad:** BLOCKER · **Referenz:** §4.3, §13.1, §13.2 P1, §5.1
- **Fundstellen:** `tool_surface/reason_code_contract.feature:190-198` ↔ `AS:3492-3502` (Given), `AS:3505-3530` (When-Scan), `AS:3533-3544` (Then)
- **Nachweis:** Das Given „a sequence of operations exercises every folder-opening tool with valid, hidden, typo'd, and SELECT-failing folder targets“ setzt nur `context.folder_taxonomy_sweep_armed = True` (Docstring: „documentation anchor“). Das Szenario enthält keinen einzigen Tool-Aufruf; `response_history` ist `[]`, `audit_dir` leer. Die Then-Steps prüfen `code in scanned_reasons` auf einer leeren Menge → **kann nie scheitern**. Die Behauptung „ein Regress, der `folder_not_found` wieder einführt, scheitert“ ist unvalidiert.
- **Korrekturauftrag:** (a) Feature: das Szenario in echte `When`-Aufrufe zerlegen (valid / hidden / typo / SELECT-failing je Folder-öffnendes Tool, Scenario Outline), (b) Step `armed` löschen, (c) Scan-Step um Positiv-Kontrolle erweitern („mindestens N Records/Responses gesehen“), sonst `AssertionError`.

### B-02 — „Tool metadata“-Steps prüfen keine Metadaten
- **Schweregrad:** BLOCKER · **Referenz:** §2.1/§2.2, §13.1, §5.1
- **Fundstellen:** `tool_surface/mcp_tool_discovery.feature:79-122` (3 Szenarien) ↔ `AS:858-880`, `AS:883-898`, `AS:901-913`
- **Nachweis:** Die Steps lauten „each read tool's metadata contains "minimum_visibility" matching:“ bzw. „… "required_capability" matching:“. Der Code ruft `describe_policy` (nicht `list_tools`) auf und prüft ausschließlich `tool in data["tool_set_available"]`. Die Tabellenspalten `minimum_visibility` (COUNT/METADATA/…/FULL) und `required_capability` werden gelesen, aber **nie verglichen** (`expected` fließt nur in den `(n/a)`-Zweig; im Write-Step ist `row["required_capability"]` gar nicht referenziert). Der Meta-Step „has no minimum_visibility and no required_capability“ prüft das Gegenteil dessen, was er behauptet (Mitgliedschaft in der Verfügbarkeitsliste, kein Abwesenheits-Check). Ein Server, der alle 26 Mappings vertauscht, besteht alle drei Szenarien.
- **Korrekturauftrag:** Metadaten aus dem `tools/list`-Result (`context.last_tools`, Felder `annotations`/`_meta`/Description – je nach Vertrag im ADR/Feature) lesen und je Zeile `actual == expected` vergleichen; für Meta-Tools Abwesenheit beider Schlüssel prüfen. Wenn der Vertrag die Quelle (`describe_policy` statt `tools/list`) vorgibt, **Feature-Text anpassen** (heute widersprüchlich).

### B-03 — „IMAP server received at most N connections“ liest den Zähler des Mock-Gmail
- **Schweregrad:** BLOCKER · **Referenz:** §13.2 P1/P2, §4.1 (Selbstbestätigung), §5.3
- **Fundstellen:** `tool_surface/bulk_mark_tagged.feature:88-95` (Account `gupta-scaratec`, Dovecot) ↔ `AS:3547-3560`, `MS:1263-1265`, `ENV:97,130-132`
- **Nachweis:** `gmail_state` wird in `before_all` immer gesetzt (`ENV:97`); `step_imap_server_received_at_most` verwendet bei `gmail_state is not None` **ausschließlich** `gmail_state.total_connections` (`AS:3551-3553`), der `else`-Zweig (`getattr(imap_fixture, "total_connections", 0)`) ist toter Code, weil `IMAPFixture` kein solches Attribut besitzt. Der Zähler wird zusätzlich in `before_scenario` zurückgesetzt und im `When` `bulk_mark_tagged` erneut auf 0 gesetzt (`MS:1263-1265`). Da `gupta-scaratec` über Dovecot läuft, bleibt der Zähler **immer 0** → `0 <= 4` ist stets wahr, unabhängig von der tatsächlichen Verbindungsanzahl.
- **Korrekturauftrag:** Step an `connection_steps.step_connections_opened_exactly`-Logik koppeln (Proxy-`connection_log_path`, `capture_witnessed_command_logs` muss die Formulierung „IMAP server received at most“ kennen, `PS:2480-2509`) oder Feature auf das vorhandene Proxy-Vokabular umstellen (`the IMAP connections opened for account "gupta-scaratec" number at most N`). `total_connections=0`-Reset im `When` entfernen (versteckte Zähler-Manipulation, auch `MS:311-313`).

### B-04 — Pool-Drain: Given und Then sind No-ops
- **Schweregrad:** BLOCKER · **Referenz:** §13.1, §13.2 P1, §5.1
- **Fundstellen:** `policy/policy_reload.feature:97, 100` ↔ `PS:2011-2019` (Given), `AS:2338-2350` (Then)
- **Nachweis:** Das Given „the server has 1 open IMAP connection in the pool“ ist `_ = (context, account_id)`; das Then „number of open IMAP connections … becomes 0 within 5 seconds“ ist ebenfalls `_ = (…)` mit Docstring „Trivially satisfied today … always true“. Der in `connection_steps` und `imap_proxy` vorhandene unabhängige Kanal (Proxy-Connection-Log, `PX:474-483`) wird nicht genutzt; die Szenario-Zeile 100 validiert nichts. Hinweis: Zeile 101 (`pool_drain`-Audit) wird dagegen tatsächlich geprüft — aber das ist Server-Selbstauskunft.
- **Korrekturauftrag:** Entweder Proxy vor `gupta-scaratec` schalten (`capture_witnessed_command_logs`), Verbindungsstand über das Proxy-Connection-Log beobachten (Poll mit Timeout) und die „1 open connection“-Vorbedingung dort belegen, oder Feature-Zeilen 97/100 streichen (ADR 0013 „kein Pool in V1“). Ein permanent wahrer Step ist unzulässig.

### B-05 — „no variable for …“ prüft das Harness-eigene Dict statt des Prozesses
- **Schweregrad:** BLOCKER · **Referenz:** §4.1, §13.2 P1, §5.3
- **Fundstellen:** `auth/secret_store_backends.feature:84` ↔ `PS:2310-2322`
- **Nachweis:** Der Step prüft `expected in context.mcp_extra_env or expected in os.environ`. Beides sind **Harness-Variablen**; ein abgelehnter `imap-mcp-oauth-bootstrap`-Prozess läuft in einem Subprozess mit kopierter Umgebung und kann diese Dicts nicht verändern. Der Step ist daher unabhängig vom Systemverhalten immer grün. Zusätzlich dupliziert er die Namensregel des Servers (`"IMAP_MCP_SECRET__" + ref.upper().replace("/","__").replace("-","_")`, Kommentar: „mirrors EnvVarSecretStore.env_name in the server“) → §4.1.
- **Korrekturauftrag:** Zielzustand unabhängig prüfen: nach dem Bootstrap-Lauf (a) den Bootstrap-Prozess über `/proc/<pid>/environ` ist nicht sinnvoll — stattdessen das **Secret-Backend beobachten** (z. B. Server-Start mit `env_var`-Backend + `get`-Probe) oder das Feature auf das tatsächlich beobachtbare Verhalten reduzieren („Bootstrap exit ≠ 0 und stderr nennt …“, Zeile 82-83 sind bereits ehrlich). Namensregel nicht im Step nachbauen.

---

## 4. HOCH

### H-01 — `bulk_mark_seen`: Zielzustand nie über zweiten Kanal geprüft
- **Referenz:** §4.3, §13.2 P1
- **Fundstellen:** `tool_surface/bulk_mark_seen.feature:21-28, 30-37, 47-54` ↔ `MS:306-329`, `GS:710-740`
- **Nachweis:** Die Szenarien prüfen nur `marked_count equals 10/0/20` (Selbstauskunft) bzw. Verbindungs-Zähler. Es gibt keinen Step, der `\Seen` auf den 10/20 Mock-Gmail-Nachrichten verifiziert, obwohl `GS:1100-1106` (`a direct IMAP FETCH … shows the flag`) und `GS:1047-1059` existieren. Ein Stub, der `marked_count` hochzählt, ohne zu speichern, besteht. (Vgl. dagegen `write_capabilities.feature:46`, das `mark_seen` korrekt per IMAP prüft.)
- **Korrekturauftrag:** Feature: Then „a direct IMAP FETCH of uid … shows the flag "\Seen"“ für jede sichtbare UID (Outline über die UID-Tabelle) bzw. neuer Summen-Step „the folder … holds exactly N messages with flag \Seen“ über `GmailState`/IMAP; Gegenprobe für nicht passende Nachrichten (Zeile 35: `0` → keine Nachricht `\Seen`).

### H-02 — `add/replace/delete_attachment`: kein IMAP-/WAL-Kanal; Replace-Inhalt nie verifiziert
- **Referenz:** §4.3, §13.2 P1
- **Fundstellen:**
  - `tool_surface/add_attachment.feature:7-13` (Header verspricht „IMAP second channel: old UID gone, new UID exists“, „WAL state committed“) vs. Szenarien `:23-71` (nur `list_attachments`/`list_messages`/`get_transaction_status`)
  - `tool_surface/replace_attachment.feature:21-29` (Titel: „changes the content hash“; Then prüft nur `result OK`, `mechanism message_rewrite`)
  - `tool_surface/delete_attachment.feature:21-32, 43-54` (Titel „fetch_attachment after delete“, Then nutzt `list_attachments`)
  - `transactions/message_rewrite_recovery.feature:21-29` (Flags erhalten nur via MCP-`search flagged:true`)
  - Steps: `MS:1314-1398`, `MS:1401-1414`
- **Nachweis:** Alle fünf Rewrite-Szenarien lesen den Zustand über denselben MCP-Server zurück. Die vorhandenen unabhängigen Steps (`AS:390-406` Folder-UID, `AS:112-127` Flag, `AS:3779-3875` Header/Occurrence, `WALReader`) werden nicht genutzt. „Old UID gone“ wird nirgends behauptet-und-geprüft; `replace_attachment` prüft **gar nicht**, dass sich der Inhalt geändert hat (Titel ≠ Assertion). Zusätzlich verwendet `_update_uid_after_rewrite` (`MS:1401-1414`) die vom Server gemeldete `new_uid` als Abbildung für spätere IMAP-Prüfungen (Selbstauskunft als Wahrheit, siehe M-06).
- **Korrekturauftrag:** Pro Szenario Then-Steps über IMAP: Anzahl/UID-Wechsel (`folder … contains exactly one message with subject`, `does not contain uid <alt>`), MIME-Struktur per `mime_witness`-Muster (Filename/Anzahl/Base64-Payload-Bytes aus dem `When` – `dGVzdA==` → `test`), Flags per `has flag "\Flagged"`; `replace`: Payload-Bytes vorher/nachher vergleichen; WAL per `the WAL transactions table has an entry with:`.

### H-03 — Recovery-Then-Steps stoßen Recovery erneut an und maskieren „runs once“
- **Referenz:** §1.3, §5.1, §4.4, §13.2 P1
- **Fundstellen:** `transactions/saga_crash_recovery.feature:38-39, 51-52, 64-65, 77-78, 90-93, 104-105, 116-117` · `transactions/draft_revision_recovery.feature:55-56, 85-86` ↔ `AS:1427-1467`, `AS:1470-1484`, `MS:793-818`
- **Nachweis:** `step_wal_entry_reaches_status_timed` pollt die WAL, **ruft aber in jeder Iteration** `client.raw_call("tools/call", {"name": "_test_run_recovery", "arguments": {"passes": 1}})` (Fehler werden geschluckt, `AS:1461-1462`) und schläft 0,5 s. Das Szenario sagt „the server's background recovery loop runs **once**“, danach folgt `Then the WAL entry … reaches status "committed" within 30 seconds`. Benötigt die Implementierung zwei Durchläufe (oder Retry-Zähler wird erhöht), besteht der Test trotzdem. Ein Then-Step darf den Zustand nicht verändern (Heisenberg-Test).
- **Korrekturauftrag:** Then-Step nur WAL **lesen** (`reader.transaction(tx_id)`), Polling ohne Seiteneffekt. Zusätzlich im Feature die Anzahl benötigter Läufe explizit machen (`runs 2 times`), und `retry_count` mit prüfen. `except Exception: pass` entfernen.

### H-04 — 5-Tupel-Fallback: `size` wird approximiert, Toleranzfenster im Then, Given überschreibt Feature-Werte
- **Referenz:** §1.3, §2.1, §13.2 P2/P3
- **Fundstellen:** `transactions/saga_crash_recovery.feature:96-108` (size 48213), `:110-119` (`size=48213`, `first_4kb_sha256=same-as-both`) ↔ `PS:532-544` (`_body_padded_to`, Docstring „aim for ~target_size“), `AS:2631-2667` (Toleranz `size <= actual <= size + 4096`, harte Kopplung an `personal`/`Archiv/Belege`), `PS:2636-2753` (Given überschreibt `size` und `sha`)
- **Nachweis:** Die Feature-Spezifikation nennt exakte Werte (`48213`, „has a size of 48213 bytes“). Das Fixture erzeugt aber nur *ungefähr* so große Nachrichten (`filler = "x" * max(target-300, 100)`) und das Then akzeptiert 48213…52309. Der Given-Step `WAL has an in-progress transaction with fallback-key (…size=48213…)` **ersetzt** `size_value` durch `len(raw)` (`PS:2671-2687`) und `sha_value` durch den Hash der real vorhandenen Nachricht – die im Gherkin sichtbaren Werte werden nicht verwendet. Der Fallback-Key des Servers („size“ ist Teil des Schlüssels!) wird also gegen eine andere Größe getestet als im Szenario beschrieben; ein Server, der `size` ignoriert, besteht.
- **Korrekturauftrag:** Fixture muss Nachrichten **exakter Größe** erzeugen (Padding in einem `X-Padding`-Header iterativ bis `RFC822.SIZE == size_bytes`, Verifikation per `FETCH RFC822.SIZE` im Given, sonst `AssertionError`). Then `has a size of N bytes` ohne Fenster. WAL-Seed: Werte aus der Tabelle unverändert schreiben; den „realen“ Hash/die „reale“ Größe nur als zusätzliche, **sichtbare** Datenspalte im Feature vorgeben.

### H-05 — WAL-Seed-Givens injizieren unsichtbare Fachdaten und duplizieren das Server-Schema
- **Referenz:** §13.2 P3, §1.3, §4.1
- **Fundstellen:** `transactions/saga_crash_recovery.feature:89, 115` (und `startup_saga_recovery.feature:118`, @pending) ↔ `PS:2756-2833`, `PS:2636-2753` (inkl. DDL `2703-2733`, `2776-2809`)
- **Nachweis:** Der Step `WAL contains an in-progress transaction with status "staged" referencing uid 905 and Message-ID "<…>"` schreibt fest `caller_id="invoice-agent"`, `src_account="gupta-scaratec"`, `src_folder="INBOX/Rechnungen"`, `dst_account="personal"`, `dst_folder="Archiv/Belege"`, `content_hash="seeded"`, `created_at=now` in die SQLite-WAL. Nichts davon steht im Szenario; das Ergebnis der Recovery hängt aber genau von Quelle/Ziel/Hash ab. Außerdem wird die Tabellendefinition (`CREATE TABLE … transactions`, Kommentar „copy the DDL here“) im Test dupliziert; zwei leicht unterschiedliche DDLs existieren (`2776` ohne, `2703` mit `fallback_*`). Weicht das Server-Schema ab, lügt der Test.
- **Korrekturauftrag:** Seed-Daten als Tabelle im Gherkin (`src_account | src_folder | src_uid | dst_account | dst_folder | message_id | content_hash`); Schema nicht selbst anlegen, sondern den Server einmal starten lassen (Schema erzeugt der Server) und dann per `sqlite3` einfügen; `content_hash` aus dem tatsächlichen Message-Inhalt ableiten (sichtbar).

### H-06 — „in-flight transaction completes under the original capabilities“: Then führt Aktionen aus und prüft eine beliebige WAL-Zeile
- **Referenz:** §5.1, §13.2 P1/P2, §4.4
- **Fundstellen:** `policy/policy_reload.feature:128-136` ↔ `AS:2829-2860`, `PS:1511-1600`, `PS:1603-1614`
- **Nachweis:** Das Then schreibt die `.resume`-Datei (Aktion im Then), joint den Move-Thread (60 s) und prüft nur, dass **irgendeine** WAL-Transaktion `committed` ist. Nicht geprüft: dass es *diese* Transaktion ist (`last_tx_id` bleibt ungenutzt), dass die Nachricht tatsächlich im Ziel liegt/aus der Quelle verschwunden ist, und dass sie „unter den Original-Capabilities“ lief (die geänderte Policy wird erst durch das folgende `move` UID 302 getestet). Der Move-Thread benutzt denselben stdio-`MCPClient` wie der Hauptthread (gemeinsamer `stdout`-Reader) – bei zusätzlichen Aufrufen vor dem Join entsteht ein Race. Marker-/Resume-Dateien sind Testhaken im Server (`IMAP_MCP_SAGA_PAUSE_*`).
- **Korrekturauftrag:** Resume in einen `When`-Step auslagern; Then: `WAL entry for this tx_id has status "committed"` (spezifische `tx_id` aus dem Thread-Ergebnis) + `direct IMAP SEARCH` auf Quelle/Ziel; Thread-Aufruf über eigenen MCPClient-Kanal oder Locking.

### H-07 — No-op bzw. zahnlose Negativ-Steps auf IMAP-Seite
- **Referenz:** §4.3, §13.2 P1, §13.1
- **Fundstellen:**
  - `transactions/intra_account_move.feature:37` („folder … is empty“) ↔ `PS:891-919` (Docstring „assert (and enforce) emptiness“, Code: `pass`)
  - `…intra_account_move.feature:63` („contains no message with uid“) ↔ `PS:1267-1272` (No-op)
  - `…intra_account_move.feature:68` („folder … is unchanged“) ↔ `AS:481-499` (prüft nur Anwesenheit *gemappter* UIDs; im Szenario ist nichts gesät → `seeded == []` → **keine Assertion**; neue/gelöschte Fremd-Nachrichten werden nie erkannt)
  - `…saga_crash_recovery.feature:91` ↔ `AS:1487-1506` (nur „Ordner nicht leer“, Name verspricht Message-ID-Treffer)
  - `…saga_crash_recovery.feature:92` ↔ `AS:1509-1526` (zählt Nachrichten im Ordner, nicht Message-ID)
  - `…saga_crash_recovery.feature:119` ↔ `AS:2695-2703` („no additional DELETE is issued“ = `_ = (context, folder)`)
- **Nachweis:** In jedem dieser Fälle klingt der Step fachlich stark, besitzt aber keine oder nur eine schwächere Assertion. Besonders `is unchanged` besteht bei einem Server, der eine Fremd-Nachricht löscht oder dupliziert.
- **Korrekturauftrag:** `is empty` → tatsächlich `folder_uids == []` prüfen; `no message with uid` → Nichtexistenz **hart** prüfen (UID-Liste vor dem `When` merken); `is unchanged` → Snapshot (UID-Menge + Message-IDs) im Given/`Before the When` merken und im Then vergleichen; Recovery-Steps → `search_by_message_id` mit der Message-ID aus dem Given; „no additional DELETE“ → Proxy-/Command-Log-Witness (`imap_command_lines`) oder Step streichen.

### H-08 — „triggers a DENY with reason sender_blacklisted“ verbirgt das komplette Given und prüft das Ergebnis teilweise gar nicht
- **Referenz:** §13.2 P3, §1.3, §5.1
- **Fundstellen:** `audit/audit_log_format.feature:94-97`, `tool_surface/reason_code_contract.feature:70-73` ↔ `MS:439-493`
- **Nachweis:** Der `When`-Step schreibt die Policy um (`builder.policies[0]` → `folder.mode="blacklist"`, `default="ENVELOPE"`, ersetzt alle Regeln durch `SenderRule(match={"from": address}, cap="NONE")`), baut eine versteckte Nachricht (`uid_hint=999`, Betreff „Ping“) und ruft `fetch_envelope` auf. Das Szenario enthält nur die Absender-Adresse; Modus, Default, Regelform, Betreff und UID sind unsichtbar (§2.1). In `audit_log_format.feature:94-97` wird zudem **nirgends** geprüft, dass überhaupt `decision DENY`/`reason sender_blacklisted` eintritt – die Then-Steps prüfen nur „record does NOT contain bank.de“ und den SHA-Digest (die Assertion wäre auch bei ALLOW grün, solange `from_domain_sha256` existiert).
- **Korrekturauftrag:** Feature: Background-Policy sichtbar als Blacklist-Policy mit Regel `from=noreply@bank.de -> cap NONE`, Nachricht per `holds a message with:`; `When invoice-agent calls fetch_envelope …`; `Then the response decision is DENY` und `reason equals "sender_blacklisted"` explizit. Den Sonderstep löschen.

### H-09 — `calls search with criteria {…}` verwendet versteckten Account/Ordner
- **Referenz:** §13.2 P3, §1.3
- **Fundstellen:** `audit/audit_log_format.feature:99-102` ↔ `MS:1430-1452` (`context.last_call_account = "gupta-scaratec"`, `last_call_folder = "INBOX/Rechnungen"`)
- **Nachweis:** Das Szenario nennt weder Account noch Ordner. Beide stammen aus dem Step-Code (Docstring: „Use the default minimal account+folder“) und hängen implizit an `PS:1064-1094` (`policy grants INBOX/Rechnungen …` registriert heimlich `gupta-scaratec` und legt den Ordner an).
- **Korrekturauftrag:** Standard-Step `calls search with account "…", folder "…", criteria …` (existiert, `MS:1455-1489`) verwenden; Kurzform löschen.

### H-10 — Zirkelschluss bei Audit-Hash-Prüfungen
- **Referenz:** §4.1, §2.2, §13.2 P2
- **Fundstellen:** `audit/audit_log_format.feature:102` ↔ `AS:1164-1188`; `audit/audit_retention.feature:64-69` ↔ `PS:1024-1061`, `AS:2772-2786`; `audit/audit_log_format.feature:72-77` ↔ `AS:1725-1776`, `AR:74-83`; `audit/audit_multi_instance.feature:69,77,88,100` ↔ `AI:222-245`
- **Nachweis:**
  - `search_query_digest` wird gegen `sha256(json.dumps(criteria, sort_keys=True))` verglichen. „canonicalized JSON“ ist im Feature **nicht definiert**; Separatoren (`", "`/`": "` als Python-Default), Sortierung und Encoding sind eine Annahme des Tests, die der Server spiegeln muss. Test und Server bestätigen sich gegenseitig (§4.1/§2.2).
  - Der External-Root-Hook-Test vergleicht gegen `expected_final_hash`, den der **Test** per `sha256(last_line + "\n")` selbst berechnet (`PS:1056-1060`) – das Feature nennt nur den Platzhalter `sha256:<hash>` (`:66, :69`). Der Wert der `eof_day`-Record (`final_hash`) wird nicht gelesen.
  - Die Hash-Kettenformel (`"sha256:" + sha256(raw_line)`) steckt dreifach in Step-/Support-Code (`AR:82`, `AS:1734`, `AI:239`), nicht im Feature.
- **Korrekturauftrag:** Feature: kanonische Form ausschreiben und den erwarteten Digest als **Literal** für eine feste Kriterienmenge angeben (wie `update_draft.feature:191` es für `X-SC-Draft-Change-Hash` vormacht); Hook-Test: `final_hash` aus der `eof_day`-Zeile der Audit-Datei lesen und mit `roots.txt` vergleichen; Kettenformel einmal in `AuditReader` kapseln und im Feature als Regel benennen.

### H-11 — Audit-„no content leak“: Abwesenheits-Witnesses ohne Positivkontrolle bzw. selbst als trivial deklariert
- **Referenz:** §4.3, §13.2 P1/P3, §4.4
- **Fundstellen:** `audit/audit_log_format.feature:86-92`, `tool_surface/reason_code_contract.feature:182-185` ↔ `PS:1343-1420` (Given), `AS:1788-1832` (Thens)
- **Nachweis:**
  - Given-Name: „creates 20 audit records across ALLOW, DENY, saga, and token_refresh“. Code: 10× `fetch_envelope`, 5× `list_accounts`, 5× `search` – **kein** Saga-, kein `token_refresh`-Record, die Anzahl 20 wird nicht verifiziert.
  - `the file does NOT contain any access token or refresh token value` – Kommentar im Code: „The scenario stages no OAuth/refresh tokens; nothing to leak … trivially satisfied“ (`AS:1802-1804`). `… any attachment filename`: es wird kein Anhang gesät, Suche nach `.pdf/.zip/.docx/.xlsx` → Vakuum. `… any Subject: header`: nur die zwei im **Step-Code** definierten Präfixe `Chain-allow-`/`Chain-deny-` (`AS:1816-1822`, `PS:1365,1383`), die im Feature nicht stehen. `BEGIN PGP`/`-----` – kein Body enthält sie.
- **Korrekturauftrag:** Positivkontrolle im Szenario: gesäte Nachricht mit konkreten sensitiven Werten (Betreff, Anhangsname, Token-Wert im Secret-Store), dann Absenz je Wert prüfen; Given-Namen korrigieren oder Saga/Token-Refresh wirklich auslösen; Needles aus dem Gherkin lesen, nicht aus dem Step.

### H-12 — Audit-Retention: Schleifen-Assertions sind vakuos; „records the filename and age“ prüft keine Werte
- **Referenz:** §4.4, §13.2 P1/P2
- **Fundstellen:**
  - `audit/audit_retention.feature:35` ↔ `AS:1957-1981` (Schleife über `glob("*.jsonl.gz")`; erwarteter Inhalt wird aus dem Platzhalterformat des Given (`PS:1676-1679`) rekonstruiert)
  - `…audit_retention.feature:77-81` ↔ `AS:2092-2103` (Modus), `AS:1993-2007` (Original weg), `MS:653-660` (`gz_filename` ignoriert: `_ = gz_filename`)
  - `…audit_retention.feature:41` ↔ `AS:2010-2026` (prüft nur `"filename" in r and "age_days" in r`)
- **Nachweis:** Existiert keine `.jsonl.gz`-Datei, laufen die Schleifen leer durch und die Steps sind grün (`for path in glob: …` ohne Mindestanzahl). Der When „compresses it to "2026-01-20.jsonl.gz"“ ignoriert den genannten Zielnamen. `retention_delete`-Record: weder `filename == "2024-01-01.jsonl.gz"` noch `age_days == 830` wird verglichen.
- **Korrekturauftrag:** Zieldatei aus dem Gherkin-Argument ableiten und Existenz zuerst assertieren; `gz`-Inhalt gegen im Given **gemerkten** SHA-256 vergleichen (nicht rekonstruieren); `retention_delete`-Record auf `filename` und `age_days` aus dem Szenario prüfen.

### H-13 — OAuth: Fehlerfälle werden im Server-Testmodus statt im Mock-Provider erzeugt; „primed“-Givens sind inert; STORE-Witness über Audit
- **Referenz:** §7.2, §13.2 P1, §1.3
- **Fundstellen:** `auth/oauth2_bootstrap.feature:30, 38, 54, 58, 67, 90` ↔ `OS:24-30`, `OS:54-58`, `OS:89-93`, `OS:131-133` (`PS:3498-3499`), `OS:181-189`, `OS:8-22`, `OS:32-52`
- **Nachweis:**
  - „mock OAuth2 provider is primed to consent/deny“ setzt nur `context.mock_oauth_browser_error`, das **nirgends gelesen** wird; der Provider wird nicht konfiguriert.
  - „responds to the next token-exchange request with error "invalid_grant"“ → `IMAP_MCP_TEST_OAUTH_INJECT_ERROR` (Env-Variable des **Servers**); „returns access tokens with lifetime 60 seconds“ → `IMAP_MCP_TEST_TOKEN_LIFETIME`; „tamper with the code_challenge“ → `IMAP_MCP_TEST_TAMPER_PKCE`. Der Fehler entsteht im System under Test, die echte Antwortverarbeitung auf einen Provider-Fehler (§7.2, §4.5 „Antwortverarbeitung“) wird nicht ausgeführt, obwohl `bdd/mock-oauth` existiert.
  - `the IMAP command STORE \Seen was NEVER issued against the provider` prüft nur, dass kein `mark_seen ALLOW` im Audit steht (`OS:181-189`) – Selbstauskunft des Servers; der Mock-Gmail-/Proxy-Command-Log wäre der unabhängige Kanal (`GS:970-989`, `imap_command_lines`).
  - `mock OAuth2 provider "google-mock" listens on {url}`: `url` wird ignoriert, Basis-URL ist hartkodiert (`OS:10`); Callback-URL mit erfundenem `state=mockstate` (`OS:37, 48`).
- **Korrekturauftrag:** Fehler/Lifetime/PKCE im Mock-Provider (`mock-oauth`-Config bzw. Request-Hook) konfigurieren; STORE-Absenz über Command-Log des Zielservers belegen; Provider-URL aus dem Gherkin verwenden; inerte Givens entweder implementieren oder aus dem Feature streichen.

### H-14 — OAuth-Then-/When-Steps schlucken Fehler oder haben keinen Effekt
- **Referenz:** §4.4, §5.3, §13.2 P1
- **Fundstellen:** `auth/oauth2_bootstrap.feature:60-63, 76, 82, 86-87` ↔ `OS:60-74`, `OS:76-87`, `OS:95-102`, `OS:104-117`, `OS:119-129`, `OS:284-292`, `OS:267-282`
- **Nachweis:**
  - `the mock OAuth2 token endpoint records at least {count}`: `if resp.status_code == 200:` – bei jedem anderen Status passiert **keine Assertion** (grün).
  - `the server reuses the persisted access token…`: `except httpx.ConnectError: pass`; Nicht-200 ebenfalls stumm grün; die Prüfung `len(reqs) == initial + 1` setzt die unsichtbare Annahme „genau eine Exchange vor dem Neustart“ voraus.
  - `the server runs for N seconds` ändert nur `IMAP_MCP_FAKE_NOW_UTC` im Harness-Dict und schläft 1 s; ein **bereits laufender** Server (Env wird beim Spawn kopiert) sieht die Zeitverschiebung nie → „at least 2 token-exchange requests“ (`:61`) ist nicht durch 50 s Laufzeit belegt.
  - `the server completes one token exchange` / `…starts and completes…` verschlucken alle Exceptions (`except Exception: pass`), das Ergebnis des Aufrufs wird nicht geprüft.
  - `no further connection attempts … occur until the operator reruns the bootstrap` prüft nur `state == needs_rebootstrap` (Server-Selbstauskunft), keine Connection-Beobachtung.
- **Korrekturauftrag:** Nicht-200 und `ConnectError` → `AssertionError`; Zeitverschiebung nur über `IMAP_MCP_FAKE_NOW_FILE` (wie `AI:34-42`) oder Server-Neustart; Aufrufergebnis asserten; „no further attempts“ per Proxy-Connection-Log belegen.

### H-15 — „the account's state is healthy“ besteht still
- **Referenz:** §4.4, §13.2 P1
- **Fundstellen:** `policy/policy_reload.feature:121` ↔ `PS:3442-3464`
- **Nachweis:** Ist `state == "healthy"`, wird `expected_state = "active"` gesetzt; findet die Schleife keinen Account in diesem Zustand, endet sie ohne Assertion, und der abschließende `raise` greift nur für `state != "healthy"`. Auch `if not content: return` (Z. 3453-3454) beendet den Step grün. Ein Account in `needs_rebootstrap` oder gar keine Antwort besteht das Given. Außerdem hängt das Mapping `healthy → active` als Fachregel im Step.
- **Korrekturauftrag:** Account `acc["id"] == account_id` gezielt suchen und `acc["state"] == expected` hart prüfen; Mapping `healthy`/`active` entweder im Feature vereinheitlichen oder als sichtbare Tabelle führen.

### H-16 — Versteckte Fachdaten in Nachrichten-Fixtures (Absender, Empfänger, Anhänge, Betreff, Message-ID, Datum)
- **Referenz:** §13.2 P3, §2.1, §1.3
- **Fundstellen:** `PS:812-888` (u. a. `827-828` Sender/To `"{user}@bdd.local"`, `854` Body `"Body for {subject}"`, `856-857` `stub.bin`/16 Byte bei `has_attachment true`, `866` Message-ID `<scenario-{uid}@bdd.local>`, Date=jetzt), `PS:3647-3733` (`from_addr="bulk@example.com"`, Betreff `Bulk seed #n`, UIDs ab 9000), `GS:710-740` (`sender-{i}@example.com`, `test@scaratec.com`, `Test message {i}`), `GS:51-70`, `PS:922-948`.
- **Betroffene Szenarien (Auswahl):**
  - `tool_surface/bulk_mark_seen.feature:25-26, 34-35, 51-52` — „holds 10 messages“ nennt keinen Absender; das Kriterium `{"from_domain": "example.com"}` (Zeile 26/43/52) trifft nur wegen des versteckten Defaults; `{"from": "nobody@nowhere.com"}` (Zeile 35) ist nur „no match“, weil der Default-Absender ein anderer ist.
  - `tool_surface/add_attachment.feature:37-45`, `delete_attachment.feature:34-41`, `replace_attachment.feature:31-38` — `has_attachment true` erzeugt unsichtbar ein 16-Byte-`stub.bin`; `attachments has length 2` (Zeile 45) hängt an dieser Annahme.
  - `tool_surface/create_reply_draft.feature:98-99, 145-146` — fehlt `to_header`, wird `To` unsichtbar zu `{user}@bdd.local`; Cc-Ableitung des Servers (To ∪ Cc minus Identität) bezieht diesen Default ein; fehlt die `date`-Spalte, trägt die Nachricht den aktuellen Zeitstempel (Attributionszeile zeitabhängig).
- **Korrekturauftrag:** Seeding-Tabellen **müssen** alle für Assertions relevanten Felder explizit führen; fehlt eine Spalte, soll der Step das *leere* Feld erzeugen oder mit `AssertionError` abbrechen („Spalte `from` erforderlich“), statt zu erraten. Für Anhänge ein explizites `the message has attachment …`-Given (existiert: `PS:3092-3109`) statt `has_attachment true`.

### H-17 — Versteckte Policy-/Caller-/Account-Defaults im Glue
- **Referenz:** §13.2 P3, §1.3, §5.1
- **Fundstellen:** `PS:249` (jedes `configured with caller:` registriert `gupta-scaratec`), `PS:2894-2934` (`minimal configuration`: Regel `from_domain=hornbach.de → ENVELOPE`, `move_out/mark_seen/mark_tagged=True`, Ordner `INBOX/Rechnungen`), `PS:965-1004` (`mark_tagged=true on`: heimliche Regel `hornbach.de → FULL`), `PS:1038-1051` / `PS:1633-1639` (Policy `audit-hook-policy`, `invoice-policy` mit Default-Caller), `PS:2960-2973` (Policy-Name aus Caller-Konvention `<prefix>-policy`), `PS:229` (Default `stdio_trusted`), `PS:2569-2594` (feste Accounts/Ordner), `OS:191-200` (`dummy-policy`, `INBOX blacklist FULL`), `OS:293-304` (Account `gmail-ronly` fest), `PB:25-27, 36` (Default-Host/Port/Provider/`token_cache`).
- **Betroffene Szenarien (Auswahl):** `audit/audit_multi_instance.feature:35,48,61,…` (`minimal configuration`), `tool_surface/non_goal_rejection.feature:15` (Beispiel: `get_audit_log`/…-Tests laufen gegen versteckte Policy), `auth/oauth2_bootstrap.feature:44-54` (`policy "ronly-policy" grants capability "mark_seen" on folder "INBOX"` → Account `gmail-ronly` aus Step, `mode whitelist/default NONE` aus Step, keine Regel).
- **Nachweis:** Das Ergebnis (ALLOW/DENY, Sichtbarkeit) hängt an Policy-Daten, die im Szenario fehlen.
- **Korrekturauftrag:** Sichtbare Given-Tabellen; Steps mit Defaults entweder entfernen oder `AssertionError` bei fehlenden Angaben; Account-/Policy-Namen nur aus dem Gherkin.

### H-18 — Gmail-Stubs: magische UIDs 505/510/520, versteckter Absender `rechnung@hornbach.de`
- **Referenz:** §13.2 P2/P3, §2.2
- **Fundstellen:** `providers/gmail_label_semantics.feature:67-68, 81-83, 97-99, 114-116, 150` ↔ `GS:327-400` (`_uid_base = {"INBOX": 505, "Rechnungen": 510, "Hornbach": 520}`, Absender/Subject/Message-ID fest), `GS:602-642` (`trash@example.com`)
- **Nachweis:** Die `When`-Zeilen verwenden `uid 505`, das Given „a Gmail message with canonical_all_mail_uid 10002 carries labels …“ nennt diese UID nirgends (nur Kommentar Zeile 80: „assigns INBOX UID 505 by convention“). Absender `rechnung@hornbach.de` ist für die PDP-Regel `from_domain=hornbach.de → FULL` (Zeilen 41-44) **entscheidend**; ändert man ihn, kippt das Ergebnis auf DENY – im Szenario unsichtbar.
- **Korrekturauftrag:** Seed-Tabelle mit `x_gm_msgid | from | subject | labels | uid_inbox | uid_rechnungen | …` (vgl. Zeilen 54-57, die das bereits vormachen); Konvention aus dem Step entfernen.

### H-19 — „saga's FETCH step retrieves RFC822 bytes from … All Mail“ ist ein `pass`
- **Referenz:** §13.1, §13.2 P1, §4.3
- **Fundstellen:** `providers/gmail_label_semantics.feature:143` ↔ `GS:525-554` (Docstring „passes as a documentation assertion“, Code `pass`)
- **Nachweis:** Die zentrale Spezifikation („deterministisch aus [Gmail]/All Mail“, nicht aus `Rechnungen`) wird nicht geprüft; die Folgesteps (`:144-146`) belegen nur Ergebnis (Nachricht angekommen, Label entfernt), nicht den Fetch-Pfad. Der unabhängige Kanal existiert: `GmailState.command_log` (`GS:970-989`, `_server_command_log`).
- **Korrekturauftrag:** Step auf `command_log` stützen („FETCH/UID FETCH von mailbox `[Gmail]/All Mail` UID 10003 vorhanden, keiner auf `Rechnungen` UID 510“).

### H-20 — „direct IMAP SEARCH … returns exactly one result“ (IU) prüft weder SEARCH noch Eindeutigkeit
- **Referenz:** §4.3, §13.2 P1
- **Fundstellen:** `providers/imap_mailbox_names_utf8.feature:206` ↔ `IU:202-224`, `IF:545-567`
- **Nachweis:** Der Step enumeriert alle UIDs, dekodiert Betreffs und gibt die **erste** Übereinstimmung zurück (`find_uid_by_decoded_subject`), assertiert nur `uid is not None`. Zwei Treffer (z. B. doppelter APPEND bei Wire-Name-Bug) bleiben unentdeckt, obwohl der Satz „exactly one“ lautet. Schlimmer: genau die Klasse von Fehlern, die das Feature spezifiziert (APPEND in falsches/doppeltes Postfach), ist so nicht erkennbar. Zusätzlich ruft der Step grundlos `context.imap.close_all()`.
- **Korrekturauftrag:** Alle Treffer zählen (`count == 1`), Folder via `to_wire` ansprechen, gleiche Implementierung wie `_assert_subject_presence`/`search_header` verwenden.

### H-21 — HTTP-Start verändert stillschweigend Caller-Konfigurationen
- **Referenz:** §1.3, §5.1, §13.2 P3
- **Fundstellen:** `tool_surface/reason_code_contract.feature:22-31, 134-141` ↔ `MS:537-549`
- **Nachweis:** Beim Start im HTTP-Modus werden alle `stdio_trusted`-Caller auf `shared_token` umgeschrieben und mit einem **Platzhalter-`token_secret_ref`** (`secret://callers/{id}/token`) versehen, wenn kein `shared_token`-Caller existiert. Das Szenario `auth_failed is reachable on HTTP transport with a wrong bearer token` (Background: `configured with caller "invoice-agent" using policy …` = `stdio_trusted`) läuft dadurch gegen eine Konfiguration, die das Feature nie beschreibt – der Server hätte laut ADR-0015 den Start verweigert. Das `auth_failed` entsteht dann u. U. wegen **fehlendem Secret** statt wegen falschem Token; der im Szenario genannte Grund (wrong bearer token) ist nicht belegt.
- **Korrekturauftrag:** Promotion entfernen; Feature wie `audit_log_format.feature:105-109` mit expliziter Callers-Tabelle und `secret store contains value …` versehen.

### H-22 — GPG-„wrong passphrase“-Szenario testet fehlenden Secret-Key; Fingerprint-Ersetzung mutiert Given-Daten
- **Referenz:** §13.2 P3, §1.3, §7.2
- **Fundstellen:** `auth/secret_store_backends.feature:86-114` ↔ `PS:2151-2210`, `PS:2213-2248`, `PS:2251-2275`, `PS:2073-2087`
- **Nachweis:** Der Keypair-Step erzeugt `%no-protection` (kein Passphrase-Schutz); `passphrase` wird nur im Context abgelegt und nie verwendet. Das Szenario „decryption failure on wrong passphrase“ löscht stattdessen den **Secret-Key** (`gpg --delete-secret-keys`, Kommentar: „Cheaper than wiring a stub pinentry“). Der Server sieht also „kein Key“, nicht „falsche Passphrase“; die Ursache im Titel/Given ≠ beobachtetes Szenario. Der im Feature genannte Fingerprint `ABCDEF…01` wird durch den echten ersetzt (`_scratch_substitute`, Z. 2085-2086) – Given-Daten werden still mutiert.
- **Korrekturauftrag:** Schlüssel mit Passphrase erzeugen (`%passphrase`), Pinentry/`--passphrase-fd` im Server-Aufruf konfigurieren (`gpg-agent`-Preset); alternativ Feature-Titel auf „missing secret key“ ändern. Fingerprint im Feature als Ausgabe des Given dokumentieren.

### H-23 — Kanonische Reason-Code-Tabelle lebt im Step-Code; „Variance discipline“ ist ein Text-Grep
- **Referenz:** §5.3, §2.2, §13.2 P2
- **Fundstellen:** `tool_surface/reason_code_contract.feature:3-8, 20, 182-188` ↔ `AS:1538-1565`, `AS:1568-1586`, `AS:1589-1670`
- **Nachweis:** `CANONICAL_REASON_CODES` ist eine im Step-Modul gepflegte Menge. Das Feature referenziert nur „ADR-0017 §2.1“ – die Tabelle ist im Szenario nicht sichtbar (§5.3: Helper ist nicht Wahrheit). Mehrere im Repo erwartete `reason`-Werte stehen **nicht** in der Menge (`invalid_grant` – `oauth2_bootstrap.feature:71`; `secret_decryption_failed` – `secret_store_backends.feature:112`; `parse_error`/`validation_error`/`account_removed` – `policy_reload.feature:71,89,101`; `ambiguous_fallback_match` – `saga_crash_recovery.feature:118`; `oauth_scope_insufficient` – `oauth2_bootstrap.feature:51`; `tool_not_applicable_for_provider` – `gmail_label_semantics.feature:135`; `missing_message_id` – `create_reply_draft.feature:200`). Der Contract-Check prüft zudem nur die Audit-Records des einen skripteten Workloads (`PS:1343-1420`), nicht die Flüsse mit diesen Codes. Die Variance-Prüfung zählt jede *Erwähnung* des Codes im Szenariotext (`AS:1637`, inkl. Titel/Kommentare) – das ist Doku-Abdeckung, kein Verhaltensnachweis.
- **Korrekturauftrag:** Code-Tabelle im Feature als Data Table führen (`reason | category | reachable via`), Step liest sie; Check über **alle** Audit-Records des Testlaufs (z. B. Aggregation in `after_scenario` in eine Datei) statt Einzelworkload; Variance-Scan auf `Then … reason equals "<code>"`-Zeilen beschränken (Kommentare ausschließen).

### H-24 — „folder_hidden looks identical whether the folder exists or not“ ohne Positivkontrolle
- **Referenz:** §4.2, §13.2 P1/P3
- **Fundstellen:** `tool_surface/folder_path_contract.feature:119-128` (Kommentar „one really exists“, Folder `Existing/But/Hidden`), Steps generisch (`AS:615-…`, `MS:1417-1427`)
- **Nachweis:** Weder wird `Existing/But/Hidden` jemals auf dem IMAP-Server angelegt (der passende Step `PS:2859-2871` existiert, wird nicht verwendet) noch wird die „same opaque shape“ verglichen (Zeilen 123-128 prüfen je `DENY`/`folder_hidden`). Ein Server, der bei existierenden vs. nicht-existierenden Ordnern unterschiedlich antwortet (z. B. zusätzlicher Feldsatz), besteht, weil nie ein existierender Ordner abgefragt wird.
- **Korrekturauftrag:** Given `the IMAP account "gupta-scaratec" has a hidden folder "Existing/But/Hidden"`; neuer Step `the two responses have the same keys and the same values except …` (Antworten aus `response_history` vergleichen).

### H-25 — „accounts describes the "invoice-agent" policy, not "overview-agent"“ ist ein No-op
- **Referenz:** §13.1, §5.1
- **Fundstellen:** `tool_surface/non_goal_rejection.feature:73-76` ↔ `AS:2492-2510` (`_ = (caller_id, other_caller)`)
- **Nachweis:** Der Step prüft nur, dass `accounts` existiert. Der Caller `overview-agent` ist im Szenario (Minimal-Config mit nur `invoice-agent`) nicht konfiguriert, es gibt also keine unterscheidbare Gegenpolicy. Die Zeile belegt den Satz im Namen nicht.
- **Korrekturauftrag:** Zweiten Caller mit anderer, unterscheidbarer Policy konfigurieren und `accounts` des aufrufenden Callers gegen die erwartete Account-Liste vergleichen.

### H-26 — Negative Flag-Prüfung besteht für nicht existierende Nachrichten
- **Referenz:** §4.3, §4.4
- **Fundstellen:** `policy/write_capabilities.feature:56`, `tool_surface/non_goal_rejection.feature:55` ↔ `AS:130-151` ↔ `IF:582-597` (`fetch_flags` liefert `()` bei Nicht-OK, `data[0] is None`, fehlendem `FLAGS`), `AS:121,146` (UID-Fallback auf Hint)
- **Nachweis:** `step_imap_message_does_not_have_flag` gibt bei fehlender UID (oder nach Fallback `lookup.get(…, uid)` auf einen *Hint* statt einer realen UID) `()` zurück → „does not have flag“ wahr. Aktuell sind die Nachrichten gesät, aber das Szenario ist nicht gegen Seeding-/Mapping-Fehler geschützt (`flush_staged_messages` läuft erst beim ersten Tool-Aufruf; ein fehlgeschlagener APPEND wirft, ein verfehltes Mapping nicht).
- **Korrekturauftrag:** `fetch_flags` bei nicht vorhandener UID `RuntimeError`; negative Steps prüfen vorab, dass die UID im Ordner existiert.

---

## 5. MITTEL

### M-01 — WAL-„committed“ nur über `get_transaction_status` (Selbstauskunft)
- **Referenz:** §13.2 P1
- **Fundstellen:** `tool_surface/add_attachment.feature:63-71`, `replace_attachment.feature:40-49`, `update_draft.feature:286-295` ↔ `AS:313-320`
- **Nachweis:** `the status response field state equals "committed"` liest die Antwort des Servers. `WALReader` und `the WAL transactions table has an entry with:` (`AS:323-381`) existieren, werden in diesen Szenarien nicht genutzt (anders als `cross_account_move_saga.feature:46-55`).
- **Korrekturauftrag:** zusätzlicher Then `the WAL entry for this tx_id has status "committed"` (`AS:2513-2529`).

### M-02 — Persistence über Server-Lesepfad statt IMAP; „no message has tag“ besteht bei leerer Liste
- **Referenz:** §4.3, §13.2 P1, §4.4
- **Fundstellen:** `tool_surface/bulk_mark_tagged.feature:34-37, 47-48, 58-60` ↔ `AS:3465-3489`; `tool_surface/create_reply_draft.feature:201-202, 320-321, 334-335, 379-380`
- **Nachweis:** Das Feature nennt „Persistenz-Validierung (§13.2)“, liest aber via `list_messages` (selber Server). `every returned message has tag` verlangt nicht-leere Liste (`AS:3469-3470`), das Gegenstück `no returned message has tag` **nicht** (`AS:3480-3489`: bei leerer/abgelehnter Antwort grün). Beide tolerieren `msg.get("tags") or msg.get("keywords")` (Schema-Raten). Reply-Negativfälle (`matched_total equals 0`) prüfen nicht den IMAP-Ordner.
- **Korrekturauftrag:** `the IMAP message … has keyword` (existiert, `AS:154-158`) je UID; Negativ-Step mit Mindestanzahl; Reply-Negativfälle per `the IMAP folder "Drafts" contains no message …` bzw. UID-Menge leer.

### M-03 — „the server terminates ungracefully“ sendet SIGTERM
- **Referenz:** §13.2 P1, §5.1
- **Fundstellen:** `audit/audit_log_format.feature:125-133` ↔ `MS:821-830`, `MC:106-119`
- **Nachweis:** `client.close()` → `Popen.terminate()` (SIGTERM) und 3 s Wait. Ein Server mit SIGTERM-Handler beendet den Audit-Writer sauber (inkl. `eof_day`); Szenario („stale 0400 file from a previous run“, Zeile 131 „ends with eof_day“) kann dann grün werden, ohne den Neustartpfad zu nutzen. In den Saga-Szenarien erzeugt der Server-Hook `IMAP_MCP_CRASH_AT` den Absturz, dort ist der Step harmlos.
- **Korrekturauftrag:** `proc.kill()` (SIGKILL) für „ungracefully“; Variante „SIGTERM“ separat benennen.

### M-04 — Gesamte Suite läuft mit `IMAP_MCP_TEST_MODE=1`
- **Referenz:** §5.3, §7.2, §13.2 P1
- **Fundstellen:** `MS:116, 551, 2022`, `AI:75`, `PS:1117, 1224, 1556`
- **Nachweis:** Jeder Server wird mit Testmodus (Test-Tools `_test_run_recovery`, `_test_run_audit_rotation`, Fake-Uhr, Crash-/Pause-Hooks, Env-basierte Fehlerinjektion) gestartet. Das Verhalten der Produktionskonfiguration wird nur dort belegt, wo keine Test-Hooks nötig sind – aber *nirgends* ohne Testmodus (z. B. `mcp_tool_discovery.feature:25-58`: „exactly these 30 tools“ – mit Testmodus aktiv).
- **Korrekturauftrag:** Testmodus nur per Tag (`@test_hooks`) aktivieren; Tool-Surface-/Auth-/Audit-Feature mindestens einmal ohne Testmodus fahren.

### M-05 — Fault-Injection-Givens ignorieren Argumente oder weichen vom Feature ab
- **Referenz:** §1.3, §7.2
- **Fundstellen:** `PS:1120-1124, 1218-1221, 1249-1252` (`_ = code` – „error 500“), `PS:1133-1155` (`_ = folder` – „SELECT of "<folder>"“, im Feature `folder_path_contract.feature:110` @pending), `PS:2517-2528` (`_ = folder`, Account hart `gupta-scaratec`, `uidvalidity_new_value="999999"`), `PS:1231-1240` + `PX:371-383` („delays … by 45 seconds“: Proxy schläft und leitet **nie** weiter) — Features `transactions/cross_account_move_saga.feature:61, 77, 95, 116`, `intra_account_move.feature:94`
- **Nachweis:** Der Fehler trifft das *nächste* Kommando mit passender Verb, unabhängig vom genannten Ordner/Code; „delay“ ist faktisch ein Hänger, nicht eine verspätete Antwort.
- **Korrekturauftrag:** Folder/Mailbox als Matcher im Proxy implementieren oder Szenario-Argument entfernen; Delay als echte Verzögerung+Weiterleitung oder Feature „never responds within N seconds“ umformulieren.

### M-06 — UID-Abbildung: stille Fallbacks und Selbstauskunft als Mapping
- **Referenz:** §1.3, §4.1, §13.2 P1
- **Fundstellen:** `MS:293, 349, 394, 430, 1073…` (`lookup.get(key, uid)` → Hint als echte UID), `MS:1401-1414` (`new_uid` aus Server-Response), `PS:2004` (`next(iter(uid_lookup.values()), 1)` – beliebige UID eines beliebigen Ordners), `IU:77-91` (beliebiger Schlüssel mit passendem Hint), `TS:160-169` (bei Mehrdeutigkeit unverändert), `AS:601-612` (`_resolve_uid_hints` abhängig von `last_call_*`)
- **Nachweis:** Fehlende Mapping-Einträge führen nicht zu Fehlern, sondern zu Hint-UIDs; die nachträgliche Rewrite-UID kommt aus dem Server. Beide Mechanismen können Fehler im System durch kompensierende Fehler im Test verdecken.
- **Korrekturauftrag:** Fehlender Mapping-Eintrag = `AssertionError` (außer in Deny-Szenarien explizit gekennzeichnet); neue UID nach Rewrite per IMAP-Suche (Message-ID/Subject) ermitteln, nicht aus der Response.

### M-07 — Doppelte/überlappende Step-Definitionen und Matcher-Reihenfolge
- **Referenz:** §5.2, §5.3
- **Fundstellen:** `GS:710` vs. `PS:3687` (`the folder "{folder}" on "{account_id}" holds {n|count:d} messages` – identisches Muster; behave lädt Step-Module alphabetisch → `gmail_steps` gewinnt, die Policy-Variante ist toter Code und die Gmail-Variante seedet **unabhängig vom Account-Typ** in `GmailState`); `AS:1368` (`contains {expected}` überlappt `contains exactly …`, `contains the literal string …`, `contains exactly one entry with:` – Funktioniert nur wegen Registrierungsreihenfolge, `AS:1372-1375`); globales `use_step_matcher` mehrfach umgeschaltet (`MS:577-610, 1492-1539`, `PS:563-638`, `AS:384-435`)
- **Korrekturauftrag:** Duplikat entfernen oder Account-Typ im Gmail-Step unterscheiden; Namen eindeutig machen (`contains exactly:` ohne Präfixüberlappung); Matcher pro Datei kapseln.

### M-08 — Betreff-Suche per Substring, ohne `to_wire`, still bei nicht selektierbarem Ordner
- **Referenz:** §4.3, §5.3
- **Fundstellen:** `policy/write_capabilities.feature:74, 120, 136`, `tool_surface/update_draft.feature:73, 184`, `reply`/`mime` Features ↔ `AS:509-552`
- **Nachweis:** IMAP `SEARCH SUBJECT` ist Substring-/ASCII-orientiert: „exactly one message with subject "Rechnung Entwurf"“ zählt auch „Rechnung Entwurf 2“. Der Folder wird nicht über `to_wire` (mUTF-7) angesprochen (`AS:526`, anders als `IF.folder_uids`), Nicht-ASCII-Betreffe werden nicht dekodiert; bei `status != "OK"` und `should_exist=False` wird ohne Fehler zurückgekehrt (`AS:528-530`), ein falsch geschriebener Ordnername macht „does not contain …“ immer grün.
- **Korrekturauftrag:** Auf `find_uid_by_decoded_subject`-Zählung (exakte Gleichheit) umstellen; nicht existierender Ordner → Fehler außer bei explizit erwarteter Abwesenheit.

### M-09 — Feste Sleeps ohne Bedingung
- **Referenz:** §4.4
- **Fundstellen:** `MS:790` (`_time.sleep(0.4)` nach SIGHUP), `OS:74` (`time.sleep(1)`), `PS:3511-3512` (`time.sleep(0.5)` nach Bootstrap-Start)
- **Nachweis:** Reload-Wirksamkeit/Prozess-Bereitschaft wird nicht beobachtet, sondern geschätzt (flaky bei langsamen CI-Hosts, falsch-grün bei schnellen nicht möglich, aber falsch-rot).
- **Korrekturauftrag:** Auf Bedingung pollen (Audit-Record `policy_reload`, Bootstrap-Prompt auf stdout, Prozess-Zustand).

### M-10 — `MCPClient`: blockierendes `readline()` ohne echtes Timeout
- **Referenz:** §4.4
- **Fundstellen:** `MC:269-290` (`timeout=10.0` wird nur zwischen Zeilen geprüft), `MC:182-194`
- **Nachweis:** Antwortet der Server nicht, blockiert `readline()` unbegrenzt; der `deadline` hat keine Wirkung. Das Harness hängt statt mit Diagnose zu scheitern.
- **Korrekturauftrag:** `select`/Thread-Reader mit Queue + `timeout`, bei Timeout Server-stderr in die Fehlermeldung.

### M-11 — `AuditReader`: Wallclock-„today“, stilles Überspringen defekter Zeilen
- **Referenz:** §4.1, §4.4, §5.3
- **Fundstellen:** `AR:64-66` (`records_today`), `AR:123-127` (`except JSONDecodeError: continue`), `AR:97-100` (`date.fromisoformat` wirft bei Nicht-Datumsnamen wie `old.jsonl`), `AS:1710, 1783` (Wallclock), `AS:2112-2133` (`except ValueError: pass`), Nutzung in `AS:970-1068, 1132-1143, 2252-2323`
- **Nachweis:** Bei gepinnter Fake-Uhr (`IMAP_MCP_FAKE_NOW_UTC`) liegt die Audit-Datei unter einem anderen Datum; `records_today()` liest die falsche Datei → Positiv-Steps scheitern laut (ok), **Negativ-Steps** (`does NOT contain the literal string`, `AS:1132-1143`) laufen auf leerer Menge und bestehen. Ein beschädigter Record (Tamper, Teilschreibung) wird ohne Meldung aus der Kette entfernt.
- **Korrekturauftrag:** Datum aus Fake-Uhr/Szenario ableiten oder alle Dateien lesen; Parsefehler als Befund melden (`verify_chain` → `False`); Negativ-Steps mit Positivkontrolle (Mindestanzahl Records).

### M-12 — „Identity is immutable“: Then prüft nach Re-Handshake mit Originaldaten
- **Referenz:** §4.3, §13.2 P1
- **Fundstellen:** `auth/caller_authentication.feature:77-82` ↔ `MS:957-1031`
- **Nachweis:** `a subsequent get_caller_identity still returns …` führt zuerst `client.initialize(initial_caller, bearer)` mit den **Original-Credentials** aus (`MS:1021`) und ruft danach `get_caller_identity` – in einem zustandslosen HTTP-Modus (Kommentar `MS:1020`: „Stateless HTTP — re-handshake“) ist das Ergebnis per Konstruktion `invoice-agent`; der Step kann die behauptete Session-Unveränderlichkeit nicht widerlegen. Außerdem posten `MS:978` auf `/mcp` (ohne Slash), der Client nutzt `/mcp/` (`MC:445`), `200/202` gelten als Erfolg, Fehlertext aus `payload.get("error") or payload.get("message")` (Schema-Raten).
- **Korrekturauftrag:** Feature auf den tatsächlichen Vertrag (stateless: jeder Request authentifiziert separat) umformulieren oder Session-ID-basiertes Verhalten testen; Then ohne Re-Authentisierung.

### M-13 — Steps, die mehr versprechen, als sie prüfen
- **Referenz:** §2.2, §5.1, §13.2 P2
- **Fundstellen und Nachweis:**
  - `AS:3039-3055` „has field … matching the pattern" → Substring (`pattern not in actual`).
  - `AS:706-721` „matches the regex" → `re.match` (nur Anfangsanker).
  - `AS:2055-2067` „none of the responses contains any field whose value matches a record from the audit file“ → prüft nur auf die Schlüssel `"seq":`/`"prev_hash":`; `MS:712-724` schluckt Tool-Fehler (`except Exception: pass`) — `audit/audit_retention.feature:58-62`.
  - `AS:1835-1852` „all five records share the same tx_id“ → keine Zählung (`≥1` + ein distinktes `tx_id`) — `audit_log_format.feature:70`.
  - `AS:1673-1701` / `AS:2726-2754` „in this order“ / saga steps → filtern nur nach `tool == expected[0][0]` bzw. prüfen Menge ohne Reihenfolge/Anzahl.
  - `AS:2806-2826` „… with tool "audit_file_missing" and the expected filename“ → Dateiname wird nicht verglichen — `audit_retention.feature:75`.
  - `AS:2789-2803` führt `list_accounts` aus (Aktion im Then) und sucht die Magic-Strings `"Active audit file disappeared"` in stderr (Herkunft nicht im Feature) — `audit_retention.feature:74`.
  - `AS:1227-1241` „no cleartext“ → Heuristik (`@` oder `<` im Wert), `None` besteht.
- **Korrekturauftrag:** Jeden Step an seinen Namen anpassen (oder umbenennen) und die fehlenden Vergleiche ergänzen; Aktionen aus Then-Steps in When verschieben; Magic-Strings im Gherkin spezifizieren.

### M-14 — Magische Konstante `hidden_folders_count equals 3` (implizites INBOX)
- **Referenz:** §2.2, §13.2 P2
- **Fundstellen:** `tool_surface/list_folders_message_count.feature:46`
- **Nachweis:** Hintergrund listet `INBOX/Rechnungen`, `Banking`, `Archiv`; `3` ergibt sich nur durch den von Dovecot automatisch erzeugten `INBOX` (nicht im Szenario). Herkunft nicht sichtbar.
- **Korrekturauftrag:** `INBOX` im Background aufführen oder die Regel („alle nicht gewährten Ordner inkl. INBOX“) im Szenario benennen.

### M-15 — `find_uid_by_decoded_subject` liefert den ersten Treffer
- **Referenz:** §4.3
- **Fundstellen:** `IF:545-567`, genutzt in `AS:3179-3194, 3843-3875, 4095-4113`, `IU:216-224`
- **Nachweis:** Duplikate werden nie bemerkt; in Draft-Szenarien kompensiert ein vorgeschalteter `exactly 1 message with header …`-Step (gut), die Reply-Szenarien (`create_reply_draft.feature:122-125`) nicht.
- **Korrekturauftrag:** Schnittstelle um „genau ein Treffer“ erweitern (`find_unique_uid_by_decoded_subject`).

### M-16 — Reset verschluckt Fehler
- **Referenz:** §4.4, §6.2
- **Fundstellen:** `IF:225-228` (`except IMAP4.error: pass` beim Löschen von Ordnern), `IF:230-241` (`_empty_folder`: kein Statuscheck), `IF:386` (`conn.create` ohne Statusprüfung)
- **Nachweis:** Nicht löschbare Ordner/Nachrichten leaken in Folge-Szenarien (inkl. `hidden_folders_count`, vgl. Kommentare `IF:216-219`, `IF:665-670`) ohne Diagnose.
- **Korrekturauftrag:** Nach Reset prüfen, dass nur `INBOX` (leer) übrig ist; sonst `RuntimeError` mit Restbestand.

### M-17 — Generische Assertions mit versteckten Transformationen
- **Referenz:** §1.3, §5.1
- **Fundstellen:** `AS:76-97` (`folders`: Objekt→Pfad; `uids`: Hint→UID), `AS:1299-1322` (Sortierung = reihenfolge-blind bei Pagination, z. B. `search_blacklist_fast_path.feature:51`), `AS:1368-1408` (polymorphes `contains`: Liste/String/UID), `AS:3266-3284` (CRLF→LF, `rstrip("\n")` auf beiden Seiten), `AS:724-737` (`_table_value_matches`: Typ-Koerzierung aus Antworttyp)
- **Nachweis:** Der Docstring `AS:16-19` verspricht „we do not try to fix the JSON-parsing path heuristically“; diese Steps tun es. Pagination-Reihenfolge ist nicht verifizierbar.
- **Korrekturauftrag:** Reihenfolge-sensitive Variante (`equals exactly in order`), Normalisierungen im Feature sichtbar (z. B. Gherkin-Zeile „line endings normalized“) oder entfernen.

### M-18 — Account-Tabelle: Spalte `host` wird ignoriert, Instanz per Namens-Substring
- **Referenz:** §1.3, §13.2 P3
- **Fundstellen:** `auth/oauth2_bootstrap.feature:24-27`, `audit/audit_multi_instance.feature:92-94` ↔ `PS:179-213` (`if "gupta" in account_id or "osthues" in account_id or "gmail-archive" in account_id`)
- **Nachweis:** `host` (`imap.localhost-mock`) steht im Szenario, wird aber überschrieben; Instanz-Wahl hängt vom Namen (`gmail-archive`→`imap-a`, sonst `imap-b`); `auth_type` wird aus der **Anwesenheit** der Spalte `oauth_scope` abgeleitet.
- **Korrekturauftrag:** Feature-Spalte `host` als Alias aus der Harness-Tabelle (`IMAP_INSTANCES`) auflösen oder Spalte entfernen; Mapping explizit (`instance` Spalte).

### M-19 — Abwesenheitscheck ohne Positivkontrolle (Fast-Path)
- **Referenz:** §4.3, §4.4
- **Fundstellen:** `tool_surface/search_blacklist_fast_path.feature:39` ↔ `AS:2979-2997`
- **Nachweis:** `the audit log does not contain any entry with tool "fetch_envelope"` ist wahr, wenn das Audit-Verzeichnis leer oder noch nicht geschrieben ist (`search`-Record existiert evtl. noch nicht; kein Flush-Abgleich).
- **Korrekturauftrag:** Vorangestellt Then „the audit log contains an entry with tool "search"“ mit Poll/Timeout, danach Negativ.

---

## 6. NIEDRIG

- **N-01 — Nackte `assert`-Anweisungen** (entfernt unter `python -O`, §5.3): `GS:747-749`, `OS:85, 112, 127, 136-137, 142, 148-149, 155, 169, 240, 245, 263-265, 277`. → durch `raise AssertionError` ersetzen.
- **N-02 — Harness liest `server/pyproject.toml`** (`AS:2957-2976`) und die Server-venv-Metadaten (`TS:193-215`). `ENV:47-49` nennt als Ziel, dass kein Python-Level-Abhängigkeit zu `../server/` entsteht; Quelltext-Lesen verschiebt Wahrheit in den Produktions-Tree. → Version über `tools/call tool_surface_info`/`pip show` im Server-venv belegen oder Quelle im Feature nennen.
- **N-03 — Trace-Negativ-Witnesses ohne Flush-Abgleich:** `no span in the trace sink contains …` (`TS:382-392`) und `_every_span(…, 0, …)` (`TS:450-461`) pollen nicht; nach `within 10 seconds … span "tool.X"` sind Kinder-Spans i. d. R. vorher exportiert (BSP-Queue-Reihenfolge), aber nicht garantiert (`_TEST_BSP_SCHEDULE_DELAY_MS=1000`). → Negativ-Witness mit Quell-Span-Wait + `imap.*`-Kinder-Anzahl.
- **N-04 — `AI:140-148`:** `thread.join(timeout=60)` ohne `is_alive()`-Prüfung → hängende Threads bleiben unbemerkt; `failures`-Liste wird von mehreren Threads befüllt (CPython-atomar, aber nicht dokumentiert).
- **N-05 — Feature-interne Inkonsistenzen (zur Entscheidung durch den Menschen):** `transactions/message_rewrite_recovery.feature:9-11` zählt 2 Szenarien („Crash after APPEND: staged recovery completes DELETE“), enthält nur das Flags-Szenario (`:21-29`); `tool_surface/non_goal_rejection.feature:40-47` Titel „yields capability_missing“, Assertion `folder_hidden`; `auth/caller_authentication.feature:20` „covered 9 of 10“ (Timing-Szenario fehlt); `startup_saga_recovery.feature`, `health_probe.feature`, `projected_config_reload.feature` sind `@pending` und verwenden Steps, die in `bdd/features/steps/` **nicht** definiert sind (z. B. `…is present exactly once in "personal" folder …`, `the WAL contains no entries`, `the handshake succeeds within 2 seconds`, `an HTTP client makes GET /healthz …`, `the config directory is projected as a Kubernetes ConfigMap volume`).
- **N-06 — Fixture-Duplikate/Aliase:** mUTF-7-Encoder in `IF:26-59` dupliziert Server-Logik (Kommentar: „Mirrors imap_core._quote_mailbox“; unabhängig implementiert ist gut, dennoch ein zweiter Ort für die Regel); Account-Aliase `gmail-archive`/`ext-user` → `gupta`, `gmail-ronly` → `archive` (`IF:82-87`) teilen sich Mailboxen mit anderen Account-IDs; `to_wire` mutiert Ordnernamen automatisch (`IF:149-164`).
- **N-07 — Typblinde Vergleiche:** `AS:1036-1068` vergleicht `str(rec.get(k)) == v` (`None` ↔ `"None"`, `True` ↔ `"True"`).
- **N-08 — Harness-Infrastruktur:** `ENV:205-208` (`docker compose` mit `capture_output=True`; `CalledProcessError` ohne stderr), `ENV:160-171` (`except Exception: pass` beim Aufräumen), `PX:259-260, 501-502` (Log-/Session-Fehler lautlos).
- **N-09 — Rechte-Szenarien unter root:** `AI:267-286`, `PS:1729`, `MS` Audit-Mode-Steps – `chmod`-basierte Unwritable-/Read-only-Zustände sind für root wirkungslos; Testlauf als root würde die Szenarien `audit_multi_instance.feature:102-108`, `fetch_attachment_sink.feature:170-177` still falsifizieren. → Vorbedingung `os.geteuid() != 0` prüfen.
- **N-10 — Zugriff auf private Mock-Zustände:** `GS:304-312, 368-373` (`state._uid_maps`, `_uid_counters`) – koppelt Steps an Mock-Interna.
- **N-11 — Proxy-Semantik:** `PX:371-383` „delay“ leitet nie weiter (siehe M-05), `PX:342-353` Modus `close` terminiert Session ohne Log-Eintrag; `PX:259-260` `except OSError: pass` beim Log.
- **N-12 — Irreführende Step-Namen:** `GS:432-462` „a direct IMAP SEARCH on … for X-GM-MSGID“ liest `GmailState` in-process, kein IMAP; `GS:836-854` `the draft is stored in …` prüft nur „Ordner nicht leer“ (jede gesäte Nachricht erfüllt es); `GS:465-487` ignoriert `comment`; `IF:277-278` Docstring steht **nach** dem Code (kein echter Docstring).

---

## 7. Querschnittsempfehlungen an `code-entwickler-flash`

1. **Assertion-Hygiene-Gate:** CI-Check, der jeden Step ohne `raise AssertionError`/Vergleich oder mit `_ = (...)`-Muster meldet (Treffer: `AS:2338-2350`, `2492-2510`, `2695-2703`, `PS:891-919`, `1267-1272`, `2011-2019`, `GS:525-554`, `AS:3492-3502`).
2. **Positiv-Kontrolle für jeden Absenz-Witness:** Konvention „Absenz-Steps verlangen vorab eine Zählung > 0“.
3. **Then liest nur:** Verbot, in Then-Steps `call_tool`, `_ensure_mcp_client`, `_test_run_recovery` oder Dateioperationen auszuführen (Treffer: `AS:1427-1467, 2789-2803, 2829-2860`, `OS:247-265`).
4. **Seeding-Fixtures:** Pflichtspalten statt Defaults (`from`, `to`, `subject`, `date`, `message_id`, Anhangsliste); `AssertionError` bei fehlenden Spalten.
5. **Kanonische Tabellen ins Gherkin:** Reason-Codes, Metadaten-Mapping, Kettenformel/Canonical-JSON-Regel.
6. **Fehlerinjektion im Dependency-Mock statt im Server-Testmodus:** OAuth-Fehler/PKCE im `mock-oauth`, Delay/Close im Proxy mit Verb+Mailbox-Matcher.
7. **Testmodus entkoppeln:** `@test_hooks`-Tag; Kern-Features zusätzlich ohne Testmodus.

---

## 8. Szenario-Index (Schnellzugriff, nur betroffene Features)

| Feature | Befunde |
|---|---|
| `audit/audit_log_format.feature` | H-08, H-09, H-10, H-11, M-03, M-13, M-11 |
| `audit/audit_retention.feature` | H-10, H-12, M-13 |
| `audit/audit_multi_instance.feature` | H-17, M-18, N-09 |
| `auth/oauth2_bootstrap.feature` | H-13, H-14, H-17, M-18 |
| `auth/secret_store_backends.feature` | B-05, H-22 |
| `auth/caller_authentication.feature` | M-12 |
| `policy/policy_reload.feature` | B-04, H-06, H-15, M-09 |
| `policy/write_capabilities.feature` | H-26, M-08 |
| `providers/gmail_label_semantics.feature` | H-18, H-19 |
| `providers/imap_mailbox_names_utf8.feature` | H-20 |
| `tool_surface/mcp_tool_discovery.feature` | B-02, M-04 |
| `tool_surface/reason_code_contract.feature` | B-01, H-08, H-11, H-21, H-23 |
| `tool_surface/folder_path_contract.feature` | H-24 |
| `tool_surface/non_goal_rejection.feature` | H-25, H-26, N-05 |
| `tool_surface/bulk_mark_seen.feature` | H-01, H-16 |
| `tool_surface/bulk_mark_tagged.feature` | B-03, M-02 |
| `tool_surface/{add,replace,delete}_attachment.feature` | H-02, H-16, M-01 |
| `tool_surface/update_draft.feature` | M-01, M-08 |
| `tool_surface/search_blacklist_fast_path.feature` | M-17, M-19 |
| `tool_surface/list_folders_message_count.feature` | M-14 |
| `tool_surface/create_reply_draft.feature` | H-16, M-02 |
| `transactions/saga_crash_recovery.feature` | H-03, H-04, H-05, H-07 |
| `transactions/draft_revision_recovery.feature` | H-03 |
| `transactions/intra_account_move.feature` | H-07, M-05 |
| `transactions/cross_account_move_saga.feature` | M-05 |
| `transactions/message_rewrite_recovery.feature` | H-02, N-05 |
| `observability/tracing_payload_mode.feature` | N-03 |

---

## 9. Ergänzungsprüfung: Restliche 20 BDD-Features & Mocks

> **Zählung:** Der Auftrag sprach von 20 offenen Features; nach erneutem Abgleich mit `glob` waren es **31** (siehe Korrektur in Abschnitt 0). Alle 31 sind hier einzeln gegen ihre Steps gelesen; zusätzlich sämtliche bislang offenen Mock-Dateien. Die Überschrift bleibt wie beauftragt.
> Neue Befunde führen die Nummerierung fort (H-27…H-33, M-20…M-33, N-13…N-17). Abkürzungen wie in Abschnitt 0; zusätzlich `MG` = `bdd/mock-gmail/src/mock_gmail/`, `MO` = `bdd/mock-oauth/`.

### 9.1 Neue Befunde — HOCH

#### H-27 — „server date is pinned to "<date>"“ hängt `T12:00:00` an; mehrere erwartete Werte beruhen darauf
- **Referenz:** §13.2 P2/P3, §1.3
- **Fundstellen:** `tool_surface/search_criteria_and_pagination.feature:23, 131-147` (Zeilen `60s`/`30m`/`6h`: `date_in_window` 2026-05-07T11:59:30Z / 11:45:00Z / 08:00:00Z) ↔ `PS:165-169` (`f"{date}T12:00:00+00:00"`); ebenso `tool_surface/search_completeness.feature:49`, `tool_surface/list_messages.feature:21`, `tool_surface/default_scope_transparency.feature:17`, `policy/sender_rule_matcher.feature:17`, `providers/gmail_search_and_counts.feature:54`
- **Nachweis:** Das Feature nennt nur das Datum. Für die Duration-Einheiten `s`, `m`, `h` entscheidet aber die Uhrzeit der Server-Uhr, ob eine Nachricht „im Fenster“ liegt (11:59:30Z liegt nur dann 30 s vor „jetzt“, wenn „jetzt“ 12:00:00Z ist). Die Konstante `12:00:00` steht ausschließlich im Step-Code. Ein Leser kann die Erwartungen der drei Zeilen nicht nachrechnen; ändert jemand den Step (z. B. auf `00:00:00`), kippen die Zeilen ohne Änderung am Feature. (IMAP-`SINCE` kennt nur Tage – die Sekunden-/Minuten-/Stundenlogik ist reine Server-Fachlogik, gerade deshalb muss die Referenzzeit sichtbar sein.)
- **Korrekturauftrag:** Step auf volle Zeitstempel umstellen (`the server clock is pinned to "2026-05-07T12:00:00Z"`), Datumsvariante entfernen oder `AssertionError`, wenn eine Duration-Einheit < `d` ohne Uhrzeit getestet wird.

#### H-28 — „delay“/„close“-Fault-Mocks leiten den Befehl nie weiter: Duplikat-/Idempotenz-Risiken sind per Konstruktion unsichtbar
- **Referenz:** §7.2, §13.2 P1
- **Fundstellen:** `tool_surface/create_draft_error_visibility.feature:151-170, 172-197` (und aus Teil 1: `transactions/cross_account_move_saga.feature:73-89`) ↔ `PX:342-353` (`mode close`), `PX:371-383` (Delay: `asyncio.sleep` + `continue`, kein Forward), `PS:1196-1240`
- **Nachweis:** Bei „closes the connection after the next APPEND“ und „delays the next APPEND response by 45 seconds“ erreicht der APPEND den Upstream **nie**. Die Persistenz-Prüfungen `matched_total equals 0` (`:170`, `:197`) sind damit unabhängig vom Server-Verhalten wahr. Reale Fehlerfälle sind anders: bei einer Verbindungsunterbrechung nach vollständig gesendetem APPEND bzw. einer langsamen Antwort wird die Nachricht **trotzdem abgelegt**; genau dort entstehen Duplikate und genau darum spezifiziert die Saga „at-least-once“ plus Message-ID-Idempotenz. `cross_account_move_saga.feature:81` („exactly one result“ im Ziel nach Timeout+Retry) kann bei diesem Mock nie ein Duplikat sehen. Die Szenarien zeigen also Wunschverhalten (§7.2), nicht das reale.
- **Korrekturauftrag:** Proxy-Modi `delay` (Befehl **nach** der Verzögerung weiterleiten, Antwort verspätet liefern) und `close_after_forward` (Befehl inkl. Literal weiterleiten, Antwort verwerfen, Verbindung schließen) ergänzen; Feature-Then für `create_draft` im Timeout-Fall auf „höchstens eine Nachricht mit Subject X“ (nicht „0“) und für die Saga auf Duplikatfreiheit nach Recovery umstellen.

#### H-29 — `fetch_attachment`: Sink-Datei wird nie gegen die ursprünglichen Anhang-Bytes verglichen
- **Referenz:** §13.2 P1, §4.1
- **Fundstellen:** `tool_surface/fetch_attachment_content.feature:47-51` (nach Teil 1 als gut markiert; hier korrigiert), `policy/visibility_levels.feature:78-89`, `tool_surface/inline_attachment_detection.feature:16-26`, `tool_surface/fetch_attachment_sink.feature` (alle `exists in the sink directory`) ↔ `AS:3579-3614`, `AS:1244-1264`, `PS:3092-3109` (Quelle: `b"x" * size`)
- **Nachweis:** „sha256 of the file … equals the response field content_hash“ vergleicht zwei **vom Server erzeugte** Größen (geschriebene Datei vs. gemeldeter Hash). Die tatsächlich erwartete Wahrheit – die im Given gesäten Bytes (`b"x"*4096`, ein Muster, das nur im Step-Code steht) – wird in diesen Szenarien nicht verwendet. Ein Server, der 4096 Nullbytes (oder Teile eines anderen Parts) schreibt und deren Hash meldet, besteht. Nur `visibility_levels.feature:88` rekonstruiert die Bytes (`step_response_content_hash_matches`), und dann aus dem **vom Server gemeldeten** `size_bytes` (`AS:1255-1260`) sowie mit einem im Feature nicht erwähnten Füllmuster.
- **Korrekturauftrag:** Anhang-Inhalt im Gherkin als sichtbare Daten (z. B. Base64/ASCII-Text oder `bytes "x" repeated 4096 times`) vorgeben und im Then Datei-Bytes **direkt** damit vergleichen; Hash im Step aus den *Given*-Bytes berechnen, nicht aus der Response.

#### H-30 — „Apple Mail“-Struktur wird vom Fixture nicht erzeugt
- **Referenz:** §13.2 P3, §7.2, §3.3
- **Fundstellen:** `tool_surface/inline_attachment_detection.feature:1-6, 16-37` ↔ `PS:3175-3193` (`inline_attachments`), `IF:353-361` (`msg.add_attachment(..., disposition="inline")`)
- **Nachweis:** Header: Apple Mail „wraps PDF attachments inside multipart/alternative with Content-Disposition: inline“. Der Fixture-Builder erzeugt dagegen `multipart/mixed` (Text + Inline-Teil auf oberster Ebene). Die Regression, wegen der das Feature existiert (Parser findet den Inline-Part innerhalb von `multipart/alternative` nicht), wird so nicht reproduziert; ein Server, der nur Top-Level-Inline-Parts erkennt, besteht beide Szenarien. Zusätzlich beweist `the file named saved_to exists` (`:26`) nur Existenz, nicht Inhalt (siehe H-29).
- **Korrekturauftrag:** Verbatim-Nachricht als DocString (`the folder … holds a verbatim message:`, `PS:684-700`) mit der echten Apple-Mail-Struktur (multipart/alternative → text/plain + application/pdf inline) verwenden.

#### H-31 — `connection_reuse.feature`: Such-Szenario besteht für einen Server, der nichts tut
- **Referenz:** §4.3, §13.2 P1/P3, §4.4
- **Fundstellen:** `performance/connection_reuse.feature:26-36` ↔ `GS:710-740`, `AS:3547-3560`/`GS:743-749`, `MS:1455-1489`
- **Nachweis:** `search … newer_than 30d` prüft nur `decision ALLOW` und `received at most 4 connections`. Es gibt keine Untergrenze (`matched_visible == 20` oder `≥ 1 Verbindung`) und keine Prüfung der Ergebnismenge; ein Stub, der ALLOW mit leerer Liste und ohne IMAP-Zugriff liefert, besteht. Szenario-Titel: „opens at most **3** connections“, Assertion: **4** (`:26/:30`, `:32/:36`). Die Regel `from_domain=example.com → ENVELOPE` (`:24`) trifft nur wegen des im Step erzeugten, im Feature unsichtbaren Absenders `sender-{i}@example.com` (`GS:722-737`) – Instanz von H-16.
- **Korrekturauftrag:** Absender in der Seeding-Tabelle sichtbar machen (`holds 20 messages from "…@example.com"`), Ergebnis (`matched_visible`, `uids has length`) prüfen und die Verbindungsschranke an Titel und Wert angleichen (3 oder 4).

#### H-32 — mock-gmail ist in Authentifizierung, Zustandsautomat und Postfach-Anlage nachsichtiger als Gmail
- **Referenz:** §7.2
- **Fundstellen:** `MG/server.py:131-135` (LOGIN akzeptiert jedes Passwort; `GmailState.password` in `state.py:165` wird nie verwendet), `MG/server.py:100-120` (kein `AUTHENTICATE`, obwohl in `_CAPS_NOT_AUTHENTICATED` `AUTH=PLAIN` beworben, `MO/README.md:117-123` „accepts XOAUTH2 … introspection“), `MG/server.py:299, 309, 389, 517, 538, 557` (`self._selected_folder or "INBOX"`: SEARCH/FETCH/STORE/COPY/MOVE/EXPUNGE ohne SELECT oder nach fehlgeschlagenem SELECT arbeiten stillschweigend auf INBOX; kein Authenticated-/Selected-State-Check), `MG/server.py:214-215` (CREATE immer OK, wirkungslos), `MG/server.py:236-240, 544-553, 563-576` (APPEND/COPY/MOVE in ein nicht existierendes Postfach legen es implizit an; Gmail antwortet `NO [TRYCREATE]`), `MG/server.py:253-254` (APPEND nach „All Mail“ vergibt erfundenes Label `\Inbox`)
- **Betroffene Features (Auswahl):** alle Gmail-Szenarien, u. a. `providers/gmail_draft_lifecycle.feature`, `providers/localized_gmail_resolution.feature`, `performance/connection_pool*.feature`, `tool_surface/bulk_mark_seen.feature`
- **Nachweis:** `providers/gmail_draft_lifecycle.feature:29-32` hält selbst fest, dass „the mock had been more forgiving than Gmail on each point, which is why the suite stayed green“. Die hier genannten Punkte sind gleichartige, noch offene Nachsichtigkeiten: Ein Server, der Credentials falsch sendet, nicht selektiert oder in ein Tippfehler-Postfach schreibt, bleibt auf dem Mock grün. XOAUTH2 – der Produktionspfad für Gmail (`connection_steps.py:69-74`) – wird gar nicht ausgeführt (`connection_pool.feature:31-33`: „the mock speaks LOGIN only“).
- **Korrekturauftrag:** (a) LOGIN gegen `state.password` prüfen und `NO [AUTHENTICATIONFAILED]` liefern, `AUTHENTICATE XOAUTH2` implementieren (Bearer gegen Mock-OAuth validieren, README-Zusage), (b) Zustandsautomat (`BAD [CLIENTBUG]` bei Befehlen außerhalb des zulässigen Zustands, kein INBOX-Fallback), (c) CREATE/APPEND/COPY/MOVE gegen existierende Postfächer prüfen (`NO [TRYCREATE]`, `NO [ALREADYEXISTS]`), (d) APPEND nach All Mail ohne erfundenes Label.

#### H-33 — mock-oauth besteht aus Stubs; Validierung deckt weder Refresh, PKCE noch Fehler ab
- **Referenz:** §7.2, §13.2 P1
- **Fundstellen:** `MO/src/mock_oauth/client.py:17-31` (`reset()` und `prime_consent()` sind `pass`), `MO/README.md:82-98` (geplant: `prime_error()`, `set_token_lifetime()`, `exchange_count()` – nicht vorhanden), `MO/config/oauth-mock.yaml:1` (`{}`), `MO/tests/test_validation.py:11-61` (nur Default-Authorization-Code-Flow), `OS:8-22, 76-129` (BDD nutzt den Wrapper nicht, sondern `httpx` auf `/default/debugger/requests`)
- **Nachweis:** README („including refresh and introspection“, Zeile 69-71) verspricht eine Validierung, die der einzige Test nicht durchführt: kein Refresh-Token-Grant, kein PKCE-Mismatch, kein `invalid_grant`, kein Introspect. Die Steuer-Operationen, auf die die BDD-Givens warten würden (Fehler/Lifetime/Consent primen), sind nicht implementiert; die Szenarien weichen deshalb auf Server-Testhooks aus (H-13). Das erklärt die Lücke, ist aber ein eigener Befund: Der Mock kann das in `oauth2_bootstrap.feature:56-94` Behauptete nicht simulieren, und seine Konformität mit diesen Fällen ist nicht nachgewiesen.
- **Korrekturauftrag:** Wrapper-API wirklich implementieren (Fehler-/Lifetime-/PKCE-Steuerung über die vom navikt-Mock tatsächlich angebotenen Mittel, ggf. `JSON_CONFIG_PATH` mit `requestMappings`), Validierungstests für Refresh, `invalid_grant`, PKCE-Mismatch ergänzen, BDD-Steps darauf umstellen; README an den Ist-Stand anpassen.

### 9.2 Neue Befunde — MITTEL

- **M-20 — Flush-on-shutdown-Witness kann durch periodischen Export bestehen.** `observability/tracing_spans.feature:141-152` ↔ `TS:28-32`, `MS:2060-2081`. `OTEL_BSP_SCHEDULE_DELAY=1000` ms: Braucht der Server zum Beenden (z. B. Pool-LOGOUT) länger als eine Sekunde – das Szenario erlaubt 5 s –, exportiert der Batch-Prozessor die Spans regulär, auch ohne Shutdown-Flush. → Verzögerung für diese beiden Szenarien deutlich über die Beendigungsdauer setzen (z. B. 60 s), damit nur der Flush liefern kann.
- **M-21 — Pool-Szenarien belegen ihre eigene Regel nicht.** `performance/connection_pool.feature:102-108` (Regel „NOOP vor Wiederverwendung“, Then nur `at most 2 connections`; NOOP wird nicht im Command-Log geprüft), `:110-113` („bounded by max_size“: `peak > n` scheitert nur nach oben, `peak ≥ 2` wird nicht verlangt – ein serieller Server besteht trivial; `connection_steps.py:200-210`), `:87-98` („selects first“: nur Teilfolge `SELECT "INBOX"/SELECT "[Gmail]/Drafts"/SELECT "INBOX"`, Bezug zum anschließenden Arbeitsbefehl nicht geprüft; Token-Substring-Match `AS:2379-2425`). → Command-Log-Zeile `NOOP` vor dem zweiten Aufruf verlangen; Parallelität belegen (Mock verzögert FETCH, `peak ≥ 2` und `≤ 4`); SELECT unmittelbar vor dem Arbeitsbefehl prüfen.
- **M-22 — Host-Zeitzonenabhängigkeit des Fixtures.** `IF:377-381` (`imaplib.Time2Internaldate(parsed_date.timestamp())` formatiert in **lokaler** Zeitzone) ↔ `MG/search.py:180-184` (`internal_day` = Tag im mitgegebenen Offset, RFC-konform). `providers/gmail_search_and_counts.feature:64-75, 89-93, 114-115`: Nachricht 901 (`2026-10-30T07:00:00Z`) hat auf einem Host in UTC−8 die Internal Date `29-Oct 23:00 -0800`; `SINCE 30-Oct-2026` liefert dann `[904]` statt `[901, 904]`. Auf Dovecot dürfte das nicht auftreten (Vergleich in der Server-Zeitzone, nicht im Offset der Internal Date – ungeprüft, da `bdd/docker/**` nicht Teil der Prüfung ist), auf dem Mock schon. → Internal Date explizit in UTC formatieren (`'%d-%b-%Y %H:%M:%S +0000'`).
- **M-23 — „IMAP pre-filter“/„fast-path“ nur im Titel behauptet.** `tool_surface/search_criteria_and_pagination.feature:3-7, 37-127` („narrows IMAP search via SINCE/BEFORE/LARGER/SMALLER/FLAGGED“), `tool_surface/list_messages.feature:99-110` („Blacklist fast-path skips per-message fetch“; Then nur `contains 2 messages`, `filtered_out 0`). Die Ergebnismenge ist identisch bei Pre- und Post-Filterung; der IMAP-Command-Log-Kanal (`the IMAP command log for … contains …`, Proxy) steht bereit und wird nicht genutzt (nur `search_blacklist_fast_path.feature:39` belegt es, über Audit). → je Prädikat `SEARCH`-Argumente im Proxy-Log prüfen oder die Titel auf das Beobachtbare reduzieren (§1.2).
- **M-24 — Ordnungsvertrag unsichtbar.** `tool_surface/list_messages.feature:36-40, 80` („message 0 … equal …“, Offset 1 → `b@example.com`), `tool_surface/search_criteria_and_pagination.feature:231-242, 266-276` (`limit/offset`, Vergleich über `contains exactly` = sortiert, `AS:1299-1322`). Die Seeds sind nach Datum absteigend, nach UID aufsteigend angelegt; welche Sortierung die Seite bestimmt (UID aufsteigend), steht nirgends. → Sortierregel im Feature nennen und Reihenfolge-sensitiv prüfen (M-17).
- **M-25 — UID-Mapping unter festem Account-Schlüssel; stale `last_call_*`.** `GS:315-318, 383-397` tragen `message_uids[("scaratec-gmail", folder, uid)]` fest ein; `providers/gmail_draft_lifecycle.feature:75-99, 160-175` und `providers/localized_gmail_resolution.feature:115-119, 160-163` laufen auf `gupta-gmail-de`. Die Zuordnung greift nie; die Szenarien funktionieren nur, weil `lookup.get(key, uid)` auf den Hint zurückfällt und Hint = UID gesetzt ist (siehe M-06). Zusätzlich setzen `MS:1545-1584` (`list_messages`) `last_call_account/folder` nicht, `AS:601-612` nutzt daher bei `the response uids equals …` (`performance/connection_pool.feature:90-92`) ggf. Werte eines früheren `search`. → Account aus dem Aufruf übernehmen; fehlender Eintrag = Fehler.
- **M-26 — HTML-Fallback: Präferenz nur positiv geprüft.** `tool_surface/fetch_body_html_fallback.feature:34-42` (`text_body contains "Plaintext wins"`); ein Server, der beide Teile verkettet, besteht. → `text_body equals "Plaintext wins"` bzw. „does not contain "HTML loses"“. (Szenario 1, `:24-32`, ist in Ordnung; `has_attachment true` erzeugt hier bewusst Multipart über den versteckten `stub.bin`, siehe H-16.)
- **M-27 — Reply-Drafts: Persistenz und Struktur ungeprüft.** `tool_surface/draft_identity.feature:119-132` (`create_reply_draft` liefert Handle: nur Response, kein `X-SC-Draft-Id`-Header per IMAP – Step `AS:410-422` existiert); `tool_surface/create_draft_format.feature:179-193` (Kommentar `:180-181`: „quoted original is rendered as a blockquote in the HTML part“, Titel „alongside the quoted text“ – nur `multipart/alternative` und „Danke, passt.“ werden geprüft). → IMAP-Header-Zählung und `blockquote`/Zitat-Text im HTML- und im Text-Teil prüfen.
- **M-28 — Schwache oder vakuose Negativ-/Wertprüfungen.** (a) `policy/visibility_levels.feature:91-100` `visible_count is a non-negative integer` (0 genügt; der Wert ist aus Seed und Regel ableitbar), (b) `policy/whitelist_folder_mode.feature:109-122` Abwesenheit der **erfundenen** Felder `hidden_from`/`hidden_subjects`, (c) `tool_surface/response_transparency.feature:109-117` kein Leak von `overview-agent` – dieser Caller existiert im Szenario nicht, (d) `policy/blacklist_folder_mode.feature:109-123` „no cleartext“ nur im Top-Level-Feld `from` (`AS:1227-1241`; Leak unter `sender`, `args_summary` etc. bliebe unentdeckt). → exakten Wert/Schlüsselmenge asserten (`the response has exactly the keys: …`), Positivkontrolle durch existierenden Gegenstand, ganzen Record auf die Adresse durchsuchen.
- **M-29 — NFC-Normalisierung unausgeübt; Typ-Koerzierung im Glue.** `policy/sender_rule_matcher.feature:87-97` (Titel „after NFC normalization“, Daten `RECHNUNG 0001`/`Große Rechnung`: weder kombinierende Zeichen noch NFD-Formen – es wird nur Groß-/Kleinschreibung geprüft); `PS:547-556` (`_coerce`: `true/false`→bool, Ziffern→int) und `PS:3077-3081` wandeln Regelwerte im Step um (`policy/sender_rule_matcher.feature:25-48`). → NFD-Variante (`"Ru\u0308ckfrage"`-artig) in den Seed; Koerzierung explizit im Feature spezifizieren.
- **M-30 — Erwartungswerte hängen an unsichtbarer Prüfreihenfolge bzw. implizitem INBOX.** `tool_surface/response_transparency.feature:71-76` (Move auf verstecktes Ziel liefert `capability_missing`, in `non_goal_rejection.feature:40-47` dagegen `folder_hidden`; der Unterschied folgt der Reihenfolge Quell-Capability vor Ziel-Sichtbarkeit, nirgends genannt), `:43, :106` und `tool_surface/list_folders_message_count.feature:46` (`hidden_folders_count equals 3`, vgl. M-14; `policy/default_deny.feature:16-21` listet `INBOX` ausdrücklich und ist in Ordnung). → Reihenfolge im Feature (Scenario-Kommentar oder `Rule:`) festhalten; INBOX in Backgrounds nennen.
- **M-31 — Localized-Gmail: Quelle nach Cross-Account-Move ungeprüft; veraltetes Feature mit schwachen Thens.** `providers/localized_gmail_resolution.feature:151-167` (nur Command-Log + Ziel; Entfernung aus `INBOX` fehlt, vgl. `gmail_label_semantics.feature:146`), `providers/localized_gmail_folders.feature:23-76` (kein §4.5-Header; Szenario 1 prüft `the draft is stored in …` = beliebige Nachricht im Ordner, `GS:836-854`; Szenario 3 nur `ALLOW`; inhaltlich von `localized_gmail_resolution.feature` überholt, §12 „nicht redundant“). → Quelle prüfen, Draft über Betreff/Header identifizieren, redundantes Feature entfernen oder vervollständigen.
- **M-32 — Erfolgspfade schreibender Tools nur über Response** (Erweiterung von H-02/M-01). `policy/draft_edit_capability.feature:110-131` (`update_draft` mit `draft_edit`: `revision 2`, `mechanism`; kein IMAP-Zustand, im Gegensatz zu `:137-177`), `policy/modify_message_capability.feature:29-39` (`add_attachment` Erfolg nur `result OK`, `mechanism`; DENY-Szenario `:17-27` ohne Prüfung, dass die Nachricht unverändert blieb). → je Szenario IMAP-Zustand prüfen (`AS:3779-3875`, `AS:390-406`).
- **M-33 — mock-gmail FETCH/ENVELOPE/Flag-Fidelity.** `MG/server.py:327-332, 343` (`"RFC822" in items` trifft auch `RFC822.SIZE`: Größenabfrage liefert den kompletten Literal samt falschem Schlüssel), `MG/server.py:310-351` (nicht unterstützte FETCH-Items, z. B. `BODY.PEEK[HEADER.FIELDS (…)]`, werden **stumm ignoriert**, während SEARCH unbekannte Schlüssel mit BAD ablehnt, `search.py:314`), `MG/server.py:358-386` (`RFC822`/`BODY[]` ohne PEEK setzen nie `\Seen`; Dovecot/Gmail tun es → ein Server, der ohne PEEK liest, wäre auf dem Mock unauffällig), `MG/server.py:739-750` (ENVELOPE aus Message-Attributen statt aus RFC822: Reply-To = From, Cc/Bcc/In-Reply-To immer `NIL`), `MG/server.py:170-171` (EXAMINE = READ-WRITE, STORE im EXAMINE-Zustand erlaubt), `MG/server.py:37-38` (beworben, aber nicht implementiert: `UNSELECT`, `IDLE`, `NAMESPACE`, `SASL-IR`, `AUTH=PLAIN`). → jeweils nachbilden oder mit `BAD` beantworten.

### 9.3 Neue Befunde — NIEDRIG

- **N-13 — Kopf-/Titel-/Zählabweichungen:** `performance/connection_reuse.feature:26, 32` („at most 3“) vs. `:30, 36` („4“); `tool_surface/create_draft_error_visibility.feature:19-39, 54, 67-68` sprechen von `imap_response`/`error.type`/`append_rejected`, die Szenarien (`:83, :110-111`) von `error.detail`; `providers/localized_gmail_folders.feature` ohne „Covered error layers“; `tool_surface/search_criteria_and_pagination.feature:9-20` zählt 33 Schichten (Kategorie „applied_scope“ überlappt).
- **N-14 — Teilweise Assertions:** `tool_surface/fetch_body_attachment_metadata.feature:31-36` (Anhang 1: `mime_type`/`size_bytes` ungeprüft, Header `:6-7` nennt vier Felder); `tool_surface/update_draft_errors.feature:55-105` (Validierungsablehnungen: nur `-32602`; Header `:7-8` behauptet „before any IMAP connection is opened“ ohne Proxy-Witness wie in `required_arguments.feature:63`).
- **N-15 — Server/Harness-Unterscheidung im Mock-Log über LOGIN-Namen:** `GS:970-989`, `GS:1201-1210` filtern `r.user != harness_user` (`test`). Bricht, sobald ein Mock-Gmail-Account mit explizitem `user: test` konfiguriert wird (Server-Verkehr wird dann ausgefiltert; `_server_command_log` meldet „keine Befehle“ und scheitert laut – kein Vakuum, aber falsch-rot).
- **N-16 — `Then the server refuses to start` ignoriert `timed_out`:** `PS:3300-3315` (`BootstrapResult.timed_out`, `server_bootstrap.py:29-31`); ein hängender, nach 5 s gekillter Server hat `returncode ≠ 0`. Schutz bieten nur die anschließenden Substring-Checks (`policy/sender_rule_matcher.feature:99-122`, `policy/blacklist_folder_mode.feature:75-107`, `policy/whitelist_folder_mode.feature:54-107`: alle prüfen die Fehlermeldung). → `timed_out` wie in `MS:2030-2034` als Fehler behandeln.
- **N-17 — Kleinigkeiten im Mock:** `MG/server.py:137-147` (LIST ignoriert Referenz/Pattern, listet `[Gmail]` nicht als `\Noselect`), `MG/server.py:168, 188` (UIDNEXT = größte sichtbare UID + 1, nicht der Zähler), `state.py:425-426` (UIDVALIDITY konstant 1), `MG/__main__.py:23` (Demo bindet 13143 wie das Harness), `MO/tests/test_validation.py` setzt `OAUTHLIB_INSECURE_TRANSPORT` global und benötigt einen laufenden Container (nicht Teil der BDD-Suite, kein CI-Hinweis).

### 9.4 Dateiweise Bestätigung (alle 31 restlichen Features)

| Feature | Ergebnis |
|---|---|
| `observability/tracing_spans.feature` | Sonst **befundfrei** (unabhängiger OTLP-Sink, Polling, Größenvergleich gegen Client-Messung `TS:314-321`, Flush-Szenarien mit Exit-Witness). Befund: M-20; Hinweis N-02 (Paketversion aus Server-venv). |
| `performance/connection_pool.feature` | M-21, M-25; H-32 (Mock-Nachsicht). Command-Log- und Connection-Witnesses (`connection_steps.py`) sind ansonsten sauber. |
| `performance/connection_pool_expiry.feature` | **Befundfrei** gegenüber Spezifikation (LOGOUT im Mock-Command-Log, exakt 2 AUTHENTICATE/LOGIN, Poll mit Deadline). Gilt H-32 (kein XOAUTH2) und N-15. |
| `performance/connection_reuse.feature` | H-31, N-13; B-03-Verwandtschaft: hier wird korrekt der Gmail-Zähler genutzt (Gmail-Account). |
| `tool_surface/required_arguments.feature` | **Befundfrei**: JSON-RPC-Fehler vom Draht, Proxy-Witness „0 Verbindungen“ (`connection_steps.py:155-181`, mit Prüfung, dass der Proxy läuft und verdrahtet ist); Meldungstexte sind im Feature-Kopf begründet. |
| `tool_surface/search_completeness.feature` | Erwartete UID-Mengen/Counts **nachgerechnet und konsistent** (z. B. `outside_scope_total` 5 bzw. 4); Befund: H-27 (Uhrzeit der Fixierung, hier ohne Auswirkung auf die Zeilen). Fehlerpfade (`-32602`, kein SEARCH im Proxy-Log) sauber. |
| `tool_surface/create_draft_format.feature` | Gut (direkter IMAP-FETCH, Blattpartien, Header-ASCII). Befund: M-27. |
| `tool_surface/draft_identity.feature` | Gut (Header-Occurrence-Count, `exactly one message with headers`). Befund: M-27 (Reply). |
| `tool_surface/update_draft_errors.feature` | Gut (IMAP-Zustand, Ordner-Isolation, WAL). `Then the WAL entry reaches status "needs_operator"` (`:178`) erbt H-03; Hinweis N-14. |
| `tool_surface/inline_attachment_detection.feature` | H-29, H-30. |
| `tool_surface/response_transparency.feature` | M-28(c), M-30. Übrige Szenarien (Envelope-Form, Poison-Marker mit Positivkontrolle `:146-155`) **in Ordnung**. |
| `tool_surface/search_criteria_and_pagination.feature` | H-27, M-23, M-24, N-13; Ergebnismengen nachgerechnet, konsistent; `size_bytes`-Werte sind weit gespreizt (H-04-Näherung unkritisch). |
| `tool_surface/list_messages.feature` | M-23, M-24, M-13-Substring (`message 0 … matching the pattern`, `:38`). |
| `tool_surface/list_attachments.feature` | **Befundfrei**: Größen/Index/Dateinamen stammen sichtbar aus dem Given (`:31-32`), Deny-Pfade (`:69-83`) geprüft. |
| `tool_surface/default_scope_transparency.feature` | **Befundfrei** (Datum reicht hier, kein Zeitfenster < 1 d; `applied_scope`-Symmetrie über `response_history`). H-27 ohne Auswirkung. |
| `tool_surface/create_draft_error_visibility.feature` | H-28, M-02 (Persistenz über `list_messages`), N-13. |
| `tool_surface/fetch_body_attachment_metadata.feature` | N-14; sonst **in Ordnung** (Größen aus Given). |
| `tool_surface/fetch_body_html_fallback.feature` | M-26. |
| `providers/gmail_search_and_counts.feature` | Vorbildlich bei Mock-Fidelity (direkte IMAP-Kommandos, Tabellen-abgeleitete Erwartungen, nachgerechnet: `SENTBEFORE/ON/SINCE/BEFORE`, `outside_scope_total 2`). Befund: M-22 (Host-TZ), H-27 (nur Datum). |
| `providers/gmail_draft_lifecycle.feature` | Stark (Command-Log, `X-GM-LABELS`-STORE-Log, Draft-Handle per IMAP). Befunde: M-25, H-32/M-33 (Mock-Nachsicht). Der Mock wird hier gegen eigene Beobachtungen, nicht gegen Gmail gepinnt – das Feature benennt das selbst (Kopf `:29-32`). |
| `providers/localized_gmail_resolution.feature` | M-25, M-31; zusätzlich Evidenz für H-20 (`:105`: `exactly one result` ohne Eindeutigkeit) und H-23 (`policy_alias_conflict` `:199, 204` fehlt in `CANONICAL_REASON_CODES`). |
| `providers/localized_gmail_folders.feature` | M-31, N-13. |
| `policy/draft_edit_capability.feature` | Stark (IMAP-Zustand in Isolations-/Unabhängigkeits-Szenarien). Befund: M-32; Deny-Szenarien `:66-109` ohne Zustandsprüfung (zulässig nach §13.4). |
| `policy/visibility_levels.feature` | M-28(a); H-29 (`:88`). Übrige Floors/Redactions **in Ordnung**; Seeds (Größen, Anhang, Header, Body) sichtbar im Background. |
| `policy/draft_consistency.feature` | **Befundfrei** (Szenario 2 `:61` per IMAP; Szenario 1 server-seitig, aber im Feature-Kopf so benannt). |
| `policy/sender_rule_matcher.feature` | M-29; Ergebnismengen nachgerechnet, konsistent; N-16 (Loader-Szenarien). |
| `policy/modify_message_capability.feature` | M-32. |
| `policy/flagged_predicate.feature` | **Befundfrei** (Flags sichtbar in den Tabellen, Regeln per `match`/`grant|cap`, AND-Kombination, `matched_rule_index`). Hinweis: Nachrichten-Datum = „jetzt“ (Default `PS:827-844`) ist hier unkritisch, weil alle Treffer im 7-Tage-Standardfenster liegen. |
| `policy/default_deny.feature` | **Befundfrei** (`hidden_folders_count 3` ist über `INBOX` im Background nachvollziehbar). |
| `policy/blacklist_folder_mode.feature` | M-28(d); sonst **in Ordnung** (Cap-Arithmetik nachgerechnet: strengste Cap gewinnt; Loader-Fehler mit Meldung). |
| `policy/whitelist_folder_mode.feature` | M-28(b); sonst **in Ordnung** (`matched_rule_index 1` aus Tabellenreihenfolge ableitbar). |

### 9.5 Dateiweise Bestätigung (Mocks & restliche Support-Dateien)

| Datei | Ergebnis |
|---|---|
| `MG/server.py` | H-32, M-33, N-17. Positiv: striktes `BAD` bei unparsbaren STORE-Labels und unbekannten SEARCH-Schlüsseln, Pre-/Post-Auth-CAPABILITY, `NO [NONEXISTENT]` bei SELECT/STATUS, Command- und Label-STORE-Log mit eigenem Parse-Ergebnis (gute §7.2-Praxis). |
| `MG/search.py` | **Befundfrei** gegenüber RFC 3501 §6.4.4 (BEFORE/ON/SINCE auf Internal Date, SENT* auf Date-Header-Tag, strikte Syntax, Gmail-Schlüssel); kleine Vereinfachung: `BODY` durchsucht Roh-Bytes ohne Transfer-Decoding (nicht szenario-relevant). |
| `MG/mime.py` | **Befundfrei** (BODYSTRUCTURE mit Disposition je Part, Großschreibung wie Gmail; Größen aus Roh-Payload). |
| `MG/__main__.py` | N-17. |
| `MG/__init__.py` | leer, **befundfrei**. |
| `MG/state.py` | in Teil 1 gelesen; keine zusätzlichen Befunde über N-17 hinaus (Label-/UID-Modell, EXCLUSIVE_LABELS, `reset()` vollständig außer UIDVALIDITY). |
| `MO/src/mock_oauth/client.py`, `MO/src/mock_oauth/__init__.py`, `MO/config/oauth-mock.yaml` | H-33. |
| `MO/tests/test_validation.py` | H-33, N-17. |
| `MO/README.md`, `MO/pyproject.toml` | H-33 (README-Zusagen vs. Ist-Stand); `pyproject.toml` ohne Befund. |
| `bdd/support/__init__.py` | **Befundfrei** (reiner Docstring; Behauptung „no imports from ../server/“ trifft zu, `AS:2957-2976`/`TS:193-215` lesen aber Server-Metadaten → N-02). |

Nicht Teil der Python-Prüfung (daher nicht bewertet): `bdd/docker/**` (Dovecot-Konfiguration/Compose). Dort liegende Annahmen (Special-Use-Flags, `auto=no`, XOAUTH2-Passdb) sind nur indirekt über `PS:104-129` und die OAuth-Features sichtbar.

---

*Ende des Berichts. Relevanz und Korrekturbedarf jedes Befunds entscheidet gemäß §13.3/§13.4 der Mensch; insbesondere sind B-01…B-05, H-01/H-02/H-03/H-13 sowie aus der Ergänzung H-28, H-29, H-32 und H-33 Kandidaten für sofortige Nacharbeit, da dort die Szenarien nicht (oder nicht unabhängig) belegen, was sie behaupten, bzw. die Mocks Wunschverhalten simulieren.*
