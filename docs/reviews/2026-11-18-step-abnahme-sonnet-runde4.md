# Finale Sichtprüfung Runde 4 — Gherkin- & Python-Step-Reviewer (Claude Sonnet 5.5)

- **Datum:** 2026-11-18
- **Maßstab:** `BDD_GUIDELINES_v1.8.0_DE.md`
- **Bezug:** `docs/reviews/2026-11-18-step-abnahme-sonnet-runde3.md`, Abschnitt 5 (zwei MITTEL-Auflagen, ein Gherkin-Punkt, empfohlene Polituren)
- **Grenzen eingehalten:** Kein Zugriff auf `server/` oder `secrets/`. Keine Änderung an `.py`/`.yaml`/`.feature`. Einzige Ausgabe ist dieser Bericht.
- **Methode:** Nur Lesen, kein Testlauf. Ob `pytest` oder `behave` grün sind, habe ich nicht geprüft. Die Tests belege ich nur über ihren Inhalt.
- Kürzel: `UR` = `usability/runner/run.py`, `UF` = `…/features.py`, `US` = `…/serverconf.py`, `UC` = `…/criteria.py`, `AS` = `bdd/features/steps/assertion_steps.py`, `TEST` = `usability/tests/test_mid_findings_t04_t09.py`.

---

## 0. Ergebnis

| Schweregrad | Anzahl |
|---|---|
| BLOCKER | 0 |
| HOCH | 0 |
| **MITTEL** | **0** |
| NIEDRIG | 4 neue (N4-01…N4-04), dazu der Altbestand aus Runde 3 |

**Gesamturteil: FREIGEGEBEN ohne Auflagen** (0 BLOCKER, 0 HOCH, 0 MITTEL). Beide MITTEL aus Runde 3 sind behoben, die Gherkin-Auflage ebenfalls, und die vier empfohlenen Polituren sind umgesetzt.

---

## 1. Die drei Punkte aus Abschnitt 5

### 1.1 R-10′-Rest — **BEHOBEN**
- `UR:71-79` (`_fake_now`): Ist `spec.server_date` gesetzt, wird es unverändert zurückgegeben. Es gibt keinen `T09:00:00Z`-Anhang mehr und keinen Vergleich mit der Matrix-Vorgabe. Enthält der String kein `T`, wirft die Funktion `ValueError("server_date must be a full ISO timestamp with time …")`. Ohne Feature-Datum gilt die Matrix-Vorgabe.
- Der Aufrufer `UR:181-195` setzt `IMAP_MCP_TEST_MODE=1` nur bei vorhandener Fake-Uhr, sonst entfernt er beide Variablen. Die Ausnahme ist im Kommentar mit ADR 0058/0060 begründet.
- `TEST:204-225` (`test_r10_fake_now_precedence_and_validation`) belegt drei Fälle: Feature-Uhr `2026-11-02T12:00:00Z` gewinnt gegen eine abweichende Matrix-Uhr, ohne Anhang. Der Nur-Datums-String `2026-11-02` wird mit `ValueError` abgelehnt. Ohne Feature-Datum gilt der Matrix-Wert.

### 1.2 T-09-Rest — **BEHOBEN**
- **Features:** `01_…:25-27`, `02_…:22-29`, `03_…:40-44` und `04_…:28-31` nutzen `with variant policy requiring:` mit Tabelle (Ordner, `mode`, `default`, Capabilities, bei `02` zusätzlich die Spalte `variant`). `05` hatte schon eine Policy-Tabelle (`a policy that grants:`). Die Prosa ist weg. Ihr alter Wortlaut würde jetzt als „unrecognised step“ am Parser scheitern, denn `UF:155-160` kennt nur noch die Tabellenform und die Standardvariante.
- **Parser:** `UF:223-256` (`_variant_policy_rows`) lehnt unbekannte Spalten ab und verlangt `folder`, `mode`, `default`. Capability-Zellen werden als Wahrheitswerte gelesen, leere Zellen bleiben ungeprüft.
- **Abgleich:** `US:38-73` (`_verify_variant_policy_requirements`) prüft je Zeile Ordner vorhanden, `mode`, `default` und jede genannte Capability gegen die Varianten-YAML. Bei jeder Abweichung wirft es `ValueError` mit dem erwarteten und dem gefundenen Wert. `policy_for` (`US:76-103`) ruft es immer auf, wenn Anforderungen da sind. `write_server_config` und `UR:166-168` reichen die Variante durch.
- **YAML gegen die Tabellen** von Hand nachgerechnet:
  - `fixed.yaml` und `today.yaml` erfüllen `01` (Drafts: `blacklist`/`FULL`/`draft_append=true`).
  - `02`: `fixed` und `format-hint` haben `draft_edit=true`, `move_out=true`, Trash `METADATA`, `accept_incoming=true`. `today` hat `draft_edit=false` (`today.yaml:31`).
  - `03`: INBOX, `INBOX/Rechnungseingänge`, Sent Mail sind `blacklist`/`FULL`.
  - `04`: INBOX `move_out=true`, `accept_incoming=false`. `INBOX/Rechnungseingänge` `move_out=false`, `accept_incoming=true`.
