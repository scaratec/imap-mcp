# Gherkin- und Spezifikationsabnahme `imap-mcp` 3.0.0 – Abschlussprüfung (Runde 3)

- **Prüfer:** Gherkin-Prüfinstanz (Claude Opus 5.5, High)
- **Datum:** 2026-11-17
- **Bezug:** [`2026-11-17-gherkin-abnahme-opus-runde2.md`](2026-11-17-gherkin-abnahme-opus-runde2.md). Stand von Runde 2: 0 BLOCKER, 1 HOCH, 8 MITTEL, 15 NIEDRIG.
- **Vorgehen:** Ich habe nur `.md`- und `.feature`-Dateien gelesen. Produktivcode, Step-Code und `docs/contract/registry.yaml` habe ich nicht geöffnet.

---

## 1. H-R01: Eine Entscheidung pro ADR

| Prüfpunkt | Ergebnis |
|---|---|
| `0056` „Canonical Special-Use Folder Resolution“ (`:1`, `:16`) | ✓ Nur noch eine Entscheidung: UTF-8/mUTF-7-Grenze plus Auflösung der Special-Use-Aliase. Die Registry ist ausgelagert (`:37`). |
| `0062` „Closed Reason and Error Code Registry“ (neu) | ✓ Eine Entscheidung. Enthält 3 ALLOW-, 17 DENY- und **22** `error.type`-Codes, darunter `internal_error` mit dem festen `error.detail` „Internal server error“ (`:19-20`), sowie 20 Audit-Codes. Nennt 3 Alternativen und hat einen Auditability-Abschnitt. |
| `0057` „Immutable Received Mail“ (`:1`, `:17`) | ✓ Eine Entscheidung: Rewrite-Primitive werden abgeschafft, mit drei Invarianten. |
| `0063` „Structured Own-Draft Composition and Revision“ (neu) | ✓ Eine zusammenhängende Entscheidung zum Lebenszyklus eigener Drafts. Legt `\Draft \Seen` beim Anhängen fest (`:22`), die Change-Hash-Formel (`:36`) und die Reihenfolge der Discard-Gates (`:38`). Löst 0031, 0034 und 0035 ab. |
| `0059` „OAuth2 PKCE CLI Bootstrap“ (`:1`, `:17`) | ✓ Eine Entscheidung. Token-Lebenszyklus und Account-Health sind ausgelagert (`:37`). |
| `0064` „Durable Account Health and Token Lifecycle“ (neu) | ✓ Eine zusammenhängende Entscheidung zum Laufzeit-Lebenszyklus der Tokens. Löst 0010 ab und zieht LIM 0017 zurück. |
| Status-Zeilen | ✓ `0010` → 0064; `0031`, `0034`, `0035` → 0063 |
| `docs/adr/README.md` | ✓ Index `0000`–`0064` (65 Einträge). Titel und Status stimmen mit den Dateien überein. |
| Feature-Verweise | ✓ `reason_code_contract.feature:3` (0062, Tabelle mit 22 Einträgen in `:68-91`), `localized_gmail_resolution.feature:3` (0056), `update_draft.feature:13` und `update_draft_errors.feature:3` (0063) |

**H-R01 ist behoben.**

## 2. MITTEL-Restpunkte aus Runde 2

| ID | Status | Nachweis |
|---|---|---|
| M-R01 | ✓ | `ROADMAP.md:9-10` (0040–0064), `:24-40` (LIM 0001 superseded, 0013/0016 must-resolve/Resolved, 0019 Mitigated, 0024 aufgenommen), `:55-71` (aktuelle ADRs). `README.md:245`, `:263` (`IMAP_MCP_APPEND_TIMEOUT`), `:270`, `:275-277`. |
| M-R02 | ✓ | `whitelist_folder_mode.feature:58-76` ist jetzt eine Outline mit beiden Reihenfolgen. Header `:11-12` auf 5 + 4 = 9 nachgezogen. |
| M-R03 | ✓ | `bulk_mark_seen.feature:6-8` entspricht ADR 0045 §1: idempotente Treffer zählen mit. |
| M-R04 | ✓ | `update_draft_errors.feature:8-12` trennt `not_found` (unbekannt, fremd, unsigniert) von `draft_not_editable` (eigener Draft, Provenienz bricht). |
| M-R05 | ✓ | ADR 0062 legt den Caller-Envelope fest. `audit_log_format.feature:235-254` prüft `ALLOW`, `ERROR`, `internal_error`, die Abwesenheit des Exception-Texts in der Antwort **und** im Audit. Die Herkunft von `RuntimeError` steht sichtbar in `:236`. |
| M-R06 | ✓ | LIM 0013 und 0016, jeweils `:15-17`: Der Body-Intent ist jetzt `must-resolve` (umklassifiziert). LIM 0016 `:89` rendert jetzt die Überschrift `## Residual risk`. |
| M-R07 | ✓ | `tracing_spans.feature:97-103`: Positivkontrolle mit `visible 1`, `returned 1`, `has_more false`, `domain.outcome accepted` (laut ADR 0053:31). Die Attributmenge ist explizit benannt (`:103`, `:109`). Zusätzlich gibt es einen Leak-Check (`:110`). |
| M-R08 | ✓ | `draft_revision_recovery.feature:111-114`: Die Flags werden vor der Revision geprüft. Die Regel steht in ADR 0063:22. |

