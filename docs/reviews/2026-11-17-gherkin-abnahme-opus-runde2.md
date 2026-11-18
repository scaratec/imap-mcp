# Gherkin- und Spezifikationsabnahme `imap-mcp` 3.0.0 – Nachprüfung (Runde 2)

- **Prüfer:** Gherkin-Prüfinstanz (Claude Opus 5.5, High)
- **Datum:** 2026-11-17
- **Bezug:** Erstbericht [`2026-11-17-gherkin-abnahme-opus.md`](2026-11-17-gherkin-abnahme-opus.md) mit 0 BLOCKER, 15 HOCH, 21 MITTEL und 18 NIEDRIG.
- **Maßstab:** `BDD_GUIDELINES_v1.8.0_DE.md` §1–§13 sowie `governance/{README, adr/README, adr/TEMPLATE, limitations/README, limitations/TEMPLATE}.md`.
- **Vorgehen:** Ich habe nur `.md`- und `.feature`-Dateien gelesen. Produktivcode, Step-Code und `docs/contract/registry.yaml` habe ich nicht geöffnet.
- **Vom Auftraggeber akzeptierte Ausnahme:** In LIM 0012, 0013, 0016 und 0017 steht im Commit-Feld „3.0.0-release (see git log)“. Die SHA wird beim 3.0.0-Commit nach der Freigabe nachgetragen. Das werte ich nicht als Befund.

---

## 1. Prüfumfang

| Bereich | Geprüft |
|---|---|
| ADRs | `0040`–`0061` vollständig (22). Status-Zeilen von `0002`, `0009`, `0010`, `0012`, `0023`, `0025`, `0031`, `0033`, `0034`, `0035`, `0037`, `0039`. Dazu `docs/adr/README.md`. |
| LIMs | `README.md`, `0001`, `0011` (bulk_move), `0013`, `0014`, `0015`, `0016`, `0019`, `0020`, `0021`, `0024` vollständig. Resolution-Notes von `0002`, `0003`, `0006`, `0008`, `0009`. |
| Bilanz und Begleitdokumente | `docs/error_path_analysis.md` vollständig, `docs/ROADMAP.md`, `README.md`, `usability/README.md` (Ablauf eines Runs). |
| BDD-Features (vollständig oder alle geänderten Abschnitte) | `non_interference_and_field_gates`, `write_visibility_and_flow_monotonicity`, `write_capabilities`, `draft_edit_capability`, `provenance_own_and_draft_ownership`, `default_deny`, `draft_consistency`, `visibility_levels` (Ausschnitt), `whitelist_folder_mode`, `flagged_predicate`, `sender_rule_matcher`, `policy_reload`, `default_scope_transparency`, `bulk_mark_seen`, `bulk_mark_tagged`, `update_draft`, `update_draft_errors`, `create_reply_draft`, `create_draft_error_visibility`, `inline_attachment_detection`, `search_criteria_and_pagination`, `search_completeness`, `response_transparency`, `reason_code_contract`, `mcp_tool_discovery`, `fetch_attachment_sink` (Header), `saga_crash_recovery`, `draft_revision_recovery`, `audit_log_format`, `secret_store_backends`, `caller_authentication` (Header), `oauth2_bootstrap` (Ausschnitt), `gmail_label_semantics` (Header), `gmail_search_and_counts` (Header), `gmail_draft_lifecycle` (Header), `localized_gmail_resolution`, `tracing_spans`, `tracing_payload_mode` |
| Usability-Features | `01`–`04` (Backgrounds, Policies der Varianten, ideale Pfade) |

### 1.1 Nachrechnung der §4.5-Header (in dieser Runde erneut gezählt)

Gezählt sind expandierte Szenarien: Jede Examples-Zeile zählt als ein Szenario.

