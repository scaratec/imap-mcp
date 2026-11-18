# Gherkin- und Spezifikationsabnahme `imap-mcp` 3.0.0

- **Prüfer:** Gherkin-Prüfinstanz (Claude Opus 5.5, High)
- **Datum:** 2026-11-17
- **Maßstab:** `BDD_GUIDELINES_v1.8.0_DE.md` §1–§13; `governance/README.md`, `governance/adr/{README,TEMPLATE}.md`, `governance/limitations/{README,TEMPLATE}.md`
- **Vorgehen:** Nur `.md`- und `.feature`-Dateien gelesen. Produktivcode, Step-Code und `docs/contract/registry.yaml` habe ich nicht geöffnet. Wo der Bericht die Registry nennt, ist ihr Inhalt so gemeint, wie ADR 0044 §3 ihn beschreibt.

---

## 1. Prüfumfang

| Bereich | Anzahl | Ergebnis in Kürze |
|---|---:|---|
| Neue ADRs `0040`–`0055` | 16 | Alle `Accepted`, alle < 120 Zeilen, Security Implications mit Auditability vorhanden. **Aber:** 7 ADRs enthalten mehrere Entscheidungen, 3 ADRs nennen nur 2 Alternativen, 8 ADRs enthalten echte API-Aufrufe. |
| Abgelöste ADRs `0001`–`0039` | 36 | Alle Status-Zeilen stimmen mit `docs/adr/README.md` überein. `0000`, `0002`, `0012` und `0037` bleiben `Accepted`. |
| `docs/adr/README.md` | 1 | Index stimmt (56 Records). |
| Limitation Records `0001`–`0023` + Weiterleitung `0011-search…` + `README.md` + `TEMPLATE.md` | 26 | Kein `Proposed` mehr. Pflichtfelder vorhanden. **Aber:** einige Commit-Felder enthalten keine SHA, mehrere `Accepted`-Bodies sind veraltet, LIM 0001 ist zweifelhaft als `Resolved` markiert. |
| `docs/error_path_analysis.md`, `docs/ROADMAP.md`, `README.md` | 3 | Die Summentabelle stimmt exakt (318 / 261 / 57 / 0). **Aber:** die Aufzählung hat inhaltliche Lücken (siehe M-02). |
| BDD-Features `bdd/features/**/*.feature` | **64** | 0 `@pending`. 5 Header-Summen sind falsch. |
| Usability-Features `usability/features/*.feature` + `usability/README.md` | 7 + 1 | Nichtdeterministisch, keine §4.5-Header nötig. Stammdaten sind teilweise versteckt. |

### 1.1 Nachrechnung der §4.5-Header aller 64 BDD-Features

Gezählt wurden expandierte Szenarien: jede Examples-Zeile zählt als ein Szenario.

| Datei | Header „Total enumerated“ | Tatsächlich | Status |
|---|---:|---:|---|
| observability/tracing_spans | 11 | 11 | ✓ |
| observability/tracing_payload_mode | 11 | 11 | ✓ |
| deployment/projected_config_reload | 8 | 8 | ✓ |
| deployment/health_probe | 14 | 14 | ✓ |
| performance/connection_pool_expiry | 2 | 2 | ✓ |
| performance/connection_pool | 7 | 7 | ✓ |
| performance/connection_reuse | 2 | 2 | ✓ |
| audit/audit_multi_instance | 8 | 8 | ✓ |
| audit/audit_log_format | 14 | 14 | ✓ |
| audit/audit_retention | 11 | 11 | ✓ |
| auth/explicit_imap_user | 3 | 3 | ✓ |
| auth/secret_store_backends | 9 | 9 | ✓ |
| auth/caller_authentication | 13 | **12** | ✗ (M-01) |
| auth/oauth2_bootstrap | 10 | 10 | ✓ |
| providers/gmail_search_and_counts | 14 | 14 | ✓ |
| providers/gmail_draft_lifecycle | 9 | 9 | ✓ |
| providers/localized_gmail_resolution | 15 | 15 | ✓ |
| providers/gmail_label_semantics | 11 | **10** | ✗ (M-01) |
| providers/imap_mailbox_names_utf8 | 12 | 12 | ✓ |
| transactions/draft_revision_recovery | 5 | 5 | ✓ |
| transactions/startup_saga_recovery | 9 | 9 | ✓ |
| transactions/intra_account_move | 8 | 8 | ✓ |
| transactions/saga_crash_recovery | 8 | 8 | ✓ |
| transactions/cross_account_move_saga | 9 | 9 | ✓ |
| policy/provenance_own_and_draft_ownership | 6 | 6 | ✓ |
| policy/write_visibility_and_flow_monotonicity | 16 | **15** | ✗ (M-01) |
| policy/non_interference_and_field_gates | 12 | 12 | ✓ (falsche Kategorie-Bezeichnung, M-01) |
| policy/draft_edit_capability | 8 | 8 | ✓ |
| policy/visibility_levels | 11 | 11 | ✓ |
| policy/draft_consistency | 2 | 2 | ✓ |
| policy/sender_rule_matcher | 27 | 27 | ✓ |
| policy/flagged_predicate | 7 | 7 | ✓ |
| policy/policy_reload | 7 | 7 | ✓ |
| policy/default_deny | 6 | 6 | ✓ |
| policy/write_capabilities | 17 | **15** | ✗ (M-01) |
| policy/blacklist_folder_mode | 7 | 7 | ✓ |
| policy/whitelist_folder_mode | 7 | 7 | ✓ |
| tool_surface/required_arguments | 13 | 13 | ✓ |
| tool_surface/search_completeness | 12 | 12 | ✓ |
| tool_surface/create_draft_format | 15 | 15 | ✓ |
| tool_surface/discard_draft | 8 | 8 | ✓ |
| tool_surface/mcp_tool_discovery | 7 | 7 | ✓ |
| tool_surface/draft_identity | 8 | 8 | ✓ |
| tool_surface/update_draft_errors | 17 | 17 | ✓ |
| tool_surface/update_draft | 13 | 13 | ✓ |
| tool_surface/fetch_attachment_sink | 13 | 13 | ✓ |
| tool_surface/inline_attachment_detection | 2 | 2 | ✓ |
| tool_surface/fetch_attachment_content | 6 | 6 | ✓ |
| tool_surface/response_transparency | 15 | 15 | ✓ |
| tool_surface/tool_surface_info | 5 | 5 | ✓ |
| tool_surface/search_criteria_and_pagination | 33 | 33 | ✓ |
| tool_surface/search_blacklist_fast_path | 3 | 3 | ✓ |
| tool_surface/reason_code_contract | 23 | **24** | ✗ (M-01) |
| tool_surface/non_goal_rejection | 30 | 30 | ✓ |
| tool_surface/list_messages | 7 | 7 | ✓ |
| tool_surface/list_attachments | 7 | 7 | ✓ |
| tool_surface/folder_path_contract | 7 | 7 | ✓ |
| tool_surface/default_scope_transparency | 8 | 8 | ✓ |
| tool_surface/create_reply_draft | 18 | 18 | ✓ |
| tool_surface/create_draft_error_visibility | 5 | 5 | ✓ |
| tool_surface/bulk_mark_tagged | 7 | 7 | ✓ |
| tool_surface/bulk_mark_seen | 5 | 5 | ✓ |
| tool_surface/fetch_body_attachment_metadata | 3 | 3 | ✓ |
| tool_surface/fetch_body_html_fallback | 2 | 2 | ✓ |
| **Summe** | **648** | **644** | 5 Dateien falsch |

### 1.2 Nachrechnung von `docs/error_path_analysis.md`

Alle 18 Zeilensummen habe ich gegen die Einzelzeilen L1–L18 nachgezählt. Sie stimmen exakt:

| Kennzahl | Ergebnis |
|---|---|
| Total | 22+8+12+22+14+9+11+10+28+11+15+13+12+40+16+32+25+18 = **318** ✓ |
| Covered | 8+2+11+20+8+2+5+7+24+8+14+13+11+37+16+32+25+18 = **261** ✓ |
| Deferred | 14+6+1+2+6+7+6+3+4+3+1+0+1+3+0+0+0+0 = **57** ✓ |
| Gap | 0 |
| `@pending` | Keine Einträge |
| `covered_by_LIM-*` | Keine Zuschläge mehr; diese Bezeichnung kommt nur noch in historischen LIM-Bodies vor |

