# Gherkin- und Spezifikationsabnahme `imap-mcp` 3.0.0 – Delta-Prüfung (Runde 4)

- **Prüfer:** Gherkin-Prüfinstanz (Claude Opus 5.5, High)
- **Datum:** 2026-11-17
- **Bezug:** [`2026-11-17-gherkin-abnahme-opus-runde3.md`](2026-11-17-gherkin-abnahme-opus-runde3.md). Urteil dort: FREIGEGEBEN, 4 NIEDRIG.
- **Umfang:** Die 7 polierten `.feature`-Dateien sowie die NIEDRIG-Punkte N-F01, N-F03 und N-F04.
- **Vorgehen:** Nur `.md` und `.feature`. Kein `server/`, kein `.py`.

---

## 1. Delta der 7 Feature-Dateien

| Datei | Änderung | Ergebnis |
|---|---|---|
| `audit/audit_log_format.feature:14-16` | Hash-Chain-Regel im Feature-Header | ✓ Entspricht ADR 0048:38: `"sha256:"` + SHA-256 über die rohe UTF-8-Zeile inklusive LF, Genesis `"sha256:"` + 64 Nullen. Konsistent mit `:82` (Regex `^sha256:[0-9a-f]{64}$`), `:179` (Manipulation an Record 2 → Bruch erst bei Record 3) und `:187` (Day-Roll über `final_hash`). Formulierung siehe N-G03. |
| `audit/audit_retention.feature:80` | Beispielzeile 1 → `2 \| 5 \| 7 \| 2026-04-20 \| … \| hot_sealed` | ✓ Nachgerechnet, siehe 1.1. Alle Seed-Dateien liegen vor dem aktiven Tag 2026-04-21. Zeile 1 prüft jetzt die inklusive Grenze `age_days == hot_days`. Das ist echte Grenzvarianz. |
| `auth/caller_authentication.feature:61-64` | Aktion aus dem `Then` in ein `When` verschoben | ✓ When/Then ist sauber getrennt, wie im HTTP-Szenario `:97-100`. |
| `transactions/saga_crash_recovery.feature:106-107` | `to` und `date` sichtbar | ✓ Beide Kopien haben vollständige, identische Hüllen. Sie unterscheiden sich nur hinter Offset 4096 (`AUTHENTIC-TAIL-119EUR` gegenüber `FORGED-TAIL-000000EUR`). Die Kollisionsprämisse ist damit vollständig sichtbar. |
| `usability/features/03_invoice_search.feature:17-18` | Tabellenregel für Uhrzeit und Richtung | ✓ 08:00:00Z, eingehend an den Owner, „to: …“ vom Owner gesendet. Literale Owner-Adresse siehe N-G04. |
| `usability/features/04_filing_and_attachments.feature:14-15` | dito | ✓ |
| `usability/features/05_policy_transparency.feature:16-17` | dito | ✓ |

Keine §4.5-Summe der 7 Dateien hat sich geändert, und es ist kein Szenario hinzugekommen oder weggefallen. Die Header-Summen bleiben gültig: `audit_log_format` 20, `audit_retention` 11, `caller_authentication` 12, `saga_crash_recovery` 8.

### 1.1 Nachrechnung `audit_retention.feature:78-85`

`age_days` = neuer UTC-Tag nach dem Roll (2026-04-22) minus Basisdatum. Diese Bezugsbasis ergibt sich aus `:67` (2024-01-12 → 831 Tage). Die Regeln stehen in `:7-9`: hot `≤ hot_days`, warm `≤ delete_after_days`, sonst gelöscht.

| Zeile | Basisdatum | age_days | Schwellen (hot/delete) | Erwartet | Ergebnis |
|---|---|---:|---|---|---|
| 80 | 2026-04-20 | 2 | 2 / 7 | hot_sealed | ✓ (Grenze, inklusiv) |
| 81 | 2026-04-19 | 3 | 1 / 6 | warm | ✓ |
| 82 | 2026-04-14 | 8 | 1 / 6 | deleted | ✓ |
| 83 | 2026-01-23 | 89 | 90 / 365 | hot_sealed | ✓ |
| 84 | 2026-01-10 | 102 | 90 / 365 | warm | ✓ |
| 85 | 2025-04-01 | 386 | 90 / 365 | deleted | ✓ |

