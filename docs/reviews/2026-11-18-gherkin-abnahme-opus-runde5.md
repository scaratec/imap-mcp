# Gherkin- und Spezifikationsabnahme `imap-mcp` 3.0.0 – Delta-Check (Runde 5)

- **Prüfer:** Gherkin-Prüfinstanz (Claude Opus 5.5, High)
- **Datum:** 2026-11-18
- **Bezug:** [`2026-11-17-gherkin-abnahme-opus-runde4.md`](2026-11-17-gherkin-abnahme-opus-runde4.md). Urteil dort: FREIGEGEBEN, 5 NIEDRIG.
- **Umfang:** Nur die nachgezogenen Stellen. Gelesen habe ich ausschließlich `.md` und `.feature`.

---

## 1. Delta-Prüfung

| # | Fundstelle | Ergebnis |
|---|---|---|
| 1 | `policy/whitelist_folder_mode.feature:88` | ✓ `startup error indicates "YAML"` unterscheidet einen Syntaxfehler jetzt von den semantischen Ablehnungen (`:90ff.`). |
| 2 | `policy/visibility_levels.feature:11,13,25,109-121` | ✓ Der Seed `X-Hornbach-Order: 4711` ist im Background sichtbar (`:25`). Das Positivszenario `fetch_headers` auf HEADERS prüft den Header-Wert, `redacted_fields ["body","attachments"]` und die Abwesenheit des Body-Texts (Positivkontrolle für den Body: `:129`). Header nachgezählt: 6 + 5 + 1 = **12** (Szenarien `:30`, `:38`, 3 Outline-Zeilen, `:64`; `:72`, 2 Outline-Zeilen, `:109`, `:122`; `:134`). ✓ |
| 3 | `transactions/startup_saga_recovery.feature:9-11,76` | ✓ Die Hash-Chain-Regel stimmt wörtlich mit ADR 0048:38 und `audit_log_format.feature:14-17` überein. `within 10 seconds` bei `retry_count >= 1` macht die asynchrone Recovery deterministisch beobachtbar. |
| 4 | `tool_surface/response_transparency.feature:6,31` | ✓ Die Sortierregel deckt `folders equals ["Archiv", "INBOX/Rechnungen"]` (`:58`) ab. Mit `provider "imap-standard"` wird `semantics "imap-standard"` in describe_policy herleitbar. |
| 5 | `policy/non_interference_and_field_gates.feature:40`, `observability/tracing_spans.feature:104` | ✓ Welt B fügt die NONE-Nachrichten jetzt additiv hinzu („additional messages are seeded“), ohne Reset. Das ist die exakte Non-Interference-Aussage. Für `bulk_mark_tagged` ergibt sich `tagged_count 1` in beiden Welten konsistent aus ADR 0045 §1, weil idempotent getroffene Nachrichten mitzählen. Die Zweitkanal-Prüfungen auf uid 102 und 103 (`:46-47`) bleiben bestehen. |
| 6 | `tool_surface/list_attachments.feature:92`, `create_reply_draft.feature:248` | ✓ Die Abwesenheit von uid 9999 bzw. 99999 ist jetzt als Vorbedingung sichtbar. Dadurch ist die opake Gleichheit zwischen `not_found` für eine NONE-Nachricht und für eine fehlende UID sauber begründet. |
| 7a | `audit/audit_log_format.feature:15` | ✓ „lowercase hex“ ergänzt (N-G03 erledigt). |
| 7b | `policy/sender_rule_matcher.feature:7` | ✓ Die Normalisierung (Kleinschreibung, IDNA-ASCII, Entfernen abschließender Punkte) steht jetzt sichtbar im Feature-Text. Das stützt die Boundary-Szenarien. |
| 7c | `policy/write_visibility_and_flow_monotonicity.feature:6-9` | ✓ Die Ergebnisformen sind je Untergrenze explizit. `mark_seen` unter METADATA liefert opakes `not_found` (ADR 0044 §2.5), `mark_tagged`, `move` und `copy` bei METADATA liefern `DENY visibility_below_ENVELOPE`. Das ist konsistent mit dem Header in `:21`. |
| 7d | `tool_surface/search_criteria_and_pagination.feature:7-8` | ✓ Der Header beschreibt Verhalten statt Mechanik. Rest siehe N-H01. |
| 7e | `docs/limitations/0024-…:41,46,64` | ✓ Alle Verweise zeigen auf ADR 0062, „22 `error.type`“ ist korrigiert (N-G01 erledigt). |
| 7f | `docs/error_path_analysis.md:42,132,500` | ✓ Die Überschrift von L16 nennt jetzt `0063`. Der Appendix nennt „eight fixture checks (12 expanded scenarios)“. L4.17 ist transparent als Querverweis gekennzeichnet (N-G02 erledigt). Die Summen 323 / 263 / 60 / 0 sind unverändert und arithmetisch korrekt. |

## 2. Verbleibende NIEDRIG-Hinweise (nicht freigaberelevant)

| ID | Fundstelle | Hinweis |
|---|---|---|
| N-H01 | `tool_surface/search_criteria_and_pagination.feature:84` | Der Szenariotitel nennt noch die Mechanik „via +1 day BEFORE padding“, während der Header (`:7-8`) bewusst nur Verhalten beschreibt. Vorschlag: „… and retains same-day messages older than 60 s“. |
| N-H02 | `policy/visibility_levels.feature:7` | Verweist auf das abgelöste ADR 0002. Richtig ist ADR 0043. |
| N-H03 | `docs/error_path_analysis.md:132` | L4.17 ist als Querverweis bewusst in Total und Deferred enthalten. Das ist transparent dokumentiert. Wer eine Bilanz ohne Duplikate möchte, entfernt die Zeile; dann gilt 322 / 263 / 59 / 0. |
| N-G04, N-G05 | `usability/features/03`–`05`; `audit/audit_retention.feature:7-9` | Aus Runde 4 offen und in dieser Runde nicht beauftragt: die literale Owner-Adresse und die explizite Bezugsbasis von `age_days`. |

---

## 3. Urteil

**FREIGEGEBEN (0 BLOCKER, 0 HOCH)** — 0 MITTEL, 5 NIEDRIG-Hinweise (N-H01–N-H03, N-G04, N-G05).

Alle nachgezogenen Stellen sind korrekt, nachrechenbar und konsistent mit den Accepted-ADRs. Neue Requirement Gaps oder Header-Abweichungen sind nicht entstanden. Die Hinweise können beliebig später erledigt werden; eine erneute Prüfung ist dafür nicht nötig.
