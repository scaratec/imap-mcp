# Vollprüfung imap-mcp: Spezifikation, Governance, Berechtigungskonzept und API-Sicherheit

- **Datum:** 2026-11-17
- **Prüfer:** unabhängiger Spezifikations-, Governance- und Sicherheits-Auditor (Claude Opus 5.5)
- **Maßstab:** `BDD_GUIDELINES_v1.8.0_DE.md` (§1–§13), `burn-your-code/governance/{README,adr/README,adr/TEMPLATE,limitations/README,limitations/TEMPLATE}.md`
- **Methode:** Ich habe nur Markdown und Gherkin gelesen. Produktivcode, Step-Code, Konfiguration und Logs habe ich nicht gelesen. Wo dieser Bericht etwas über die Implementierung sagt, stammt die Aussage aus Projektdokumenten (ROADMAP, LIMs, ADRs, Feature-Kommentare). Die Fundstelle ist jeweils angegeben.
- **Schweregrade:** `KRITISCH` > `HOCH` > `MITTEL` > `NIEDRIG`

---

## 1. Inventar und Prüfumfang

| Dokumentgruppe | Gelesen (vollständig) | Bemerkung |
|---|---|---|
| Maßstab / Governance (burn-your-code) | 6 Dateien | Guidelines v1.8.0 DE, governance README, ADR-README + TEMPLATE, LIM-README + TEMPLATE |
| Projekt-Übersichtsdokumente | 7 Dateien | `README.md`, `bdd/README.md`, `usability/README.md`, `docs/ROADMAP.md`, `docs/error_path_analysis.md`, `docs/spec-audit/README.md`, `docs/spec-audit/TEMPLATE.md` |
| Spec-Audit-Zyklen | **0** | `docs/spec-audit/` enthält **keinen einzigen** Zyklusbericht (nur README und TEMPLATE) |
| ADRs | **40 Records** (0000–0039) + `README.md` + `TEMPLATE.md` | — |
| Limitation Records | **23 Records** (0001–0022, davon 0011 doppelt) + `README.md` + `TEMPLATE.md` | — |
| BDD-Feature-Files | **68** (nicht 57, wie im Auftrag angegeben) | policy 11, tool_surface 31, transactions 6, providers 6, auth 4, audit 3, performance 3, deployment 2, observability 2 |
| Usability-Feature-Files | 7 | 01–07 |

Die Zahl der Feature-Files im Auftrag (57) ist veraltet. Auch die Projektdokumente widersprechen sich bei ihren eigenen Kennzahlen:

- `README.md:13` und `README.md:506`: 241 Szenarien. `ROADMAP.md:12`: 419 passed / 34 skipped. `ROADMAP.md:59`: 525 passed.
- `README.md:521`: „33 ADRs“. Tatsächlich sind es 40.
- `README.md:328`: Tool-Set 2.2.0. `ROADMAP.md:9`: Release 2.1.0. `ROADMAP.md:129`: Pool „erledigt 2.3.0“.

---

## 2. Gesamturteil zum Berechtigungskonzept

### 2.1 Diagnose in einem Satz

**Das Konzept schützt Ordner, aber nicht Nachrichten.** Es verspricht `NONE` = „existiert aus Sicht des Callers nicht“ (`ADR 0002:35`). Gleichzeitig legt es in jeder Antwort datenabhängige Zählwerte, unterscheidbare Fehlercodes und ordnergebundene Schreibrechte offen. Diese drei Bauteile machen das Versprechen strukturell unerfüllbar. Der Betriebsvorfall (Sent Mail / All Mail) ist kein Konfigurationsfehler, sondern die vorgesehene Wirkung der Spezifikation: `default_deny.feature:64-71` und `whitelist_folder_mode.feature:109-122` legen genau dieses Orakel als **Soll-Verhalten** fest.

### 2.2 Gebrochene Sicherheitsinvarianten

| # | Invariante, die das Projekt verspricht | Fundstelle | Gebrochen durch |
|---|---|---|---|
| I-1 | `NONE`: „Nothing. The folder or message does not exist from the caller's perspective.“ | `ADR 0002:35` | `matched_total`/`filtered_out` (`ADR 0017:50`), `outside_scope_total` „before policy filtering“ (`ADR 0036:26-30`), `uid_not_found` vs. `sender_not_whitelisted` (`ADR 0017:77`: „The message exists but is out of scope“), `message_count` in `list_folders` |
| I-2 | „No rule identifiers exposed“ | `ADR 0017:102-104`, `:208-210` | `matched_rule_index` in `whitelist_folder_mode.feature:52`, `flagged_predicate.feature:93` |
| I-3 | „A SenderRule cannot grant a visibility level the FolderPolicy does not permit“ | `ADR 0001:54-56` | Es gibt keine Ordner-Obergrenze. Whitelist-Formel `max(default, max(grant))` (`ADR 0003:47-48`) |
| I-4 | „An LLM cannot escalate its own access by composing a cleverer request“ | `ADR 0001:129-131`, `ADR 0018:133-137` | `mark_tagged` setzt `\Flagged` (`ADR 0005:40`), auf das eine `flagged: true → FULL`-Regel reagiert (`README.md:314-324`). Gmail-`mark_tagged` setzt Labels (`ADR 0019:71-73`). `copy`/`move` in Ordner mit höherer Sichtbarkeit |
| I-5 | „Archive immutability: once in the archive, no caller … can … remove it“ | `ADR 0005:106-109` | Gmail: ein `move` nach `\Trash`/`\Spam` entfernt die Nachricht aus **allen** Labels (`gmail_draft_lifecycle.feature:21-24`, `:194-196`) |
| I-6 | „Every PDP outcome is recorded“ / „Every call is audited“ | `ADR 0021:178-180`, `ADR 0015:71-74` | Audit-Eintrag erst nach dem Handler (`ROADMAP.md:163`), Exceptions ohne Eintrag (`:164`), -32602 ohne Eintrag (`error_path_analysis.md:139-140`), Such-Orakel nur als `ALLOW/OK` mit Hash (`ADR 0021:110-112`) |
| I-7 | Der OAuth-Scope ist eine zweite, unabhängige Autorisierungsschicht | `ADR 0009:24-28`, `:103-105` | Für Gmail-IMAP gibt es keinen Read-only-Scope. Für XOAUTH2 ist `https://mail.google.com/` nötig, `gmail.readonly` ist ein Gmail-API-Scope. Der Mock in `oauth2_bootstrap.feature:27,44-54` simuliert Wunschverhalten (§7.2) |
| I-8 | Die Caller-Identität bindet alle Handles | `ADR 0015:67-69` | `get_transaction_status` prüft den Caller nicht (`README.md:73-80`, `ROADMAP.md:128`) |

### 2.3 Angriffspfade

Angreifer ist ein kompromittierter oder per Prompt-Injection gesteuerter Agent mit normalem Tool-Budget.

**Pfad A: Metadaten ohne jedes Leserecht exfiltrieren** (Verallgemeinerung des Sent-Mail-Vorfalls)
1. Voraussetzung: In einem Ordner gewährt **irgendeine** Regel ≥ `METADATA`. Dann ist `search` auf dem Ordner erlaubt (`visibility_levels.feature:91-111` zeigt, dass das Ordner-Gate das Maximum über alle Regeln ist).
2. `search(criteria={…}, scope:"all")` liefert `matched_total` **vor** dem Policy-Filter. Jede Abfrage beantwortet also eine Ja/Nein-Frage über `NONE`-Nachrichten.
3. Mit `since`/`before` isoliert der Angreifer tagesgenau einzelne Nachrichten. Mit `size_gt`/`size_lt` bestimmt er die Größe per Binärsuche bytegenau (~20 Abfragen). `from_domain` gegen ein Wörterbuch liefert den Absender. `to_contains` und `subject_contains` sind **Substring**-Prädikate (`ADR 0004:36-37`): Empfänger und Betreff lassen sich Zeichen für Zeichen rekonstruieren. `has_attachment` und `flagged` liefern je ein weiteres Bit.
4. Im Audit steht `ALLOW / OK` mit `search_query_digest` (SHA-256). Zählwerte werden ausdrücklich nicht protokolliert (`ADR 0017:166-168`). Es gibt keinen DENY und kein Probe-Signal.

**Pfad B: Volltext ohne Leserecht**
1. UIDs aufzählen: `fetch_envelope`/`move`/`create_reply_draft` antworten bei nicht existierender UID mit `uid_not_found` (`intra_account_move.feature:62-68`, `create_reply_draft.feature:326-333`), bei existierender `NONE`-Nachricht mit DENY `sender_not_whitelisted` (`write_capabilities.feature:99-107`). Das liefert die vollständige UID-Menge.
2. `copy` braucht **nur** `accept_incoming` auf dem Ziel (`ADR 0016:59`, `mcp_tool_discovery.feature:106`). Der Angreifer kopiert jede `NONE`-UID in einen Ordner mit Blacklist-Default `ENVELOPE`/`FULL` oder in ein anderes Konto (Saga, `ADR 0019:144-150` räumt das ausdrücklich ein).
3. Danach liest er den Inhalt mit `fetch_body`/`fetch_attachment`.

**Pfad C: Selbst-Eskalation über „Nutzersignale“**
1. `mark_tagged(tags=["\Flagged"], mode:"add")` auf eine `NONE`-Nachricht. Erlaubt ist das, weil Capabilities nur auf Ordnerebene gelten (`ADR 0005:49-53`).
2. Eine Regel `flagged: true → FULL` (Beispiel in `README.md:314-324`, `flagged_predicate.feature:80-93`) gewährt jetzt `FULL`. Auf Gmail kommt die Nachricht außerdem in `[Gmail]/Starred`.
3. Gmail zusätzlich: Ein frei gewähltes Keyword wird zum Label (`ADR 0019:71-73`). Die Nachricht erscheint damit in jedem Label-Ordner mit höherer Freigabe, und zwar **ohne** `accept_incoming`-Prüfung.

**Pfad D: Policy-Übernahme über die Attachment-Senke** (lokales Deployment, Sidecar-Grenze)
- Der Dateiname der Senke ist vorhersagbar: bereinigter Name + `md5(bytes)[:8]` (`ADR 0028:83-95`). Der Pfad wird offengelegt (LIM 0016). Der Agent kann in der Senke Dateien anlegen (`ADR 0029:362-364`). Kein Dokument verlangt `O_NOFOLLOW`/`O_EXCL`.
- Ein vorab angelegter Symlink lenkt einen `fetch_attachment`-Schreibvorgang mit **angreiferkontrollierten Bytes** (dem Anhang einer eingehenden Mail) auf jede Datei, die der Server-Benutzer schreiben darf: `policies/*.yaml`, `wal.db`, die Audit-Tagesdatei.

**Pfad E: Fremde Saga lesen und auslösen** (HTTP, mehrere Caller): `get_transaction_status(tx_id)` ohne Caller-Bindung (`ROADMAP.md:128`, `:140-146`).

### 2.4 Grundsatzantwort

Das Konzept ist **im Kern nicht tragfähig** und muss in der Autorisierungssemantik **neu geschnitten** werden. Die Infrastruktur (PDP-Ort, lineare Stufen, geschlossene Grammatik, Saga/WAL, Audit-Kette) ist reparierbar. Neu zu schneiden sind fünf Achsen:

