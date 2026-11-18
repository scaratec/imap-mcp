# Spec-Audit Cycle <NN> — YYYY-MM-DD

> Diese Datei ist nach Abschluss unveränderlich. Korrekturen entstehen
> als neuer Zyklus, der hierauf verweist.

## Header

| Feld | Wert |
|---|---|
| Zyklus-Nummer | <N> |
| Datum | YYYY-MM-DD |
| Audit-Agent | <name oder agent id, z. B. claude-spec-audit-XX> |
| Implementierungs-Agent | <name oder agent id, der die geprüfte Codebasis erstellt hat> |
| Trigger | <Major-Tag / PR-Serie / LIM-Schließung / Kadenz> |
| Eingelesene Commit-Range | `<base..head>` (oder einzelner Commit-Hash) |
| Eingelesene Artefakte | `bdd/features/**/*.feature`, `bdd/features/steps/**/*.py`, `bdd/support/**/*.py`, `docs/adr/**/*.md`, `docs/limitations/**/*.md`, `docs/error_path_analysis.md` |
| Bewusst NICHT eingelesen | `server/src/**` (Produktivcode — siehe README §Wer) |
| Ergebnis | <PASS / N findings (B blocking, M must-fix, A accept, I invalid)> |
| Bewertung des Hauptbetreuers | <eingetragen am Ende durch den Hauptbetreuer> |

## Prüfung 1 — Persistence-Validierung

> Operationalisiert §4.3. Für jedes Szenario mit einer schreibenden
> Operation: Existiert ein nachgelagerter Prüfschritt, der den
> Zielzustand über einen zweiten, unabhängigen Kanal validiert?
> (Direkt-DB, Datei lesen, Queue, separater GET. Die API-Response der
> schreibenden Operation **selbst** ist kein unabhängiger Kanal.)

| Szenario (Datei:Zeile) | Schreibende Operation | Unabhängiger Kanal? | Befund |
|---|---|---|---|
| `bdd/features/transactions/intra_account_move.feature:33` | `move` (UID MOVE auf Dovecot) | `direct IMAP SEARCH` auf Source und Target | OK |
| `bdd/features/transactions/cross_account_move_saga.feature:34` | `move` (saga) | `direct IMAP SEARCH` auf Source/Target + WAL-File-Read | OK |
| <…> | <…> | <…> | <OK / FINDING-N> |

### Findings — Prüfung 1

> Pro Befund einen Eintrag. Format: `Finding 1.NN — <kurze Zusammenfassung>`

#### Finding 1.<NN> — <Kurzbeschreibung>

- **Szenario:** `<datei:zeile>`
- **Beobachtung:** <was hat der Audit-Agent festgestellt?>
- **Erwartet:** <wie sähe die ehrliche Verifikation aus?>
- **Schwere:** blocking / must-fix / accept / invalid
- **Bewertung des Hauptbetreuers:** <ausgefüllt nach Lesen des Findings>

## Prüfung 2 — Herkunftsanalyse für Then-Werte

> Operationalisiert §2.2. Für jeden konkreten Wert in einem
> `Then`-Schritt: Ist er entweder (a) wörtlich in einem `Given` oder
> `When` enthalten, oder (b) durch eine im Szenario erkennbare
> Geschäftsregel aus den sichtbaren Daten ableitbar?

Diese Prüfung umfasst implizit den **reason-code-spezifischen
Prüfauftrag** aus [LIM-0024](../limitations/0024-reason-code-symmetry-in-contract-tests.md)
und [ADR-0062](../adr/0062-closed-reason-and-error-code-registry.md):
Jede `reason`- und `error.type`-Assertion wird gegen die geschlossene
Registry in `docs/contract/registry.yaml` abgeglichen, mit mindestens
zwei unabhängigen Szenarien je kanonischem Caller-Reason-Code
(Variance-Disziplin §2.3).

| Szenario | Then-Wert | Herkunft | Befund |
|---|---|---|---|
| `<datei:zeile>` | `<Wert>` | (a) wörtlich aus `<Given/When>` / (b) abgeleitet aus `<Regel>` / (c) Vokabular-Token aus `ADR-0062` | OK |