| Datei | Header-Kategorien | Summe Header | Tatsächlich | Status |
|---|---|---:|---:|---|
| policy/write_visibility_and_flow_monotonicity | – | 17 | 17 | ✓ (M-01 behoben) |
| policy/write_capabilities | 3+2+3+3+2+1+1 | 15 | 15 | ✓ (M-01 behoben) |
| auth/caller_authentication | 2+2+3+4+1 | 12 | 12 | ✓ (M-01 behoben) |
| providers/gmail_label_semantics | – | 10 | 10 | ✓ (M-01 behoben) |
| tool_surface/reason_code_contract | 1+19+6+1 | 27 | 27 | ✓ (M-01 behoben) |
| policy/provenance_own_and_draft_ownership | 7×1 | 7 | 7 | ✓ |
| policy/default_deny | 6×1 | 6 | 6 | ✓ |
| policy/draft_consistency | 1+1 | 2 | 2 | ✓ |
| tool_surface/update_draft_errors | 9+6+2+1 | 18 | 18 | ✓ |
| tool_surface/search_criteria_and_pagination | 12+8+3+5+5+1 | 34 | 34 | ✓ |
| tool_surface/response_transparency | 2+1+6+1+2+4 | 16 | 16 | ✓ |
| tool_surface/mcp_tool_discovery | 1+1+3+2 | 7 | 7 | ✓ |
| tool_surface/create_draft_error_visibility | 5×1 | 5 | 5 | ✓ |
| transactions/draft_revision_recovery | 5×1 | 5 | 5 | ✓ |
| audit/audit_log_format | 1+1+1+1+1+1+2+1+1+1+1+2+6 | 20 | 20 | ✓ |
| auth/secret_store_backends | 10×1 | 10 | 10 | ✓ |
| observability/tracing_spans | 1+1+2+1+2+1+1+1+1+2 | 13 | 13 | ✓ |
| observability/tracing_payload_mode | 1+1+1+1+1+1+1+3+1 | 11 | 11 | ✓ |

Alle in dieser Runde nachgezählten Header stimmen. Es gibt kein `@pending`.

### 1.2 Nachrechnung von `docs/error_path_analysis.md`

| Kennzahl | Ergebnis |
|---|---|
| Total | **323** ✓ (Summe der 18 Schichtzeilen, gegen die Einzelzeilen L1–L18 geprüft) |
| Covered | **263** ✓ |
| Deferred | **60** ✓ (A und B sind jetzt definiert) |
| Gap | **0** ✓ |
| Mock-Fidelity | in den Appendix ausgelagert und nicht mitgezählt ✓ |

Die Arithmetik ist korrekt. Zur inhaltlichen Zuordnung siehe N-R07 und N-R08.

---

## 2. Status der Befunde aus Runde 1

### 2.1 HOCH

| ID | Status | Nachweis |
|---|---|---|
| H-01 | **behoben** | Positivkontrolle und World-Reset sind vorhanden. Die Zählsemantik („matched, applied, idempotent“) steht in ADR 0045 §1. Rest in `bulk_mark_seen.feature:7`, siehe M-R03. |
| H-02 | **behoben** | Der objektbezogene Zweitkanal (Flag und Verbleib von uid 201/110) ist ergänzt. |
| H-03 | **behoben** | `default_scope_transparency` prüft `\Seen` und `$Recent` auf 701 und 702 direkt per IMAP. |
| H-04 | **behoben** | Es gibt Szenarien mit gemischter Sichtbarkeit (COUNT-cap und Untergrenze bei den Kriterien) in `bulk_mark_seen` und `bulk_mark_tagged`. |
| H-05 | **behoben** | Kontoweiter cap ist unterscheidbar geprüft, inklusive Positivkontrolle über `[Gmail]/All Mail`. |
| H-06 | **behoben** | ADR 0043 regelt Google `may_hide`. Geprüft in `write_visibility…:164` (`policy_may_hide`). |
| H-07 | **behoben** | ADR 0044 §2.5 enthält die Ausnahme für eigene Drafts. Der Feature-Text von `draft_edit_capability.feature:6-9` nennt sie. |
| H-08 | **behoben** | ADR 0042 sowie `update_draft_errors.feature:80-100` (fremd und unsigniert → `not_found`). Der Feature-Text ist noch nicht nachgezogen, siehe M-R04. |
| H-09 | **behoben** | Die HMAC-Formel steht in ADR 0048 und `audit_log_format.feature:14-18`. Die Change-Hash-Formel steht in ADR 0057 §3 und `update_draft.feature:9-13`. |
| H-10 | **behoben** | `update_draft.feature:273-278`: genau eine Nachricht, deterministischer Sieger und Verlierer. |
| H-11 | **behoben** | `saga_crash_recovery.feature:105-114`: `committed`, 2 Kopien, je ein Tail, Quelle leer. |
| H-12 | **behoben** | Registry in ADR 0056. Neue Szenarien: `secret_unavailable` (`secret_store_backends.feature:141-164`), `internal_error` (`audit_log_format.feature:235-248`), `saga_aborted` (`policy_reload.feature:147-158`). Zum Antwort-Envelope bei `internal_error` siehe M-R05. |
| H-13 | **teilweise behoben → Rest H-R01** | Mit 0056–0061 wurde aufgeteilt. Drei neue bzw. umgeschnittene ADRs bündeln aber weiterhin unabhängige Entscheidungen, siehe H-R01. |
| H-14 | **behoben** | Alle ADRs 0040–0061 nennen mindestens 3 Alternativen. |
| H-15 | **behoben** | ADR 0058 enthält das geschlossene Inventar mit 6 Hooks. Outline über alle 6 Hooks: `audit_log_format.feature:289-301`. `usability/README.md` dokumentiert `IMAP_MCP_TEST_MODE=1` und das Risiko. |