Die Arithmetik ist korrekt. Die inhaltliche Vollständigkeit ist es nicht (siehe M-02 und H-12).

---

## 2. Befunde

### BLOCKER

Keine.

### HOCH

#### H-01 — Der zentrale Non-Interference-Test kann trivial bestehen und hängt von seiner eigenen Reihenfolge ab
- **Guideline:** §4.3 (Positivkontrolle), §13.2 Prüfung 1, §2.1
- **Fundstellen:**
  - `bdd/features/policy/non_interference_and_field_gates.feature:29-53` (Then-Schritt `:43`, Beispiel `:53`)
  - `bdd/features/tool_surface/bulk_mark_seen.feature:7`
- **Nachweis:**
  1. **Keine Positivkontrolle.** Die Antwort `world_without_none` (`:37`) wird nie selbst geprüft: kein `decision`, keine `uids`, kein `total_visible`, kein `tagged_count`. Wenn der Server in beiden Welten `DENY` oder `ERROR` liefert, besteht `:43` trotzdem.
  2. **Zustandsbehaftetes Beispiel `bulk_mark_tagged`.** Der erste Aufruf (Welt A) setzt `$Probed` auf uid 101. Der zweite Aufruf (Welt B) trifft also eine schon markierte Nachricht. Je nachdem, ob `tagged_count` geänderte oder gematchte Nachrichten zählt, ist das Ergebnis 0 oder 1. `bulk_mark_seen.feature:7` legt „modified visible messages only“ fest. Das ADR 0045 §1 lässt die Semantik für `bulk_mark_tagged` offen. Die Gleichheit in `:43` hängt damit an einer unsichtbaren Regel, und die beiden Welten haben nicht denselben Vorzustand.
  3. Die Prüfung von `\Seen` auf uid 102 (`:44`) passt zu keinem Werkzeug der Outline.
- **Behebung:**
  ```gherkin
  When triage-agent calls <call_without_secret>
  Then the response decision is ALLOW
  And the response field <witness_field> equals <witness_value>
  And the JSON response is remembered as "world_without_none"
  Given the IMAP account "gupta-scaratec" is reset and the folder "Sent" holds messages:
    | uid | ... 101, 102, 103 ... |
  When triage-agent calls <call_without_secret>
  Then the JSON response equals the remembered "world_without_none"
  And the IMAP message at "Sent" uid 102 does NOT have keyword "$Probed"
  And the IMAP message at "Sent" uid 103 does NOT have keyword "$Probed"
  And the IMAP message at "Sent" uid 101 has keyword "$Probed"   # nur bulk_mark_tagged
  ```
  Dazu Examples-Spalten `witness_field` / `witness_value`, z. B. `uids` / `[101]`, `tagged_count` / `1`, `visible_count` / `1`. Außerdem `tagged_count` im ADR verbindlich als „modified“ oder „matched“ festlegen.

#### H-02 — Abgelehnte Schreibzugriffe ohne objektbezogenen Zweitkanal
- **Guideline:** §4.3, §13.2 Prüfung 1
- **Fundstellen:**
  - `bdd/features/policy/non_interference_and_field_gates.feature:55-76`: `mark_seen` und `move` auf die NONE-Nachricht uid 201. Then (`:67-70`) prüft nur die Antwort.
  - `bdd/features/policy/write_visibility_and_flow_monotonicity.feature:55-72`: Then `:66` prüft für alle drei Beispiele nur „Archiv is empty“. Für `mark_tagged` ist das das falsche Objekt. Für `move` und `copy` fehlt „INBOX still contains uid 110“.
- **Nachweis:** Gerade bei NONE-Nachrichten ist die Unverändertheit die Sicherheitsaussage. Ein Server, der uid 201 still mit `\Seen` markiert oder verschiebt und danach `not_found` meldet, besteht beide Szenarien.
- **Behebung:**
  ```gherkin
  # non_interference …:70 ergänzen
  And the IMAP message at "Sent" uid 201 does NOT have flag "\Seen"
  And the IMAP folder "Sent" still contains uid 201
  And the IMAP folder "Archiv" is empty
  # write_visibility …:66 ersetzen durch Examples-Spalte <unchanged_check>
  | mark_tagged | ... | the IMAP message at "INBOX" uid 110 does NOT have keyword "$Tagged" |
  | move        | ... | the IMAP folder "INBOX" still contains uid 110                       |
  ```

#### H-03 — Bulk-Schreibvorgänge ohne Zweitkanal
- **Guideline:** §4.3, §13.2 Prüfung 1
- **Fundstelle:** `bdd/features/tool_surface/default_scope_transparency.feature:66-72`
- **Nachweis:** `bulk_mark_seen` und `bulk_mark_tagged` schreiben. Geprüft werden nur `marked_count` und `tagged_count` sowie `applied_scope`. Ob uid 701 wirklich `\Seen` und `$Recent` hat und uid 702 (außerhalb des Fensters) unberührt bleibt, wird nicht geprüft. Das ist aber die eigentliche Aussage des Szenarios.
- **Behebung:**
  ```gherkin
  And the IMAP message at "INBOX" uid 701 has flag "\Seen"
  And the IMAP message at "INBOX" uid 702 does NOT have flag "\Seen"
  ...
  And the IMAP message at "INBOX" uid 701 has keyword "$Recent"
  And the IMAP message at "INBOX" uid 702 does NOT have keyword "$Recent"
  ```

#### H-04 — Die Sichtbarkeitsuntergrenze für Bulk-Schreibvorgänge wird nie mit gemischter Sichtbarkeit geprüft
- **Guideline:** §2.3 (Varianz), §4.3, ADR 0045 §1 (letzter Absatz), ADR 0043 §3.2
- **Fundstellen:** `bdd/features/tool_surface/bulk_mark_seen.feature:25-73`, `bdd/features/tool_surface/bulk_mark_tagged.feature:29-99`
- **Nachweis:** Alle 12 Szenarien säen ausschließlich gleichartige Nachrichten, die alle sichtbar sind und alle passen. Die Kerninvariante lautet: Nachrichten unterhalb der Untergrenze oder unterhalb von `req_vis(criteria)` bleiben unberührt und fehlen in `marked_count` / `tagged_count`. Dafür gibt es kein Szenario. Der Titel von `bulk_mark_tagged.feature:1` („ENVELOPE-visible messages“) wird damit nicht belegt.
- **Behebung:**
  ```gherkin
  Scenario: bulk_mark_tagged skips METADATA-capped matches and excludes them from tagged_count
    Given policy "inbox-policy" grants folder on account "gupta-scaratec":
      | folder | mode      | default | mark_tagged | rules                                   |
      | INBOX  | blacklist | FULL    | true        | [{from_domain=bank.de -> cap METADATA}] |
    And the folder "INBOX" holds messages:
      | uid | from_address    | to_address      | subject   | date                 |
      | 901 | noreply@bank.de | me@example.org  | Beleg A   | 2026-05-06T09:00:00Z |
      | 902 | vendor@obi.de   | me@example.org  | Beleg B   | 2026-05-06T09:00:00Z |
    When inbox-agent calls bulk_mark_tagged with ..., criteria {"newer_than": "7d"}, tags ["archive"], mode "add", scope "all"
    Then the response field tagged_count equals 1
    And the IMAP message at "INBOX" uid 902 has keyword "archive"
    And the IMAP message at "INBOX" uid 901 does NOT have keyword "archive"
  ```
  Analog für `bulk_mark_seen` (Untergrenze METADATA, cap COUNT) und für `req_vis(criteria)` (z. B. `subject_contains` bei cap METADATA).

#### H-05 — Der kontoweite Gmail-cap wird nicht unterscheidbar geprüft, und es fehlt eine Positivkontrolle
- **Guideline:** §2.3, §4.3 (Positivkontrolle), ADR 0040 §3.1
- **Fundstelle:** `bdd/features/policy/write_visibility_and_flow_monotonicity.feature:134-161`
- **Nachweis:**
  - Nachricht 50011 trägt nur das Label `Banking` (`:153`). Ihre Unsichtbarkeit ergibt sich schon aus der Vorrangregel für spezifische Labels in Verbindung mit `Banking cap NONE`. Die kontoweite Wirkung einer `cap`-Regel auf andere Ordner zeigt das Szenario nicht. Eine Implementierung ohne kontoweiten cap besteht es.
  - Es gibt keine Positivkontrolle (eine Hornbach-Nachricht, die über `[Gmail]/All Mail` sichtbar ist). Ein Server, der `All Mail` komplett verweigert, besteht ebenfalls.
  - Fehlerpfad L18.12 behauptet die Abdeckung trotzdem.