Für alle Zeilen gilt `hot + warm = delete`, wie ADR 0048:39 es verlangt. Szenario `:34-51` stimmt ebenfalls: 2025-12-20 hat 123 Tage, ist also älter als 90 und wird gzip; 2026-03-15 hat 38 Tage und bleibt plain.

## 2. NIEDRIG-Punkte aus Runde 3

| ID | Status | Nachweis / Rest |
|---|---|---|
| N-F01 | ✓ behoben | Mock-Fidelity wird jetzt getrennt ausgewiesen: `gmail_search_and_counts.feature:17-23` (7 SUT + 7 Fixture), `gmail_draft_lifecycle.feature:14-20` (5 + 4), `localized_gmail_resolution.feature:11-23` (15 + 1). Die SUT-Summen stimmen. |
| N-F03 | teilweise behoben | `0024:10` und `:45` verweisen jetzt auf ADR 0062. Rest siehe N-G01. |
| N-F04 | weitgehend behoben | Die Überschrift von L8 nennt jetzt ADR 0059 und ADR 0064 (`error_path_analysis.md:34`, `:196`). L4.17 ist als Querverweis-Duplikat markiert (`:132`). Rest siehe N-G02. |

## 3. Verbleibende NIEDRIG-Punkte (nicht freigaberelevant)

| ID | Fundstelle | Befund / Behebung |
|---|---|---|
| N-G01 | `docs/limitations/0024-…:41`, `:64` und `:46` | Zeilen 41 und 64 verweisen für die Registry noch auf ADR 0056; richtig ist **ADR 0062**. Zeile 46 nennt „21 `error.type` codes“; laut ADR 0062:19 sind es seit `internal_error` **22**. |
| N-G02 | `docs/error_path_analysis.md:132`, `:30`, `:42`, `:500` | Zeile 132: L4.17 ist mit „not double-counted“ beschriftet, wird aber in L4 unter Deferred (2) und damit in 323/60 mitgezählt. Diesen Widerspruch auflösen: entweder den Text anpassen oder die Zeile entfernen und die Summen neu rechnen (322/59). Zeile 500: Der Appendix spricht von „eight scenarios“, laut den Feature-Headern sind es expandiert 1 + 4 + 7 = 12, weil Check 6 eine Outline mit 5 Zeilen ist. Besser: „eight checks (12 expanded scenarios)“. Zeile 42: Die Überschrift von L16 nennt `0057`; für Drafts und Replies gilt jetzt `0063`. |
| N-G03 | `audit/audit_log_format.feature:15` | Präziser „`"sha256:" + lowercase hex(SHA-256(...))`“ schreiben, so wie in ADR 0048:38. Die Hex-Kodierung lässt sich derzeit nur über die Regex in `:82` erschließen. |
| N-G04 | `usability/features/03…:17-18`, `04…:14-15`, `05…:16-17` | Die Regel nennt „mailbox owner“, aber keine literale Owner-Adresse. Die Grades hängen nicht davon ab, aber für vollständige Ground Truth (ADR 0060) die Adresse einmal angeben, z. B. „owner = `<adresse>`“. |
| N-G05 | `audit/audit_retention.feature:7-9` | Die Bezugsbasis von `age_days` (neuer UTC-Tag nach dem Roll) ist nur aus `:67` (831 Tage) rückrechenbar. Ein Halbsatz im Header macht sie explizit: „age_days = new UTC date − file base date“. |

---

## 4. Urteil

**FREIGEGEBEN (0 BLOCKER, 0 HOCH)** — 0 MITTEL, 5 NIEDRIG (N-G01 bis N-G05).

- Alle 7 polierten Feature-Dateien sind korrekt, nachrechenbar und widerspruchsfrei zu ADR 0048, 0049, 0046 und 0060.
- Durch das Delta ist kein neuer Requirement Gap und kein Header-Fehler entstanden.
- Die verbleibenden Punkte betreffen nur Querverweise und Formulierungen in `.md` und Feature-Headern. Sie können mit dem 3.0.0-Commit oder danach erledigt werden; eine weitere Prüfung ist dafür nicht nötig.