### 2.2 MITTEL

| ID | Status | Nachweis / Rest |
|---|---|---|
| M-01 | **behoben** | Siehe Tabelle 1.1. |
| M-02 | **behoben** | Mock-Fidelity steht im Appendix, fehlende Pfade sind ergänzt, die Summen neu gerechnet. Rest bei der Klassifizierung siehe N-R07 und N-R08. |
| M-03 | **behoben** | Exakte Grenzfälle: `search_criteria…:83` (60s, 11:59:00 vs. 11:58:59), `:85-92` (`older_than` strikt), `search_completeness.feature:72-78` (Mitternacht). |
| M-04 | **weitgehend behoben → Rest M-R02** | Behoben: `all_recipients_in_domains` mit 404, INTERNALDATE in beiden Richtungen, `\Flagged` per remove/replace und bulk, Cc und rfc822/Bcc bei `draft_recipients`, `message/rfc822`, Regel (c), WAL-Provenienz. Offen: Reihenfolge der Whitelist-Regeln. |
| M-05 | **behoben** | Zweites `policy_alias_conflict`, `list_labels` per COUNT, HEADERS-cap. |
| M-06 | **behoben** | Zeitregel `create_reply_draft.feature:10-12`, Cc positiv geprüft in `:65`. |
| M-07 | **behoben** | `oauth2_bootstrap.feature:111` schließt Verbindungen. ADR 0049 und 0059 regeln den Scope-Wechsel. |
| M-08 | **behoben** | Registry in ADR 0056 (3 ALLOW, 17 DENY, 21 `error.type`, 20 Audit). |
| M-09 | **behoben** | In 0040–0061 kein echter Programmcode mehr gefunden. |
| M-10 | **behoben** | 0002, 0012 und 0037 sind durch 0043, 0061 und 0053 abgelöst (mit Auditability). |
| M-11 | **weitgehend behoben → Rest M-R06** | Resolution-Notes sind ergänzt, die Daten in LIM 0009 konsistent, LIM 0016 hat Residual risk und Triggers. Offen: Widerspruch zwischen Kopf und Body in 0013 und 0016. |
| M-12 | **behoben** | LIM 0014, 0015, 0020, 0021 auf 3.0.0 aktualisiert. 0019 steht auf `Mitigated`. |
| M-13 | **behoben** | LIM 0001 ist `Superseded by LIM-0024`. LIM 0024 ist `permanent` / `Mitigated`. |
| M-14 | **weitgehend behoben → Rest N-R04** | Korrigiert: `draft_edit_capability.feature:210`, `default_deny.feature:62` (kein SELECT), `visibility_levels.feature:80` (`size_bytes`), `response_transparency.feature:98` (target-hidden schlägt source-capability), `mcp_tool_discovery.feature:108`, `create_reply_draft.feature:68` (X-SC-Provenance), `draft_consistency.feature:60` (Body), `provenance…:104` (Titel präzisiert). Offen: `write_capabilities.feature:149`. |
| M-15 | **behoben** | `mcp_tool_discovery.feature:110-121` führt `required_capabilities` als Liste. Im Beschreibungstext steht noch der Singular, siehe N-R05. |
| M-16 | **behoben** | `create_draft_error_visibility.feature:102,121` erwartet „exactly one“. `draft_revision_recovery.feature:138-144` spezifiziert Antwort, WAL und 2 Revisionen. |
| M-17 | **teilweise behoben → Rest M-R07** | `clientInfo`-Kürzung (`tracing_spans.feature:73-76`) und eine Zwei-Welten-Prüfung (`:92-103`) sind ergänzt. Der Zwei-Welten-Vergleich hat aber keine Positivkontrolle und keine benannte Attributmenge. |
| M-18 | **behoben** | `audit_log_format.feature:186-224` (Positivkontrolle), `:278-287` (Server versiegelt selbst). |
| M-19 | **behoben** | Variantenpolicies stehen sichtbar im Background (`01:25`, `02:22`, `03:38`, `04:26`), ideale Pfade sind je Variante angegeben (`02:27`). Kleiner Rest siehe N-R10. |
| M-20 | **behoben** | ADR 0046 §4 legt eine einheitliche Widerrufsregel fest. `policy_reload.feature:128-158`. |
| M-21 | **behoben** | `update_draft_errors.feature:124,147-150,189-192` und `write_visibility…` prüfen per IMAP. |

### 2.3 NIEDRIG (Runde 1)