1. **Schutzobjekt** ist die Nachricht, nicht der Ordner. Bei Gmail heißt das: über alle Labels hinweg.
2. **Non-Interference** für alles unterhalb der Lesestufe des Tools: keine datenabhängigen Zählwerte, keine unterscheidbaren Fehler.
3. **Write ≤ Read:** Jede Schreiboperation verlangt eine Mindest-Sichtbarkeit pro Nachricht und darf die Sichtbarkeit nie erhöhen (kein „read-up by relocation“).
4. **Keine vom Agenten schreibbaren Policy-Eingaben** in gewährenden Regeln (Flags, Keywords, Labels, Draft-Header).
5. **Richtungsbewusstes Regelmodell** mit provenienzgebundener Identität für eigene Artefakte.

Details folgen in Abschnitt 5.

---

## 3. Befunde zur Governance- und BDD-Guideline-Konformität

### KRITISCH

#### G-01 — Spec-Audit nie durchgeführt; LIM 0001 auf ein nicht existierendes Artefakt hin „Resolved“
- **Kategorie:** Governance / §13
- **Fundstellen:** `docs/spec-audit/` enthält nur `README.md` und `TEMPLATE.md`. `docs/spec-audit/README.md:9-16` verlangt einen Zyklus vor jedem Major-Tag, jeder PR-Serie mit ≥ 3 Szenarien und jeder LIM-Schließung. `docs/limitations/0001-reason-code-symmetry-in-bdd.md:10-13`: „Spec-Audit-Zyklus vom 2026-05-06 bestätigt Steady-State“. `ROADMAP.md:79` nennt dieselbe LIM nur „Mitigated“.
- **Nachweis:** Mehrere Major-Tags (1.0.0, 2.0.0) und dutzende Feature-Serien sind ohne Zyklus gelaufen. Die einzige behauptete Prüfung ist nicht abgelegt.
- **Auswirkung:** Kein Szenario ist im Sinne von §13 als ehrlich verifiziert belegt. Die Mitigation „Spec audit“, die jede LIM als Nr. 5 nennt, existiert nicht. LIM 0001 ist nicht Resolved: Die verlangte strukturelle Ablösung (`0001…:30-39`) fand nicht statt.
- **Korrektur:** LIM 0001 auf `Mitigated` zurücksetzen. Zyklus 1 sofort nach diesem Bericht durchführen, mit Step-Code-Zugriff gemäß §13.3. Spec-Audit als CI-Pflichtartefakt für Release-Tags einführen.