- **Behebung:**
  ```gherkin
  And a Gmail message on "g-work" has:
    | x_gm_msgid | message_id          | from_address    | ... | uid_inbox | uid_all_mail |
    | 50012      | <gm-50012@work.org> | noreply@bank.de | ... | 312       | 50012        |
  And the Gmail message 50012 carries labels ["INBOX"]
  And policy rule on INBOX additionally grants {from_domain=bank.de -> FULL}
  When triage-agent calls fetch_body with account "g-work", folder "INBOX", uid 312
  Then the response field error.type equals "not_found"          # kontoweiter cap aus Banking greift
  And a Gmail message 50013 from rechnung@hornbach.de labelled ["INBOX"] is visible via
      fetch_envelope on "[Gmail]/All Mail" uid 50013 with visibility_applied "FULL"   # Positivkontrolle
  ```

#### H-06 — ADR 0043 und ADR 0040 widersprechen sich bei `completeness` auf Gmail
- **Guideline:** §1.1 (Widerspruchsfreiheit), §12
- **Fundstellen:**
  - `docs/adr/0043-non-interference-transparency-and-search.md:37` und `:43-46`
  - `docs/adr/0040-message-centric-policy-and-gmail-composition.md:44-47`
  - `bdd/features/policy/write_visibility_and_flow_monotonicity.feature:145` und `:155`
- **Nachweis:**
  - Laut ADR 0043 gilt `may_hide` genau dann, wenn der Modus `whitelist` ist oder der Ordner eigene `cap < default`-Regeln hat.
  - `[Gmail]/All Mail` ist in `:145` aber als `blacklist FULL rules []` konfiguriert, also gilt `may_hide = false`, und der Server würde `completeness = "complete"` melden.
  - Gleichzeitig blendet `:155` beide Nachrichten aus, nämlich über den kontoweiten cap und den Label-Vorrang aus ADR 0040.
  - Der Agent erhielte damit das falsche Signal „vollständig“. Das Feature prüft `completeness` an dieser Stelle gar nicht.
- **Behebung:**
  - Ein neues ADR, das ADR 0043 §2 für `provider: google` ersetzt: `may_hide = true`, sobald irgendeine kontoweite `cap`-Regel existiert oder der Ordner `[Gmail]/All Mail` oder `[Gmail]/Starred` ist.
  - Im Feature an `:155` ergänzen: `And the response field completeness equals "policy_may_hide"`.

#### H-07 — ADR 0044 §2.6 widerspricht dem Szenario zur reinen Schreib-Revision und ADR 0045
- **Guideline:** §1.1, §12
- **Fundstellen:**
  - `docs/adr/0044-normalized-envelope-and-code-registry.md:65`
  - `bdd/features/policy/draft_edit_capability.feature:181-207`
  - `docs/adr/0045-write-visibility-floors-and-flow-monotonicity.md:68`
- **Nachweis:** ADR 0044 verlangt: Wenn eine adressierte `draft_id` auf `eff_vis(m) < METADATA` auflöst, ist die Antwort `ALLOW + ERROR not_found`. Das Feature erwartet aber `update_draft` → `OK` mit revision 2 auf einem Drafts-Ordner `whitelist NONE rules []`, also bei `eff_vis = NONE`. ADR 0045 Neutral stützt das Feature. Zwei Accepted-ADRs widersprechen sich hier, und die Implementierung muss eines davon brechen.
- **Behebung:** Ein ADR, das ADR 0044 §2.6 ersetzt und eine ausdrückliche Ausnahme festlegt: Eigene Drafts mit verifizierter Provenienz sind für `update_draft` und `discard_draft` vom Sichtbarkeits-Gate ausgenommen. In `draft_edit_capability.feature` diese Regel im Feature-Text nennen.

#### H-08 — Existenz-Orakel für Draft-Handles: `draft_not_editable` statt `not_found` bei fremden oder unsignierten Handles
- **Guideline:** §1.1 (ADR-Treue), Non-Interference (ADR 0043 §1, ADR 0044 §2.6)
- **Fundstellen:**
  - `bdd/features/tool_surface/update_draft_errors.feature:80-99`
  - `docs/adr/0042-cryptographic-and-wal-provenance.md:48-52`
  - `bdd/features/policy/provenance_own_and_draft_ownership.feature:114-131`
  - `docs/limitations/0022-externally-edited-drafts-lose-their-handle.md:24-25`
- **Nachweis:**
  - Die Nachricht stammt von `anika@example.com`, hat keine `X-SC-Provenance` und hat keinen WAL-Eintrag dieses Callers. Laut ADR 0042 gilt „non-existent or foreign draft_id → not_found“. `draft_not_editable` ist nur für eigene, extern veränderte Drafts vorgesehen.
  - Das Feature erwartet trotzdem `draft_not_editable`.
  - Dadurch kann ein Caller in einem reinen Schreib-Drafts-Ordner (`list_drafts` verweigert) unterscheiden, ob ein bekannter Handle noch existiert: `draft_not_editable` heißt „existiert“, `not_found` heißt „existiert nicht“.
  - Für fremde, aber signierte Drafts liefert `provenance…:128` dagegen `not_found`. Das Verhalten ist also inkonsistent.
- **Behebung:**
  ```gherkin
  Scenario: Unsigned message carrying an unknown X-SC-Draft-Id is indistinguishable from a missing handle
    ...
    Then the response field error.type equals "not_found"
    And the audit log contains an entry with audit_detail_reason "foreign_caller"
  ```
  `draft_not_editable` nur dort erwarten, wo ein eigener WAL-Eintrag existiert und der HMAC wegen einer externen Änderung scheitert (wie in `provenance…:68-88`).

#### H-09 — Kryptographische Literale lassen sich nicht nachrechnen, und `X-SC-Draft-Change-Hash` fehlt im ADR
- **Guideline:** §2.2, §13.2 Prüfung 2
- **Fundstellen:**
  - `bdd/features/audit/audit_log_format.feature:44`, `:125` (`hmac-sha256:c893…`), `:142` (`hmac-sha256:9f5c…`)
  - `bdd/features/tool_surface/update_draft.feature:191`, `:199`, `:225`, `:239` (vier 64-hex-Werte)
  - `docs/adr/0042-cryptographic-and-wal-provenance.md:26-32`
- **Nachweis:**
  - **Audit-HMACs:**
    - Der Schlüssel liegt in `:44` als 64-stellige ASCII-Hex-Zeichenkette vor. Ob als HMAC-Key die 64 ASCII-Bytes oder die 32 dekodierten Bytes dienen, steht nirgends.
    - Beim `search_query_digest` bleibt offen, welche JSON-Struktur kanonisiert wird: nur `criteria` oder auch `account`, `folder` und `scope`.
    - Beim `from_domain_digest` bleibt offen, ob die Eingabe `bank.de` oder ein Präfix ist.
    - Die Literale sind damit „magic values“ im Sinne von §13.2 P2.
  - **Change-Hash:** Es gibt überhaupt keine Formel (`:9` nennt nur „canonical_changes_hash“). Der Header `X-SC-Draft-Change-Hash` fehlt in ADR 0042, das genau drei `X-SC-*`-Header vorschreibt und alle anderen entfernt. Auch die Semantik des idempotenten Replays (`idempotent_replay`, kein `tx_id`) beruht auf keinem ADR.
- **Behebung:**
  - Im Feature einen DocString mit der Regel einfügen:
    ```gherkin
    And the HMAC rule for audit digests is:
      """
      key   = bytes.fromhex(<file content>)            # 32 bytes
      input = UTF-8 of canonical JSON of the criteria object only, sort_keys, separators (",", ":")
      value = "hmac-sha256:" + hex(HMAC_SHA256(key, input))
      """
    ```
  - Für `X-SC-Draft-Change-Hash` die Formel analog im Feature angeben, z. B. `sha256(str(base_revision) || "\n" || canonical_json(changes))`.
  - Den Header, die Replay-Regel und die Ausnahme vom Strip-Gebot in einem neuen ADR festhalten, das ADR 0042 ersetzt.