| ID | Status |
|---|---|
| N-01 | behoben (Inline-Links) |
| N-02 | behoben (ADR 0048 nennt und listet sechs Defekte) |
| N-03, N-04, N-14, N-15, N-16 | in dieser Runde nicht erneut geprüft; nicht freigaberelevant |
| N-05 | Im ROADMAP keine privaten Pfade mehr festgestellt |
| N-06 | Durch Löschen der Weiterleitung „gelöst“. Das ist ein neuer Governance-Verstoß, siehe N-R06. LIM 0011 (bulk_move) zitiert jetzt 0045, 0046 und 0052 ✓ |
| N-07 | nicht erneut geprüft |
| N-08 | offen: `mcp_tool_discovery.feature:136-143` doppelt weiterhin `tool_surface_info.feature` |
| N-09 | offen: `response_transparency.feature:108`, `visibility_levels.feature:99` (`body`) |
| N-10 | offen: magische Schritte „the only visible message“ (`provenance…:57`, `create_reply_draft.feature:69`) und „an interrupted revision leaves …“ (`draft_revision_recovery.feature:82`) |
| N-11, N-12, N-13 | nicht erneut geprüft |
| N-17 | behoben (`01:38,55,75` harmful; `02:21` Drafts leer) |
| N-18 | teilweise behoben (A/B definiert), Rest siehe N-R08 |

---

## 3. Restbefunde Runde 2

### BLOCKER

Keine.

### HOCH

#### H-R01 — Noch immer mehrere unabhängige Entscheidungen pro ADR (Rest von H-13)
- **Guideline:** `governance/adr/README.md` Schreibregel 1 („one decision per ADR“); `docs/adr/README.md` Abschnitt Format
- **Fundstellen:**
  - `docs/adr/0056-canonical-folder-resolution-and-code-registry.md:1`, `:16`. Titel und Decision verbinden zwei voneinander unabhängige Entscheidungen mit „and“:
    1. RFC-6154-Ordnerauflösung und mUTF-7-Übersetzung;
    2. geschlossene Code-Registry für `reason` und `error.type`.
    Genau diese beiden Punkte hatte Runde 1 als getrennt zu führende Records vorgeschlagen. Wird die Registry künftig um einen Code erweitert, müsste die Ordnerauflösung mit abgelöst werden.
  - `docs/adr/0059-oauth2-pkce-and-durable-account-health.md:1`, `:18`:
    1. CLI-Bootstrap per PKCE und Scope-Vorgabe;
    2. dauerhafter `needs_rebootstrap`-Zustand und Account-Health im WAL sowie die reaktive Retry-Regel.
    Das sind drei unabhängig voneinander änderbare Entscheidungen.
  - `docs/adr/0057-immutable-received-mail-and-structured-drafts.md:1`, `:13`:
    1. Abschaffung der Rewrite-Tools für empfangene Mail;
    2. strukturierte Komposition, Anhangsbearbeitung, Reply-Isolation, Change-Hash-Revision und Discard per Handle.
- **Bewertung:**
  - Der Auftraggeber erklärt, die ADRs seien „strikt Einzel-Entscheidungen“. Für diese drei Records trifft das nachweislich nicht zu.
  - Die Records sind von heute und 3.0.0 ist nicht getaggt. Eine Aufteilung ist also ohne Verletzung der Unveränderlichkeit möglich und kostet wenig.
  - 0045, 0046, 0048 und 0049 akzeptiere ich nach erneuter Lektüre als je **eine** kohärente Invariante mit Unterpunkten: Schreibautorisierung, Transfer-Integrität, Audit-Trail und Konfigurations-Integrität. Daraus folgt kein Befund mehr.
- **Behebung:**
  - ADR 0056 aufteilen in „0056 Canonical Special-Use Folder Resolution“ und „0062 Closed Reason/Error Code Registry“.
  - ADR 0059 aufteilen in „OAuth2 PKCE CLI Bootstrap“ und „Durable Account Health and Reactive Token Retry“.
  - ADR 0057 aufteilen in „Received Mail Is Immutable“ und „Structured Own-Draft Composition and Revision“.
  - In `docs/adr/README.md` den Index und in den Features die ADR-Verweise anpassen (`reason_code_contract.feature:3`, `localized_gmail_resolution.feature:3`, `update_draft.feature:13`).

### MITTEL