#### G-02 — Schein-Garantien: grüne Szenarien für Verhalten, das in Produktion nicht existiert
- **Kategorie:** §4.3, §7.2, §13.1 (Stub mit Statuscode), Triade
- **Fundstellen und Nachweis:**
  - Retention: `audit_retention.feature:29,39,46,79` („When the audit rotation task runs“) prüft ADR 0022 „enforced by the audit writer when it rotates at UTC midnight“ (`ADR 0022:37-40`). Laut `ROADMAP.md:165` ruft **nur** das Test-Tool `_test_run_audit_rotation` `AuditWriter.rotate()` auf: „nichts wird komprimiert oder gelöscht“. LIM 0009 steht trotzdem auf `Resolved` (`0009…:3,14-17`).
  - Saga-Recovery: `cross_account_move_saga.feature:118` und `saga_crash_recovery.feature:38,51,64,77,90,104,116` („the server's background recovery loop runs“). Eine solche Schleife gibt es nicht. `run_pending_recovery` hat genau einen Aufrufer, das Test-Tool (`error_path_analysis.md:361-369`). `ADR 0006:62-65` (Accepted) verspricht Recovery beim Start.
  - OAuth-Scope: `oauth2_bootstrap.feature:27,44-54` simuliert einen IMAP-fähigen `gmail.readonly`-Scope. Den bietet Gmail nicht an. `error_path_analysis.md:208` (L4.14) wertet das als „covered“.
- **Auswirkung:** Die Suite ist grün, die Produktion verhält sich anders. Das ist genau das Muster aus §13.1.
- **Korrektur:** Die Szenarien auf das Produktionsverhalten umschreiben (Rotation beim Tageswechsel ohne Test-Hook, Recovery beim Start). Bis dahin `@pending` setzen **und** eine LIM oder einen Defekt mit Verweis anlegen. LIM 0009 wieder öffnen. Den Gmail-Scope-Mock auf reales Verhalten zurückbauen und ADR 0009 berichtigen.

#### G-03 — Bekannte Autorisierungs- und Audit-Defekte ohne ausführbare Spezifikation und ohne LIM
- **Kategorie:** Triade (Governance-README Regel 2 „No silent acceptance“), §4.2
- **Fundstellen:** `ROADMAP.md:128` (#20, `get_transaction_status` ohne Caller-Prüfung, „im Code verifiziert“), `:134` (#26 `caller_addr`), `:137` (#29 stiller Total-Deny), `:163` (#33 Audit nach Ausführung), `:164` (#34 Exceptions ohne Audit), `:165` (#35 Retention). `README.md:73-80` veröffentlicht #20 als Betriebsanweisung.
- **Nachweis:** Für keinen dieser Defekte gibt es ein rotes oder `@pending`-Szenario. `caller_authentication.feature` enthält kein Szenario mit zwei Callern und fremder `tx_id`. `audit_multi_instance.feature:102-108` prüft das nicht schreibbare Audit nur mit einem **lesenden** Tool und ohne Nachweis, dass keine IMAP-Seitenwirkung eintrat. ROADMAP lehnt LIMs für Defekte zu Recht ab (`:121-124`), ersetzt sie aber durch nichts Ausführbares.
- **Auswirkung:** Die schwerste bekannte Autorisierungslücke existiert nur in Prosa. Kein Test wird rot, wenn ein Betreiber sie aktiviert.
- **Korrektur:** Für jeden Defekt ein rot-zuerst-Szenario anlegen. Beispiel: „Caller B fragt `tx_id` von Caller A ab → DENY `tx_not_found` und keine IMAP-Aktion (Proxy-Log)“. Zusätzlich eine Startprüfung: HTTP mit > 1 Caller wird abgelehnt, bis #20 behoben ist.

#### G-04 — Feature-Files legen Verträge fest, die Accepted ADRs widersprechen, und widersprechen sich gegenseitig
- **Kategorie:** Triade (Feature → ADR), §1.1
- **Nachweis:**
  | Feature-Vertrag | Widerspricht |
  |---|---|
  | `matched_rule_index` in `whitelist_folder_mode.feature:52`, `flagged_predicate.feature:93` | `ADR 0017:102-104` („Policy rule identifiers … are never included“), `:208-210` |
  | flaches `error_type` in `intra_account_move.feature:67,78,87,97`, `cross_account_move_saga.feature:64,108`, `gmail_label_semantics.feature:102`, `localized_gmail_resolution.feature:146` | `ADR 0027:97-118` (`error.{type,detail}`, `error_type` entfällt) **und** `response_transparency.feature:130,143` („does not contain any field named error_type“) |
  | `imap_response` in `gmail_label_semantics.feature:103` | `ADR 0027:114-118`, `response_transparency.feature:131,144` |
  | `missing_capability` in `write_capabilities.feature:55,86,96,135`, `draft_edit_capability.feature:60,86,108,172`, `discard_draft.feature:141` u. a. | `ADR 0027:114-118` („dropped“). `ADR 0034:38-39` verlangt das Feld wieder: Widerspruch zwischen ADRs |
  | `required_scope`/`granted_scope` in `oauth2_bootstrap.feature:52-53` | `ADR 0027:115` |
  | Unbekannte Argumente werden still ignoriert (`non_goal_rejection.feature:73-76`, `required_arguments.feature:25-26`, `error_path_analysis.md:100`) | `ADR 0026:114-116`, `:282-286` (`additionalProperties: false`, „closes the smuggling vector“) |
  | `file_dir` ohne `acknowledge_plaintext` (`secret_store_backends.feature:28-38`) | `ADR 0011:131-135` („refuses to run … without an explicit acknowledge_plaintext: true“) |
  | Move auf ein Ziel ohne Policy → `capability_missing` (`response_transparency.feature:76`) | Move auf ein Ziel ohne Policy → `folder_hidden` (`non_goal_rejection.feature:40-45`, dessen Titel aber `capability_missing` sagt) |
- **Auswirkung:** Der Implementierer muss sich zwischen Spezifikationen entscheiden. Die Suite kann nur grün sein, wenn die Implementierung je Tool unterschiedliche Envelopes liefert, also genau den Zustand, den ADR 0027 beseitigen sollte.
- **Korrektur:** Pro Widerspruch eine Owner-Entscheidung treffen. Danach entweder die Feature-Datei oder (per superseding ADR) die Entscheidung angleichen. Eine schema-basierte Envelope-Prüfung für alle Tools in einer Feature-Datei bündeln.

#### G-05 — Die „geschlossenen“ Vokabulare sind faktisch offen
- **Kategorie:** Triade, §2.2, LIM 0001
- **Fundstellen:** `ADR 0017:100-104` (Closure Rule), `ADR 0027:102-118` (geschlossene `error.type`-Enumeration), `reason_code_contract.feature:182-188`.
- **Nachweis:** Folgende Reason-Codes und Error-Types kommen in Features, ADRs oder LIMs vor, fehlen aber in der kanonischen Tabelle (`ADR 0017:71-90`) bzw. der Enumeration (`ADR 0027:104-111`):
  - Reason-Codes: `missing_message_id` (`create_reply_draft.feature:200`), `tool_not_applicable_for_provider` (`gmail_label_semantics.feature:135`), `oauth_scope_insufficient` (`oauth2_bootstrap.feature:51`), `invalid_grant` (`:71`), `needs_rebootstrap` (LIM 0017/0018), `parse_error`/`validation_error` (`policy_reload.feature:71,89`), `account_removed` (`:101`), `ambiguous_fallback_match` (`saga_crash_recovery.feature:118`), `secret_decryption_failed` (`secret_store_backends.feature:112`), `identity_immutable`, `no_caller_identity`, `unknown_caller_id`, `wrong_auth_type` (`caller_authentication.feature:46,57,81,108`), `no_matching_rule` (`ADR 0001:125-128`).
  - Error-Types: `uid_not_found` für Move, `target_folder_missing`, `same_source_and_target`, `uid_stale`, `target_append_failed`, `target_unreachable`, `provider_rejected`, `rewrite_failed`.
  - Decision-Wert `INFO` (`ADR 0017:89`) gegenüber `ADR 0027:92` (nur `ALLOW`/`DENY`).
  - `saga_not_configured` wird von keinem Szenario erreicht. Damit ist die Varianzpflicht aus `ADR 0017:94-98` verletzt.
- **Auswirkung:** `reason_code_contract.feature:182-188` kann nur grün sein, wenn der Step-Code eine eigene, unsichtbare Kodeliste verwendet. Das ist versteckte Fachlogik im Glue Code (§1.3, §13.2 Prüfung 3). Der Vertragstest ist damit entwertet.
- **Korrektur:** Eine maschinenlesbare Registry (`docs/contract/reason-codes.yaml`, `error-types.yaml`) als einzige Quelle einführen, die ADR 0017/0027 und die Features referenzieren. Ein CI-Check gleicht emittierte Codes und Feature-Literale gegen sie ab. Fehlende Codes per ADR nachtragen oder abschaffen.

#### G-06 — Destruktive Fähigkeiten ohne ADR: `modify_message`, `add/replace/delete_attachment`; `draft_edit` nur in einem Proposed-ADR
- **Kategorie:** Triade (Feature → ADR), ADR-README §„One decision“
- **Fundstellen:** `modify_message_capability.feature:3-5`, `add_attachment.feature`, `replace_attachment.feature`, `delete_attachment.feature`. `ADR 0026:9-10,230` erwähnt die drei Tools nur als „by accretion“ entstanden. `ADR 0005:34-45` kennt fünf Capabilities. `README.md:266-271` listet weder `modify_message` noch `draft_edit`. `ADR 0031:58-64` (`draft_edit`) ist `Proposed`.
- **Nachweis:** Kein ADR begründet, dass empfangene Nachrichten umgeschrieben werden dürfen: Original wird expunged, `INTERNALDATE` zurückgesetzt (`ADR 0031:19`: „normally a defect“), keine Sicherheitsanalyse. Das widerspricht `ADR 0005:27-30` („messages cannot be modified in place … capability set must map to these IMAP primitives“) und `ADR 0018:29-33` (Löschen nur per Move in den Papierkorb).
- **Auswirkung:** Die gefährlichste Schreibfähigkeit (Fälschung eingehender Belege, siehe S-10) ist nicht entschieden, nicht in der Policy-Doku und nicht sicherheitsbewertet.
- **Korrektur:** Ein ADR „Message rewrite capability“ verfassen. Empfehlung: auf eigene Draft-Handles beschränken oder entfernen. README-Capability-Tabelle ergänzen. ADR 0031 annehmen oder ablehnen.

### HOCH

#### G-07 — Proposed-ADRs und Proposed-LIMs werden als bindend behandelt
- **Kategorie:** ADR- und LIM-Lifecycle
- **Nachweis:** ADR 0029, 0030, 0031 und 0032 stehen auf `Proposed` (`docs/adr/README.md:92-95`). Trotzdem sind sie implementiert, veröffentlicht (Tool-Set 2.1.0 laut `ADR 0031:104`) und durch aktive Features abgesichert (`update_draft*.feature`, `draft_identity.feature`, `audit_multi_instance.feature`). Die LIMs 0012 und 0017–0022 stehen auf `Proposed` und liegen auf `main`, obwohl das Projekt-README dies verbietet („Must not be merged to main“, `docs/limitations/README.md:78-79`). Accepted-ADRs zitieren sie als Begrenzung, z. B. ADR 0029 → LIM 0019–0021, ADR 0030 → LIM 0022.
- **Auswirkung:** Das Projekt verlässt sich auf Ausreden, die der Owner nie genehmigt hat. Diese Ungenehmigtheit betrifft auch die zentrale Sicherheitsgrenze des Standard-Deployments (LIM 0019).
- **Korrektur:** Owner-Review in einem Durchgang: jede Proposed-Record auf Accepted, Withdrawn oder Rejected setzen. Den Index entsprechend nachziehen.

#### G-08 — Lifecycle-Missbrauch: „Superseded in part“ und „Amends:“ verändern Accepted-ADRs in place
- **Kategorie:** ADR-Governance (Immutability, Lifecycle `Proposed → Accepted → Deprecated|Superseded`)
- **Nachweis:** Den Statuswert „Superseded in part … otherwise Accepted“ gibt es im Lifecycle nicht (`ADR 0004:3`, `ADR 0016:3`, `ADR 0017:3`). Das Feld „Amends:“ fehlt im Template (`ADR 0033:6`, `0034:6`, `0035:6`, `0036:6`). Inline-Amendments in Accepted- bzw. Proposed-ADRs: `ADR 0023:216-223`, `ADR 0029:155-175,212-237,335-348`. Beispiel für die Folge: `ADR 0025:236-238` verwirft „accept both [names]“, `ADR 0033:49-51` führt genau das ein. ADR 0025 bleibt dabei unverändert „Accepted“.
- **Auswirkung:** Kein Leser kann aus einem einzelnen ADR die geltende Regel ablesen. Die Ketten sind nicht navigierbar, und die Regel „Stand on its own“ ist verletzt.
- **Korrektur:** Für jede Teilablösung einen echten superseding ADR schreiben. Das alte ADR erhält `Superseded by ADR-XXXX`. Inline-Amendments in Accepted-ADRs entfernen und als neue ADRs anlegen.

#### G-09 — `error_path_analysis.md`: Tabellen falsch, Stati veraltet, ganze Schichten fehlen
- **Kategorie:** §4.5, Governance-README („Every enumerated error path is covered … or explicitly excluded“)
- **Nachweis (Zeilen gegen Abschnitte nachgezählt):**
  | Layer | Summenzeile (`:30-49`) | Abschnitt | Abweichung |
  |---|---|---|---|
  | L3 | 8 total / 7 covered / 1 def. | 9 Zeilen, 8 covered, 1 def. (`:169-179`) | Total und Covered falsch |
  | L4 | 16 / 6 | 17 covered (L4.1–16, L4.18) / 5 def. (`:193-216`) | falsch |
  | L5 | 12 / 2 | **6 covered / 8 deferred** (`:230-245`) | grob falsch |
  | L11 | 8 (+3 LIM-0009) / 0 | 10 covered / 1 deferred (L11.11) (`:398-410`) | falsch |
  | L8, L9, L14 | „covered_by_LIM-0003/0006/0002“ | Die LIMs sind Resolved, die Abschnitte sagen „covered“ | veraltet |
  - „Gap 0“ in allen Schichten, obwohl `select_failed` nur `@pending` ist (`folder_path_contract.feature:102`) und es weder für #20 noch für #33 ein Szenario gibt.
  - Die `@pending`-Szenarien in `folder_path_contract.feature:68,102` und `imap_mailbox_names_utf8.feature:103` tauchen in keiner Zeile auf.
  - L4.19–L4.22 „deferred B: covered by the generic loader refuses to start pattern“ (`:218-222`). Laut `ROADMAP.md:137` gibt es genau diese Prüfung nicht.
  - **Es fehlt eine Schicht.** Keine Schicht fragt „welche Information oder Wirkung erreicht der Caller unterhalb seiner Sichtbarkeit?“. Es fehlt das Produkt Schreib-Tool × Sichtbarkeitsstufe, und es fehlen Seitenkanäle (Zählwerte, Fehlerklassen, Timing). Die eigene Warnung in `:82-87` („complete only along the axis it was cut on“) trifft hier vollständig zu.
- **Korrektur:** Neue Schicht L18 „Authorization side channels and write×visibility“ anlegen (Vorschlag in Abschnitt 5.5). Alle Summen aus den Abschnitten neu berechnen. Veraltete LIM-Stati entfernen.

#### G-10 — Arithmetisch falsche §4.5-Tabellen in Feature-Headern
- **Kategorie:** §4.5
- **Nachweis:**
  | Datei | Behauptet | Tatsächlich |
  |---|---|---|
  | `visibility_levels.feature:8-11` | 14/14 | 10 Ausführungen; `visibility_below_COUNT` fehlt |
  | `write_capabilities.feature:16-22` | 14/14 („capability missing: 5“, „present: 5“) | 9 Szenarien; `mark_tagged`-missing, `copy` (missing und present) fehlen |
  | `reason_code_contract.feature:16-20` | 18 | 21 Szenarien |
  | `non_goal_rejection.feature:8-12` | 10 („does not exist: 5“) | Outline mit 14 Zeilen + 5 Szenarien |
  | `blacklist_folder_mode.feature:8-11` | 6 | 7 Szenarien |
  | `search_criteria_and_pagination.feature:9-20` | 33 | 30 (applied_scope doppelt gezählt) |
  | `list_messages.feature:9-18` | 8 | 7 |
  | **`message_rewrite_recovery.feature:8-11`** | 2 covered, darunter „Crash after APPEND“ | **nur 1 Szenario (Flags). Das namensgebende Crash-Szenario fehlt.** |
  | `folder_path_contract.feature:20-29` | 7 covered | 2 davon `@pending` |
  | `add_attachment.feature:10` | „old UID gone, new UID exists“ | nirgends geprüft |
  | `inline_attachment_detection.feature`, `localized_gmail_folders.feature`, `explicit_imap_user.feature` | — | kein Header |
- **Korrektur:** Header aus den Szenarien generieren (Linter). Das fehlende Crash-Szenario für den Attachment-Rewrite schreiben.

#### G-11 — Persistenzvalidierung (§4.3 / §13.2 Prüfung 1): Lücken und ein unmöglicher zweiter Kanal

Ergebnis je Schreib-Tool:

| Tool | Bewertung | Fundstelle |
|---|---|---|
| `move` (intra, cross, Gmail) | OK (direktes IMAP-SEARCH, WAL) | `intra_account_move.feature`, `cross_account_move_saga.feature`, `gmail_*` |
| `copy` cross-account | OK | `cross_account_move_saga.feature:125-134` |
| `copy` intra-account | **kein einziges Szenario** | — |
| `mark_seen` | OK für `seen true`; `seen false` ungeprüft | `write_capabilities.feature:40-46` |
| `bulk_mark_seen` | **FINDING:** nur `marked_count`, kein zweiter Kanal, Absender nicht spezifiziert | `bulk_mark_seen.feature:21-54` |
| `mark_tagged` | nur `add`; `remove`/`replace`/`\Flagged` ungeprüft | `write_capabilities.feature:58-64` |
| `bulk_mark_tagged` | OK (separates `list_messages`) | `bulk_mark_tagged.feature:36-60` |
| `create_draft` | OK (direkter IMAP-FETCH) | `create_draft_format.feature:74 ff.`, `draft_consistency.feature:61` |
| `create_reply_draft` | OK | `create_reply_draft.feature:114-137` |
| `update_draft` | OK (Header via IMAP) | `update_draft.feature:68-73` |
| `discard_draft` | OK (direktes SEARCH) | `discard_draft.feature:71-72` |
| `add_attachment` | **FINDING:** Der zweite Kanal fragt `list_attachments` auf der **alten UID 801** ab (`add_attachment.feature:31`). Ein Rewrite erzeugt aber eine neue UID (`ADR 0031:121`), und IMAP vergibt UIDs streng aufsteigend. Der Test ist nur grün, wenn Server oder Step-Code alte UIDs heimlich umleiten. Das ist ein undokumentierter Vertrag oder Glue-Magie (§13.2 Prüfung 3) | `add_attachment.feature:31`, `delete_attachment.feature:31,51` |
| `replace_attachment` | **FINDING:** kein Nachweis des neuen Inhalts; Titel „changes the content hash“ ohne Hash-Assertion | `replace_attachment.feature:21-29` |
| `modify_message` (Erfolg) | **FINDING:** nur Response | `modify_message_capability.feature:29-39` |
| `fetch_attachment` (Senke) | OK (Datei, Größe, SHA) | `fetch_attachment_content.feature:49-51` |
- **Korrektur:** Für Rewrites die neue UID oder Message-ID in der Response liefern und per direktem IMAP-SEARCH nach Message-ID prüfen. `copy` intra und `bulk_mark_seen` mit IMAP-Flag-Prüfung ergänzen.

#### G-12 — `@pending` ohne LIM; „specified @pending“ als Ausweichzustand; die Pending-Spezifikationen sind selbst ungültig
- **Kategorie:** LIM-Governance („No silent acceptance“), §4.5
- **Nachweis:**
  - `startup_saga_recovery.feature:1` (Feature-Level `@pending`) sichert eine **Accepted**-Entscheidung (`ADR 0006:62-65`). Dafür gibt es keine LIM, nur den ROADMAP-Eintrag „Feature“ (`ROADMAP.md:130`).
  - `imap_mailbox_names_utf8.feature:30-43`: „Tracked here in-file because it is small enough not to need an external ticket“. Das ist die stille Akzeptanz, die verboten ist.
  - `error_path_analysis.md:72-80` erfindet den Zustand „specified @pending“, der weder Gap noch Deferred ist und keine LIM braucht.
  - **Inhaltlich ungültig:** `startup_saga_recovery.feature:57-62` und `projected_config_reload.feature:89,105,125,144,155,171,188` verwenden `whitelist` mit `default: FULL/ENVELOPE/BODY`. Der Loader weist das laut `ADR 0003:52-54` und `whitelist_folder_mode.feature:54-71` ab. Diese Szenarien können so nie grün werden. Außerdem verwendet `startup_saga_recovery.feature:75,103` den WAL-Status `appended`, den `ADR 0016:72` nicht kennt.
- **Korrektur:** Für jedes `@pending` gilt: entweder LIM (bei echter Begrenzung) oder Defekt mit Termin. Die ungültigen Policies in den Pending-Features korrigieren.

#### G-13 — Requirement Gaps (§2.1, §2.2, §13.2 Prüfung 2)
- **Nachweis (Auswahl):**
  - `hidden_folders_count equals 3` geht nur mit einem impliziten, nicht deklarierten `INBOX` auf (`response_transparency.feature:22-26,43`, `list_folders_message_count.feature:17-21,46`).
  - Absender und Anzahl werden im Step-Code erfunden: `bulk_mark_seen.feature:25` („holds 10 messages“ + Kriterium `example.com` → 10), `connection_reuse.feature:27,33`, `has_attachment=true` ohne Anhang (`add_attachment.feature:39-40`, `replace_attachment.feature:33-34`, `delete_attachment.feature:36-37`).
  - `create_draft_format.feature:187-191`: Die Quellnachricht hat **keine** `message_id`, erwartet wird trotzdem `ALLOW`. Laut `create_reply_draft.feature:48-49,187-202` folgt daraus DENY `missing_message_id`. Der Step-Code muss also eine Message-ID injizieren.
  - Magische Steps mit dem Erwartungswert im Text: „triggers a DENY with reason sender_blacklisted“ (`reason_code_contract.feature:71`, `audit_log_format.feature:95`). Zirkulär nach §4.1.
  - `gmail_label_semantics.feature:67,81-83`: UID 505 „by convention“, Absender unbekannt, also auch die Sichtbarkeit unbekannt.
  - Vakuöse Negativprüfungen: `response_transparency.feature:117` (`overview-agent` existiert nicht), `folder_path_contract.feature:119-128` („Existing/But/Hidden“ wird nicht angelegt; der Vertraulichkeitstest beweist nichts).
  - `secret_store_backends.feature:87,94`: Der Fingerprint wird laut `LIM 0007:148-151` vom Step-Code ersetzt.
  - `saga_crash_recovery.feature:27`: „allows cross-account move between these folders“ verbirgt die Policy.
- **Korrektur:** Alle fachlich relevanten Daten ins Szenario. Magische Steps auflösen.

#### G-14 — LIM-Template und Lifecycle sind vom Referenz-Template abgedriftet; Pflichtfelder fehlen
- **Kategorie:** LIM-Governance
- **Nachweis:**
  - `docs/limitations/TEMPLATE.md:3-10` fehlen `Scope`, `Date resolved`, `Resolution commit`, `Date withdrawn` und `Withdrawal commit`. Der Lifecycle (`README.md:73-76`) kennt kein `Withdrawn`.
  - **Keine** Resolved-LIM nennt einen `Resolution commit` (0001–0009, 0011-search).
  - LIM 0010: `Accepted` ohne `Approved by`/`Date approved` (`0010…:3-8`, Index `README.md:133` „—“). Es fehlen die Abschnitte *Resolution intent*, *Why the clean solution is not chosen*, *Residual risk* und *References*.
  - LIM 0011-search: nie genehmigt, trotzdem `Resolved` (`README.md:135`). Es fehlen *Why not*, *Residual risk* und *References*.
  - Nummernkollision 0011, eingeräumt in `README.md:148-156`, ungelöst.
  - Fehlende *Related Guidelines* in LIM 0014, 0015 und 0016.
  - Mitigations, die nicht existieren: „README guidance (TODO)“ in `0014…:80-83`, `0016…:90-92`; „tmpfiles.d snippet (TODO)“ in `0015…:85-89`.
  - LIM 0002 und 0003 versprechen Folge-LIMs für Rest-Risiken (`0002…:136-138`, `0003…:140-142`). Die Risiken sind eingetreten (`error_path_analysis.md:556-568`: Mock-Fidelity-Fehler 2.2.0; G-02: Scope-Mock), Folge-LIMs gibt es nicht.
  - LIM 0011-bulk: Intent „must-resolve — when a caller use case demands it“ ist eine bedingte Klassifikation. Das Template lässt das nicht zu.
  - Defekte Links in LIM 0004–0009 (`0006-cross-account-move-saga.md`, `0007-wal-schema.md`, `0008-saga-idempotency.md`, `0015-caller-identity.md`, `0014-policy-reload.md` u. a.). Private Plan-Pfade (`/home/randy/.claude/plans/…`).
- **Korrektur:** Template auf den Stand von burn-your-code bringen. Resolution-SHAs nachtragen. 0011-search umnummerieren und eine Weiterleitungsnotiz hinterlassen. 0010 genehmigen oder vervollständigen.

#### G-15 — Defekte als LIM getarnt; LIMs mit falschen Kernaussagen
- **Kategorie:** LIM-README „Default posture“ / unzulässige Begründungen
- **Nachweis:**
  - LIM 0012: „There is no security implication“ (`0012…:173-175`), obwohl es selbst feststellt, dass `STORE NO` verschluckt wird und `result: OK` zurückkommt (`:48-49`). Das ist ein stilles Fehlschlagen von Schreib-Tools (§4.3). Die Begründung ist Aufwand (`:94-126`). Das Feld `imap_response` wurde inzwischen von ADR 0027 abgeschafft, die LIM ist veraltet.
  - LIM 0017: „deferred on a cost/benefit basis, not on difficulty“ (`:68-70`) ohne Zahlen. Gleichzeitig verletzt es die Accepted-Aussage „`needs_rebootstrap` is a hard stop“ (`ADR 0010:123-125`).
  - LIM 0018 sagt über sich selbst „The remediation-guidance half … has no such excuse“ (`:80-84`).
  - LIM 0022 behauptet „There is no clean solution“ (`:57-74`). Ein In-Message-Inhalts-Hash (`X-SC-Draft-Content-Hash` über den kanonischen Body) erkennt genau den stillen Fall „Header erhalten, Inhalt geändert“. ADR 0031 hat mit `X-SC-Draft-Change-Hash` den Mechanismus bereits. Das Argument „isolierter Drafts-Ordner“ (`:76-79`) ist auf Gmail unmöglich, dort gibt es genau einen `\Drafts`-Ordner.
  - LIM 0011-search führt Fähigkeiten ein: 7-Tage-Default, Paginierung und die `matched_total`-Semantik (`:40-94`). Eine LIM darf das nicht („A Limitation Record cannot [introduce a capability]“, `README.md:164-165`). `ADR 0026:143-144` schreibt den 7-Tage-Default fälschlich ADR 0017 zu.
  - LIM 0016: „There is no attack vector that uses the disclosure to change server behaviour“ (`:53-56`). Das ist falsch, siehe S-11.
  - LIM 0019: „An agent restricted to imap-mcp's own MCP tools … cannot widen its policy at all“ (`:151-156`). Das ist falsch, siehe S-04, S-05, S-06.
- **Korrektur:** 0012, 0017 und 0018 als Defekte mit Szenarien führen. 0022 als `must-resolve` neu fassen. 0016 und 0019 berichtigen.

### MITTEL

#### G-16 — Formale ADR-Regeln verletzt
- `ADR 0029`: 519 Zeilen (> 500). Mindestens vier Entscheidungen in einem ADR (Topologie, `/healthz`, Startup-Recovery, Container-Image). Veraltete Aussage „There is no connection pool today“ (`:289-295`).
- Echter Code oder Implementierungsdetails: `ADR 0011:39-44` (Python-Protocol), `ADR 0025:91-95` (Pseudo-Python mit Escapes), `ADR 0024:12-13,64-68,145-148`, `ADR 0026:36-41,146-151`, `ADR 0027:33,122-124,130`. Widersprüchliche Zählung „18“ vs. „19“ Call-Sites (`ADR 0025:29,98`).
- Interne Widersprüche: `ADR 0026:60` „25 tools“ vs. `:225` „26 tools“.
- Defekte Links: `ADR 0001:67,69` (falsche Dateinamen; das Audit-ADR ist 0021, nicht 0007). `ADR 0013:27,159` (Push-Non-Goal steht in 0018, nicht 0014). `ADR 0023:230-232`.
- Öffentlichkeitsregel 6 verletzt: `ADR 0020:153` (private GitLab-URL).
- Dünne Pflichtsektionen: ADR 0033–0039 haben Security-Abschnitte von 2–6 Zeilen ohne *Auditability*. ADR 0036 erklärt die neue Vor-Filter-Zählung mit „The same disclosure already exists“ für unbedenklich (`:60-62`). ADR 0038/0039 nennen nur 2 Alternativen.

#### G-17 — Varianz (§2.3) bei kritischen Codes unzureichend
- `visibility_below_COUNT`: zwei strukturgleiche Szenarien, nur der Ordnername unterscheidet sich (`reason_code_contract.feature:153-169`). Das ist Scheinvarianz.
- `forbidden_system_flag` wird nur mit `\Deleted` getestet (`reason_code_contract.feature:115`, `non_goal_rejection.feature:52`, `bulk_mark_tagged.feature:84`). `\Recent` fehlt, ebenso `\Seen`/`\Answered`/`\Draft` über `mark_tagged` (siehe S-17).
- Einzelszenario-Codes: `missing_message_id`, `tool_not_applicable_for_provider`, `policy_alias_conflict`, `oauth_scope_insufficient`, `needs_rebootstrap`.
- `sender_blacklisted` immer mit `bank.de` oder über einen magischen Step.
- `blacklist_folder_mode.feature:109-123` heißt „Audit distinguishes sender_blacklisted from sender_not_whitelisted“, prüft aber nur einen der beiden Codes.

#### G-18 — Technische Innereien und Testkonstrukte (§1.1, §1.2, §12)
- Quellzeilen im Feature-File: `imap_mailbox_names_utf8.feature:33,109` (`imap_core.py:55-67`).
- Implementierungsstrategie über eine vakuöse Prüfung getestet: `search_blacklist_fast_path.feature:39`. Ein interner Envelope-Fetch ist nie ein auditierter Tool-Call, die Assertion kann nicht fehlschlagen.
- Meta-Szenario über die Spezifikation selbst: `reason_code_contract.feature:187-188`.
- Interna in Kommentaren und Headern: `tracing_payload_mode.feature:3`, `fetch_attachment_sink.feature:83-129`, `create_draft_error_visibility.feature:16-17,90-92` (veraltete `imap_response`-Kommentare).
- Titel widersprechen der Assertion: `non_goal_rejection.feature:40` vs. `:45`; `cross_account_move_saga.feature:57` („staged“) vs. `:70` („pending“); `bulk_mark_seen.feature:47` („at most 2“) vs. `:54` („at most 4“); `connection_reuse.feature:26,32` („3“) vs. `:30,36` („4“); `bulk_mark_tagged.feature:16` („single IMAP session“) vs. `:95`.
- Veralteter Kontext: `connection_pool.feature:3-12` („The server does not implement it“).

#### G-19 — ADRs mit sachlich falschen oder nicht eingehaltenen Kernaussagen, ohne LIM

| ADR | Aussage | Realität (Projektdoku) |
|---|---|---|
| 0006:62-65 | Recovery beim Start | nur über Test-Tool (`error_path_analysis.md:361-369`) |
| 0009:35-39 | Bootstrap mit Loopback-Callback | CLI mit `input()`, kein Listener (`LIM 0021:45-48`) |
| 0009:52 | `gmail.readonly` als IMAP-Scope | für Gmail-IMAP nicht verfügbar |
| 0010:123-125 | `needs_rebootstrap` „hard stop“ | geht bei Reload/Neustart verloren (`LIM 0017:51-59`) |
| 0011:131-135 | `acknowledge_plaintext` erzwungen | nicht in README-Konfig, Feature läuft ohne |
| 0012:44-46,85-87 | Lockfiles; `mypy --strict` als CI-Gate | CI ohne Versionsstand (`ROADMAP.md:172`) |
| 0014:76-77 | Caller→Policy-Referenz wird geprüft | fehlt (`ROADMAP.md:137`) |
| 0015:71-74 / 0021:178-180 | jeder Call / jedes PDP-Ergebnis auditiert | -32602, Exceptions nicht (`ROADMAP.md:164`) |
| 0021:43-44 | fsync vor Bestätigung | Audit nach dem Handler (`ROADMAP.md:163`) |
| 0021:78-79 | HTTP `caller_addr = http:<ip>:<port>` | hart `stdio:pid` (`ROADMAP.md:134`) |
| 0022:37-40 | Retention beim Tageswechsel | läuft nie (`ROADMAP.md:165`) |
| 0039:60-61 | „each run executes in an isolated environment“ | „Runs do not execute in a container … not a sandbox“ (`usability/README.md:122-130`) |

#### G-20 — Dokumentations- und Kennzahlendrift
Siehe Abschnitt 1. Hinzu kommt `governance/README.md:4`, das auf Guidelines **v1.9.0 EN** verweist, während das Projekt v1.8.0 DE als Maßstab nennt.

### NIEDRIG

#### G-21 — Uneinheitliche Bezeichner
- `token_ref` (`ADR 0015:45`) vs. `token_secret_ref` (`README.md:213`).
- `folder:` (`ADR 0003:30`) vs. `path:`.
- „Randy Nel Gupta“ vs. „Randy Gupta“ als Decider.
- `tool_surface_info` gibt `reason: folder_default_applied` zurück (`ADR 0027:151`): Ein Ordner-Reason für ein Meta-Tool ist semantisch falsch.

---

## 4. Befunde zum Berechtigungskonzept und zur API-Sicherheit

### KRITISCH

#### S-01 — Such- und Listen-Orakel über `NONE`-Nachrichten (bitweise Exfiltration von Envelope-Daten)
- **Mechanismus:** `search`/`list_messages` werten die Caller-Kriterien serverseitig über **alle** Nachrichten aus. Erst danach filtert die Policy. Die Antwort enthält `matched_total`, `filtered_out` und bei Default-Fenster `outside_scope_total`, das ausdrücklich „before policy filtering“ zählt (`ADR 0036:26-30`, `search_completeness.feature:18-20`). Jede Abfrage beantwortet so eine Prädikatfrage über unsichtbare Nachrichten. Substring-Prädikate (`to_contains`, `subject_contains`, `ADR 0004:36-37`) erlauben die Rekonstruktion Zeichen für Zeichen. `size_gt`/`size_lt` liefern die Größe per Binärsuche, `since`/`before` isolieren tagesgenau, `has_attachment` und `flagged` liefern je ein Bit. `bulk_mark_*` (`marked_count`/`tagged_count`) wirken zusätzlich als **schreibendes** Orakel: Sie markieren die unsichtbaren Treffer mit (siehe S-04).
- **Fundstellen:** `ADR 0017:39-50` (Zählwerte), `:191-195` (das ADR räumt den Leak ein und erklärt den Angreifer mit „interactive query budget“ für nicht relevant, obwohl genau der kompromittierte Agent dieses Budget hat), `ADR 0036:57-62`. Als Soll-Verhalten festgelegt in `default_deny.feature:64-71` (Titel „is invisible“, Assertion `matched_total 1 / filtered_out 1`), `whitelist_folder_mode.feature:25-29,109-122`, `blacklist_folder_mode.feature:53-57`, `search_criteria_and_pagination.feature:280-288`, `list_messages.feature:96`, `tracing_spans.feature:96-103` (die Hidden-Zählung steht sogar im Trace).
- **Betroffenheit:** Jeder Ordner, in dem **irgendeine** Regel ≥ `METADATA` gewährt. Der Sent-Mail-Vorfall ist ein Beispiel.
- **Audit:** `ALLOW/OK` mit SHA-256-Digest (`ADR 0021:110-112`). Zählwerte werden nicht geloggt (`ADR 0017:166-168`). Der Betreiber kann Probing weder erkennen noch rekonstruieren. Die Annahme aus ADR 0001 („repeated DENY … indicate … probing“, `ADR 0001:126-128`) greift nicht, weil nie ein DENY entsteht.
- **Lösung:** Non-Interference (Invariante N-1 in Abschnitt 5). Kriterien nur über die für das Tool sichtbare Menge auswerten. `matched_total`, `filtered_out` und `outside_scope_total` nur über sichtbare Nachrichten zählen. Transparenz **statisch** aus der Policy herleiten statt dynamisch aus den Daten, z. B. `completeness: "policy_may_hide"`, wenn der Ordner verbergende Regeln hat. Wer exakte Hidden-Counts braucht, schaltet sie pro Ordner explizit frei (`disclose_hidden_counts: exact`), Default `none`.

#### S-02 — Existenz- und UID-Orakel über die Fehlertaxonomie
- **Mechanismus:** Bei UID-adressierten Calls sind drei Fälle unterscheidbar: nicht existierende UID → `uid_not_found` (ALLOW+ERROR); existierende `NONE`-Nachricht → DENY `sender_not_whitelisted`/`sender_blacklisted`; existierende Nachricht mit zu niedriger Stufe → `visibility_below_*`. Weil UIDs aufsteigend vergeben werden, lässt sich die komplette UID-Menge eines Ordners aufzählen. Das gilt auch für Ordner, in denen der Caller **nichts** sieht.
- **Fundstellen:** `ADR 0017:77` (Reaktionstext: „The message exists but is out of scope“), `:214-218` (falsche Behauptung „attacker … learns only that their specific choice is [blacklisted] — which they already know“. Bei UID-Probing wählt der Angreifer aber nicht den Absender). `write_capabilities.feature:99-107` (Archive-Muster, Ordner ohne Regeln, liefert `sender_not_whitelisted`). `intra_account_move.feature:27-30,62-68` (`uid_not_found` in einem reinen `NONE`-Ordner). `create_reply_draft.feature:326-333`, `add_attachment.feature:47-51`.
- **Zusatz:** `sender_blacklisted` vs. `visibility_below_X` vs. `ALLOW` teilt die Nachrichten nach Blacklist-Regeln auf. Das ist ein Orakel über die Regelmitgliedschaft.
- **Lösung:** Für jede Adressierung unterhalb `METADATA` einheitlich `not_found` zurückgeben (identische Shape und Reason, kein Audit-Unterschied im Response-Pfad). `sender_not_whitelisted` und `sender_blacklisted` nur intern im Audit verwenden (`audit-only`). `visibility_below_X` nur liefern, wenn der Caller die Existenz ohnehin sieht (≥ `METADATA`).

#### S-04 — Blinde Schreibzugriffe auf `NONE`- und unterprivilegierte Nachrichten
- **Mechanismus:** Schreib-Capabilities gelten ausschließlich pro Ordner (`ADR 0005:49-53`, `:82-85`). Nur `fetch_*` prüfen die Sichtbarkeit pro Nachricht. Die Features legen blinde Schreibzugriffe ausdrücklich fest: `intra_account_move.feature:27-44` und `cross_account_move_saga.feature:27-45` verschieben Nachrichten aus `whitelist/NONE`-Ordnern **ohne Regeln**.
- **Matrix (aus den Dokumenten abgeleitet):**

| Tool | Mindest-Sichtbarkeit pro Nachricht laut Spec | Bei `NONE` möglich? | Beleg |
|---|---|---|---|
| `mark_seen` | keine | ja | `ADR 0005:39` |
| `bulk_mark_seen` | keine; Zähler gibt Hidden-Treffer preis | ja (+ Orakel) | `ADR 0026:23-27` „per-message authorization shape“ ist undefiniert |
| `mark_tagged` / `bulk_mark_tagged` | keine | ja (inkl. `\Flagged`, Gmail-Labels) | `ADR 0005:40`, `ADR 0019:71-73` |
| `move` | keine | **ja, spezifiziert** | `intra_account_move.feature:27-44` |
| `copy` | keine; nur `accept_incoming` am Ziel | ja | `ADR 0016:59` |
| `discard_draft` | keine | ja (jede Nachricht mit Handle, in jedem Ordner mit `move_out`) | `ADR 0034:27-39` |
| `update_draft` | keine | **ja, spezifiziert** (inkl. Empfänger setzen) | `draft_edit_capability.feature:214-239` |
| `create_reply_draft` | BODY, aber nur auf Ordnerebene getestet | **unspezifiziert** pro Nachricht | `create_reply_draft.feature:361-380` |
| `add/replace/delete_attachment` | **keine dokumentiert** | unspezifiziert, laut Capability-Modell ja | G-06 |
- **Lösung:** Jedes Schreib-Tool deklariert `min_visibility` pro Nachricht (Vorschlag in Abschnitt 5.3) und wird pro Nachricht geprüft. `bulk_*` wirken nur auf Nachrichten oberhalb der Schwelle und zählen nur diese.

#### S-05 — Lese-Eskalation durch Umlagerung (copy, move, Saga, Gmail-Labels)
- **Mechanismus:** Die Policy hängt am Ordner, die Nachricht hat keine Identität. Kopiert oder verschoben in einen Ordner bzw. ein Konto mit höherer Freigabe (Blacklist-Default, passende Whitelist-Regel), wird eine `NONE`-Nachricht lesbar. Bei `copy` reicht `accept_incoming` am Ziel. Cross-Account-Sagas holen bei Gmail deterministisch aus `[Gmail]/All Mail`, einem Ordner, für den der Caller keine Policy haben muss. Gmail-`mark_tagged` hängt Labels an und umgeht damit sogar `accept_incoming`.
- **Fundstellen:** `ADR 0016:59`, `ADR 0019:71-73`, `:144-150` („a `move_out` capability on a Gmail label implies read access sufficient to copy the entire message body cross-account“; das wird eingeräumt, aber nicht verhindert). `gmail_label_semantics.feature:137-146`, `localized_gmail_resolution.feature:151-167`. Kein Feature prüft eine Verweigerung.
- **Betroffenheit:** Jede Policy mit mindestens einem Ordner, der `accept_incoming` hat und höhere Sichtbarkeit bietet als die Quelle. Das ist der Normalfall (Archiv, Rechnungsablage).
- **Lösung:** Fluss-Monotonie (Invariante W-2): `move`/`copy`/Label-Add werden verweigert, wenn `visibility_target(m) > visibility_source(m)`, mit Reason `relocation_would_escalate`. Gmail-Label-Operationen werden wie `copy` behandelt (Capability + Monotonie).

#### S-06 — Selbst-Eskalation über vom Agenten schreibbare Policy-Eingaben (`\Flagged`, Keywords, Labels)
- **Mechanismus:** Das Prädikat `flagged` (`ADR 0004:39`) bewertet einen Zustand, den der eingeschränkte Principal selbst setzen kann (`mark_tagged`, `ADR 0005:40`). Die Sicherheitsbewertung behandelt nur den **Nutzer** als Flag-Setzer (`ADR 0004:94-97`). README empfiehlt das Muster ausdrücklich („grant full access to all starred messages“, `README.md:314-324`). Auf Gmail entspricht `\Flagged` der Mitgliedschaft in `[Gmail]/Starred` (`ADR 0033:16`). Ist `Starred` freigegeben, eskaliert der Agent auch dort. Ein künftiges `label`-Prädikat (`ADR 0004:134`) würde das Problem verallgemeinern.
- **Prompt-Injection:** Eine eingehende Mail kann den Agenten schlicht anweisen, sie als wichtig zu markieren.
- **Lösung:** Im Loader erzwingen, dass agent-schreibbare Zustände (Flags, Keywords, Labels) nur in `cap`-Regeln vorkommen dürfen, nie in `grant`. Alternativ entzieht der Server `mark_tagged` die Hoheit über jedes Flag, das eine Policy-Regel referenziert. Das echte Nutzersignal „vom Menschen markiert“ lässt sich über IMAP nicht nachweisen. Das ist eine Owner-Entscheidung (Abschnitt 5.6).

#### S-07 — `get_transaction_status` ohne Caller-Bindung (bekannt, nicht mitigiert)
- **Fundstellen:** `README.md:73-80`, `ROADMAP.md:128,140-146`, `ADR 0029:107-118` (die „Mitigation“ ist reine Betriebsanweisung), `ADR 0031:138`.
- **Mechanismus:** Ein beliebiger Caller mit `tx_id` liest fremde Transaktionen und **stößt** IMAP-Schreibvorgänge unter fremden Credentials an. Polling ist der einzige Recovery-Trigger (`error_path_analysis.md:365-368`). Der Server lehnt Konfigurationen mit mehreren Callern auf HTTP nicht ab.
- **Lösung:** `tx_id` an `caller_id` binden (WAL-Feld existiert laut `ADR 0007:39-41`). Fremde IDs liefern `not_found`. Bis zur Behebung: Startprüfung „HTTP mit > 1 Caller → fatal“. Rot-Szenario anlegen (siehe G-03).

### HOCH

#### S-03 — Offenlegung der Policy-Struktur: `matched_rule_index`, `describe_policy`, Regelmitgliedschaft
- **Mechanismus:** `matched_rule_index` (`whitelist_folder_mode.feature:52`, `flagged_predicate.feature:93`) zusammen mit `sender_rules_count`, `default_visibility` und `max_visibility` aus `describe_policy` (`response_transparency.feature:99-105`) und den Such-Orakeln (S-01) erlaubt die Rekonstruktion der Regelliste in ihrer Reihenfolge: Der Angreifer sucht mit Kandidaten-`from_domain`, prüft, ob die Nachricht sichtbar wird, und liest ab, welcher Index griff. ADR 0017 nennt die Policy selbst schützenswert (`:17-21`).
- **Lösung:** `matched_rule_index` entfernen (es widerspricht ohnehin `ADR 0017:102-104`). `sender_rules_count` nur noch als Bool („hat verbergende Regeln“) ausgeben.

#### S-08 — Mehrfach abgelegte Nachrichten / Gmail-Labels: die Sichtbarkeit hängt vom gewählten Zugangsweg ab; Gmail-Trash umgeht Archiv-Unveränderlichkeit und `move_out`
- **Mechanismus:**
  1. Der Caller **wählt** den Ordner, über den er eine Nachricht adressiert. Effektiv gilt also das Maximum über alle Projektionen, für die er eine Policy hat (`INBOX`, User-Labels, `All Mail`, `Sent Mail`, `Starred`). Ein `NONE` in `INBOX` hat keine Bedeutung, wenn dieselbe Nachricht über `[Gmail]/All Mail` (Blacklist `ENVELOPE`) erreichbar ist. Umgekehrt der reale Vorfall: der eigene Draft (`FULL` in Drafts) ist nach dem Senden in `Sent Mail` (Whitelist `NONE`) unlesbar, je nach All-Mail-Policy aber doch wieder lesbar. Das Modell kennt keine Konfliktregel zwischen Projektionen. ADR 0033 löst nur Konflikte zwischen **Namen** (`policy_alias_conflict`), nicht zwischen **Labels**.
  2. Ein `move` nach `\Trash` oder `\Spam` entfernt die Nachricht aus **allen** Labels (`gmail_draft_lifecycle.feature:21-24`, `:194-196` prüft, dass sie aus All Mail verschwindet). Damit bricht `ADR 0005:106-109` (Archiv-Unveränderlichkeit), und `move_out` wird auf allen anderen Labels umgangen.
  3. `canonical_all_mail_uid` legt die UID-Struktur eines Ordners offen, den der Caller nicht sehen darf, und erlaubt die Korrelation über Ordner hinweg (`ADR 0019:138-140`: „not a leak“; das trifft nicht zu, siehe S-23).
  4. `labels` in `fetch_envelope` „subject to visibility policy“ (`ADR 0019:54-56`) ist ungetestet. `list_labels` wird nur positiv geprüft (`gmail_label_semantics.feature:126-132`, „includes at least“). Ob Namen verborgener Labels ausgegeben werden, prüft kein Szenario. Das widerspräche `ADR 0017:211-213`.
- **Lösung:** Nachrichtenidentität (Gmail: `X-GM-MSGID`). Die effektive Sichtbarkeit wird über **alle** Labels der Nachricht mit „deny/cap overrides“ berechnet (Owner-Entscheidung, Abschnitt 5.6). Gmail-Trash und -Spam gelten als Entfernung aus allen Labels und brauchen `move_out` auf **jedem** aktuellen Label. Negativszenarien für `list_labels` und `labels` ergänzen.

#### S-09 — Informationsfluss über Drafts: Reply-Zitat, Empfänger-Exfiltration, Weitergabe zwischen Callern
- **Mechanismus:**
  - `create_reply_draft` kopiert Body und Adressen einer Quelle in `Drafts`. Ob die BODY-Prüfung pro Nachricht oder nur pro Ordner läuft, ist ungetestet (`create_reply_draft.feature:361-380` nutzt nur einen Blacklist-Ordner-Default). Ist sie ordnerbasiert, wird `NONE`-Inhalt in einen `FULL`-Ordner gewaschen.
  - `update_draft` darf unter `NONE` `to`/`cc`/`bcc` setzen (`ADR 0031:33-41`, `draft_edit_capability.feature:214-239`). Ein Prompt-injizierter Agent hängt `bcc: angreifer@…` an einen Entwurf, den er nicht lesen kann. Der Mensch sendet ihn, und der Inhalt geht hinaus. Es gibt keine Policy für ausgehende Empfänger.
  - Mehrere Caller teilen einen Drafts-Ordner mit unterschiedlichen Policies. Ein Entwurf von A (mit BODY auf der Quelle) ist für B lesbar, auch wenn B die Quelle nicht sehen darf. Das Modell kennt keine Herkunftskennzeichnung.
- **Lösung:** Pro-Nachricht-BODY für Reply-Quellen. Empfänger-Allowlist für Drafts (`recipient_domain` in `cap`-/Capability-Form, z. B. `draft_recipients: [domain…]`). Drafts tragen eine Herkunftskennzeichnung (Caller und höchste Quellsensitivität). Lesen von Drafts nur, wenn die Herkunft zur eigenen Policy passt.

#### S-10 — Draft-Identität über einen fälschbaren Header; Handle-Leak an Empfänger; Draft-Übernahme
- **Mechanismus:** `X-SC-Draft-Id`/`X-SC-Draft-Rev` sind Klartext-Header (`ADR 0030:23-30`). Jede Nachricht mit diesem Header gilt als „vom Server erstellt“ und damit als editierbar und verwerfbar (`ADR 0031:64`, `draft_edit_capability.feature:137-159`, `update_draft_errors.feature:117-136` arbeiten mit wörtlich eingeschleusten Nachrichten). Der Header geht beim Senden an die Empfänger (`ADR 0030:57`). Ein Empfänger kennt danach die `draft_id` und kann eine Nachricht mit derselben ID und höherer `Rev` einschleusen. Landet sie per `copy` oder Prompt-Injection in Drafts, gilt nach „highest revision wins“ (`ADR 0030:36-38`) der Angreiferinhalt. `discard_draft` wirkt auf **jeden** Ordner mit `move_out` (`ADR 0034:27-39`), etwa INBOX oder Sent Mail, und verschiebt dort jede Nachricht mit dem Handle in den Papierkorb. „The handle is not a capability“ (`ADR 0030:70`) gilt nicht, wenn der Handle selbst fälschbar ist.
- **Lösung:** Der Server signiert Handles: `X-SC-Draft-Sig = HMAC(server_key, draft_id‖rev‖content_hash)`. Unsignierte oder falsch signierte Nachrichten sind `editable: false`. Den Handle vor dem Senden entfernen, wo möglich (in der Doku empfehlen); serverseitiges Provenienz-Register (WAL). `discard_draft` nur in Ordnern mit `\Drafts`-Flag zulassen.

#### S-11 — Attachment-Rewrite: Fälschung und Vernichtung eingehender Belege
- **Mechanismus:** `replace_attachment` auf eine empfangene Rechnung ersetzt das PDF. Das Original wird expunged statt in den Papierkorb verschoben. `INTERNALDATE` springt auf jetzt (`ADR 0031:19`). Flags bleiben erhalten (`message_rewrite_recovery.feature:21-29`), die Fälschung sieht also aus wie eine empfangene Mail. Es gibt keine Visibility-Anforderung, kein ADR und keine Sicherheitsanalyse (G-06). Das widerspricht `ADR 0018:27-39` (kein Hard-Delete) und berührt GoBD- und Beweisfragen.
- **Lösung:** Die Tools nur auf eigene, signierte Drafts anwenden (`update_draft` um Attachments erweitern) oder entfernen. Rewrite empfangener Nachrichten verbieten. Wenn der Owner sie dennoch will: Original in den Papierkorb, `FULL` + `modify_message` + Audit-Intent vorab.

#### S-12 — Attachment-Senke: Symlink/TOCTOU, vorhersagbare Namen, keine Trennung der Caller
- **Mechanismus:** Siehe Pfad D. `ADR 0028:240-246` behauptet „blast radius … bounded to writes inside that one directory“, ohne `O_NOFOLLOW`, `O_EXCL`, `openat(dirfd)` oder Prüfung des Dateityps zu verlangen. Im Sidecar-Szenario folgt der Server einem Symlink, den der Agent im gemeinsamen `emptyDir` anlegt, in den eigenen Mount-Namespace (`/state`). Damit fällt die Vertrauensgrenze, die ADR 0029 gerade herstellen will. Lokal ist das Ziel `policies/*.yaml`: Ein Anhang mit YAML-Inhalt wird zur Policy (Neustart/SIGHUP). Zusätzlich: eine geteilte Senke für alle Caller (LIM 0013), der Pfad steht in der Tool-Beschreibung (LIM 0016), und der md5-Präfix legt den Inhalts-Fingerprint offen.
- **Fehlende Szenarien:** `fetch_attachment_sink.feature` testet nur Pfadtraversal im Dateinamen, keine vorhandenen Symlinks oder Dateien.
- **Lösung:** Pro Caller ein Unterverzeichnis (0700), `O_CREAT|O_EXCL|O_NOFOLLOW` über `openat`, zufällige Namen (UUID) und Hash nur in der Response, 0600, fsync. Szenario „vorab angelegter Symlink → `sink_not_writable`, Ziel unverändert“.

#### S-13 — Audit-Blindstellen und manipulierbare Zeitbasis
- **Mechanismus:**
  1. Such-Orakel erscheinen als `ALLOW/OK`. Der Kriterien-Digest ist ein ungesalzener SHA-256, nur gegen bekannte Klartexte prüfbar (`ADR 0021:185-188`). Zählwerte fehlen.
  2. Audit erst nach der Ausführung (`ROADMAP.md:163`).
  3. Keine Einträge bei Exceptions und -32602.
  4. `caller_addr` auf HTTP wertlos.
  5. Retention läuft nie.
  6. `external_root_hook` läuft mit `shell=True` und erbt die komplette Umgebung inklusive Secrets (`ADR 0029:384-391`). Das steht nicht in ADR 0022.
  7. `IMAP_MCP_FAKE_NOW_UTC`/`…_FILE` sind **nicht** an `IMAP_MCP_TEST_MODE` gebunden (`ADR 0023:216-223`). Sie verschieben Audit-Zeitstempel, die Tagesgrenze und nach `ADR 0024:62-68` vermutlich auch die zeitbasierten Policy-Prädikate (`newer_than`/`older_than`). ADR 0023 nennt sie nicht einmal als Primitive. Auch `_test_run_audit_rotation` und `_maybe_pause` (LIM 0008) fehlen in der Aufzählung, die angeblich vollständig ist (`ADR 0023:120-122`).
- **Lösung:** Intent-Eintrag vor jedem Schreibvorgang (Entscheidung von #33 umsetzen). „Probe“-Felder im Audit (`hidden_matches_suppressed: true`, ohne Zahl). Kriterien als HMAC mit Betreiberschlüssel oder verschlüsselt an den Betreiber. Alle Test-Hooks an `TEST_MODE` binden und in ADR 0023 vollständig aufzählen. Hook ohne Shell, mit leerer Umgebung.

### MITTEL

#### S-14 — Das Regelmodell ist eingangszentriert und richtungsblind
- „Sender Rule Matcher Grammar“ (`ADR 0004:1`) mit `from`/`from_domain`. Für Empfänger gibt es nur `to` (To und Cc vermischt) und das Substring-Prädikat `to_contains` (`ADR 0004:35-36`). `to_contains: firma.de` trifft auch `firma.de.evil.example` und `notfirma.de`. Es gibt kein `to_domain`, kein `cc`/`bcc` und keine „alle Empfänger ∈ Domainmenge“-Semantik.
- Auch in `Sent Mail` heißt die Ablehnung `sender_not_whitelisted`. `from` ist dort immer der Eigentümer, Absenderregeln sind also wirkungslos.
- `newer_than`/`older_than` werten den fälschbaren `Date:`-Header aus (`ADR 0004:40-41`), `since`/`before` dagegen `INTERNALDATE` (`gmail_search_and_counts.feature:19-24`). Policy und Suche sehen damit unterschiedliche Zeiten. Ein Angreifer setzt `Date:` so, dass eine Mail zeitbasierte Caps umgeht.
- **Lösung:** Präfixe `sender.*` und `recipients.*` mit `domain_in`/`address_in` und den Quantoren `any`/`all`. Zeit nur aus `INTERNALDATE`. Neutrale Reason-Codes `message_not_granted`/`message_capped`.

#### S-15 — OAuth-Scope als Schein-Verteidigung
- Siehe I-7 und G-02. Gmail-IMAP kennt keinen engeren Scope. Der Satz „A caller cannot perform an operation unless *both* allow it“ (`ADR 0009:71-73`) ist für Gmail falsch.
- **Lösung:** ADR 0009 berichtigen. Auf Gmail gibt es nur eine Schicht, die Policy, und diese muss deshalb strenger sein.

#### S-16 — Weitere Offenlegungen auf COUNT- und Struktur-Ebene
- `list_folders` gibt `message_count` aus. Laut `gmail_search_and_counts.feature:29-30,153-155` ist das der STATUS-Wert, also inklusive `NONE`-Nachrichten. Ordner, deren höchste Stufe `NONE` ist, werden trotzdem gelistet (Archive, `write_capabilities.feature:103-104`), entgegen „list_folders: COUNT“ (`README.md:338`).
- `folder_stats` liefert `hidden_count`.
- `list_labels` gibt Zählwerte pro Label aus (`ADR 0019:57-59`).
- **Lösung:** Gelistet werden nur Ordner mit einer höchsten Stufe ≥ COUNT. Zählwerte nur über sichtbare Nachrichten, außer `disclose_hidden_counts`.

#### S-17 — Capability-Umgehung über Systemflags in `mark_tagged`
- `ADR 0005:40` erlaubt `\Flagged` und Keywords: „No other system flags“. Getestet wird nur die Ablehnung von `\Deleted`. Ob `mark_tagged` mit `\Seen` `mark_seen: false` umgeht oder `\Draft`/`\Answered` setzt, prüft kein Szenario.
- **Lösung:** Allowlist (`\Flagged` + Keywords) als Szenario-Outline mit allen Systemflags.

#### S-18 — Policy-Integrität im dokumentierten Standard-Deployment nicht gegeben
- LIM 0019 (Proposed) beschreibt eine reale Eskalation am 2026-06-22. README und Quickstart (`README.md:37-64`) empfehlen genau die verwundbare Konstellation ohne Warnung. Mit S-12 braucht der Angriff nicht einmal eine Shell.
- **Lösung:** Quickstart auf einen getrennten OS-Benutzer umstellen. Die Warnung prominent platzieren. LIM 0019 genehmigen und um S-12 ergänzen.

#### S-19 — Mehrere Caller: geteilte Caches, Konfiguration wird nicht abgewiesen
- `ADR 0029:38-43` (Alias-Cache, Token-Cache, eine Audit-Kette, ein WAL), `:409-413` („nothing in the server refuses that configuration“).
- **Lösung:** Siehe S-07. Den Zustand pro Caller trennen oder einen Einzel-Caller-Modus hart erzwingen.

#### S-20 — Mehrere lokale Instanzen teilen sich `wal.db`
- `ADR 0007:46-47`, `:69-72` (Single-Process-Annahme) gegenüber ADR 0032 (neun parallele stdio-Instanzen). Das WAL wird von keinem ADR für mehrere Instanzen behandelt. Recovery-Trigger durch Polling kann dieselbe Transaktion parallel vorantreiben.
- **Lösung:** Lock auf Transaktionsebene (`BEGIN IMMEDIATE` + Lease-Spalte) und ein Szenario mit zwei Instanzen.

#### S-21 — Prompt-Injection-Kette ohne Gegenmittel im Modell
- Das Modell kennt keine Kennzeichnung „nicht vertrauenswürdiger Inhalt“. Inhalte aus `fetch_body` können Anweisungen enthalten (S-06 markieren, S-05 kopieren, S-09 Bcc, S-10 Draft einschleusen). ADR 0001 behauptet Prompt-Injection-Resistenz (`:129-131`), betrachtet aber nur die Request-Form und nicht die Tool-Kombinatorik.
- **Lösung:** Invarianten aus Abschnitt 5, die auch bei feindlichem Agenten gelten (das Modell nimmt ausdrücklich einen kompromittierten Agenten an).

### NIEDRIG

#### S-22 — Isolationsversprechen der Usability-Läufe
- `ADR 0039:60-61` gegenüber `usability/README.md:122-130,251-254`: Läufe mit dem HOME des Nutzers, Browser-Tools in `agy` ohne Einschränkung, Payload-Modus `full`. Risiko für Fixtures gering, aber das ADR stellt den Zustand falsch dar.

#### S-23 — `canonical_all_mail_uid` als Dichte-Seitenkanal
- Abstände zwischen den All-Mail-UIDs sichtbarer Nachrichten verraten Anzahl und Zeitverteilung unsichtbarer Nachrichten.

#### S-24 — Roher IMAP-Statustext in `error.detail`
- `create_draft_error_visibility.feature:26-29` und `ADR 0025:198-205`: Server-Statustexte können Mailbox- oder Kontodetails enthalten. Das ist vertretbar, sollte aber in einer Allowlist erfasst sein.

---

## 5. Vorschlag für ein konsistentes Ziel-Berechtigungs- und API-Konzept (Tool-Set 3.0.0)

### 5.1 Grundsatz

**Neuschnitt der Autorisierungssemantik, Reparatur der Infrastruktur.** Beibehalten werden PDP-Ort, Default-Deny, die lineare Stufenskala, die geschlossene Prädikatgrammatik, Saga/WAL, die Audit-Kette und die Gmail-Alias-Auflösung. Neu definiert werden das Schutzobjekt, das Transparenzmodell, das Verhältnis von Schreiben zu Lesen, die Regel-Eingaben und die Identität eigener Artefakte.

### 5.2 Sicherheitsinvarianten (normativ, jede mit mindestens zwei Szenarien und Gegenproben)

| ID | Invariante |
|---|---|
| **M-1 Nachrichtenidentität** | Schutzobjekt ist die Nachricht: Gmail über `X-GM-MSGID`, Standard-IMAP über `(account, mailbox, UIDVALIDITY, UID)`. Für Gmail ist die effektive Sichtbarkeit `eff(m) = f({policy(label) | label ∈ labels(m)})` mit **deny/cap-overrides** (Minimum), unabhängig vom adressierten Ordner. |
| **N-1 Non-Interference** | Für jedes Tool mit Mindeststufe L gilt: Antwort (Felder, Zählwerte, Fehlerklasse, Paginierung, Reihenfolge), Audit-Ergebnisfeld und Seiteneffekte sind identisch, gleich ob Nachrichten mit `eff(m) < L` existieren oder nicht. Ausnahme nur bei expliziter Ordner-Freigabe `disclose_hidden_counts` (Default `none`). |
| **N-2 Einheitliches Not-found** | Jede UID- oder Handle-Adressierung einer Nachricht mit `eff(m) < METADATA` liefert dasselbe `not_found` wie eine nicht existierende UID. |
| **W-1 Write ≤ Read** | Jedes Schreib-Tool hat eine Mindeststufe pro Nachricht (5.3) und wird pro Nachricht geprüft. Bulk-Tools wirken nur auf Nachrichten oberhalb der Schwelle und zählen nur diese. |
| **W-2 Fluss-Monotonie** | `move`, `copy`, Label-Add und Cross-Account dürfen `eff(m)` für den Caller nicht erhöhen: `eff_target(m) ≤ eff_source(m)`, sonst DENY `relocation_would_escalate`. |
| **W-3 Keine Selbstfreigabe** | Agent-schreibbare Zustände (Flags, Keywords, Labels, Draft-Header) dürfen in `grant`-Regeln nicht vorkommen. Der Loader weist das ab. |
| **W-4 Kein Hard-Delete, keine Fälschung** | Empfangene Nachrichten werden nie umgeschrieben. Entfernen heißt immer Papierkorb. Gmail-Trash/-Spam verlangen `move_out` auf **allen** aktuellen Labels. |
| **P-1 Provenienz** | Vom Server erzeugte Artefakte tragen eine serverseitig signierte Herkunft (`HMAC(server_key, draft_id‖rev‖content_hash)`) und sind zusätzlich im WAL registriert. Nur signierte Artefakte sind editierbar oder verwerfbar. Eine optionale Regel `grant_own: BODY` macht eigene, gesendete Nachrichten in `Sent Mail` lesbar (Signatur oder Registereintrag + Message-ID). Das löst den Draft→Sent-Vorfall ohne `to_contains`-Krücke. |
| **C-1 Caller-Bindung** | `tx_id`, `draft_id` und Sink-Dateien sind an `caller_id` gebunden. Fremde liefern `not_found`. |
| **A-1 Vollständiges Audit** | Intent-Eintrag vor jedem Schreibvorgang. Ist das Audit nicht schreibbar, wird nichts ausgeführt. Jede Anfrage erzeugt einen Eintrag, auch -32602 und Exceptions. Unterdrückte Treffer werden als Bool vermerkt. Kriterien als HMAC mit Betreiberschlüssel. |
| **T-1 Test-Hooks** | Ohne `TEST_MODE` existiert kein Test-Hook (Uhr, Crash, Pause, Recovery, Rotation). Ein Start mit gesetzten Hook-Variablen ohne `TEST_MODE` ist fatal. |
| **V-1 Geschlossene Vokabulare** | Reason-Codes und Error-Types stehen in einer maschinenlesbaren Registry. ADRs, Features und Server verweisen auf sie. CI weist Abweichungen ab. |

### 5.3 Schreib-Tools: Mindeststufen pro Nachricht (Vorschlag)

| Tool | Capability | Mindeststufe pro Nachricht | Zusatz |
|---|---|---|---|
| `mark_seen`, `bulk_mark_seen` | `mark_seen` | `METADATA` | Zähler nur über sichtbare Nachrichten |
| `mark_tagged`, `bulk_mark_tagged` | `mark_tagged` | `ENVELOPE` | Flag-Allowlist; Gmail-Label = `copy`-Semantik (W-2) |
| `move` | `move_out` + `accept_incoming` | `ENVELOPE` | W-2, W-4 |
| `copy` | `move_out_copy` (neu, quellseitig) + `accept_incoming` | `ENVELOPE` | W-2; die Quelle muss zustimmen |
| `create_reply_draft` | `draft_append` | `BODY` an der Quelle (pro Nachricht) | Draft erbt das Sensitivitätslabel der Quelle |
| `update_draft`, `discard_draft` | `draft_edit` / `move_out` + `accept_incoming` | nur signierte eigene Drafts (P-1) | Empfänger-Allowlist `draft_recipients`; `discard_draft` nur in `\Drafts` |
| Attachment-Tools | **entfallen** für empfangene Mail; für eigene Drafts in `update_draft` integriert | `FULL` | W-4 |
| `fetch_attachment` | — | `FULL` | Senke pro Caller, `O_EXCL\|O_NOFOLLOW` |

### 5.4 Regel- und Transparenzmodell

- **Regeln:** `match` mit Namensräumen. `sender.address_in`, `sender.domain_in`; `recipients.{to,cc,bcc,any}.domain_in|address_in` mit den Quantoren `any`/`all`; `subject.contains` (nur `cap`); `size.*`; `received.newer_than` (INTERNALDATE); `provenance: own` (P-1). Agent-schreibbare Prädikate (`flags.*`, `labels.*`) nur in `cap`.
- **Konfliktregel Gmail:** `eff(m) = min` über alle Labels mit Policy. Labels ohne Policy tragen nichts bei. Ist **kein** Label der Nachricht freigegeben, gilt `NONE`. Owner-Entscheidung: alternativ ein expliziter Vorrang pro Label.
- **Transparenz statisch statt dynamisch:** `describe_policy` meldet pro Ordner `may_hide: true|false`. Such-Antworten enthalten `completeness: "complete" | "policy_may_hide" | "window_limited"`, abgeleitet aus Policy und Fenster, nicht aus den Daten. `outside_scope_total` zählt nur sichtbare Nachrichten. `matched_rule_index`, `sender_rules_count`, `hidden_count` und `filtered_out` entfallen oder erscheinen nur bei `disclose_hidden_counts`.
- **Reason-Codes (Response):** `account_hidden`, `folder_hidden`, `not_found`, `visibility_below_<L>` (nur bei Existenz-Sichtbarkeit ≥ METADATA), `capability_missing`, `relocation_would_escalate`, `recipient_not_allowed`, `policy_alias_conflict`, `forbidden_flag`. `sender_*` nur noch im Audit.

### 5.5 Nötige ADR- und Spezifikationsänderungen

1. **Neue ADRs (Supersession):**
   - „Message as Protection Object and Gmail Label Precedence“ (ersetzt Teile von 0001, 0003, 0019)
   - „Non-Interference Transparency“ (ersetzt 0017 §1/§2.3-Reaktionstexte, 0036 `outside_scope_total`)
   - „Write ≤ Read and Flow Monotonicity“ (ersetzt 0005 „folder-level only“, 0016 `copy`)
   - „Direction-Aware Message Rule Grammar“ (ersetzt 0004, 0024 Zeitbasis)
   - „Signed Draft Provenance“ (ersetzt 0030/0031 Identitätsteil, 0034 Ordnerbeschränkung)
   - „Message Rewrite Removed for Received Mail“ (beendet `modify_message`)
   - „Audit Intent and Probe Records“ (ergänzt 0021, setzt ROADMAP #33/#34 um)
   - „Per-Caller Sink with Safe File Creation“ (ersetzt 0028 §2/§4)
   - „Contract Registry“ (V-1)
   - „Tool Set 3.0.0“
2. **Neue Fehlerpfad-Schicht L18** „Authorization side channels & write×visibility“, enumeriert als 13 Schreib- und 13 Lese-Tools × {NONE, COUNT, METADATA, ENVELOPE, BODY, FULL} × {Antwortfelder, Fehlerklasse, Seiteneffekt, Audit}. Pflichtszenarien als Outline mit Gegenprobe („dieselbe Antwort mit und ohne versteckte Nachricht“).
3. **Rot-zuerst-Szenarien** für S-01, S-02, S-04, S-05, S-06, S-07, S-10, S-11 und S-12, jeweils mit unabhängigem Kanal (IMAP-Proxy-Log, direkte IMAP-Abfrage, Dateisystem-Prüfung).
4. **Feature-Bereinigung:** `default_deny.feature:64-71`, `whitelist_folder_mode.feature:25-29,52,109-122`, `blacklist_folder_mode.feature:53-57`, `flagged_predicate.feature:93`, `intra_account_move.feature:27-30`, `cross_account_move_saga.feature:27-30`, `search_completeness.feature:18-20,69-79` auf die neuen Invarianten umschreiben.
5. **Governance-Paydown:** G-01 bis G-21 (Spec-Audit-Zyklus 1, Proposed-Review, LIM-Template, Registry, Header-Linter).

### 5.6 Offene Owner-Entscheidungen

1. **Hidden Counts:** Völlig abschaffen (empfohlen) oder pro Ordner opt-in mit `bucketed`/`exact`? Und gilt das auch für `list_folders.message_count` und `folder_stats.hidden_count`?
2. **Gmail-Konfliktregel:** Minimum über Labels (empfohlen), expliziter Vorrang, oder „adressierter Ordner zählt“ (Status quo, unsicher)?
3. **`flagged` als Nutzersignal:** Ganz entfernen, nur als `cap` zulassen (empfohlen), oder `mark_tagged` die Hoheit über `\Flagged` entziehen?
4. **Attachment-Rewrite:** Für empfangene Mail entfernen (empfohlen) oder mit Papierkorb-Erhalt und `FULL` beibehalten?
5. **Eigene gesendete Mail lesbar machen** (`provenance: own`): signierter Header, serverseitiges Register oder beides?
6. **Ausgehende Empfänger-Policy** für Drafts (`draft_recipients`): Pflicht oder optional?
7. **Mehrere Caller:** Hart verbieten bis S-07/S-19 behoben sind, oder Zustand pro Caller vollständig trennen?
8. **Lokales Deployment:** Getrennter OS-Benutzer als neuer Standard (empfohlen), oder LIM 0019 als `permanent` genehmigen?
9. **Audit-Kriterien:** HMAC mit Betreiberschlüssel oder an den Betreiber verschlüsselter Klartext?
10. **Bruch:** Tool-Set 3.0.0 als Hard Cut mit Migrationshinweisen (empfohlen) gegenüber stufenweiser Härtung in 2.x. Bei 2.x bleiben die Orakel bis zum Abschluss offen.

---

### Befundstatistik

| Teil | KRITISCH | HOCH | MITTEL | NIEDRIG | Summe |
|---|---|---|---|---|---|
| Governance / BDD (G) | 6 | 9 | 5 | 1 | 21 |
| Berechtigung / API-Sicherheit (S) | 6 (S-01, S-02, S-04, S-05, S-06, S-07) | 7 (S-03, S-08–S-13) | 8 (S-14–S-21) | 3 (S-22–S-24) | 24 |
| **Gesamt** | **12** | **16** | **13** | **4** | **45** |

*IDs sind thematisch vergeben; S-03 ist als HOCH eingestuft und im HOCH-Block aufgeführt.*