#### H-10 — Das Concurrency-Szenario erwartet eine unerklärte zweite Nachricht und widerspricht damit dem eigenen Feature
- **Guideline:** §12, §2.1
- **Fundstelle:** `bdd/features/tool_surface/update_draft.feature:269-273`, im Vergleich zu `:36-46` und `:60-72`
- **Nachweis:**
  - Der Background legt genau einen Draft an. Nach einem erfolgreichen CAS-Sieger muss laut `:60-72` („never duplicated“) genau eine Nachricht mit `X-SC-Draft-Id` existieren.
  - `:273` erwartet „exactly 2 messages … across the background draft and the scenario, of which 1 carries the returned draft_id at revision 2“. Woher die zweite Nachricht stammt, ist nicht erkennbar.
  - Entweder bleibt Rückstand des Verlierers liegen (dann ist der Lease-Schutz verletzt) oder das Then ist falsch.
- **Behebung:**
  ```gherkin
  Then 1 concurrent update_draft response has result "OK" with revision 2 and 1 has result "ERROR" with error.type "draft_revision_conflict"
  And the IMAP folder "Drafts" contains exactly 1 message with header "X-SC-Draft-Id" set
  And that message carries X-SC-Draft-Rev "2" and the subject of the winning call
  ```

#### H-11 — Die Erwartung zum Message-ID-Kollisionsfall (S-07) ist nicht deterministisch
- **Guideline:** §2.1, §12, ADR 0046 §2
- **Fundstelle:** `bdd/features/transactions/saga_crash_recovery.feature:105-112`
- **Nachweis:**
  - „reaches a terminal or operator state without losing the authentic payload“ (`:111`) und „at least one mailbox … contains“ (`:112`) lassen mehrere Ergebnisse zu: `committed`, `needs_operator` oder das Löschen der gefälschten Zielkopie.
  - ADR 0046 §2 legt das Ergebnis eindeutig fest: Bei einem Crash `post_fetch` gibt es noch keinen APPEND. Also wird angehängt, die neue UID verifiziert und die Quelle erst danach gelöscht. Die gefälschte Kopie bleibt unberührt.
  - Auch die Unversehrtheit der gefälschten Nachbarnachricht im Ziel wird nicht geprüft.
- **Behebung:**
  ```gherkin
  Then within 10 seconds the WAL entry for the crashed move has status "committed"
  And the folder "personal:Archiv/Belege" contains exactly 2 messages with message-id "<m-908@hornbach.de>"
  And exactly one of them has a body ending with "AUTHENTIC-TAIL-119EUR"
  And exactly one of them has a body ending with "FORGED-TAIL-000000EUR"
  And a direct IMAP SEARCH on "gupta-scaratec:INBOX/Rechnungen" for message-id "<m-908@hornbach.de>" returns zero results
  ```

#### H-12 — Codes aus der geschlossenen Registry ohne ein einziges Szenario, dazu ein Widerspruch beim gpg-Fehlerpfad
- **Guideline:** §4.5, §2.3, LIM 0001 (Resolution)
- **Fundstellen:**
  - `docs/adr/0044-normalized-envelope-and-code-registry.md:77-78`
  - `docs/adr/0050-secret-stores-and-oauth-posture.md:28` und `:63`
  - `bdd/features/auth/secret_store_backends.feature:125-138`
  - `bdd/features/tool_surface/reason_code_contract.feature:9-14`
  - `docs/limitations/0001-reason-code-symmetry-in-bdd.md:10-13`
- **Nachweis:**
  - Die `error.type`-Werte `secret_unavailable` und `saga_aborted` sowie der audit-only-Grund `internal_error` (ADR 0048 §1 „Total coverage“) erscheinen in keinem Feature und in keiner Zeile von `error_path_analysis.md`.
  - LIM 0001 gilt als „Resolved“ unter Berufung auf „variance-verified Gherkin contract scenarios“. Diese Codes sind aber weder erreichbar noch variiert.
  - ADR 0050 §1 schreibt vor: gpg-Entschlüsselungsfehler → `ALLOW + ERROR secret_unavailable`. Das Feature erwartet in `:131` `auth_failed`. Für das Caller-Token vor dem Handshake ist `auth_failed` plausibel. Das ADR unterscheidet diese Fälle aber nicht, und für Konto-Secrets fehlt jedes Szenario.
- **Behebung:**
  - Szenarien ergänzen:
    - Konto-Passwort als `gpg_file` mit falscher Passphrase → `list_folders` liefert `ALLOW + ERROR secret_unavailable`, und der Audit enthält keinen gpg-stderr.
    - Saga-Abbruchpfad (`saga_aborted`).
    - Unerwartete Exception (Test-Hook) → Audit-Eintrag `reason: internal_error`, `error_class` gesetzt, keine Exception-Message.
  - Die zugehörigen L-Zeilen in `error_path_analysis.md` nachtragen.
  - ADR 0050 präzisieren: Caller-Token-Secrets führen zu `auth_failed`, Konto-Secrets zu `secret_unavailable`.

#### H-13 — Governance-Verstoß: mehrere Entscheidungen pro ADR
- **Guideline:** `governance/adr/README.md` Schreibregel 1; `docs/adr/README.md:48`
- **Fundstellen** (jeweils der Decision-Satz):
  - `0040:22` (Hierarchie, Zwei-Stufen-PDP, Label-Komposition, `object_id`, Label-Offenlegung, Trash-Regel)
  - `0044:18` (4 fett markierte Entscheidungen)
  - `0045:18` (5)
  - `0046:19` (6)
  - `0048:20` (5)
  - `0049:17` (4)
  - `0050:18` (4)
- **Nachweis:** Die Titel verbinden mehrere Entscheidungen mit „and“. Damit wiederholt sich das Problem, dass künftige Ablösungen nur teilweise greifen. Genau das hat die vielen „Superseded by …“-Ketten verursacht.
- **Behebung:** Die ADRs vor dem Release-Tag aufteilen. Sie sind von heute, also ist das noch ohne Immutabilitätsverletzung möglich. Zum Beispiel ADR 0044 in „Normalized Envelope“, „Canonical Folder Resolution“, „Opaque not_found“ und „Closed Code Registry“.

#### H-14 — Pflicht von mindestens 3 Alternativen verfehlt
- **Guideline:** Prüfauftrag „>= 3 Alternativen“; `governance/adr/README.md` Abschnitt Format
- **Fundstellen:**
  - `docs/adr/0053-non-interfering-telemetry-and-usability-isolation.md:57-60` (2)
  - `docs/adr/0054-generic-mail-mediation-scope-boundary.md:44-47` (2)
  - `docs/adr/0055-bounded-per-account-connection-pool.md:54-57` (2)
- **Behebung:** Die tatsächlich diskutierten dritten Optionen nachtragen, zum Beispiel:
  - 0053: „Bucketed visible counts in traces“
  - 0054: „In-process OCR behind feature flag“
  - 0055: „Global cross-account pool / adaptive max_size“

#### H-15 — Das Test-Mode-Gate in ADR 0048 ist unvollständig, und der Usability-Runner widerspricht ihm
- **Guideline:** §1.1 (ADR-Konsistenz)
- **Fundstellen:**
  - `docs/adr/0048-pre-effect-audit-and-test-boundary.md:46` (geschlossene Liste mit 6 Env-Variablen) im Vergleich zu `:50` (Startup-Invariante nennt nur `CRASH_AT`, `FAKE_NOW_UTC`, `FAKE_NOW_FILE`, `SAGA_PAUSE_*`)
  - `bdd/features/audit/audit_log_format.feature:259-262`
  - `usability/README.md:213`
- **Nachweis:**
  - `IMAP_MCP_SAGA_RESUME_FILE` und `IMAP_MCP_APPEND_TIMEOUT` sind als Test-Hooks deklariert, werden beim Start aber nicht geprüft. In Produktion würden sie also unbemerkt wirken.
  - Das Feature variiert nur `FAKE_NOW_UTC`.
  - Der Usability-Runner übergibt `IMAP_MCP_FAKE_NOW_UTC` ohne dokumentiertes `IMAP_MCP_TEST_MODE=1`. Laut ADR müsste der Server dann den Start verweigern. Ist `TEST_MODE` doch gesetzt, kann der Agent die versteckten `_test_run_*`-Tools aufrufen.