#### M-R01 — `ROADMAP.md` und `README.md` widersprechen dem aktuellen ADR- und LIM-Stand
- **Guideline:** §1.1 (Widerspruchsfreiheit), §12
- **Fundstellen:**
  - `docs/ROADMAP.md:9-10`: „ADR-0040 through ADR-0055“. Richtig ist 0040–0061.
  - `docs/ROADMAP.md:24`: „0001–0009 | Resolved“. LIM 0001 ist aber `Superseded by LIM-0024`.
  - `docs/ROADMAP.md:28`, `:31`: LIM 0013 und 0016 stehen als `permanent`. Laut LIM-Kopf sind sie `must-resolve` / `Resolved`.
  - `docs/ROADMAP.md:34`: LIM 0019 steht als `Accepted`. Richtig ist `Mitigated`. LIM 0024 fehlt in der Tabelle ganz.
  - `docs/ROADMAP.md:68-69`: Verweise auf ADR 0012 und ADR 0053 für Usability. Richtig sind ADR 0061 und ADR 0060. Auch die Einträge #44, #45 und #49 nennen abgelöste ADRs.
  - `README.md:275`: „Limitation Records (0001–0023)“. Es fehlt 0024.
  - `README.md:245`, `:269`, `:276`: Verweise auf ADR 0050, 0037 und 0044. Richtig sind 0059 (needs_rebootstrap), 0053 (Trace-Modi) und 0056 (Registry/Ordner).
  - `README.md:256-270`: In der Env-Tabelle fehlt `IMAP_MCP_APPEND_TIMEOUT`. ADR 0058:32 behauptet aber ausdrücklich, die Variable sei als Operator-Setting „im README dokumentiert“.
- **Behebung:** ROADMAP-Tabelle und Links auf 0040–0061 und LIM 0001–0024 nachziehen. In der README-Env-Tabelle ergänzen: `| IMAP_MCP_APPEND_TIMEOUT | no | Seconds to wait for a tagged APPEND reply before append_timeout (ADR 0058 §…) |`.

#### M-R02 — Der Titel „regardless of rule ordering“ ist weiterhin nicht belegt (Rest von M-04)
- **Guideline:** §2.3 (Varianz), §12
- **Fundstelle:** `bdd/features/policy/whitelist_folder_mode.feature:58-71`
- **Nachweis:**
  - Es gibt nur eine Reihenfolge: `[FULL, ENVELOPE]`. Vorher stand die andere im Feature, also wurde die Reihenfolge nur umgedreht statt variiert.
  - Eine Implementierung nach „first match wins“ besteht das Szenario genauso wie eine nach „max of matches“. Die in `:5` behauptete Regel `max(grant-of-matching-rules)` wird also nicht von „first match“ unterschieden.
- **Behebung:**
  ```gherkin
  Scenario Outline: Two overlapping whitelist rules grant the maximum level in order <order>
    Given policy "invoice-policy" grants folder:
      | folder           | mode      | default | rules   |
      | INBOX/Rechnungen | whitelist | NONE    | <rules> |
    ...
    Then the response field visibility_applied equals "FULL"
    Examples:
      | order          | rules                                                                                |
      | FULL first     | [{from_address=rechnung@hornbach.de -> FULL}, {from_domain=hornbach.de -> ENVELOPE}] |
      | ENVELOPE first | [{from_domain=hornbach.de -> ENVELOPE}, {from_address=rechnung@hornbach.de -> FULL}] |
  ```
  Danach den §4.5-Header nachziehen (+1).

#### M-R03 — `bulk_mark_seen.feature` beschreibt eine andere Zählsemantik als ADR 0045 §1
- **Guideline:** §1.1, §12
- **Fundstelle:** `bdd/features/tool_surface/bulk_mark_seen.feature:6-7` („report marked_count over modified visible messages only“) im Vergleich zu ADR 0045 §1
- **Nachweis:**
  - ADR 0045 §1 legt nach H-01 fest: Gezählt werden die gematchten, angewendeten Nachrichten, idempotente Treffer eingeschlossen.
  - Der Feature-Text sagt „modified only“. Eine Nachricht, die schon `\Seen` trägt, würde danach nicht gezählt.
  - Genau diese Unschärfe war Ursache von H-01. Der Feature-Text ist die Single Source of Truth und muss deshalb mit dem ADR übereinstimmen.
- **Behebung:** In `:6-7` schreiben: „report marked_count as the number of matched, sufficiently visible messages to which the flag state was applied (including messages that already carried it, ADR 0045 §1)“. Ein idempotenter Fall im Szenario (eine schon `\Seen`-Nachricht zählt mit) wäre wünschenswert.