### Reason-Code-Coverage (Subprüfung von Prüfung 2)

| Reason-Code (`ADR-0062`) | Mindestens 2 unabhängige Szenarien? | Belege |
|---|---|---|
| `authenticated` | <yes/no> | <datei:zeile, datei:zeile> |
| `folder_default_applied` | <yes/no> | … |
| `rule_matched` | <yes/no> | … |
| `account_hidden` | <yes/no> | … |
| `needs_rebootstrap` | <yes/no> | … |
| `folder_hidden` | <yes/no> | … |
| `policy_alias_conflict` | <yes/no> | … |
| `visibility_below_<LEVEL>` | <yes/no> | … |
| `capability_missing` | <yes/no> | … |
| `forbidden_flag` | <yes/no> | … |
| `relocation_would_escalate` | <yes/no> | … |
| `recipient_not_allowed` | <yes/no> | … |
| `tool_not_applicable_for_provider` | <yes/no> | … |
| `auth_failed` | <yes/no> | … |
| `unknown_tool` | <yes/no> | … |

### Findings — Prüfung 2

#### Finding 2.<NN> — <Kurzbeschreibung>

- **Szenario:** `<datei:zeile>`
- **Then-Wert:** `<Wert>`
- **Beobachtung:** <warum lässt sich der Wert nicht herleiten?>
- **Schwere:** blocking / must-fix / accept / invalid
- **Bewertung:** <…>

## Prüfung 3 — Daten-Symmetrie zwischen Szenario und Step-Code

> Operationalisiert §1.3. Für jeden `Given`-Step, der Testdaten
> vorbereitet: Verwendet der Step-Code ausschließlich Daten, die im
> Szenario sichtbar sind, oder fügt er eigene fachliche Werte hinzu?
> (Technische Infrastruktur-Daten — Fremdschlüssel, IDs für
> referenzielle Integrität, Verbindungsstrings — sind ausgenommen.)

| Step-Modul (Datei:Zeile) | Geprüfter Step | Injizierte Werte | Im Szenario sichtbar? | Befund |
|---|---|---|---|---|
| `bdd/features/steps/policy_steps.py:<n>` | `<step text>` | `<liste>` | <yes/no> | OK |

### Findings — Prüfung 3

#### Finding 3.<NN> — <Kurzbeschreibung>

- **Step:** `<step text>`
- **Modul:** `<datei:zeile>`
- **Injizierter fachlicher Wert:** `<Wert>`
- **Verwendet in Assertion:** `<datei:zeile>` (oder: nicht für Assertion)
- **Schwere:** blocking / must-fix / accept / invalid
- **Bewertung:** <…>

## Zusammenfassung

| Prüfung | Findings (B/M/A/I) | Status |
|---|---|---|
| 1 — Persistence | 0/0/0/0 | PASS |
| 2 — Herkunft | 0/0/0/0 | PASS |
| 3 — Symmetrie | 0/0/0/0 | PASS |
| Reason-Code-Coverage | <X>/<Y> codes mit ≥ 2 Szenarien | PASS |
| **Gesamt** | <…> | <PASS / FINDINGS> |

## Trigger-Auswirkung auf LIMs

> Pro existierender LIM zählen: Findings, die kausal auf diese LIM
> zurückgehen, in diesem und im vorherigen Zyklus. Wenn die "Triggers
> for revisit"-Schwelle einer LIM erreicht ist, hier vermerken.

| LIM | Findings dieses Zyklus | Findings vorheriger Zyklus | Schwelle erreicht? |
|---|---|---|---|
| LIM-0001 | <n> | <n> | <yes/no> |
| LIM-0002 | <n> | <n> | <yes/no> |
| <…> | | | |

## Notizen für den nächsten Zyklus

> Hinweise an den nächsten Audit-Agenten: methodische Verbesserungen,
> falsch interpretierte Konventionen, neue Reason-Codes, die
> dokumentiert werden sollten.

---

**Abschluss:** Diese Datei ist nach Eintrag der Bewertungen durch den
Hauptbetreuer eingefroren. Spätere Korrekturen entstehen als neuer
Zyklus.
