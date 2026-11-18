# Gherkin- und Spezifikationsabnahme `imap-mcp` 3.0.0 – Delta-Check (Runde 6)

- **Prüfer:** Gherkin-Prüfinstanz (Claude Opus 5.5, High)
- **Datum:** 2026-11-18
- **Bezug:** [`2026-11-18-gherkin-abnahme-opus-runde5.md`](2026-11-18-gherkin-abnahme-opus-runde5.md). Urteil dort: FREIGEGEBEN.
- **Umfang:** `usability/features/01`–`05` sowie der Abschnitt „What happens in one run“ / „Server variants“ in `usability/README.md`. Gelesen habe ich nur `.feature` und `.md`.

---

## 1. Variantenpolicy als Gherkin-Tabelle (`with variant policy requiring:`)

| Datei | Fundstelle | Ergebnis |
|---|---|---|
| `01_draft_format_decision.feature` | `:25-27` | ✓ `[Gmail]/Drafts` blacklist FULL mit `draft_append`. Das deckt den idealen Pfad mit einem Aufruf (`:29`, `create_draft`) vollständig ab. |
| `02_draft_revision_and_discard.feature` | `:22-29` | ✓ Je Variante (`fixed`, `format-hint`, `today`) stehen `[Gmail]/Drafts` und `[Gmail]/Trash` mit allen vier relevanten Fähigkeiten in der Tabelle. Damit ist N-R10 vollständig erledigt. `today` hat `draft_edit=false`, aber `move_out` und `accept_incoming` auf Trash. Der ideale Pfad „create, create, discard“ für `today` (`:34`) ist also nach ADR 0063 §3 ausführbar: Drafts-Rolle und `move_out` → Trash existiert → `accept_incoming`. Die Fluss-Monotonie ist gewahrt, weil Trash auf METADATA steht und damit unter FULL bleibt (ADR 0045). Die Tabelle stimmt mit `usability/README.md:233-236` überein. |
| `03_invoice_search.feature` | `:40-44` | ✓ INBOX, `INBOX/Rechnungseingänge` und `[Gmail]/Sent Mail` stehen jeweils auf blacklist FULL. Das deckt S1 (über zwei Ordner) und den Sent-Mail-Fall mit `to_address` ab. Die Ground Truth (sechs Rechnungen, Ausschluss von 1004–1006) lässt sich ohne versteckte Stammdaten herleiten. |
| `04_filing_and_attachments.feature` | `:28-31` | ✓ INBOX hat `move_out`, `Rechnungseingänge` hat `accept_incoming`, beide FULL, also gibt es keine Eskalation. A1 (`move` für 1001 und 1002) und A2 (`fetch_attachment` bei FULL) sind ausführbar. |

Durch die Umstellung von Prosa auf eine Tabelle trennen die Backgrounds jetzt sauber zwischen Stammdaten (Policy je Variante) und Bewegungsdaten (Mailbox-Seeds und Tasks) (§2.4). Die Policy wird zudem transparent als Tabelle dargestellt (§3).

## 2. Owner-Adresse (N-G04)

| Datei | Fundstelle | Ergebnis |
|---|---|---|
| `03_invoice_search.feature` | `:17-18` | ✓ Eingehende Zeilen sind an `owner@de-gmail.example` adressiert, „to: …“-Zeilen werden von `owner@de-gmail.example` gesendet (Zeile 3001). |
| `04_filing_and_attachments.feature` | `:14-15` | ✓ |
| `05_policy_transparency.feature` | `:16-17` | ✓ Diese Datei nutzt weiterhin die eigene Szenario-Policy (`with a policy that grants:`, `:27-30`), wie in `usability/README.md:211` für P1/P2 vorgesehen. |

**N-G04 ist erledigt.**

## 3. Hinweis (NIEDRIG, nicht freigaberelevant)

| ID | Fundstelle | Hinweis |
|---|---|---|
| N-I01 | `usability/README.md:209-211` und `:233-236` | Der Step „with variant policy requiring:“ macht die Tabelle zu einer Anforderung an die Fixture in `fixtures/policies/`. Ob das eine Mindestmenge oder eine exakte Gleichheit bedeutet, und wie der Runner reagiert, wenn eine Fixture abweicht (z. B. Exit 2 wie bei anderen Konfigurationsfehlern, `:196-200`), sagt die README nicht. Ein Satz würde genügen, z. B. „The runner asserts that the variant fixture grants exactly the rows of the scenario's `variant policy requiring` table and exits 2 otherwise.“ |

Aus Runde 5 sind N-H01 bis N-H03 und N-G05 unverändert offen. Sie waren nicht Gegenstand dieser Runde.

---

## 4. Urteil

**FREIGEGEBEN: 0 BLOCKER, 0 HOCH, 0 MITTEL.**

Die Variantenpolicies in `01`–`04` sind jetzt explizite, vollständige und mit der README übereinstimmende Gherkin-Tabellen, und die idealen Pfade sind je Variante ausführbar. Die Owner-Adresse `owner@de-gmail.example` ist in `03`–`05` sichtbar. Weitere Nacharbeit ist nicht erforderlich.