#### M-R04 — Die Feature-Beschreibung von `update_draft_errors` widerspricht dem eigenen Szenario und ADR 0042 (Rest von H-08)
- **Guideline:** §1.1, §12
- **Fundstelle:** `bdd/features/tool_surface/update_draft_errors.feature:9-10` im Vergleich zu `:80-100` und ADR 0042
- **Nachweis:** Der Text sagt: „Tampered **or unsigned** X-SC-\* messages return ALLOW + ERROR draft_not_editable“. Das Szenario `:80` erwartet für unsignierte fremde Nachrichten aber `not_found`. Das ist auch richtig so, denn sonst entstünde ein Existenz-Orakel.
- **Behebung:**
  ```text
  - Unknown handles, foreign handles, and unsigned messages carrying an
    X-SC-Draft-Id return ALLOW + ERROR not_found.
  - The caller's own WAL-registered draft whose X-SC-Provenance no longer
    verifies (stripped or externally edited) returns ALLOW + ERROR
    draft_not_editable.
  ```

#### M-R05 — `internal_error`: Antwort-Envelope nicht spezifiziert, Herkunft von `error_class` unsichtbar, kein Leak-Check auf die Antwort
- **Guideline:** §1.1 (ADR 0044 behauptet, dass alle Antworten drei normierte Formen haben), §2.2, §4.3
- **Fundstellen:**
  - `bdd/features/audit/audit_log_format.feature:236-248`
  - ADR 0044 (Envelope) und ADR 0056 (Registry: `internal_error` nur als Audit-Code)
- **Nachweis:**
  1. `:238` prüft nur `result equals "ERROR"`. Für `decision` und `error.type` gibt es keine Erwartung. Kein Accepted-ADR legt fest, welches `error.type` der Caller bei einer unerwarteten Exception erhält. Damit ist die geschlossene Registry für diesen Pfad offen.
  2. Der Wert `error_class` = `RuntimeError` (`:245`) lässt sich aus dem Given (`:236`) nicht ableiten. Dass der Hook eine `RuntimeError` wirft, steht weder im Feature noch erkennbar in ADR 0058.
  3. Geprüft wird nur, dass `SENSITIVE-EXCEPTION-DETAIL` nicht im **Audit** steht. Dass der Text nicht in der **MCP-Antwort** (`error.detail`) erscheint, wird nicht geprüft, obwohl ADR 0044 §3 die Spiegelung von Eingaben verbietet.
- **Behebung:**
  - In ADR 0044/0056 (bzw. dem abgespaltenen Registry-ADR aus H-R01) festlegen, welcher Caller-Code bei einer unerwarteten Exception zurückkommt, z. B. `ALLOW + ERROR`, `error.type: "internal_error"`.
  - Im Feature:
    ```gherkin
    Given the server is armed with IMAP_MCP_TEST_INJECT_INTERNAL_ERROR="list_accounts:SENSITIVE-EXCEPTION-DETAIL"
      # ADR 0058: the hook raises RuntimeError(<detail>) inside the named tool
    When invoice-agent calls list_accounts
    Then the response decision is ALLOW
    And the response field result equals "ERROR"
    And the response field error.type equals "internal_error"
    And the JSON response does NOT contain the literal strings:
      | forbidden_string           |
      | SENSITIVE-EXCEPTION-DETAIL |
    ```

#### M-R06 — LIM 0013 und LIM 0016: Kopf `must-resolve`, Body `permanent` (Rest von M-11)
- **Guideline:** `governance/limitations/README.md` (Resolution intent), `TEMPLATE.md`
- **Fundstellen:**
  - `docs/limitations/0013-single-attachment-sink-path.md:4` (`must-resolve`) im Vergleich zu `:15-22` (Abschnitt „Resolution intent“: „`permanent`. … There is no future state …“)
  - `docs/limitations/0016-attachment-sink-path-disclosed-in-tool-description.md:4` im Vergleich zu `:15-21` (gleicher Widerspruch). Außerdem fehlt zwischen `:93` und `:94` die Leerzeile vor `## Residual risk`, sodass die Überschrift nicht gerendert wird.
- **Nachweis:** Laut Auftraggeber gibt es „keine permanent+Resolved-Widersprüche mehr“. Der Kopf wurde umklassifiziert, der Body begründet aber weiterhin, warum der Zustand **dauerhaft** bleibt. Ein Agent liest daraus zwei gegensätzliche Anweisungen.
- **Behebung:** Den Body-Abschnitt „Resolution intent“ ersetzen, z. B. durch „`must-resolve` (reclassified 2026-11-17 by owner). The original permanent rationale is superseded: ADR 0047 delivers per-caller subdirectories / a static description, removing the limitation.“ Außerdem die Leerzeile in 0016 ergänzen.