- **Behebung:**
  - ADR-Invariante: „any variable listed in §4.1“.
  - Feature-Outline über alle 6 Variablen:
    ```gherkin
    Scenario Outline: Test hook <var> without IMAP_MCP_TEST_MODE refuses startup
      When the server process is started without IMAP_MCP_TEST_MODE and with environment <var>="<value>"
      Then the server refuses to start
    ```
  - In `usability/README.md` den Test-Mode-Status und das Risiko für die Agenten-Oberfläche dokumentieren.

### MITTEL

#### M-01 — Fünf §4.5-Header falsch gezählt, eine Kategorie falsch benannt
- **Guideline:** §4.5
- **Fundstellen und Nachrechnung:**
  - `policy/write_visibility_and_flow_monotonicity.feature:18-24`: Header 16, tatsächlich 15 = 1+3+3+1+2+1+1+3. Die Gmail-Kategorie „3“ umfasst nur 2 Szenarien, denn das Label-add-Szenario ist schon unter Flow gezählt.
  - `policy/write_capabilities.feature:10-18`: Header 17, tatsächlich 15. mark_seen wird nach Szenarien gezählt (3), mark_tagged nach Unterfällen (4, obwohl es 2 Szenarien sind). Die Zählweise ist uneinheitlich.
  - `auth/caller_authentication.feature:10-17`: Header 13, tatsächlich 12. Das Szenario `:91-101` wird als „valid token“ und als „caller_addr“ doppelt gezählt.
  - `providers/gmail_label_semantics.feature:18-29`: Header 11, tatsächlich 10. `:142-161` wird doppelt gezählt.
  - `tool_surface/reason_code_contract.feature:9-14`: Header 23, tatsächlich 24. Die Erreichbarkeit umfasst 16 Szenarien (3 ALLOW + account_hidden + folder_hidden + 5 Outline-Zeilen + COUNT + capability + forbidden + relocation + recipient + provider), nicht 15.
  - `policy/non_interference_and_field_gates.feature:16`: „mixed METADATA + FULL“. Das zweite gezählte Szenario `:114-126` ist aber ENVELOPE + FULL.
- **Behebung:** Einheitlich nach expandierten Szenarien zählen und die Header korrigieren. Wo bewusst nach Unterfällen gezählt wird, die Zuordnung offenlegen, z. B. `: 2 (scenario :142 covers both)`.

#### M-02 — Inhaltliche Lücken in `error_path_analysis.md` und Mock-Selbsttests als SUT-Abdeckung
- **Guideline:** §4.5, §1.2
- **Fundstellen:** `docs/error_path_analysis.md:12-32` sowie die Zeilen L12.2 (`:292`), L14.11 (`:340`), L14.25–L14.28 (`:354-357`), L14.34–L14.36 (`:363-365`)
- **Nachweis:**
  1. **Nicht aufgezählte Fehlerpfade aus Accepted-ADRs:**
     - `draft_ambiguous`, `empty_reply_text`, `sink_not_configured`, `sink_not_writable` (fehlender Pfad / read-only, Unlink nach ENOSPC), `draft_revision_conflict` (als eigene Zeile), `secret_unavailable`, `saga_aborted`
     - `internal_error` im Audit
     - ADR 0049 §3.1 (`..data` instabil → 3 Retries → fail closed)
     - ADR 0046 §4 (Policy-Widerruf beim Startup-Recovery: vor APPEND → `aborted`, `staged` ohne `move_out` → `needs_operator`)
     - ADR 0053 (`clientInfo` auf 128 Zeichen gekürzt)
     - ADR 0050 (Fingerprint-Wechsel löscht `needs_rebootstrap`, worauf LIM 0021:17 sich stützt)
  2. **L12.2 behauptet „startup & SIGHUP“.** Ein Szenario für einen YAML-Syntaxfehler beim Start existiert nicht. Nur der SIGHUP-Fall ist abgedeckt (`policy_reload.feature:58-72`, `projected_config_reload.feature:69-74`).
  3. **Acht „covered“-Einträge prüfen den Mock, nicht das System unter Test:** L14.11, L14.25–28 und L14.34–36, also die Szenarien „The mock …“. Fixture-Validierung zählt nicht als Fehlerpfad des Systems.
- **Behebung:** Zeilen ergänzen. Die acht Mock-Selbsttests in einen eigenen Abschnitt „Fixture-Fidelity (nicht gezählt)“ verschieben und die Summen neu rechnen. L12.2 auf SIGHUP beschränken oder ein Startup-Szenario ergänzen.

#### M-03 — Zeitgrenzen werden nie exakt an der Grenze geprüft
- **Guideline:** §2.3; der Prüfauftrag betont Uhrzeiten bei Duration-Tests
- **Fundstellen:**
  - `bdd/features/tool_surface/search_criteria_and_pagination.feature:66-91`
  - `bdd/features/tool_surface/search_completeness.feature:60-70`
  - `bdd/features/providers/gmail_search_and_counts.feature:76-86`
  - ADR 0041:39-42
- **Nachweis:**
  - ADR 0041 legt fest: `newer_than` ist inklusiv (`<=`), `older_than` strikt (`>`), `since` inklusiv, `before` exklusiv.
  - Alle Beispiele liegen mindestens 15 Minuten bzw. einen Tag von der Grenze entfernt, z. B. bei 60s mit 11:59:30 gegenüber dem Cutoff 11:59:00.
  - Ein Fehler wie `<` statt `<=` oder ein Off-by-one bei `before` bleibt unentdeckt.
- **Behebung:**
  ```gherkin
  Examples: boundary
    | uid_a | uid_b | duration | date_in_window       | date_outside         |
    | 2071  | 2072  | 60s      | 2026-05-07T11:59:00Z | 2026-05-07T11:58:59Z |
  # calendar: Nachricht mit INTERNALDATE 2026-10-01T00:00:00Z bei before "2026-10-01" -> ausgeschlossen,
  #           bei since "2026-10-01" -> enthalten
  ```

#### M-04 — Varianzlücken bei sicherheitskritischen Prädikaten und Schutzregeln
- **Guideline:** §2.3, §4.2
- **Fundstellen:**
  - `policy/sender_rule_matcher.feature:55`: `all_recipients_in_domains` im Regelkontext. Keine Nachricht mischt erlaubte und fremde Empfänger, also würde „any“ dasselbe Ergebnis liefern. Im Criteria-Kontext unterscheidet `search_criteria_and_pagination.feature:59` korrekt.
  - `policy/sender_rule_matcher.feature:125-136`: INTERNALDATE gegenüber gefälschtem `Date:`. Nur eine Richtung ist geprüft, ohne Positivkontrolle. Ein Server, der alles verbirgt, besteht.
  - `policy/whitelist_folder_mode.feature:58-71`: Nur eine Regelreihenfolge. „Last match wins“ besteht ebenfalls.
  - `policy/flagged_predicate.feature:6` behauptet „setting or clearing“. `:97-123` prüft nur `mode "add"`. Das Entfernen über `remove` oder `replace` mit `\Flagged` fehlt.
  - `policy/write_visibility_and_flow_monotonicity.feature:186-210`: `draft_recipients` ohne Cc-Fall und ohne `rfc822`-Modus mit `Bcc:`-Header (ADR 0045:49 nennt beide ausdrücklich).
  - `tool_surface/create_reply_draft.feature:178-203`: Der Ausschluss von `message/rfc822`-Unternachrichten (ADR 0045:48) ist nicht geprüft.
  - `tool_surface/inline_attachment_detection.feature:24-56`: Nur Regel (b) aus ADR 0047 §1. Regel (c) (Nicht-Text mit Dateiname ohne Disposition) und der Negativfall (inline text/plain ohne Dateiname) fehlen.
  - `policy/provenance_own_and_draft_ownership.feature:43-58`: Nur der HMAC-Zweig von ADR 0042 §4.2. Der WAL-Zweig (Header beim Senden entfernt, Abgleich über Message-ID und Digest) fehlt.
- **Behebung:** Je ein unterscheidendes Beispiel ergänzen, z. B. für `all_recipients_in_domains`:
  ```gherkin
  | 404 | x@a.example | me@scaratec.com | leak@evil.example | Mixed | 1024 | 2026-04-20T09:00:00Z |
  # all_recipients_in_domains=["scaratec.com","ra-buechner.de"] -> [401, 402], 404 NICHT enthalten
  ```

