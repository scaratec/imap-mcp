# Sichtprüfung Runde 5 — zwei kleine CI-Runner-Korrekturen (Claude Sonnet 5.5)

- **Datum:** 2026-11-18
- **Maßstab:** `BDD_GUIDELINES_v1.8.0_DE.md`
- **Bezug:** `docs/reviews/2026-11-18-step-abnahme-sonnet-runde4.md` (Urteil: FREIGEGEBEN ohne Auflagen)
- **Grenzen eingehalten:** Kein Zugriff auf `server/` oder `secrets/`. Keine Änderung an `.py`/`.yaml`/`.feature`. Einzige Ausgabe ist dieser Bericht.
- **Methode:** Nur Lesen, kein Testlauf. Ob die beiden Szenarien in der CI grün laufen, habe ich nicht geprüft.

## 1. Befund

### 1.1 `_find_scrubbed_python` und `step_create_root_hook_recorder` — in Ordnung
- Fundstelle: `bdd/features/steps/assertion_steps.py:3194-3236`.
- `_find_scrubbed_python` probiert `/usr/bin/python3`, `/bin/python3` und zuletzt `sys.executable`. Jeder Kandidat muss eine ausführbare Datei sein und unter `env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}` `import sys; sys.exit(0)` mit Rückgabecode 0 schaffen (Timeout 2 s). Ein Python, das ohne `LD_LIBRARY_PATH` nicht startet (z. B. aus `actions/setup-python`), fällt dadurch heraus. Fängt nur `OSError` und `subprocess.SubprocessError`, kein blankes `except`. `os` ist in der Datei importiert (`:27`).
- `step_create_root_hook_recorder` schreibt `#!{py_interp}` als Shebang. Das Skript nutzt nur die Standardbibliothek (`sys`, `os`, `json`), braucht also kein Site-Packages.
- Das Skript schreibt weiter `argv` und die Namen der Umgebungsvariablen nach `out_path`. Die Prüfstufen der Features („environment keys do NOT include …“, `AS:3253-3270` ff.) bleiben unverändert und lesen dieselbe Datei. Fachlich ändert sich nichts, es ist reine Harness-Infrastruktur.
- Rest NIEDRIG (N5-01): Findet keiner der drei Kandidaten ein lauffähiges Python, fällt die Funktion still auf `sys.executable` zurück. Dann scheitert erst der Hook-Aufruf des Servers, und die Szenarien melden das über `… records 1 invocation …` (`AS:3253-3270`: „Output file … does not exist“). Das ist laut, nicht still. Eine direkte `AssertionError`-Meldung an der Stelle wäre klarer.

### 1.2 `secret_store_backends.feature:121-122` — in Ordnung
- Nach `Then the handshake succeeds` (`:120`) folgt `When the MCP client calls get_caller_identity on the established HTTP session` (`:121`) und `Then the response field caller_id equals "invoice-agent"` (`:122`). Erst danach kommt `the audit file does NOT contain any of the literal strings` (`:123-126`).
- Der Step für den Aufruf existiert (`deployment_steps.py:270-276`) und liest `context.mcp_http`, den die vorherigen HTTP-Startschritte der Szenarien dieses Features setzen. Er speichert die Antwort über `_store_result`.
- Fachlich ist der Aufruf die nötige Positivkontrolle für die Absenzprüfung (§4.3): Die Audit-Datei enthält damit mindestens einen Record. Seit der Politur N3-02 (`AS:2965-2980`) wirft der Step ohne `*.jsonl`-Datei. Ein reiner Handshake schreibt offenbar keine Datei, deshalb scheiterte das Szenario. Der `caller_id`-Wert `invoice-agent` steht wörtlich im Gherkin (`:119`), er ist nicht aus dem Server abgeleitet (§2.1 erfüllt).
- Es kommt kein Szenario hinzu, die Kopfzählung bleibt dadurch gleich. Die Kopf-Tabelle habe ich nicht erneut nachgerechnet.
- Anmerkung (N5-02, NIEDRIG, kein Handlungsbedarf): Das Fehlerpfad-Szenario mit falscher Passphrase (`:128-141`) hat die Positivkontrolle schon vor der Absenzprüfung (`the audit log contains an entry with tool "auth_failed" …`).

## 2. Nicht gelesen oder nicht geprüft
- Kein Lauf von `behave` oder in der CI. Ob die Szenarien mit `record_root.py` und das `gpg_file`-Szenario dort grün werden, ist nicht bestätigt.
- Der Feature-Kopf von `secret_store_backends.feature` (Zeilen 1-84) und die Zählung der Fehlerpfade sind nicht erneut gelesen. Die Änderung berührt nur ein Szenario.
- `server/` und `secrets/` waren gesperrt.

## 3. Gesamturteil

# FREIGEGEBEN ohne Auflagen

**0 BLOCKER · 0 HOCH · 0 MITTEL.** Beide Korrekturen sind Harness-Anpassungen ohne Änderung der fachlichen Aussage, die Absenzprüfung im `gpg_file`-Szenario ist durch den Aufruf sogar besser abgesichert. Zwei NIEDRIG-Anmerkungen (N5-01, N5-02) blockieren nichts. Der NIEDRIG-Altbestand aus Runde 3 und 4 ist unverändert.