- **Tests:** `TEST:228-238` prüft alle echten Szenarien aus `FEATURES` gegen `today`, `fixed` und `format-hint`. `TEST:241-280` mutiert die YAML: `draft_edit` weg (`02`), `draft_append` weg (`01`), `default` von INBOX auf `METADATA` (`03`), `move_out` weg (`04`). Jeder Fall erwartet `ValueError` mit der konkreten Meldung. Damit sind die Tests nicht mehr an erfundene Beschreibungen gebunden.
- **Hauptagent-Auflage (Gherkin):** erfüllt, Tabellen statt Prosa in `02` und `03`, dazu `01` und `04`.

### 1.3 Polituren — **UMGESETZT**
| ID | Stand |
|---|---|
| N3-02 | `AS:2965-2980`: Der Step wirft jetzt, wenn kein `*.jsonl` im Audit-Verzeichnis liegt (`:2970-2971`), und wirft bei leerem Text. Damit ist der Pass bei leerem Verzeichnis weg. |
| N3-08 | `UC:259`: `shorter = first_body is not None and len(final) < len(first)`. Fehlt der erste Body, ist R1 nicht mehr `fulfilled`. |
| N3-09 | `UC:500-511`: Die „jede leere Liste“-Schleife am Ende ist entfernt. Leer zählt nur noch bei `policy_may_hide`, `window_and_policy_limited` oder fehlendem `completeness`. `UM:409-421` verhält sich entsprechend. |
| `OWNER_ADDRESS` | `usability/runner/fixtures.py:33` ist `owner@de-gmail.example`. In `test_fixtures.py` steht kein fest verdrahteter Besitzer. Andere Tests habe ich nicht auf die alte Adresse durchsucht (Abschnitt 3). |

---

## 2. Neue NIEDRIG-Befunde (kein Hindernis)

| ID | Fundstelle | Befund und Begründung |
|---|---|---|
| N4-01 | `UR:74` | Die Prüfung „vollständiger Zeitstempel“ ist nur `"T" in server_date`. Ein Wert wie `2026-11-02T` wäre durchgekommen. Das Feature-Muster `_DATE` (`UF:168`) lässt beliebigen Text zu. Genauer prüfen, z. B. mit `datetime.fromisoformat`. |
| N4-02 | `US:46` | Eine Zeile mit unbekanntem `variant`-Namen (Tippfehler wie `fixd`) wird still übersprungen, weil `req.variant not in (None, "*", variant)` zutrifft. Der Test `TEST:228-238` prüft alle drei echten Varianten und würde einen Tippfehler in einer Feature-Datei nicht melden. Unbekannte Variantennamen beim Parsen ablehnen. |
| N4-03 | `US:67-73` | Der Abgleich prüft nur, was die Tabelle nennt. Gewährt die YAML zusätzlich eine nicht genannte Capability (z. B. `copy_out` auf einem Ordner der Tabelle), meldet er nichts. Für die Aufgabe „Prosa an YAML binden“ ist das vertretbar, ein Vollabgleich wäre strenger. |
| N4-04 | `UR:191`, `AS` (`PS:142-149`) | Die Fake-Uhr wird mit `Z`-Suffix unverändert an den Server gegeben. Der BDD-Step wandelt `Z` in `+00:00`. Ob der Server beide Formen annimmt, kann ich ohne `server/` nicht sehen. |

---

## 3. Nicht gelesen oder nicht geprüft

- Kein Lauf von `pytest` oder `behave`. Ob `TEST` und die betroffenen Features grün sind, ist nicht bestätigt.
- `usability/runner/matrix.py` und die Matrix-Datei habe ich nicht gelesen (Vorgabe für `IMAP_MCP_FAKE_NOW_UTC`).
- `usability/runner/fixtures.py` nur Zeilen 1-80, `UR` nur Zeilen 55-114 und 160-199, `UF` Zeilen 25-344.
- Andere Testdateien (`test_measurements.py`, `test_grading.py`, `samples.py`) habe ich nicht auf die alte Besitzer-Adresse durchsucht.
- `server/` und `secrets/` waren gesperrt. Der Altbestand NIEDRIG aus Runde 3 (N3-01, N3-03 bis N3-07, N3-10 bis N3-13, Abschnitt 3.2) ist unverändert und nicht Teil dieser Sichtprüfung.

---

## 4. Gesamturteil

# FREIGEGEBEN ohne Auflagen

**0 BLOCKER · 0 HOCH · 0 MITTEL.** Alle 20 MITTEL-Auflagen aus den Berichten `…-runde2.md` und `…-restfeatures.md` sind nach Runde 3 und dieser Sichtprüfung erledigt. Offen sind nur NIEDRIG-Punkte (N3-xx, N4-01…N4-04 und der Altbestand); sie blockieren die Freigabe nicht. Die Aussage stützt sich auf gelesenen Code und gelesene Tests, nicht auf einen Testlauf.