#### M-R07 — Die Telemetrie-Non-Interference hat keine Positivkontrolle und vergleicht eine unbenannte Attributmenge (Rest von M-17)
- **Guideline:** §4.3 (Positivkontrolle), §2.1, ADR 0053 §1
- **Fundstelle:** `bdd/features/observability/tracing_spans.feature:92-103`
- **Nachweis:**
  1. „whose count and outcome attributes are remembered“ (`:97`): Welche Attribute gemeint sind, bleibt offen. Das ist ein magischer Schritt nach §2.1.
  2. In Welt A wird kein Wert geprüft. Ein Server, der auf `tool.search` gar keine Zählattribute ausgibt, besteht das Szenario. `:82-90` belegt die Attribute nur für `tool.list_messages`, nicht für `tool.search`.
  3. Das ist dasselbe Muster wie H-01 aus Runde 1, nur auf dem Telemetrie-Kanal.
- **Behebung:**
  ```gherkin
  Then within 10 seconds the trace sink contains a span "tool.search" with attributes:
    | attribute           | value |
    | mcp.result.visible  | 1     |
    | mcp.result.returned | 1     |
    | mcp.result.has_more | false |
    | domain.outcome      | ok    |
  And the attributes mcp.result.visible, mcp.result.returned, mcp.result.has_more, domain.outcome, mcp.response.size_bytes of that span are remembered as "span_without_none"
  ...
  Then ... equal the remembered "span_without_none"
  And no span in the trace sink contains the value "Streng geheim"
  ```

#### M-R08 — „\Draft and \Seen survive“ ohne Vorzustand
- **Guideline:** §2.2 (Herleitbarkeit), §4.3
- **Fundstelle:** `bdd/features/transactions/draft_revision_recovery.feature:100-120`
- **Nachweis:**
  - Das Then erwartet `\Seen` auf der Revision 2. Dass `create_draft` überhaupt mit `\Seen` anhängt, steht weder im Given noch im Feature-Text. Nur `gmail_draft_lifecycle.feature:9` sagt das, und zwar für Gmail, nicht für Dovecot.
  - Ein Server, der `\Seen` erst bei der Revision setzt, besteht das Szenario. Ein „Überleben“ ist damit nicht belegt.
- **Behebung:** Nach dem `create_draft`:
  ```gherkin
  Then the IMAP message in folder "Drafts" with subject "Still a draft" has flags:
    | flag   |
    | \Draft |
    | \Seen  |
  ```
  Alternativ die Regel im Feature-Text nennen („create_draft appends with flags \Draft \Seen, ADR 0057 §…“).

### NIEDRIG

| ID | Paragraf | Fundstelle | Befund / Behebung |
|---|---|---|---|
| N-R01 | Präzision | `docs/adr/0053-…:17` „bounds,exporter“; `0060-…:12` „missingvariant“; `0059-…:59` „Hammer“ | Tippfehler. |
| N-R02 | Präzision | `docs/adr/0060-…:9` | „three inconsistencies“, aufgezählt sind nur 2. |
| N-R03 | ADR-Naming | Dateinamen `0053-non-interfering-telemetry-and-usability-isolation.md` und `0048-pre-effect-audit-and-test-boundary.md` | Die Dateinamen nennen noch Themen, die nach 0060 bzw. 0058 ausgelagert sind. Da noch nicht getaggt: umbenennen und die Links anpassen. |
| N-R04 | §12 | `bdd/features/policy/write_capabilities.feature:149` | Der Titel behauptet „deposit“ bzw. „copy back out“, ausgeführt wird keins von beiden (Rest von M-14). |
| N-R05 | §1.1 | `bdd/features/tool_surface/mcp_tool_discovery.feature:10` | Text nennt noch „required_capability“ (Singular). Richtig: `required_capabilities`. |
| N-R06 | LIM-Governance | `docs/limitations/0011-search…` (gelöscht) | `governance/limitations/README.md`: „Deleting a LIM file is non-conformant.“ Die Datei als Tombstone wiederherstellen (`Status: Superseded by LIM-0023`, keine Resolution-Felder). |
| N-R07 | §4.5 | `gmail_search_and_counts.feature:18-20` (7), `gmail_draft_lifecycle.feature:15-18` (4), `localized_gmail_resolution.feature:12` (1) | Die Feature-Header zählen Mock-Fidelity-Szenarien als „Covered error layers“. `error_path_analysis.md` zählt sie ausdrücklich nicht. Im Header als „Fixture fidelity (not SUT, not counted)“ getrennt führen. |
| N-R08 | §4.5 | `error_path_analysis.md` L4.17, L8.11 | „deferred B“, obwohl die Definition (Non-Goal, Sub-ms-Race, Live-Gmail-Edge) nicht passt. Entweder als A führen oder den B-Grund angeben. |
| N-R09 | Verweise | `localized_gmail_resolution.feature:3` („ADR 0044 §2“); `tracing_payload_mode.feature:3` (ADR 0037, abgelöst) | Auf ADR 0056 bzw. 0053 umstellen. |
| N-R10 | Usability | `usability/features/02_draft_revision_and_discard.feature:22` | Für die Variante „today“ fehlt `accept_incoming` auf `[Gmail]/Trash`. Die idealen Pfade von R1 (`:27`), R2 und R3 enthalten aber `discard_draft`, das laut `mcp_tool_discovery.feature:121` `accept_incoming` voraussetzt. Klarstellen. |
| N-R11 | §1.2 | `fetch_attachment_sink.feature:6-8` | OS-API-Namen (`O_DIRECTORY`, `O_NOFOLLOW`, `openat`) im Feature-Text. Wie in ADR 0047 als Mechanik beschreiben („no-follow, exclusive-create“). |
| N-R12 | §4.3 | `provenance_own_and_draft_ownership.feature:69-72` | Es wird nicht direkt per IMAP geprüft, dass die Sent-Kopie wirklich keine `X-SC-*`-Header trägt. Positivkontrolle für den WAL-Zweig ergänzen. |
| N-R13 | §4.3 | `update_draft_errors.feature:80-100` | Der Titel verspricht „without existence leak“. Geprüft wird nur `error.type`, nicht die Gleichheit mit der Antwort für einen unbekannten Handle (`:70-78`, inklusive `reason folder_default_applied`). Ergänzen: `And the response field reason equals "folder_default_applied"` oder einen JSON-Gleichheitsvergleich. |
| N-R14 | Tippfehler | `tracing_spans.feature:134` | „an closed loopback port“ → „a closed loopback port“. |
| N-R15 | LIM-Lifecycle | `docs/limitations/0024-…:3` | Der Record springt direkt auf `Mitigated`, ohne dass ein `Accepted`-Zustand dokumentiert ist. Ein Satz im Body genügt („approved and mitigated on the same day by …“). |