#### M-05 — Varianz bei kanonischen Caller-Reason-Codes
- **Guideline:** §2.3; LIM 0001 Mitigation 4
- **Fundstellen:**
  - `policy_alias_conflict` kommt nur in `providers/localized_gmail_resolution.feature:167-180` vor.
  - `tool_not_applicable_for_provider` kommt zweimal mit identischer Eingabe vor (`list_labels` auf imap-standard): `gmail_label_semantics.feature:163-166` und `reason_code_contract.feature:166-169`.
  - `visibility_below_HEADERS` und `visibility_below_COUNT` haben jeweils zwei identische Muster: `visibility_levels.feature:30-36` und `:61` gegenüber `reason_code_contract.feature:118` und `:122-128`.
- **Behebung:** Ein zweites, inhaltlich verschiedenes Szenario je Code, z. B.:
  - Alias-Konflikt über `[Gmail]/Sent Mail` / `[Gmail]/Gesendet` mit unterschiedlichen `default`-Werten
  - `visibility_below_COUNT` über eine `blacklist`, deren einzige Regel `cap NONE` ist, bei `default COUNT`?
  - `visibility_below_HEADERS` per cap HEADERS→ENVELOPE in einem BODY-Ordner

#### M-06 — Zeitregel und Reply-all-Zusammensetzung in `create_reply_draft` nicht sichtbar
- **Guideline:** §2.1, §13.2 Prüfung 2
- **Fundstellen:** `bdd/features/tool_surface/create_reply_draft.feature:45`, `:69`; `:59-63`
- **Nachweis:**
  - Die Attribution „On 2026-05-15 10:56“ verwendet die Ortszeit aus dem Date-Header (`+0300`), nicht UTC (07:56Z). Die Regel steht nirgends, und nur dieses Szenario unterscheidet beide Lesarten.
  - Beim Reply-all wird nur geprüft, was im Cc fehlt (agent@). Die positive Cc-Zusammensetzung (`ops@scaratec.bg`, `partner@third.example`) wird nicht geprüft.
- **Behebung:** Im Feature-Text festlegen: „Attribution uses the source Date header's local wall-clock time (YYYY-MM-DD HH:MM), without offset; falls back to omission when Date is absent.“ Außerdem ergänzen: `has header "Cc" containing exactly "ops@scaratec.bg, partner@third.example"`.

#### M-07 — Erwartungen ohne sichtbare Herleitung oder ohne ADR-Grundlage
- **Guideline:** §2.2; Rückverfolgbarkeit Feature → ADR
- **Fundstellen:**
  - `auth/oauth2_bootstrap.feature:111-115`: „3 AUTHENTICATE XOAUTH2 commands“. Ob die zweite Anfrage überhaupt neu authentifiziert oder die gepoolte Verbindung (max_size 2, idle_ttl 120 s) wiederverwendet, ist nicht ableitbar.
  - `policy/policy_reload.feature:115-126`: „OAuth scope change → needs_rebootstrap“. Kein Accepted-ADR legt das fest. ADR 0050 kennt nur `invalid_grant` und die Ablehnung beim Start.
- **Behebung:** Im Given angeben, dass der Revoke alle offenen Sessions trennt („…and closes every authenticated connection“). Die Scope-Wechsel-Regel in einem ADR verankern oder das Szenario streichen.

#### M-08 — Die „geschlossene“ Registry deckt Codes aus anderen ADRs und Features nicht ab
- **Guideline:** §1.1, §12
- **Fundstellen:**
  - Registry: `docs/adr/0044…:75-78`
  - Fehlende Codes:
    - `ambiguous_target_match` (ADR 0046:38; `saga_crash_recovery.feature:134`)
    - `invalid_grant` (ADR 0050:40) im Vergleich zum Feature `token_refresh_failed` (`oauth2_bootstrap.feature:129`)
    - `identity_immutable` (`caller_authentication.feature:131`; LIM 0007:121-122)
    - `retention_delete` und Step `escalated` (ADR 0048:40, ADR 0046:78)
- **Behebung:** Die Registry in ADR 0044 vervollständigen (ggf. durch ein ablösendes ADR) und ADR 0050 auf `token_refresh_failed` angleichen.

#### M-09 — Echter Programmcode in ADRs
- **Guideline:** `governance/adr/README.md` Schreibregel 5
- **Fundstellen:**
  - `0041:50` (`email.utils.getaddresses`)
  - `0042:22` (`secrets.token_hex(32)`), `:50` (`hmac.compare_digest`)
  - `0044:61` (`_quote_mailbox`)
  - `0046:52` (`row is not None and row.caller_id == context.caller_id`)
  - `0047:27-28`, `:36-38` (`os.open(...)`, `os.mkdir(..., dir_fd=...)`)
  - `0048:33`, `:41` (`subprocess.run(..., shell=False, env=...)`, `shlex.split`), `:40` (`_seal_past_days`)
  - `0049:21` (`len(callers) == 1`), `:39-40` (`os.readlink`, `yaml.safe_load`)
- **Behebung:** Als Mechanik beschreiben, z. B. „opened relative to the parent directory descriptor with no-follow and exclusive-create semantics“. Konkrete API-Namen gehören in die Implementierung oder ein verlinktes Designdokument.

#### M-10 — Formmängel in weiterhin geltenden Alt-ADRs
- **Guideline:** Prüfauftrag „Security Implications inkl. Auditability“, „keine privaten URLs“, Schreibregel 3
- **Fundstellen:**
  - `docs/adr/0012…:78-100`: kein Auditability-Punkt
  - `docs/adr/0037…:58-66`: kein Auditability-Punkt; `:79` verweist auf den internen Pfad `.scaratec/handbuch/observability.md`
  - `docs/adr/0002…:47-48`, `:51`: verweist als geltende Regel auf die abgelösten Transparenzfelder aus ADR 0017
- **Behebung:** Die Lücken über ein ablösendes ADR schließen oder dokumentieren, warum keine Auditability-Auswirkung besteht.

#### M-11 — Formale Mängel in LIMs
- **Guideline:** `governance/limitations/README.md` (Lifecycle, Resolved, Withdrawn), `TEMPLATE.md`
- **Fundstellen:**
  - **Commit-Feld ohne SHA:**
    - `0001:16` („3.0.0 registry paydown“)
    - `0009:20` („3.0.0 automatic day-seal trigger“)
    - `0012:10`, `0013:10`, `0016:10`, `0017:10` („3.0.0-release (see git log)“)
  - **`Resolved` ohne Abschnitt Resolution oder Resolution note:** `0001`, `0002`, `0003`, `0006`, `0008`
  - **Widersprüchliche Daten in LIM 0009:** Kopf `:14` sagt 2026-11-17, Abschnitt `:97` sagt 2026-05-06.
  - **LIM 0016:** Abschnitte „Residual risk“ und „Triggers for revisit“ fehlen.
  - **`Resolved` bei Intent `permanent`** (Widerspruch in sich): `0013:3-4`, `0016:3-4`
- **Behebung:** Echte SHAs eintragen. „Resolution note“-Abschnitte ergänzen. Bei `permanent` + `Resolved` stattdessen `Superseded` bzw. die Intent-Änderung über eine neue Freigabe dokumentieren.

#### M-12 — `Accepted`-LIMs mit veraltetem, ADR-widersprechendem Inhalt
- **Guideline:** `governance/README.md` (LIM sagt dem Agenten, was er nicht reparieren soll); §1.1
- **Fundstellen:**
  - **LIM 0014** `:23-27`, `:46-50`: „returns the filename“, `saved_to: "filename.pdf"`. Laut ADR 0047 ist es aber `<caller_id>/<artifact_id>_…` plus `saved_path`.
  - **LIM 0015** `:31-35`, `:58-62`: md5-Dateiname mit Überschreiben; der Audit nennt den absoluten Pfad. Beides widerspricht ADR 0047 (frische `artifact_id`, kein Pfad im Audit).
  - **LIM 0019** `:191-197`: Der Trigger „distinct OS principal … can move to Mitigated“ ist durch ADR 0051 eingetreten. Der Status bleibt trotzdem `Accepted`. Das Dokument stützt sich durchgehend auf abgelöste ADRs (0001, 0014, 0015, 0021).
  - **LIM 0020** `:18-27`, `:79-82`, `:134-137`: StatefulSet und ADR 0029/0022/0032 werden zitiert, gültig sind aber ADR 0051 (Recreate-Deployment) und ADR 0048.
  - **LIM 0021** `:30-37`, `:44-48`, `:84-85`, `:98-105`:
    - Behauptet, die CLI öffne keinen Listener (ADR 0050 sieht Loopback als Standard vor).
    - Behauptet, LIM 0017 und 0018 belegten offene Lücken (0017 ist zurückgezogen, 0018 hält die Lücke für geschlossen).
    - Spricht von „deferred … debt recorded“, obwohl der Intent `permanent` ist.