## 3. NIEDRIG-Punkte aus Runde 2 (Stichprobe)

Behoben sind:
- **N-R01:** Die Tippfehler in 0053 und 0060 sind korrigiert; „hammer“ steht jetzt kleingeschrieben in 0064:11.
- **N-R02:** 0060:9 spricht jetzt von „two“.
- **N-R04:** In `write_capabilities.feature:156-167` werden Deposit und das Zurückkopieren ausgeführt.
- **N-R05:** `mcp_tool_discovery.feature:10` verwendet `required_capabilities`.
- **N-R06:** Der Tombstone für `0011-search` ist wiederhergestellt.
- **N-R09:** Die ADR-Verweise in `localized_gmail_resolution.feature:3` und `tracing_payload_mode.feature:3` sind korrigiert.
- **N-R10:** `02_…feature:22` ist korrigiert.
- **N-R11:** `fetch_attachment_sink.feature:6-9` beschreibt den Mechanismus statt Systemaufrufe zu nennen.
- **N-R12:** `provenance…:70` ist korrigiert.
- **N-R13:** `update_draft_errors.feature:102` ist korrigiert.
- **N-R14:** `tracing_spans.feature:141` ist korrigiert.
- **N-R15:** LIM 0024:15 ist korrigiert.

Verbleibend, nicht freigaberelevant:

| ID | Fundstelle | Befund / Behebung |
|---|---|---|
| N-F01 | `gmail_search_and_counts.feature:18-20`, `gmail_draft_lifecycle.feature:15-18`, `localized_gmail_resolution.feature:12` | N-R07 ist **nicht** umgesetzt. Die Header zählen Mock-Fidelity weiterhin als „Covered error layers“, obwohl `error_path_analysis.md` diese Fälle ausdrücklich nicht mitzählt. Getrennt als „Fixture fidelity (not SUT, not counted)“ ausweisen. |
| N-F02 | Dateinamen `0056-…-and-code-registry.md`, `0057-…-and-structured-drafts.md`, `0059-…-and-durable-account-health.md`, außerdem `0048`, `0053` | N-R03 (in Runde 3 nicht beauftragt). Nach der Aufteilung beschreiben die Dateinamen Inhalte, die jetzt in 0062, 0063 und 0064 stehen. Vor dem Tag umbenennen und die Links anpassen. |
| N-F03 | `docs/limitations/0024-…:10`, `:41` | Verweist für die Registry und den Wire-Vertrag noch auf ADR 0056. Richtig ist **ADR 0062**. |
| N-F04 | `docs/error_path_analysis.md:196` (Überschrift L8), `:132` (L4.17) | Die Überschrift von L8 nennt nur ADR 0059, obwohl L8.6, L8.7, L8.10 und L8.11 nach ADR 0064 gehören. L4.17 ist als „deferred A“ eingestuft, verweist aber auf eine BDD-Abdeckung (L9.29, L12.9). Das ist ein Duplikat und kein Unit-Test-Fall. Als Duplikat kennzeichnen. Die Summen bleiben gleich. |

---

## 4. Finales Urteil

**FREIGEGEBEN (0 BLOCKER, 0 HOCH)** — 0 MITTEL, 4 NIEDRIG (N-F01 bis N-F04).

- Alle 15 HOCH- und 21 MITTEL-Befunde aus Runde 1 sowie der HOCH-Restpunkt und die 8 MITTEL-Restpunkte aus Runde 2 sind nachweislich behoben.
- Die `.feature`-Dateien sind als fachliche Single Source of Truth widerspruchsfrei zu den Accepted-ADRs 0040–0064. Ihre Werte lassen sich nachrechnen, und die sicherheitskritischen Invarianten sind über unabhängige Zweitkanäle mit Positivkontrolle belegt.
- Die vier NIEDRIG-Punkte betreffen nur Header-Ausweisung, Dateinamen und Querverweise. Sie können mit dem 3.0.0-Commit oder danach erledigt werden; eine erneute Prüfung ist dafür nicht nötig.