---

## 4. Positiv bestätigt (Runde 2)

- Alle Sicherheitsinvarianten aus Runde 1 sind jetzt über einen unabhängigen Zweitkanal mit Positivkontrolle belegt:
  - Non-Interference
  - `Write <= Read`
  - Fluss-Monotonie
  - kontoweiter Gmail-cap
  - Schutz vor Selbst-Eskalation über `\Flagged`
  - opakes `not_found` für Draft-Handles
  - Integrität bei der Message-ID-Kollision
  - Audit-Intent vor dem Effekt
- Kryptographische Literale lassen sich jetzt nachrechnen: Die Formeln für die HMAC-Digests und `X-SC-Draft-Change-Hash` stehen sichtbar im Feature-Text.
- Das Test-Mode-Gate ist geschlossen: 6 Hooks in ADR 0058, eine Outline über alle 6, der Usability-Runner ist dokumentiert.
- Die Registry ist geschlossen und erreichbar: `secret_unavailable`, `saga_aborted` und `internal_error` haben jeweils ein Szenario.
- Alle in dieser Runde nachgezählten §4.5-Header und die Bilanz 323 / 263 / 60 / 0 stimmen.

---

## 5. Freigabe-Urteil

**NACHARBEIT ERFORDERLICH** — 0 BLOCKER, **1 HOCH**, 8 MITTEL, 15 NIEDRIG.

Von den 15 HOCH-Befunden aus Runde 1 sind 14 vollständig behoben. Die fachliche Gherkin-Spezifikation (Single Source of Truth) ist damit im Kern freigabefähig. Offen bleibt **ein** Governance-Punkt:

1. **H-R01:** ADR 0056 (`docs/adr/0056-canonical-folder-resolution-and-code-registry.md:1,16`), ADR 0057 (`…0057-…:1,13`) und ADR 0059 (`…0059-…:1,18`) je in Einzel-Entscheidungen aufteilen und den Index und die Feature-Verweise nachziehen.

Zusätzlich empfohlen vor dem Tag, ohne Einfluss auf das Urteil:

- **Spezifikationslücken mit kleinem Aufwand:**
  - M-R05: `internal_error` im Caller-Envelope festlegen und den Leak-Check auf die Antwort ergänzen.
  - M-R07: Positivkontrolle für die Zwei-Welten-Prüfung der Spans.
  - M-R02: Whitelist-Reihenfolge als Outline.
- **Textkorrekturen an Feature und LIM:** M-R03, M-R04, M-R06, M-R08.
- **Dokumente:** M-R01 (ROADMAP und README).

Nach Behebung von H-R01 gilt: **FREIGEGEBEN (0 BLOCKER, 0 HOCH)**. Eine erneute Vollprüfung ist dafür nicht nötig. Es genügt, die drei aufgeteilten ADRs und ihren Index gegenzulesen.