- **Behebung:** Die Bodies an 3.0.0 anpassen (über einen neuen LIM-Record, der den alten ablöst, falls das Owner-Review verlangt wird). LIM 0019 auf `Mitigated` setzen. LIM 0021 in einen `permanent`-Teil (Consent auf der Workstation) und einen `must-resolve`-Teil (Device Grant, beschreibbarer Cloud-Store) aufteilen.

#### M-13 — LIM 0001 ist zu Unrecht als `Resolved` markiert
- **Guideline:** `governance/limitations/README.md` (Resolution intent)
- **Fundstelle:** `docs/limitations/0001-reason-code-symmetry-in-bdd.md:3-13` im Vergleich zu `:104-105`
- **Nachweis:** Der unveränderte Body nennt die Symmetrie „structural to contract testing, not a defect“. Eine Registry beseitigt diese Symmetrie nicht. Zudem hängt die Auflösung an der Erreichbarkeit aller Codes, und die ist nicht gegeben (H-12).
- **Behebung:** Durch eine neue LIM mit Intent `permanent` ablösen oder wieder auf `Accepted` setzen.

#### M-14 — Titel versprechen mehr, als die Then-Schritte belegen
- **Guideline:** §12
- **Fundstellen:**
  - `policy/draft_edit_capability.feature:209`: Titel „denies get_draft with not_found“, Then `:224-225` prüft aber `DENY visibility_below_BODY`.
  - `policy/default_deny.feature:54`: „before mailbox selection“ wird nicht belegt (kein Proxy-Check auf `SELECT`).
  - `policy/visibility_levels.feature:72`: „exposes UIDs and sizes“; `size_bytes` wird nicht geprüft.
  - `tool_surface/response_transparency.feature:81`: „deterministic gate priority“. Die Beispiele `:97-98` enthalten keine gleichzeitig verletzten Gates, die Reihenfolge wird also nicht geprüft.
  - `tool_surface/mcp_tool_discovery.feature:108`: „and per-message floor“ wird nicht geprüft.
  - `policy/write_capabilities.feature:149`: „can deposit … nor copy back out“; im Szenario wird keins von beiden ausgeführt.
  - `tool_surface/create_reply_draft.feature:42`: „signed draft“ ohne Provenienz-Check.
  - `policy/draft_consistency.feature:45`: „stores the exact draft“, geprüft wird nur der Betreff.
  - `policy/provenance_own_and_draft_ownership.feature:90`: „spoofing … does not match“ deckt nur den Fall mit verändertem Body ab.
- **Behebung:** Titel anpassen oder Then-Schritte ergänzen. Für die Gate-Reihenfolge zum Beispiel:
  ```gherkin
  | move with source {... "folder": "Archiv", "uid": 1}, target {... "folder": "Banking"} | folder_hidden |
  # Quelle ohne move_out UND Ziel verborgen -> folder_hidden gewinnt vor capability_missing
  ```

#### M-15 — `required_capability` (Singular) widerspricht ADR 0052 `required_capabilities`
- **Guideline:** §1.1
- **Fundstellen:** `bdd/features/tool_surface/mcp_tool_discovery.feature:108-121`; `docs/adr/0052…:61`
- **Nachweis:** `move`, `copy` und `discard_draft` benötigen zusätzlich `accept_incoming` am Ziel. Eine einzelne Fähigkeit gibt den Vertrag falsch wieder.
- **Behebung:** Die Spalte `required_capabilities` als Liste führen, z. B. `move | ["move_out","accept_incoming(target)"]`.

#### M-16 — Then-Schritte, die mehrere Ergebnisse zulassen oder fehlen
- **Guideline:** §2.1, §4.2
- **Fundstellen:**
  - `tool_surface/create_draft_error_visibility.feature:102` und `:121`: „at most 1 message“. Ob nach `append_failed` oder `append_timeout` ein verwaister Draft existieren darf, bleibt offen. Das führt zu Duplikaten beim Retry.
  - `transactions/draft_revision_recovery.feature:122-140`: Weder die Antwort von `update_draft` noch der IMAP-Zustand nach der Eskalation sind spezifiziert (Wie viele Revisionen bleiben?).
- **Behebung:** Das deterministische Ergebnis festlegen, z. B. „contains exactly 1 message … and list_drafts reports it with editable=true“ bzw. „exactly 2 messages with that draft_id (rev 1 and 2)“.

#### M-17 — Non-Interference in der Telemetrie zu schwach geprüft
- **Guideline:** §4.3, ADR 0053 §1
- **Fundstellen:** `bdd/features/observability/tracing_spans.feature:77-85`; `bdd/features/observability/tracing_payload_mode.feature`
- **Nachweis:**
  - Geprüft wird nur, dass ein bestimmter Attributname fehlt (`mcp.result.matched`). Es gibt keinen Zwei-Welten-Vergleich der Span-Attribute.
  - Die Kürzung von `clientInfo` auf 128 druckbare ASCII-Zeichen (ADR 0053 Security) ist nicht geprüft.
  - Die Redaction über „all 27 tools including nested structures“ ist nur an 3 Tools belegt.
- **Behebung:** Eine Zwei-Welten-Outline wie in H-01, die die Menge der Span-Attribute und ihre Werte vergleicht. Eine `clientInfo`-Outline mit einem 300 Zeichen langen Namen und Steuerzeichen.

#### M-18 — Zirkuläre oder zu schwache Positivkontrollen im Audit
- **Guideline:** §4.3, §1.2
- **Fundstellen:** `bdd/features/audit/audit_log_format.feature:183-209`, `:248-257`
- **Nachweis:**
  - `ya29.super-secret-token-value` liegt in `accounts/dummy/oauth_token` (`:185`), und kein Konto liest diese Datei. Dass der Wert im Audit fehlt, ist also trivial.
  - Für `fetch_body` und `create_draft` wird nicht geprüft, dass sie erfolgreich waren. Ohne Erfolg würden die verbotenen Strings ohnehin nicht im Audit landen.
  - `:252` setzt den Dateimodus 0400 selbst, und `:256` prüft danach genau diesen Modus. Das ist zirkulär.
- **Behebung:** Ein OAuth-Konto, das das Token nachweislich verwendet. Prüfungen `response decision is ALLOW` und `field subject equals …` vor dem Audit-Check. `:252` streichen und den Modus durch den Server versiegeln lassen.

#### M-19 — Usability: versteckte Stammdaten und Widersprüche zum Runner
- **Guideline:** §2.1, §2.4; ADR 0053 §2 („complete, self-consistent ground truth“)
- **Fundstellen:**
  - `usability/features/01…:25`, `02…:21`, `03…:38`, `04…:26` („with the policy of the server variant“)
  - `usability/README.md:230-236`
- **Nachweis:**
  - Die Ground Truth hängt von nicht sichtbaren Policies ab. S4 (`03…:86-95`) braucht Lesezugriff auf Sent Mail, A1 (`04…:29-41`) braucht `move_out` und `accept_incoming`.
  - Die Variante `today` gewährt kein `draft_edit`. Der ideale Pfad von R1 (`02…:26`) ist dort also unmöglich, und `ideal_ratio` wird verzerrt.
  - `README.md:234-236` spricht noch von `@pending`-Serververhalten.
- **Behebung:** Die Policy je Variante als Tabelle im Background zeigen (wie `05…:25-28`). Den idealen Pfad je Variante angeben. Den `@pending`-Satz streichen.

#### M-20 — Unterschiedliche Widerrufssemantik bei Live-Saga und Crash-Recovery
- **Guideline:** §12 (Widerspruchsfreiheit)
- **Fundstellen:** `bdd/features/policy/policy_reload.feature:128-145`; `docs/adr/0049…:41`; `docs/adr/0046…:51`
- **Nachweis:**
  - Live: Eine Saga, die bei `staged` pausiert, schließt nach dem Entzug von `move_out` mit `committed` ab und löscht die Quelle.
  - Nach einem Crash im selben Zustand führt ADR 0046 §4 zu `needs_operator`.
  - Dieselbe Policy-Lage liefert also je nach Crash-Zeitpunkt unterschiedliche Datenergebnisse.
- **Behebung:** Die Semantik in einem ADR vereinheitlichen und den Startup-Pfad als Szenario ergänzen.

#### M-21 — Weitere schreibende Ablehnungen ohne IMAP-Zweitkanal
- **Guideline:** §4.3
- **Fundstellen:**
  - `tool_surface/update_draft_errors.feature:101-111` (`draft_ambiguous`: beide Kopien unverändert?), `:125-133` (`capability_missing`), `:163-171` (future revision)
  - `policy/write_visibility_and_flow_monotonicity.feature:105-107` (`move`: Quelle nicht mehr in INBOX?), `:180-184` (INBOX nicht geprüft)
- **Behebung:** Je ein Schritt der Form `the IMAP folder "Drafts" contains exactly … with X-SC-Draft-Rev "1"` bzw. `the IMAP folder "INBOX" does not contain uid 210`.

### NIEDRIG

| ID | Paragraf | Fundstelle | Befund / Behebung |
|---|---|---|---|
| N-01 | ADR-Format | z. B. `0040:9`, `0041:9`, `0044:9`, `0050:9` | Referenz-Links wie `[ADR 0001]` haben keine Definition und rendern nicht. Inline-Links verwenden. |
| N-02 | ADR-Präzision | `0048:9-16` | „seven defects“, aufgezählt sind sechs. |
| N-03 | ADR-Referenzen | `0049:41`, `0049:77`, `0050:38` | Verweis auf das abgelöste ADR 0013 statt ADR 0055. |
| N-04 | ADR-Lifecycle | `0029`, `0030`, `0031` (Status); `0042:9`, `0051:9` | Der Übergang `Proposed → Superseded` ist im Lifecycle nicht vorgesehen. Historische „(Proposed)“-Erwähnungen. |
| N-05 | Private Pfade | `ROADMAP.md:74`, `:81`, `:87`; `limitations/README.md:5`; `0000:85` | Absolute Pfade unter `/home/randy`, Link außerhalb des Repos, interner Hinweis auf „objbox“. |
| N-06 | LIM-Nummerierung | `0011-search…:3-10`; `limitations/README.md:81` | Die Kollision ist nur durch eine Weiterleitung aufgelöst: Zwei Dateien mit `0011-` existieren weiter, und `Superseded` wird mit `Date resolved` vermischt. Die Weiterleitung als reinen Tombstone ohne Resolution-Felder führen. LIM 0011 (bulk_move) `:19`, `:34`, `:47` zitiert noch die abgelösten ADRs 0026 und 0006. |
| N-07 | §1.1 | `audit/audit_retention.feature:5` | Privater Funktionsname `_seal_past_days` in einem Feature-Text. |
| N-08 | §12 Redundanz | `tool_surface_info.feature` ↔ `mcp_tool_discovery.feature:136-143`; `draft_consistency.feature`; `list_attachments.feature:62-72` ↔ `:28-48`; `default_scope_transparency.feature:61-64` | Doppelte Szenarien zusammenführen. |
| N-09 | Feldnamen | `visibility_levels.feature:98-99`, `response_transparency.feature:107-108` (`body`); `gmail_label_semantics.feature:60` (`semantics`); `fetch_attachment` mit `part_id` (ADR 0047:44) gegenüber `index` (ADR 0043:66) | Feldnamen weichen von ADR 0043 ab. Dass `part_id` bei 0 beginnt, steht nirgends. |
| N-10 | Gherkin-Stil | `non_interference…:38` (Given nach When); `gmail_search_and_counts.feature:67-74`, `gmail_draft_lifecycle.feature:67` (Then ohne When); magische Schritte wie „the only visible message“ und „an interrupted revision leaves …“ | Stil und Lesbarkeit. |
| N-11 | §2.3 Stichprobe | `non_goal_rejection.feature:53-73` | 19 von etwa 31 Namen aus ADR 0052 §3 (es fehlen u. a. `hard_delete`, `delete_draft`, `store_deleted_flag`, `bypass_redaction`, `_test_run_audit_rotation`). `bulk_move` ist als Non-Goal geführt, obwohl LIM 0011 es als aufgeschoben einstuft. |
| N-12 | §2.1 | `gmail_label_semantics.feature:158`; `connection_reuse.feature:39` | Sortierregel für `labels` und die Standard-Seitengröße werden nicht genannt. |
| N-13 | §2.1 | `audit_retention.feature:7-9`, `:71`; `README.md:78-80` | Der Parameter `warm_days` geht in die Regel nicht ein; die README legt eine Dauer-Semantik nahe. |
| N-14 | Header-Text | `health_probe.feature:20` | Nennt 4 Pfade, die Examples haben 5. |
| N-15 | §2.1 | `caller_authentication.feature:45` (Assertion nur `"auth"`); `oauth2_bootstrap.feature:48` (Zuordnung `access_denied` → `user_denied` nicht genannt) | Schwache Assertion bzw. unsichtbare Zuordnung. |
| N-16 | §2.1 | `discard_draft.feature:125-142` | Reihenfolge `trash_unavailable` vor dem Fähigkeits-Gate am Ziel wird nicht genannt. |
| N-17 | Usability | `01…:47-77` (E2/E3 ohne `harmful`-Kriterium); `02…:38` („draft that existed before the run“ läuft ins Leere, Drafts nicht als leer angegeben) | Rubrik-Lücken. |
| N-18 | §4.5 | `error_path_analysis.md` L3.9, L4.19, L8.8, L13.8 | „deferred B“ ohne definierten Grund B. |

---

## 3. Positiv bestätigt

- **27 Tools.** Die Aufteilung 13/10/4 stimmt überein in ADR 0052, README, ROADMAP und `mcp_tool_discovery.feature:33-63`.
- **Keine Altfelder.** In keinem Feature wird ein verbotenes Feld positiv erwartet: `matched_total`, `filtered_out`, `outside_scope_total`, `hidden_*_count`, `canonical_all_mail_uid`, `matched_rule_index`, `missing_capability`, `error_type`, `imap_response`. Sie kommen nur in Absenz-Assertions vor.
- **Opakes `not_found`.** Fehlende UID und UID unterhalb von METADATA werden über JSON-Gleichheit verglichen (`non_interference…:55-76`). `folder_hidden` ist für existierende und nicht existierende Ordner identisch (`folder_path_contract.feature:108-114`).
- **Zweitkanal per Draht/WAL/IMAP** ist durchgängig vorhanden bei Saga, Crash-Recovery, Intra-Move (inklusive Schutz fremder `\Deleted`-Nachrichten), Drafts, Sink-Symlinks und Audit-Intent vor APPEND (`audit_multi_instance.feature:119-133`).
- **Sauber nachrechenbare Erwartungswerte:**
  - `search_criteria_and_pagination.feature`: alle 33 Fälle
  - `search_completeness.feature`, `gmail_search_and_counts.feature`
  - `audit_retention.feature`: `age_days` 831, sechs Altersfälle
  - `fetch_attachment_sink.feature`: RFC-2047-Dekodierung `fa_2026-6_Kuba`, `Rechnung_M_rz`
  - `create_draft_format.feature`: `size_bytes` 9
  - Usability S3: 451,00 EUR
- Die Status-Übergänge aller 36 abgelösten ADRs stimmen mit dem Index überein. Keine LIM steht mehr auf `Proposed`.

---

## 4. Freigabe-Urteil

**NACHARBEIT ERFORDERLICH** — 0 BLOCKER, **15 HOCH**, 21 MITTEL, 18 NIEDRIG.

Vor der Freigabe zwingend zu beheben:

1. **Sicherheitsinvarianten nachweisbar machen:** H-01, H-02, H-03, H-04, H-05, H-11.
2. **ADR-Widersprüche und Orakel auflösen:** H-06, H-07, H-08, H-15.
3. **Nachrechenbarkeit herstellen:** H-09, H-10.
4. **Registry-Abdeckung vervollständigen:** H-12.
5. **ADR-Formgates erfüllen:** H-13, H-14. Das geht noch ohne Verletzung der Unveränderlichkeit, weil alle 16 ADRs vom heutigen Tag sind und 3.0.0 noch nicht getaggt ist.
