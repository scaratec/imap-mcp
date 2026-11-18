# Vollprüfung: Spezifikation, Governance und Berechtigungssicherheit

**Projekt:** `imap-mcp` · **Prüfdatum:** 2026-11-17 · **Ergebnis:** nicht konsistent; Sicherheitsmodell und öffentliche Verträge benötigen einen Neuschnitt.

**Befundbilanz:** **37 Befunde: 7 KRITISCH, 22 HOCH, 7 MITTEL, 1 NIEDRIG.** Davon Governance/BDD: 14; Berechtigungen/API: 23. Mehrere Belegstellen oder Angriffsvarianten unter derselben ID zählen nicht als zusätzliche Befunde.

**Beweisgrenze:** Dies ist eine vollständige Prüfung des unten inventarisierten Markdown-/Gherkin-Bestands, kein Code-Audit und kein ausgeführter Penetrationstest. Es wurden ausschließlich `.md` und `.feature` mittels `glob`/`read` gelesen. Kein Zugriff auf Python, YAML, JSON/JSONL, Shell, laufende Server oder echte Postfächer. Insbesondere sind Step-Code-Ehrlichkeit, CI-Ergebnisse und tatsächliche Laufzeitimplementierung nicht unabhängig bestätigt. Ein als „belegt“ bezeichneter Angriff folgt aus dem spezifizierten Vertrag; als „dokumentierter Defekt“ bezeichnete Implementierungsaussagen stammen aus den zitierten Projekttexten. Eine fehlende Gegenprobe wird ausdrücklich nicht als ausgeführter Exploit ausgegeben.

**Zentrale Entscheidung:** `NONE = existiert für den Caller nicht` und queryabhängige Zähler vor der Berechtigungsfilterung sind unvereinbar. Ebenso sind ordnerlokale, von Leserechten unabhängige Schreibrechte ohne Informationsflusskontrolle mit einer objektbezogenen Vertraulichkeitsgarantie unvereinbar. Zusätzliche `to_domain`-Prädikate allein reparieren das nicht.

## 1. Geprüfter Dokumentenbestand

### 1.1 Vollständig gelesene Referenzen

Unter `/home/randy/Projekte/privat/burn-your-code/`:

| Datei | Vollständiger gelesener Bereich | Maßgebliche Anforderungen |
|---|---:|---|
| `BDD_GUIDELINES_v1.8.0_DE.md` | 1–661 | §§1–13; insbesondere 92–95, 120–128, 213–324, 566–627 |
| `governance/README.md` | 1–134 | Triade, insbesondere 59–69 |
| `governance/adr/README.md` | 1–135 | Pflichtgliederung 30–50, Lifecycle 66–89, Schreibregeln 91–112 |
| `governance/adr/TEMPLATE.md` | 1–83 | Metadaten, Consequences, Security, Alternatives |
| `governance/limitations/README.md` | 1–216 | Keine Selbstfreigabe, vollständige Felder, Lifecycle, Nummerierung |
| `governance/limitations/TEMPLATE.md` | 1–117 | `Scope`, Freigaben, Resolution-SHA, Resolution intent, Restrisiko |

Der Governance-Überblick verweist zwar bereits auf Guidelines 1.9.0 (§14), der Prüfmaßstab dieses Auftrags bleibt ausdrücklich **1.8.0, §§1–13**. Es wurden keine zusätzlichen Anforderungen einer ungelesenen Version unterstellt.

### 1.2 Projektbestand und Inventurkorrektur

Vollständig gelesen wurden **74 Projekt-Markdown-Dateien**, zusammen mit den sechs Referenzen also **80 Markdown-Dateien**, sowie **75 Feature-Dateien**:

| Bestand | Zahl | Umfang |
|---|---:|---|
| ADRs | 40 | `docs/adr/0000-…md` bis `0039-…md`, alle lückenlos |
| ADR-Register | 2 | `docs/adr/README.md:1-102`, `docs/adr/TEMPLATE.md:1-72` |
| LIM-Records | 23 | 0001–0022, darunter **zwei verschiedene 0011** |
| LIM-Register | 2 | `docs/limitations/README.md:1-169`, `docs/limitations/TEMPLATE.md:1-101` |
| Übrige Pflichtdokumente | 7 | `README.md:1-535`, `bdd/README.md:1-70`, `usability/README.md:1-299`, `docs/ROADMAP.md:1-205`, `docs/error_path_analysis.md:1-730`, `docs/spec-audit/README.md:1-99`, `docs/spec-audit/TEMPLATE.md:1-145` |
| BDD | **68** | Alle durch `bdd/features/**/*.feature` ermittelten Dateien; Einzelinventar in §2.5 |
| Usability | **7** | Alle durch `usability/features/**/*.feature` ermittelten Dateien; §2.5 |

Die Auftragsannahme „57 BDD-Dateien“ ist im vorgefundenen Bestand überholt. Es wurden **alle 68** gelesen, nicht nur 57 ausgewählt. `docs/spec-audit/*.md` liefert nur README und TEMPLATE, **keinen abgeschlossenen Audit-Zyklus**. Das Ziel dieses Berichts existierte vor der Erstellung nicht. Keine bestehende Projektdatei wurde verändert.

Status aus den ADR-Dateien selbst: **33 Accepted**, **2 teilweise superseded/sonst Accepted** (0004, 0017), **1 Superseded** (0016), **4 Proposed** (0029–0032). Damit bestehen 35 ganz oder teilweise geltende Entscheidungen. Der Index enthält alle 40 Nummern, bildet aber drei dieser Statusangaben falsch ab.

Status aus den LIM-Dateien: **10 Resolved**, **6 Accepted**, **7 Proposed**; keine Mitigated-/Withdrawn-/Superseded-Einträge. Ein vorhandener Proposed-Entwurf wird hier nicht als genehmigte Ausnahme behandelt. Aus seiner bloßen Anwesenheit lässt sich auch kein Merge nach `main` beweisen.

## 2. Formales Modell & Matrix-Prüfung

### 2.1 Zustands- und Informationsflussmodell

Der notwendige Zustand ist nicht nur eine Liste von Ordnern:

`S = (Caller, Auth, PolicyGeneration, Accounts, FolderAliases, Objects, Memberships, Content, Flags, DraftIdentity, WAL, Sink, Audit, Traces)`.

- Ein Objekt `m` gehört zu einem Account `a`; seine IMAP-Adressen sind Tupel `(a, f, UIDVALIDITY, uid)`. Eine UID allein ist keine dauerhafte Nachrichtenidentität.
- Bei Gmail ist `memberships(m)` eine Menge. Unterschiedliche UIDs unter unterschiedlichen Labels adressieren dasselbe Objekt; Änderungen können mehrere Ansichten betreffen (`docs/adr/0019-gmail-label-semantics.md:12-22`).
- Der aktuelle PDP berechnet `V(c,a,f,m)` auf `NONE < COUNT < METADATA < ENVELOPE < HEADERS < BODY < FULL`. Whitelist: Maximum passender Grants; Blacklist: Minimum aus Default und passenden Caps (`docs/adr/0003-whitelist-blacklist-folder-modes.md:45-57`).
- Schreibrechte `K(c,a,f)` sind getrennte Folder-Booleans. Die ersten fünf werden in ADR 0005 festgelegt; `modify_message` erscheint später ohne entsprechend vollständige Accepted-Entscheidung, `draft_edit` in Proposed ADR 0031.
- Eine Operation ist `T(c,a,f,m,args,S) → (Response, S', AuditEvents, TraceEvents)`. Response allein reicht zur Sicherheitsbewertung nicht: verschobene Inhalte und Sink-Dateien sind zusätzliche Ausgabekanäle.

**Erforderliche Non-Interference:** Für zwei Zustände `S ≈c S*`, die sich ausschließlich in für `c` verborgenen Objekten/Feldern unterscheiden, müssen alle erlaubten adaptiven Aufruffolgen dieselben callerbeobachtbaren Ergebnisse liefern. Dazu gehören Rückgabefelder, Fehlerklassen, Pagination und erreichbare Dateien. Audit-Inhalte sind grundsätzlich operatorseitig; ihre Vollständigkeit ist eine zusätzliche Invariante. Timing-Nichtinterferenz wird von keinem gelesenen Szenario bewiesen.

**Aktuelles Gegenbeispiel:** Sei `q` eine vom Caller gewählte Query und `W` das Zeitfenster. Der spezifizierte Vertrag ist sinngemäß:

`M(q,W) = |{m : q(m) ∧ W(m)}|`

`MV(q,W) = |{m : q(m) ∧ W(m) ∧ V(c,a,f,m) ≥ METADATA}|`

`filtered_out = M − MV`.

Fügt man in `S*` genau eine passende `NONE`-Nachricht hinzu, bleiben die sichtbaren Nachrichten gleich, aber `M` und `filtered_out` steigen. Bereits **eine** erfolgreiche Query unterscheidet die beiden Zustände. Für `outside_scope_total` gilt dasselbe vor dem Defaultfenster; ADR 0036 verlangt ausdrücklich Zählung **vor** PDP-Filterung (`docs/adr/0036-search-window-transparency-and-calendar-dates.md:26-30`). Das ist keine lediglich theoretische Seitenuhr, sondern ein deterministischer Antwortkanal.

**Unaufgelöste Reihenfolge:** ADR 0001 verlangt PDP vor *jeder* IMAP-Operation (`docs/adr/0001-default-deny-hierarchical-policy.md:33-35,59-60`); ADR 0004 benötigt bereits gefetchte Envelope-/Headerdaten (`docs/adr/0004-sender-rule-matcher-grammar.md:49-52`); ADR 0033 benötigt einen `LIST` zum Auflösen vor der Ordnerpolicy (`docs/adr/0033-gmail-special-use-folder-resolution.md:41-58`). Ohne ausdrücklich zweistufige Autorisierung – Account-/Discovery-Gate, begrenzte interne Metadatenermittlung, Objekt-/Aktionsentscheidung – sind diese Reihenfolgen nicht gleichzeitig erfüllbar.

### 2.2 Alle 30 Tools: Vertrag, Ausgabekanal und Angriffsfläche

Abkürzungen: `C/M/E/H/B/F` = COUNT/METADATA/ENVELOPE/HEADERS/BODY/FULL; `N` = NONE. `K` bezeichnet Capability-Gating ohne festgelegte Nachrichten-Untergrenze. Ein „offen“ bezeichnet fehlende Absicherung, nicht einen nachgestellten Laufzeitbefund. Die Tool-Liste und Metadaten sind in `bdd/features/tool_surface/mcp_tool_discovery.feature:25-122` vollständig enthalten.

| Tool | Visibility / Capability | Zu prüfender Ausgabekanal oder Seiteneffekt | Befund |
|---|---|---|---|
| `list_accounts` | AccountPolicy | `hidden_accounts_count` hängt von verborgenen Accounts ab | S-08; `default_deny.feature:38-41` verlangt den Leak |
| `list_folders` | laut Discovery C; schreibbare N-Ordner werden dennoch gelistet | `hidden_folders_count`, `message_count`; rohe Folder-Zahl statt autorisierter Nachrichtenmenge | S-08; Widerspruch `write_capabilities.feature:99-107` |
| `list_labels` | C, Gmail | Hidden-Label-Zahl, Message-Counts, providerabhängiger DENY; Rechte derselben Nachricht in anderen Labels unbestimmt | S-08/S-10/S-18 |
| `folder_stats` | C | `visible_count`, `hidden_count`, `visibility_level`; keine belastbare gemischte N/C/M-Verteilung geprüft | S-08; ein bloß nichtnegativer Count genügt nicht |
| `search` | M | `matched_total`, `filtered_out`, `outside_scope_total`; Gmail-All-Mail-UID; Prädikate über höher geschützte Felder | S-01/S-08/S-17 |
| `list_messages` | M laut Discovery | gleiche Zähler wie search; From/Subject/Attachment-Existenz versus M-Untergrenze | S-01/S-08 |
| `fetch_envelope` | E | Sender-DENY vs Abwesenheit; `matched_rule_index`; Labels; Redaktionshinweise | S-08/S-09 |
| `fetch_headers` | H | gleiche UID-Existenz-/Reason-Frage; Headerdaten dürfen nicht durch niedriger berechtigte Queries erschlossen werden | S-01/S-09; vollständiges Negativprodukt fehlt |
| `fetch_body` | B | Text/HTML; Attachment-Metadaten an anderer Stelle B-, hier teilweise F-gebunden; Attachment-Body-Abgrenzung | S-08/S-11; G-09 |
| `list_attachments` | B | MIME-Namen/Typen/Größen; UID-/Part-Existenz bei niedrigerer Sicht muss einheitlich gesperrt sein | S-09; B/F-Bytes-Trennung grundsätzlich formulierbar |
| `fetch_attachment` | F | **Filesystem-Schreiboperation** trotz Read-Gruppe; `saved_to`, Hash/Größe, Sink-Fehler | S-05/S-16/S-19/S-23 |
| `list_drafts` | M | ausdrücklich `subject`, Handle, Revision, `editable`; fehlende Mixed-Visibility-Redaktion | S-08/S-12 |
| `get_draft` | B | Body/Headers/Attachments-Metadaten; Fremdhandle, Revision/Ambiguität, N-Nachrichten in sonst offenem Folder | S-09/S-12/S-13 |
| `mark_seen` | K=`mark_seen` | Blindänderung an N-Nachricht; Erfolg/Fehler als Existenzsignal; unbekannte UID nicht konsistent spezifiziert | S-09; vollständige Matrix fehlt |
| `bulk_mark_seen` | K=`mark_seen`, individuelle Sichtprüfung unbestimmt | queryabhängiger `marked_count` und ggf. Suchzähler; Blind-Bulk-Writes | S-01/S-09; G-07 |
| `mark_tagged` | K=`mark_tagged` | `\Flagged` verändert Grant-Prädikat; Gmail-Labels verändern Sicht und Mitgliedschaften | S-02/S-03/S-10 |
| `bulk_mark_tagged` | K=`mark_tagged`, individuelle Sichtprüfung unbestimmt | `tagged_count` als Prädikatorakel; Selbstfreigabe per Stern/Label, `replace`-Flag-Erhaltung offen | S-01/S-02/S-03 |
| `move` | src `move_out`, dst `accept_incoming` | N→F-Transfer; All-Mail-Fetch; globale Gmail-Label-Nebenwirkungen; Crash/EXPUNGE | S-02/S-07/S-10/S-22 |
| `copy` | dst `accept_incoming`; keine Source-Export-Capability | N/B→F kopiert auch nicht lesbare Inhalte/Attachments; Archiv ist damit nicht nichtlesbar | S-02/S-07 |
| `create_draft` | dst `draft_append`; Ziel-N erlaubt | callergewählte From/To-/MIME-Inhalte; Handle-Overwrite ist für einen eingebrachten X-SC-Header geprüft; verlorene ACKs nicht idempotent entschieden | S-12/S-14/S-18/S-22 |
| `create_reply_draft` | dst `draft_append` **und laut Feature Source ≥ B** | serverseitiges Zitat; nur Folder-M-Untergrenze negativ getestet, nicht Message-Cap in F-Folder | **S-11: Lücke, kein belegter allgemeiner Blind-Reply** |
| `update_draft` | `draft_edit`, ausdrücklich auch N | Headeränderung kann Leseregel freischalten; Erhalt fremder Bodies; Revision/Replay-Oracle, fehlende Ownership/CAS | S-03/S-12/S-13 |
| `discard_draft` | src `move_out`, Trash `accept_incoming` | alle Handle-Träger **im benannten Folder**; Count, Trash-Name, globale Gmail-Auswirkung, Teilfehler | S-02/S-09/S-10/S-12 |
| `add_attachment` | `modify_message`, keine Mindest-Sicht | Blind-Rewrite; `has_attachment`/Größe ändern Grant; gesamtes RFC822 neu gespeichert | S-03/S-12/S-13/S-22 |
| `replace_attachment` | `modify_message`, keine Mindest-Sicht | Dateiname als Auswahl-/Existenzorakel; fremde Bytes überschreibbar; mehrdeutige Namen offen | S-09/S-12/S-13 |
| `delete_attachment` | `modify_message`, keine Mindest-Sicht | Dateinamenorakel; fremde Teile löschbar; `has_attachment=false`/Größe als Freigabeweg | S-03/S-09/S-12 |
| `describe_policy` | authentifizierter Caller, keine Read-Grants nötig | eigene Policy, Modes, Rule-Counts und Maxima sind vorgesehen; versteckte Account-/Folder-Zahlen verletzen N; keine Regeln/anderen Caller zulässig | S-08; nicht jedes eigene Policy-Metadatum ist ein Mail-Leak |
| `get_caller_identity` | authentifiziert | eigene Identität; kein textbelegter Mail-N-Leak | Kein isolierter Leak belegt; Session-/Auth-Kontrakt G-09/S-20 |
| `get_transaction_status` | kein Ownership-Gate dokumentiert | fremde WAL-Daten **und Recovery-Schreibzugriff**; kein reines Read-Meta-Tool | S-04/S-15 |
| `tool_surface_info` | authentifiziert | Version/Protokoll/Änderungen; kein Mailzustand erforderlich | Kein isolierter Mail-Leak belegt; Versionsvertrag G-09 |

`gmail_results` und ein callerseitiges Feld `gm_msgid` sind im geprüften Bestand nicht als vollständiger Response-Vertrag festgelegt. `X-GM-MSGID` ist in den Provider-Features eine IMAP-/Fixture-Identität, `canonical_all_mail_uid` dagegen ausdrücklich ein Response-Feld. Aus der Aufgabenfrage allein wird hier **keine tatsächliche Ausgabe** zusätzlicher Felder behauptet. Für alle Gmail-Ergebniskollektionen fehlt eine gemeinsame Allowlist: gleiche autorisierte UID-Menge, keine Nebenliste mit N-Treffern, keine versteckten Label-Mitgliedschaften.

Ebenso ist ein fester, allein vom gewährten Level abhängiger Hinweis `redacted_fields=[body,attachments]` **nicht automatisch** ein Inhaltsorakel. Er wird eines, wenn Anwesenheit/Abwesenheit oder Umfang vom verborgenen konkreten MIME-Inhalt abhängen. Dieses Unabhängigkeitsgebot ist nicht spezifiziert/getestet.

### 2.3 Vollständige 13-Schreibtool-Matrix

Die sieben Sichtspalten beziehen sich auf die **vorbestehende Quellnachricht**, außer bei `create_draft`: dort auf das Ziel, weil es noch keine Quellnachricht gibt.

Legende: `K` = gemäß Orthogonalitätsvertrag reicht das Capability-Paar auch ohne Lesefreigabe; `K!` = dieses Verhalten ist zusätzlich unmittelbar durch ein Szenario mit N belegt; `K?` = Capability ist genannt, aber die zusätzlich behauptete individuelle Bulk-Autorisierung hat keinen festgelegten Visibility-Floor; `D*`/`A*` = die BODY-Grenze ist spezifiziert, jedoch nicht für alle individuellen Message-Caps abgesichert. Das ist eine **Vertragsmatrix**, keine Behauptung, alle Zellen seien ausführbar getestet.

| Tool | N | C | M | E | H | B | F | Source-Capability | Ziel-Capability | Sicherheitsfolge |
|---|---|---|---|---|---|---|---|---|---|---|
| `mark_seen` | K | K | K | K | K | K | K | `mark_seen` | — | Blind-Write; keine unabhängige Source-Objektberechtigung |
| `bulk_mark_seen` | K? | K? | K? | K? | K? | K? | K? | `mark_seen` | — | unbekannte Selektionsmenge; queryabhängige Counts |
| `mark_tagged` | K | K | K | K | K | K | K | `mark_tagged` | für Labels nicht festgelegt | Stern-/Label-Eskalation |
| `bulk_mark_tagged` | K? | K? | K? | K? | K? | K? | K? | `mark_tagged` | für Labels nicht festgelegt | Bulk-Eskalation und Orakel |
| `move` | K! | K | K | K | K | K | K | `move_out` | `accept_incoming` | vollständiger Inhalt kann in F-Ziel gelangen |
| `copy` | K! | K | K | K | K | K | K | keine eigene Export-Capability | `accept_incoming` | selbst ohne `move_out` aus Archiv herauskopierbar |
| `create_draft` | K! | K | K | K | K | K | K | keine Quelle | `draft_append` | Write-only Neuanlage ist möglich; Receipt-Semantik gesondert definieren |
| `create_reply_draft` | D* | D* | D* | D* | D* | A* | A* | Source-Zugriff ≥ BODY laut Feature | `draft_append` | per-Message-Check und korrekte Zitatprojektion erforderlich |
| `update_draft` | K! | K | K | K | K | K | K | `draft_edit` | gleicher Folder, keine weitere | Blind-Rewrite, eigene/fremde Handles nicht unterschieden |
| `discard_draft` | K | K | K | K | K | K | K | `move_out` | `accept_incoming` auf aufgelöster Trash | alle gefundenen Revisionen; Count-/Mitgliedschaftsleak |
| `add_attachment` | K | K | K | K | K | K | K | `modify_message` | gleicher Folder, keine weitere | Blind-Rewrite verändert Policy-Inputs |
| `replace_attachment` | K | K | K | K | K | K | K | `modify_message` | gleicher Folder, keine weitere | Überschreiben ungelesener Attachments |
| `delete_attachment` | K | K | K | K | K | K | K | `modify_message` | gleicher Folder, keine weitere | Löschen ungelesener Attachments |

**Capability-Achse, vollständig als Fallunterscheidung:**

1. Kein Account-/Folder-Zugang: DENY vor objektbezogener Verarbeitung; die Priorität gegenüber Capability-Fehlern widerspricht sich stellenweise (G-09).
2. Benötigte Source-Capability fehlt: DENY `capability_missing` für jedes oben mit Source-Capability bezeichnete Tool, unabhängig vom Read-Level.
3. Benötigte Ziel-Capability fehlt: DENY für `move`, `copy`, beide Create-Tools und `discard_draft`; bei `move`/`discard_draft` sind damit alle vier Source-/Target-Bool-Kombinationen definiert: nur `true/true` lässt passieren.
4. Alle benötigten Capabilities vorhanden: die sieben Sichtzellen oben gelten; keine Ziel-Read-Berechtigung ist verlangt. Gerade **Ziel-FULL statt Ziel-NONE** ist die nicht gesperrte Eskalationskombination.
5. Für `mark_tagged` als Gmail-Labelmutation fehlt die Abbildung auf **alle betroffenen** Source-/Target-Capabilities. Für Rewrite-Tools werden APPEND/DELETE ohne `draft_append`/`move_out` impliziert. Es existiert keine allgemeine „Autorisierung des gesamten Zustandsdeltas“.

Belege: `docs/adr/0005-per-folder-write-capabilities.md:34-53,82-85`; `bdd/features/transactions/intra_account_move.feature:27-43`; `bdd/features/transactions/cross_account_move_saga.feature:27-30,125-134`; `bdd/features/policy/write_capabilities.feature:109-123`; `bdd/features/policy/draft_edit_capability.feature:214-239`; `bdd/features/policy/modify_message_capability.feature:3-5`; `bdd/features/tool_surface/create_reply_draft.feature:357-380`.

Keine Datei prüft dieses Produkt vollständig. Die meisten Mutationsbeispiele verwenden F, Bulk-Beispiele E. Ein bestandener F-Happy-Path belegt weder eine Verweigerung auf N noch den Erhalt der Vertraulichkeit nach einer Mutation.

### 2.4 Zentrale Fehlerpfadbilanz: Nachrechnung, nicht Übernahme

Notation `C/D/P` = im Text als covered/deferred/specified-pending bezeichnet. In der Spalte „Summary“ werden die alten `+LIM`-Zuschläge zur nominellen Coverage addiert; sie sind **kein aktueller Coverage-Beweis**.

| Layer und Detailbeleg in `docs/error_path_analysis.md` | Summary: Total; C/D/P | Einzelzeilen: Total; C/D/P | Ergebnis |
|---|---|---|---|
| L1, 96–119 | 22; 6/16/0 | 22; 6/16/0 | Arithmetik gleich; Referenzen teils falsche Ursache |
| L2, 148–157 | 8; 4/4/0 | 8; 4/4/0 | Batch/Protokollversion werden durch Discovery nicht geprüft |
| L3, 169–179 | **8; 7/1/0** | **9; 8/1/0** | Ein vollständiger Fall unterschlagen |
| L4, 193–216 | **22; 16/6/0** | **22; 17/5/0** | L4.18 ist zusätzlich covered, nicht deferred |
| L5, 230–245 | **14; 12/2/0** | **14; 6/8/0** | Sechs Coverage-Zuordnungen zu viel in Summary |
| L6, 259–269 | 9; 2/7/0 | 9; 2/7/0 | NIL und BYE durch andere Szenarien nur behauptet |
| L7, 283–295 | 11; 5/6/0 | 11; 5/6/0 | L7.7 verweist auf „implicit“/zukünftigen Unit-Test |
| L8, 306–317 | 10; (0+6 LIM)/4/0 | 10; 6/4/0 | LIM-0003 ist Resolved; Parallelrefresh nicht im referenzierten Szenario |
| L9, 331–359 | 27; (13+2 LIM)/4/8 | 27; 15/4/8 | LIM-0006 Resolved; Startup-Feature hat außerdem neunten Audit-Fall |
| L10, 378–389 | 10; 7/3/0 | 10; 7/3/0 | Zahlen gleich; Atomic-write-/Pfadfälle fehlen |
| L11, 398–410 | **11; (8+3 LIM)/0/0** | **11; 10/1/0** | `fsync failure` steht tatsächlich auf deferred |
| L12, 419–433 | 13; 12/1/0 | 13; 12/1/0 | Neue Accounts ≠ Scopeänderung; fehlende Policyreferenz nicht belegt |
| L13, 442–455 | 12; 9/3/0 | 12; 9/3/0 | Timeout/Refresh-Drain/IDLE bewusst offen; Gründe nicht einheitlich gültig |
| L14, 500–540 | 39; (29+7 LIM)/3/0 | 39; 36/3/0 | LIM-0002 Resolved; semantische/securitybezogene Fälle fehlen |
| L15, 594–611 | 16; 0/0/16 | 16; 0/0/16 | vollständig pending, keine verifizierte Coverage |
| L16, 645–675 | 29; 29/0/0 | 29; 29/0/0 | neue Draft-/Search-Fälle, keine vollständige Sicherheitsmatrix |
| L17, 689–713 | 23; 23/0/0 | 23; 23/0/0 | Counts sind Eigenschaften, teils im selben Szenario; keine 23 unabhängigen Fehlerversuche |
| **Summe** | **284; 200/60/24** | **285; 195/66/24** | **195+66+24=285**, nicht 284 |

Die Summary-Summe **182 + 18 historische LIM-Zuschläge + 24 pending + 60 deferred = 284** ist als Addition ihrer eigenen Zeilen korrekt (`docs/error_path_analysis.md:32-49`). Sie stimmt aber nicht mit den Detailzeilen überein. **195 ist ebenfalls keine bestätigte echte Coverage**, sondern nur die nachgezählte Zahl der als covered bezeichneten Detailzeilen.

Konkrete unzulässige Coverage-Schlüsse: L2.7/L2.8 (`:156-157`) aus funktionierender Discovery/Tool-Set-Version; L6.6/L6.9 (`:266-269`) aus fehlender Message-ID bzw. implizitem Healthcheck; L7.7 (`:291`) aus „will be explicit in unit test“; L8.10 (`:317`) aus einem einzelnen zeitgesteuerten Refresh. Ein passender Layername ersetzt keinen unterscheidbaren Fehlerstimulus und keinen beobachteten Zielzustand.

Nicht vollständig enumeriert sind insbesondere: quer über alle Tools Query-/Output-Non-Interference, Sicht-/Capability-Produkte, fremde Transaktionen und Handles, globale Gmail-Nebenwirkungen, Rewrite-Mehrdeutigkeiten, gleichzeitige Revisionen, Audit-Intent-/Outcome-Ausfälle, reale Retention-Auslösung, Sink-Symlinks/Kollisionen/Quoten, Export-/Download-Ownership, Provider- und UID-Epochenwechsel. „Gap 0“ ist deshalb auch unabhängig von der Rechenkorrektur nicht belastbar. Das Dokument räumt bei fehlender Policy selbst eine ungezählte „highest-value gap“ ein (`:613-631`).

### 2.5 Alle Feature-Header und Rückreferenzen

Dies ist zugleich das vollständige Feature-Inventar. Pfade in der folgenden Tabelle sind relativ zu **`bdd/features/`**; `H` ist die Summe der enumerierten Headerpositionen, nicht die Zahl expandierter Szenarien. `=` bedeutet ausschließlich **arithmetisch passende Headeraddition**, nicht vollständige oder laufende Tests. `—` bedeutet keine Fehlerpfadquantifizierung. Für Querschnittslücken gelten G-05 bis G-11 auch bei `=`. ADR-Zuordnungen nennen die maßgebliche Entscheidung; „nur Liste“ kennzeichnet fehlende Detailentscheidung.

| Feature | Headerbereich; H / behauptet covered | Rechen-/Belegprüfung und ADR-Anbindung |
|---|---|---|
| `audit/audit_log_format.feature` | 9–22; 12/12 | =; 0021; mehrere Eigenschaften im selben Szenario, keine Vorab-Audit-Schreibprobe |
| `audit/audit_multi_instance.feature` | 20–28; 7/7 | =; **0032 Proposed**, 0021/0022 |
| `audit/audit_retention.feature` | 8–16; 7/7 | =; 0022; Helperauslösung statt Produktions-Day-Roll |
| `auth/caller_authentication.feature` | 10–20; 10/9 | =; 0015; Timing nicht beobachtet, mehr Examples als Headerfälle |
| `auth/explicit_imap_user.feature` | 1–21; — | keine Tabelle; explizites Login-Feld ohne entsprechende konkrete ADR-Regel |
| `auth/oauth2_bootstrap.feature` | 10–19; 8/8 | =; 0009/0010; kein Beleg für echtes XOAUTH2/Provider-Read-only |
| `auth/secret_store_backends.feature` | 13–21; 7/7 | =; 0011; keine Schreibatomizität/GPG-Persistenz/Plaintext-Acknowledgement |
| `deployment/health_probe.feature` | 34–43; 8/8 | =, aber **alle pending**, 14 expandierte Fälle; 0029 Proposed |
| `deployment/projected_config_reload.feature` | 51–60; 8/8 | =, aber **alle pending** und teils ungültige Policies; 0014/0029 |
| `observability/tracing_payload_mode.feature` | 42–52; 13/13 | =; 0037; Modus/Secret-Prüfung mehrfach in derselben Outline |
| `observability/tracing_spans.feature` | 34–44; 10/10 | =; 0038; Exporterausfall nicht geprüft |
| `performance/connection_pool.feature` | 35–42; 6/6 | =; 0013; kein Token-Drain/Acquire-Timeout/Multi-Prozess-Limit |
| `performance/connection_pool_expiry.feature` | 23–26; 2/2 | =; 0013; kurze Settings statt Default-Grenzen |
| `performance/connection_reuse.feature` | 8–11; 2/2 | =; 0013/LIM-0011-performance; Header nennt fälschlich LIM-0010 |
| `policy/blacklist_folder_mode.feature` | 8–11; 6/6 | =; 0003; zusätzliche Auditprobe, keine Cross-Label-Komposition |
| `policy/default_deny.feature` | 7–13; 6/6 | =; 0001; Szenario 64–71 verlangt gerade das N-Orakel |
| `policy/draft_consistency.feature` | 16–19; 2/2 | =; 0005/0030; unbedingter Header „same caller must discover“ gilt nicht bei N |
| `policy/draft_edit_capability.feature` | 18–27; 9/9 | =; 0031 Proposed; N-Write ausdrücklich positiv |
| `policy/flagged_predicate.feature` | 8–15; 6/6 | =; 0004; keine Caller-Selbstmarkierung als Gegenprobe |
| `policy/modify_message_capability.feature` | 7–10; 2/2 | =; keine vollständige Accepted-Capability-Entscheidung; positiver Fall nur Response |
| `policy/policy_reload.feature` | 9–17; 7/7 | =; 0014; Mid-saga-Setup unvollständig, interne Zustandsbehauptungen |
| `policy/sender_rule_matcher.feature` | 9–14; 20/20 | = als Addition; „3 combination“-Fälle nicht separat zugeordnet, NFC-Probe ohne NFC-Varianz; 0004 |
| `policy/visibility_levels.feature` | 8–11; 14/14 | = als 7+7; keine eindeutige 14-Fall-Zuordnung, 10 expandierte Szenarien; 0002 |
| `policy/whitelist_folder_mode.feature` | 7–10; 7/7 | =; 0003; `matched_rule_index` widerspricht 0017 |
| `policy/write_capabilities.feature` | 16–22; 14/14 | = als Addition, aber u.a. `copy` und `mark_tagged`-Capability-DENY fehlen hier; 0005 |
| `providers/gmail_draft_lifecycle.feature` | 44–53; 9/9 | =; 0019/0030/0031/0033/0034; globale Label-Nebenwirkung positiv festgeschrieben |
| `providers/gmail_label_semantics.feature` | 10–21; 10/10 | = als Eigenschaften; 0019; ungeprüfte All-Mail-Policy wird umgangen |
| `providers/gmail_search_and_counts.feature` | 43–51; 14/14 | =; 0017/0019/0026/0036; Date und INTERNALDATE absichtlich gleichgesetzt |
| `providers/imap_mailbox_names_utf8.feature` | 19–28; 11/10, 1 pending | =; 0025; `DEGRADED`-Audit ohne ADR, malformed nur In-file-Schuld |
| `providers/localized_gmail_folders.feature` | 1–76; — | keine Tabelle; 0033; alle drei Happy Paths mit vorgelagertem `list_folders` |
| `providers/localized_gmail_resolution.feature` | 31–42; 14/14 | =; 0033; ein Konfliktfall, keine LIST-Fehler-/Rename-/alle-Tools-Matrix |
| `tool_surface/add_attachment.feature` | 7–13; 5/5 | =; 0026 nur Tool-Liste; alte UID nach Rewrite, WAL statt Mailinhalt |
| `tool_surface/bulk_mark_seen.feature` | 7–12; 4/4 | =; 0026; kein gespeicherter Seen-Zielzustand |
| `tool_surface/bulk_mark_tagged.feature` | 9–17; 7/7 | =; 0026; Rücklesen ohne Kardinalität kann leer/vakuos bestehen |
| `tool_surface/create_draft_error_visibility.feature` | 46–55; 5/5 | =; 0027; Lost-ACK wird fälschlich mit Nicht-Persistenz gleichgesetzt |
| `tool_surface/create_draft_format.feature` | 26–34; 11/11 | =; 0035; strukturierte Header-Injection nicht negativ geprüft |
| `tool_surface/create_reply_draft.feature` | 51–74; **16/15** | **4+3+2+4+3=16**, nicht 15; Basis-Rezept überwiegend Feature-only, 0035 ergänzt HTML |
| `tool_surface/default_scope_transparency.feature` | 10–14; 7/7 | =; 0026; nur search/list, nicht symmetrisch Bulk |
| `tool_surface/delete_attachment.feature` | 7–11; 3/3 | =; 0026 nur Liste; alte UID, bloße Anzahl statt richtige verbliebene Bytes |
| `tool_surface/discard_draft.feature` | 29–38; 8/8 | =; 0034; keine Mid-Batch-Fehler-/N-/Fremdhandle-Probe |
| `tool_surface/draft_identity.feature` | 16–24; 7/7 | =; 0030 Proposed; zwei Create-Erfolgsfälle nur Selbstauskunft |
| `tool_surface/fetch_attachment_content.feature` | 11–20; 8/8 | = als Eigenschaften; 0028; Quelle-gegen-Datei-Bytevergleich fehlt |
| `tool_surface/fetch_attachment_sink.feature` | 13–27; 13/13 | =; 0028; kein Symlink/Collision/ENOSPC/langer Extension-Test |
| `tool_surface/fetch_body_attachment_metadata.feature` | 9–12; 2/2 | =; 0002/0026; B/F-Vertrag nicht einheitlich |
| `tool_surface/fetch_body_html_fallback.feature` | 11–14; 2/2 | =; Fallback ursprünglich Feature-only; HTML-Ausgabe selbst uneindeutig |
| `tool_surface/folder_path_contract.feature` | 20–29; 7/7 | =, aber **2 pending**, nicht 7 laufend abgesichert; 0025 |
| `tool_surface/inline_attachment_detection.feature` | 1–37; — | keine Tabelle; zusätzliche Attachment-Klassifikation nicht durch Matcher-Grammatik abgedeckt |
| `tool_surface/list_attachments.feature` | 8–14; 5/5 | =; 0026; nur globale B/E-Floors, keine gemischten Caps/UID-Fehler |
| `tool_surface/list_folders_message_count.feature` | 9–14; 4/4 | =; 0016 nennt dieses konkrete neue Count-Feld nicht; versteckte Nachrichten im sichtbaren Folder fehlen |
| `tool_surface/list_messages.feature` | 9–18; 8/8 | = als Eigenschaften; 0026 nur Liste/Scope; kein M-Redaktionsfall |
| `tool_surface/mcp_tool_discovery.feature` | 13–19; 8/8 | =; 0026–0036; Metadata-Gates ersetzen keine Autorisierung |
| `tool_surface/non_goal_rejection.feature` | 8–12; 10/10 | = als Addition; **14** Non-goal-Namen statt Header „5“; Extra-Args widersprechen 0026 |
| `tool_surface/reason_code_contract.feature` | 16–20; 18/18 | `1 (per code)` nicht expandiert; 18 Basiscodes + Schließung + Entfernung wären 20; inzwischen zusätzlich Alias-Code |
| `tool_surface/replace_attachment.feature` | 7–11; 3/3 | =; 0026 nur Liste; behaupteter Hashwechsel wird überhaupt nicht geprüft |
| `tool_surface/required_arguments.feature` | 37–43; 9/9 | =; 0024/0026/0031; Extra-Keys ausdrücklich ausgenommen, entgegen 0026 |
| `tool_surface/response_transparency.feature` | 10–19; 15/15 | =; 0017/0027; nur exemplarischer Envelope, `matched_rule_index` nicht verboten |
| `tool_surface/search_blacklist_fast_path.feature` | 9–14; 4/4 | = als Eigenschaften; LIM-0011 statt klarer eigener Entscheidung; Audit beweist keine fehlenden internen FETCHes |
| `tool_surface/search_completeness.feature` | 37–46; 10/10 | =; 0036; keine nicht-pushdownfähigen Outside-Prädikate/N-Vergleichswelten |
| `tool_surface/search_criteria_and_pagination.feature` | 9–20; 33/33 | = als Eigenschaften; 0004/0024/0026; drei Sub-day-Beispiele unter sechs Einheiten setzen unsichtbare Uhrzeit voraus |
| `tool_surface/tool_surface_info.feature` | 9–15; 5/5 | =; 0027/0028; Versionstext allein beweist keine Kompatibilität |
| `tool_surface/update_draft.feature` | 16–28; 11/11 | =; 0031 Proposed; serieller Replay, keine echte Gleichzeitigkeit |
| `tool_surface/update_draft_errors.feature` | 10–28; 16, hier 13/anderswo 3 | **5+5+2+4=16; 13+3=16**; gleiche Fehlerursache CR/LF zulässig gruppiert; 0031 Proposed |
| `transactions/cross_account_move_saga.feature` | 13–21; 7/7 | =; 0006–0008; alte Error-Felder und N-Blind-Transfers |
| `transactions/draft_revision_recovery.feature` | 13–20; 5/4 | =; 0030/0031; 1 offener Delete-Refusal-Fall ohne gültige LIM |
| `transactions/intra_account_move.feature` | 12–19; 6/6 | =; 0006; Fallback-Crash/andere Deleted-Nachrichten fehlen |
| `transactions/message_rewrite_recovery.feature` | 8–11; 2/2 | **nur Flag-Szenario 21–29**, kein Crash-/Recovery-Szenario überhaupt |
| `transactions/saga_crash_recovery.feature` | 13–21; 7/7 | =; 0006–0008; expliziter Recovery-Helper, keine Startup-Auslösung |
| `transactions/startup_saga_recovery.feature` | 40–49; 8/8 | = als Headeraddition, aber **9 Szenarien, alle pending**, ungültiger Background |

Es fehlen Fehlerpfadtabellen in genau drei BDD-Dateien: `auth/explicit_imap_user.feature`, `providers/localized_gmail_folders.feature`, `tool_surface/inline_attachment_detection.feature`. Die reine Anzahl unterschiedlicher Szenarien darf nicht mit Fehlerursachen verwechselt werden; mehrfach in einem Szenario geprüfte Eigenschaften sind zulässig, benötigen aber eine explizite Zuordnung statt eines pauschalen „covered“.

**Usability-Inventar:** Alle sieben Dateien wurden vollständig gelesen; keine enthält eine numerische Fehlerpfadtabelle. Ihre Servervoraussetzungen dürfen deshalb nicht als zusätzliche deterministische Coverage gezählt werden (ADR 0039).

| Datei | Bereich | ADR-/BDD-Verknüpfung und formale Lücke |
|---|---:|---|
| `usability/features/01_draft_format_decision.feature` | 1–79 | 0035/0039; E1–E3 prüfen überwiegend Format, nicht Inhalt/Adressat des tatsächlichen Auftrags |
| `usability/features/02_draft_revision_and_discard.feature` | 1–86 | 0030/0031/0034/0039; R1 prüft Kürzung nicht; R3 ohne ideal-path-Zahl |
| `usability/features/03_invoice_search.feature` | 1–110 | 0026/0036/0039; Rechnungsbetrag ist kein Zahlungsnachweis; „ideale“ SEARCH-Pfade liefern nicht alle zur Klassifikation benötigten Inhalte |
| `usability/features/04_filing_and_attachments.feature` | 1–54 | 0019/0028/0039; A1/A2 mit Mailbox-/Dateizeugen, Idealpfad vorab nicht aus vollständigem API-Ergebnis hergeleitet |
| `usability/features/05_policy_transparency.feature` | 1–49 | 0001/0017/0039; bewertet Umgehungsversuche, sperrt sie aber nicht serverseitig |
| `usability/features/06_feedback_form.feature` | 1–59 | 0039; Schema als externer Verweis, unter diesem Audit nicht lesbar; Bewertungsdetails teilweise README-only |
| `usability/features/07_run_metrics.feature` | 1–50 | 0038/0039; fehlende Idealpfade, Bytes/Token als „share“ ohne Umrechnung, Schema-Rejections nicht im Audit |

**Pending-Bilanz:** `health_probe.feature:1` = 14 expandierte Fälle; `projected_config_reload.feature:1` = 8; `startup_saga_recovery.feature:1` = 9; `folder_path_contract.feature:68,102` = 2; `imap_mailbox_names_utf8.feature:103` = 1. **Summe 34**, verteilt auf 28 Scenario-/Outline-Deklarationen. Keine dieser fünf Dateien benennt eine gültig freigegebene LIM für ihre heutige Auslassung. Neue Proposed-Funktionalität kann ausdrücklich unverbindlich bleiben; ein bestehendes Accepted-Versprechen wie Startup-Recovery aus ADR 0006 wird dadurch nicht suspendiert.

### 2.6 ADR → Feature → LIM: vollständiger Entscheidungsabgleich

Alle ADRs besitzen die sechs Pflicht-Hauptabschnitte. Das bestätigt nur ihre Anwesenheit, nicht ihre Widerspruchsfreiheit. `A/P/S/AS` = Accepted/Proposed/Superseded/teilweise superseded, sonst Accepted. Die Zeilenzahl ist die vollständige Dateilänge. Für organisatorische oder rein interne Technologieentscheidungen ersetzt kein erfundener Gherkin-Test eine Architektur-/Lieferkettenprüfung; diese wurde unter der Lesebeschränkung nicht als durchgeführt behauptet.

| ADR, Status, Zeilen | Durchsetzende Feature-Familien | Nicht durchgesetzte/konfligierende Regeln und LIM-Bezug |
|---|---|---|
| 0000, A, 95 | dokumentarische Governance, kein Laufzeitfeature | Immutabilität/Index/Supersession nicht eingehalten; G-03/G-04/G-14 |
| 0001, A, 163 | `policy/default_deny`, whitelist/blacklist, reload | Pre-IMAP-PDP vs notwendige Fetches; N-Nichtexistenz, vollständiger Audit-Trail, Eskalationsverbot; LIM-0019 nur Proposed |
| 0002, A, 114 | `visibility_levels`, `list_attachments`, Fetch-Features | keine 7-Level-/Mixed-Message-/alle-Tools-Projektion; Count-/Predicate-Orakel; kein gültiger Verzicht |
| 0003, A, 120 | whitelist/blacklist und deren Loader-Ablehnungen | keine Multihoming-Komposition; neue Pending-Features verlangen verbotene Defaults |
| 0004, AS, 150 | `sender_rule_matcher`, `flagged_predicate`, Search-Criteria | Cc-only, Recipient-Domain, SMTP-Envelope vs Header, echte NFC/Adressgrenzen, mutable Attribute; Duration durch 0024 ersetzt |
| 0005, A, 144 | `write_capabilities`, Capability-Features | kein geschlossenes 7-Capability-Modell, keine Export-/Informationsflusskontrolle; Archiv- und Draft-Isolation widersprechen Mutationen |
| 0006, A, 159 | intra/cross-account/saga-crash | Startup nur pending; kein sicherer Fallback-Abbruch, keine vollständige Content-Verifikation, keine Tx-Ownership; LIM-0004/0005 historisch Resolved |
| 0007, A, 149 | direkte WAL-Assertions in Transaktionsfeatures | 0600/FULL-Durability/90-Tage-Purge nicht eigenständig abgesichert; „keine Bodies“ vs WAL-RFC822; Mehrprozess-Koordination ungeklärt |
| 0008, A, 171 | `saga_crash_recovery` | Einzel-Message-ID-Kollision, normalisierte ID, Bodyabweichung hinter 4 KiB, Hash-Witness nicht Gate; LIM-0006 Resolved deckt das nicht |
| 0009, A, 155 | `oauth2_bootstrap` | kein State-Mismatch-/TLS-/realer Provider-Scope-Beweis; Flow-Listener vs Paste-CLI; LIM-0010 ungenehmigt, 0017/0018/0021 Proposed |
| 0010, A, 155 | Token-Cache-/80%-Szenarien | Reactive-refresh genau einmal, dauerhaftes needs_rebootstrap, Refresh-Rotation/Parallelität/Drain fehlen; LIM-0017 widerspricht Proaktivitätsbehauptung |
| 0011, A, 176 | `secret_store_backends`, Bootstrap | atomisches put/delete, GPG-Schreiben, `acknowledge_plaintext`, Nichtleck bei allen Backends nicht abgesichert; LIM-0021 beschreibt Einschränkung |
| 0012, A, 131 | Infrastruktur-/Lieferkettendisziplin, kein Technologiebeweis durch BDD | Lockfiles/hash-verified installs/strict CI nicht verifiziert; ROADMAP dokumentiert ungepinnte CI; keine gültige Ausnahme |
| 0013, A, 167 | drei Performance-Features, `policy_reload` | Acquire-Timeout, OAuth-Drain, Max-age während laufender Lease, globale Instanzlimits; L13-Deferral ist keine Freigabe |
| 0014, A, 180 | `policy_reload`, projected reload pending | Referenzintegrität beim Start dokumentiert fehlend; atomare Generation statt nur Einzeldateien; Policy-Provenienz außerhalb Git nicht gesichert |
| 0015, A, 160 | `caller_authentication`, SecretStore | echte Zwei-Caller-Isolation, get_transaction_status, Tokenwechsel/Wiederverbindung, Transportadressen; keine Identitätsdelegation behauptet, aber S-04 erreichbar |
| 0016, S, 179 | Discovery, 0025/0026-Nachfolger | nicht als aktueller vollständiger Tool-Vertrag verwendbar; Index bezeichnet ihn weiter Accepted |
| 0017, AS, 255 | `response_transparency`, `reason_code_contract` | Counts widersprechen N; Rule-Index wird verlangt; Closed vocabulary/2-fache Varianz nicht vollständig; LIM-0001-Auflösung ohne Audit-Artefakt |
| 0018, A, 164 | Discovery/Non-goal-Rejection | unbekannte Testtools im Produktionsmodus nicht explizit geprüft; kein Schutz gegen Komposition legitimer Tools; EXPUNGE-Verbot mit Rewrite/Fallback unaufgelöst |
| 0019, A, 190 | alle Gmail-Provider-Features | All-Mail trotz hidden, komplette Labels nur „subject to policy“ ohne Negativtest; Pair-STORE nicht atomar; globale Trash-/Spam-Auswirkungen; LIM-0002 Resolved |
| 0020, A, 160 | Discovery positiv keine Domain-Tools | Architektur-/Abhängigkeitsgrenze nicht aus Feature-Ausführung beweisbar; „persistiert genau“ lässt Audit/Sink außer Betracht |
| 0021, A, 228 | Audit-Format, Transitions, Hashkette | Intent vor Seiteneffekt, unerwartete Exceptions, HTTP caller_addr, neue Fehler-/Eventfelder, filename in saved_to_absolute; ROADMAP 26/33/34 |
| 0022, A, 191 | `audit_retention` | Produktionsauslösung fehlt laut ROADMAP 35; exakte Grenztage/Hookfehler/Ankerverlust fehlen; LIM-0009 ist trotzdem Resolved |
| 0023, A, 240 | Crashs, Recovery-Schritte, Discovery, Audit | Tool-Absenz ≠ Aufrufverbot; globale Fake-Clock-/Pause-/Rotation-Hooks nicht vollständig enumeriert; Test-Recovery statt echter Auslösung |
| 0024, A, 193 | sechs Duration-Einheiten, zwei Schema-Rejects | falsche ältere-als-Rundung, Date-vs-INTERNALDATE, unsichtbare Uhrzeit, kein Request-Maximum; Schema-Audit widersprüchlich |
| 0025, A, 262 | `folder_path_contract`, UTF8, localized resolution | zwei pending; alle Folder-Opening-Tools nicht negativ geprüft; canonical-only durch 0033 ersetzt ohne saubere Statusnavigation |
| 0026, A, 375 | Discovery, Scope, list_attachments, Bulk, Arguments | fünf/sechs verschiedene Änderungen; 25/26 Zahlen im selben ADR; ignored Extra-Args; neue Tools nur aufgezählt statt vollständig entschieden |
| 0027, A, 292 | Response-Envelope, Tool-Surface-Info | alte `error_type`/`imap_response`/`missing_capability` ausdrücklich weiterhin gefordert; Closed enums lückenhaft; 2.2-„additiv“ trotz Alias-Break |
| 0028, A, 333 | beide Attachment-Sink-Features | geteilter Sink, Symlink/Collision/Atomicity/Retention/Readbarkeit; vier Accepted-LIMs, alle ohne Scope und mit identischen Proposer/Approver |
| 0029, P, **519** | health/projected/startup sämtlich pending | >500 Zeilen; Topologie + Probe + Recovery + Distribution; Single-caller nur Anleitung, nicht erzwungen; LIM-0019–0021 Proposed |
| 0030, P, 106 | `draft_identity`, Recovery/Errors | ausgelieferte Kern-API auf Proposed-Basis; kein authentischer Besitzer/Originalitätsbeweis; Fremdclient-Stale-Revision; LIM-0022 Proposed |
| 0031, P, 173 | update_draft/-errors, draft_edit, revision recovery | keine CAS-Atomizität; Owner-Bindung nicht implementierbar aus Headern allein; Delete-Refusal explizit offen; enthält 3 Tools + Capability + Saga |
| 0032, P, 144 | `audit_multi_instance` | als umgesetzt dokumentiert, aber unentschieden; truncated tail/clock rollback/bootstrap-Pfad nicht vollständig abgedeckt; kein Vorab-Write-Intent |
| 0033, A, 124 | `localized_gmail_resolution`, Gmail lifecycle | nur ein Alias-Konflikt; identische Duplikate, LIST-Ausfall, Reload/Rename und alle Tools offen; Folder-absent-/select-failed-Regel konfligiert |
| 0034, A, 102 | `discard_draft`, Gmail lifecycle | „Nothing moved on any error“ vs expliziter Teilfehler; Handle-only keine Ownership; keine N-/Trash-hidden-/Batch-Fehlerprobe |
| 0035, A, 100 | `create_draft_format`, Usability E | strukturierte Header-Injection nicht geprüft; From offen; HTML-Zitat-Sicherheitsprojektion fehlt; Descriptions-Regel 0026 nicht sauber ersetzt |
| 0036, A, 82 | `search_completeness`, Gmail search/counts | N-Orakel explizit erweitert; exakte Outside-Zählung ohne FETCH für nicht-pushdownfähige Prädikate unerfüllt spezifiziert |
| 0037, A, 82 | `tracing_payload_mode` | nur Raw-Create + Envelope, nicht alle 30 Tool-Payloads/Fehler/Nested-Felder; full ist Opt-in, keine automatische Rechtfertigung für gemeinsame Trace-Sinks |
| 0038, A, 84 | `tracing_spans` | keine Exporterausfallprobe; callergewählte Namen und Counts pauschal als nichtpersonenbezogen bezeichnet |
| 0039, A, 77 | alle sieben Usability-Features | tatsächliche Isolation schwächer als „isoliert“; incomplete rubrics/Idealpfade; keine deterministische Ersatzdeckung |

**Feature → ADR-Lücken:** Die bloße Aufzählung eines Tools in ADR 0026 entscheidet nicht über `modify_message`-Destruktivität, die Source-Untergrenze von Reply, Reply-all-/Attribution-/Missing-Message-ID-Regeln, Attachment-Auswahl nach Dateinamen, UID-Nachfolge nach Rewrite, `list_messages`-Feldprojektion oder `list_folders.message_count`. Diese Regeln leben ganz oder teilweise nur in Features bzw. in LIM-0011-performance. Expliziter IMAP-User, MIME-Inline-Erkennung und `mailbox_name_decode/DEGRADED` sind weitere ungedeckte Verträge. Spätere ADRs dokumentieren einzelne Ergänzungen, nicht rückwirkend jede dieser Regeln.

### 2.7 LIM-Einzelabgleich

Für **alle 23** fehlt das im Referenztemplate geforderte Metadatum `Scope`; für **alle zehn Resolved** fehlt `Resolution commit` einschließlich eines identifizierbaren Paydown-SHA im Resolution-Nachweis. Das lokale Template lässt diese Felder selbst weg (`docs/limitations/TEMPLATE.md:3-10`). „Resolved“ ist dadurch nicht verifizierbar. Ein fehlender Scope ist ein Metadatenverstoß auch dann, wenn sich der betroffene Bereich aus Prosa erschließen lässt.

| Record unter `docs/limitations/` | Status; Metadatenbeleg | Zusätzlicher Befund / Bezug |
|---|---|---|
| `0001-reason-code-symmetry-in-bdd.md` | Resolved; 3–17 | Freigabe vorhanden; Audit vom 06.05. behauptet, kein Archiv; offene Task-Platzhalter 41–43, widersprüchlicher Prüfauftrag 134–138 |
| `0002-gmail-scenarios-not-runnable.md` | Resolved; 3–15 | Freigabe vorhanden; verbleibende Mock-Lücken sollten eigene LIM erhalten (98–102,136–138), heute nur ROADMAP 48 |
| `0003-oauth2-scenarios-not-runnable.md` | Resolved; 3–16 | Freigabe vorhanden; XOAUTH2-Mock-Wiring als Mitigation 99–107, neuere L13-Dokumentation erklärt es nicht vorhanden |
| `0004-imap-fault-injection-via-env-var.md` | Resolved; 3–15 | Freigabe vorhanden; Resolution 104–154 ohne SHA; „quickest way“ 19–25 statt substantieller Ausnahme |
| `0005-imap-mitm-scenarios-deferred.md` | Resolved; 3–11 | Freigabe vorhanden; Resolution 117–154 ohne SHA; Aufwand/Scope als Grund |
| `0006-5-tuple-fallback-deferred.md` | Resolved; 3–18 | Freigabe vorhanden; ausdrücklich bloße Arbeitsaufteilung 60–73, kein externer/theoretischer Hinderungsgrund |
| `0007-http-transport-deferred.md` | Resolved; 3–13 | Freigabe vorhanden; 70–73 nur Sprint-Scope; dokumentierte Fixture-Promotion von Auth 148–155 |
| `0008-sighup-policy-reload-deferred.md` | Resolved; 3–21 | Freigabe vorhanden; Restverweis LIM-0003 und reine Umsetzungskosten; keine Resolution-SHA |
| `0009-audit-day-roll-deferred.md` | Resolved; 3–22 | Freigabe vorhanden; Test-Rotation statt Produktionsauslösung; ROADMAP 35 widerspricht Erledigung |
| `0010-production-imap-bdd-gaps.md` | Accepted; 3–8 | **Approved by/Date approved fehlen**; Why/Residual risk/References fehlen als Mindestgliederung; empirisch reparierte Bugs ohne Regressionstests |
| `0011-search-performance-large-mailbox.md` | Resolved; 3–16 | **Freigabename/-datum, Related Guidelines, Resolution-Nachweis fehlen**; führte neue API/Scope/Pagination als LIM ein |
| `0011-bulk-move-deferred.md` | Accepted; 3–10 | Proposer=Approver; doppelte Nummer; Zeitdruck 48–50; kann Feature-Deferral, nicht Autorisierung offener Sicherheitsbugs begründen |
| `0012-wire-response-transparency-uneven-across-tools.md` | Proposed; 3–11 | keine Freigabe; `commit pending`; normale Fehlerklassifikation/Silent-success-Defekte als Debt normalisiert; von ADR 0027 überholte Fields |
| `0013-single-attachment-sink-path.md` | Accepted; 3–10 | Proposer=Approver; Scope fehlt; `Related Guidelines: none specific`; dokumentiert Cross-caller-Dateizugriff |
| `0014-attachment-sink-requires-caller-filesystem-access.md` | Accepted; 3–9 | Proposer=Approver; Related Guidelines fehlen; README-Mitigation noch TODO 80–83 |
| `0015-attachment-sink-cleanup-out-of-scope.md` | Accepted; 3–9 | Proposer=Approver; Related Guidelines fehlen; TODO-/Zukunftsmitigation 85–89,107–109 |
| `0016-attachment-sink-path-disclosed-in-tool-description.md` | Accepted; 3–9 | Proposer=Approver; Related Guidelines fehlen; Mitigation README TODO 90–92 |
| `0017-expired-oauth-token-not-proactively-or-durably-detectable.md` | Proposed; 3–10 | Freigabe-Platzhalter; dauerhafte Zustandslücke, nicht durch „proactive exists“ behebbar erklärt |
| `0018-oauth-reauth-not-initiable-from-mcp-no-remediation-guidance.md` | Proposed; 3–10 | Freigabe-Platzhalter; billige Remediation ausdrücklich nur zur Zusammenhaltung verschoben (80–84) |
| `0019-policy-integrity-requires-out-of-process-trust-boundary.md` | Proposed; 3–11 | Freigabe-Platzhalter; dokumentierter totaler Policy-Rewrite; legitime topologische Grenze, aber keine genehmigte Betriebsfreigabe |
| `0020-audit-chain-fragments-across-per-agent-instances.md` | Proposed; 3–15 | Freigabe-Platzhalter; Kettenvollständigkeit extern ungelöst; kein Accepted-Abzug von 0021/0022 |
| `0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md` | Proposed; 3–14 | Freigabe-Platzhalter; 98–101 erklären einen Teil ausdrücklich ungeprüft; Rotation/Persistenz nicht über „permanent“ verdecken |
| `0022-externally-edited-drafts-lose-their-handle.md` | Proposed; 3–10 | Freigabe-Platzhalter; eigenes Beispiel 102–111 widerspricht field-wise Erhalt aus ADR 0031; Isolationsmitigation behauptet mehr als ADR 0005 |

Die LIM-README indexiert alle 23 Dateien samt Nummernkollision. Die Kollision ist also **bekannt, nicht behoben** (`docs/limitations/README.md:148-156`). Bei den fünf identischen Proposer-/Approver-Namen ist aus den Texten die Identität dieser beiden Rollen nachweisbar, nicht die tatsächliche Implementierungsrolle. Ein getrennter Review-/Freigabenachweis zur verlangten No-self-approval-Disziplin fehlt; es wird nicht behauptet, ein LLM habe eine menschliche Freigabe erfunden.

### 2.8 Persistence-Ledger für schreibende Szenarien

„Zweiter Kanal“ und „richtiger Endzustand“ sind zwei verschiedene Prüfungen. Ein Readback, der nur eine Anzahl oder einen WAL-Status betrachtet, beweist nicht die richtigen Message-/Attachment-Bytes. Reine Validierungs-DENYs dürfen nach Guidelines §13.4 bewusst nur den Fehler prüfen; daraus darf aber kein Beleg „kein Seiteneffekt bei allen Fehlern“ entstehen.

| Schreibpfad / konkrete Szenarien | Beobachtung des Endzustands | Ergebnis |
|---|---|---|
| `policy/write_capabilities.feature:40-136` | direkte IMAP-Flags/Keywords/Source-/Target-/Draft-Abfragen bei tatsächlich schreibenden Erfolgen | zweiter Kanal vorhanden; keine gesamte Visibility-/Capability-Matrix |
| `tool_surface/bulk_mark_seen.feature:21-28,30-37,47-54` | nur `marked_count`, einmal Connection-Zahl | **kein Seen-Zielzustand**, auch 20er-Erfolgsfall nicht |
| `tool_surface/bulk_mark_tagged.feature:24-60` | separates `list_messages` | Kanal vorhanden, aber „every/no returned message“ ohne Anzahl/UIDs besteht auch auf leerer Liste |
| `tool_surface/bulk_mark_tagged.feature:62-69,88-95` | nur Count/Connections | Nichtänderung bzw. persistierte Tags fehlen |
| `policy/modify_message_capability.feature:29-39` | nur ALLOW/OK/mechanism | **fehlt** |
| `tool_surface/add_attachment.feature:23-45` | Readback mit **alter** UID | kein spezifizierter Mapping-Vertrag nach DELETE/APPEND; `test`-Bytes nicht geprüft |
| `tool_surface/add_attachment.feature:53-71` | `matched_total=1` bzw. `get_transaction_status=committed` | kann mit unveränderter Originalmail bestehen; Zielinhalt nicht belegt |
| `tool_surface/replace_attachment.feature:21-29,40-49` | Response bzw. WAL-Status | Titel verspricht Content-Hashwechsel, **kein Then prüft ihn** |
| `tool_surface/delete_attachment.feature:21-32,43-54` | Readback alter UID, nur Länge 1/0 | richtige verbliebene Datei/Bytes nicht geprüft; abwesende alte UID darf kein erfolgreicher Leernachweis sein |
| `transactions/message_rewrite_recovery.feature:21-29` | Search nach bereits vorher gesetztem Stern | kann ohne Rewrite bestehen; kein Crash/Recovery geprüft |
| `policy/draft_consistency.feature:29-61` | separater List-Read bzw. direkte IMAP-Subject-Abfrage | Kanal vorhanden; erster Fall prüft nur Zähler |
| `tool_surface/create_draft_error_visibility.feature:70-197` | separates Listen nach jedem Call | Kanal vorhanden; Lost-ACK-/Timeout-Givens tragen die erwartete Abwesenheit nicht |
| `tool_surface/create_draft_format.feature:55-164,179-193` | direkte IMAP-MIME-/Header-/teilweise Body-Abfragen | Kanal vorhanden; MIME-Struktur allein beweist nicht den vollständigen Inhalt; HTML-Reply-Quelle ohne explizite Message-ID |
| `tool_surface/draft_identity.feature:34-49,119-132` | nur Response-Handle/Revision | **fehlt** in beiden Create-Erfolgen |
| `tool_surface/draft_identity.feature:54-118,134-192` | direkte Header/Flags oder `get_draft`/`list_drafts` | Kanal vorhanden; Header-Origin/UID-Abwesenheit nicht für alle Pfade geprüft |
| `tool_surface/create_reply_draft.feature:96-380` | positives Readback von Subject/Header/Body; negative Draft-Counts | Kanal vorhanden; References/komplette Cc-Menge trotz Headerankündigung nicht verifiziert; kein Message-Cap-Negativfall |
| `tool_surface/update_draft.feature:49-284` | get_draft, direkte IMAP-Header/Counts | Kanal vorhanden; Attachment-Erhalt nur als Name/Typ, nicht Bytes; Zwischenrevisionen teilweise nur Response |
| `tool_surface/update_draft.feature:286-295` | Transaktionsstatus | WAL-Commit, **nicht** gespeicherter Subject-Zielzustand |
| `policy/draft_edit_capability.feature:110-131` | nur Response | **fehlt** |
| `policy/draft_edit_capability.feature:137-177,214-239` | direkte Header/Revision | Kanal vorhanden; bei 214 wird die tatsächlich geänderte `body_text` nicht zurückgelesen |
| `tool_surface/update_draft_errors.feature:80-98,142-218,229-247` | Unverändertheits-/Ambiguitäts-/Konflikt-Zeugen, teilweise WAL | Kanal vorhanden für diese Fälle; restliche reine Validierungen keine erfolgreiche Write-Coverage |
| `transactions/draft_revision_recovery.feature:37-164` | direkte Mailbox und WAL; nachgelagerter Read | Kanal vorhanden; wirklicher Produktions-Recovery-Trigger fehlt, Delete-Refusal ausdrücklich ungetestet |
| `tool_surface/discard_draft.feature:51-201` | direkte Searches auf beiden Seiten, Counts/UID-Unverändertheit | Kanal vorhanden; letzter Fremddraft-Fall prüft das Ziel nicht, Teilfehler nicht enthalten |
| `transactions/intra_account_move.feature:33-98` | direkte Source/Target-Abfragen, Fallback-Wire-Log, WAL-Absenz | Kanal vorhanden; Bytes und nicht adressierte Deleted-Nachrichten fehlen |
| `transactions/cross_account_move_saga.feature:34-134` | direkte Source/Target/WAL-Zeugen in unterschiedlicher Kombination | Kanal vorhanden, **nicht in jedem Fall alle drei**, anders als Header 8–11; kein vollständiger Zielbytevergleich |
| `transactions/saga_crash_recovery.feature:30-119` | direkte Mailbox-/WAL-/Wire-Prüfungen | Kanal vorhanden; Einzel-ID-Dedup-Fall ohne tatsächlichen Content-Gleichheitsbeweis |
| `transactions/startup_saga_recovery.feature:68-166` | vorgesehene unabhängige Zeugen | **pending**, keine aktuelle durchsetzende Coverage |
| `providers/gmail_label_semantics.feature:66-124,137-146` | direkte labelübergreifende Searches/STORE-Log | Kanal vorhanden; UID-/MSGID-Setup teils konventionsabhängig, All-Mail-Autorisierung nicht geprüft |
| `providers/localized_gmail_folders.feature:23-45`; `localized_gmail_resolution.feature:88-167` | IMAP-Speicherort/Commands/Source-/Target-Searches | Kanal vorhanden; eingeschränkte Label-/Policy-Kombinationen |
| `providers/imap_mailbox_names_utf8.feature:164-206,212-227` | erste Writes mit Source/Target-IMAP, letzter nur Audit | **Audit-Fall prüft nicht den Mail-Endzustand** |
| `providers/gmail_draft_lifecycle.feature:74-120,129-222` | direkte Fixture- und Tool-Mutationen mit IMAP-Nachprüfung | Kanal vorhanden; kein fremdes geschütztes Label als Nichtänderungszeuge |
| `tool_surface/fetch_attachment_content.feature:35-65` | Datei lesen, Größe/Hash, Dateianzahl | Kanal vorhanden; File-Hash gegen Response ist keine unabhängige Gleichheit mit Quellpart |
| `tool_surface/fetch_attachment_sink.feature:42-79,91-141` | meist Dateiexistenz; **56-69 nur Response** | zwei erfolgreiche Dateischreibszenarien ohne Dateizeuge; übrige ohne vollständigen Quellbytevergleich |
| `tool_surface/inline_attachment_detection.feature:16-26`; `policy/visibility_levels.feature:78-89` | Datei vorhanden, beim Visibility-Fall zusätzlicher Attachment-Hashvergleich | Kanal vorhanden; Inline-Fall prüft weder Größe noch Quellbytes |
| `observability/tracing_payload_mode.feature:80-95` | nur Trace-Sink des create_draft-Aufrufs | Trace-Persistenz, **nicht** Draft-Persistenz |
| `auth/oauth2_bootstrap.feature:29-94`, `secret_store_backends.feature:75-84` | SecretStore-/Endpoint-/Nichtpersistenz-Zeugen | Kanal benannt; konkrete Tokens, Ablauf-/Parallelitäts- und Providerbedingungen nicht vollständig sichtbar |
| `policy/policy_reload.feature:32-136`, `deployment/projected_config_reload.feature:76-215` | nachgelagerte Tool-/Audit-/Pool-Beobachtung | erster Block teilweise beobachtbar; zweiter komplett pending/teils ungültige Inputs |
| Audit-/Tracing-Features, Pool-Shutdown/Expiry | Datei-/Collector-/IMAP-Verbindungslog als Zeuge des jeweils geprüften betrieblichen Effekts | vorhandene Kanäle nicht mit Mail-Persistenz oder automatisch ausgeführtem Lifecycle verwechseln |
| Usability E/R/A und S3 | am Ende Mailbox bzw. Sink als Evidenz vorgesehen | kein Ersatz für deterministische Write-Negativtests; Rubriken prüfen nicht durchgängig den fachlichen Zielinhalt |

## 3. Befunde Governance & BDD-Guidelines

### HOCH

#### G-01 — Das Schuldenregister ist nicht formal freigegeben und vollständig

- **Kategorie:** LIM-Governance, No-self-approval, Mindestmetadaten.
- **Beleg:** `docs/limitations/0010-production-imap-bdd-gaps.md:3-8`; `docs/limitations/0011-search-performance-large-mailbox.md:3-16`; `docs/limitations/0011-bulk-move-deferred.md:5-8`; `docs/limitations/0013-single-attachment-sink-path.md:5-10`; entsprechender Metadatenblock 0014–0016; Gesamtinventar §2.7. Referenz: `burn-your-code/governance/limitations/TEMPLATE.md:3-15` und `README.md:55-67,115-125`.
- **Beweis:** Accepted 0010 und Resolved 0011-performance ohne Approval/Date; 23 fehlende Scopes; zehn fehlende Resolution-SHAs; fünf identische Proposer/Approver ohne getrennten Reviewnachweis. Proposed 0012/0017–0022 sind keine genehmigten Ausnahmen. Das lokale Template perpetuiert fehlende Pflichtfelder.
- **Korrektur:** Owner muss Status und getrennte Freigabe nachvollziehbar bestätigen oder Records zurückweisen; Metadaten/Resolution-Artefakte vervollständigen. Keine rückdatierte oder aus Tests abgeleitete Genehmigung. Reine Bugs als Bugs führen, nicht nachträglich als akzeptierte Sicherheitsgrenze deklarieren.

#### G-03 — Geltende Entscheidungen und tatsächlich beschriebene API haben verschiedene Status

- **Kategorie:** ADR-Lifecycle/Supersession/Triade.
- **Beleg:** `docs/adr/README.md:67,79-80,92-95`; `docs/adr/0004-sender-rule-matcher-grammar.md:3`; `docs/adr/0016-mcp-tool-set.md:3`; `docs/adr/0017-response-transparency-for-filtered-data.md:3`; `docs/adr/0030-draft-identity-and-revisions.md:3`; `docs/adr/0031-draft-revision-via-rewrite-saga.md:3`; `docs/adr/0032-shared-audit-chain-across-local-instances.md:3`; `README.md:347-365`.
- **Beweis:** Index nennt die drei supersedierten/teilersetzten ADRs schlicht Accepted. Draft-/Revision-/Audit-Kernverhalten wird als vorhandene API spezifiziert, seine tragenden Entscheidungen bleiben Proposed. Accepted 0034/0035 bauen auf Proposed 0030/0031 auf. 0025 canonical-only und 0026 Description-Disziplin stehen weiterhin neben gegenteiligen neueren Regeln.
- **Korrektur:** Vor Release eindeutigen operativen Entscheidungsgraphen beschließen; Amendments abschnittsgenau als Nachfolgeentscheidungen führen, Indizes synchronisieren. Keine Behauptung „Accepted“, die nur aus einer Implementierung oder einem grünen Feature folgt.

#### G-05 — Fehlerpfad-Nullbilanz und Coverage-Zahlen sind nicht belastbar

- **Kategorie:** Guidelines §§4.2/4.5, Triaden-Enumeration.
- **Beleg:** `docs/error_path_analysis.md:32-49,169-179,193-216,230-245,398-410,625-631`; `bdd/features/tool_surface/create_reply_draft.feature:51-74`; `bdd/features/transactions/message_rewrite_recovery.feature:8-29`.
- **Beweis:** Nachrechnung §2.4: 285 Detailzeilen, 195 nominell covered, 66 deferred, 24 pending statt 284/200/60/24. Reply-Header summiert 16 statt 15. Rewrite-Recovery behauptet zwei gedeckte Fälle und enthält überhaupt keinen Crashfall. Drei BDD-Dateien ohne Quantifizierung; zahlreiche neue Sicherheitsachsen sind gar nicht enumeriert. Discovery belegt weder JSON-RPC-Batches noch inkompatible ProtocolVersions.
- **Korrektur:** Ursachen-IDs mit exakten Scenario-/Examples-Zuordnungen; Zustände specified/executed/deferred/failed getrennt. Ausschlüsse fachlich begründen; Sicherheits-/Integritätsfehler nicht als bloß „operational“ ausblenden. Zahlen aus derselben Matrix ableiten, nicht parallel manuell pflegen.

#### G-06 — Pending-Verträge sind teilweise unerfüllbar und besitzen keine gültige Ausnahme

- **Kategorie:** Guidelines §§2/4/12; Accepted-Pflichten vs Red-first-Spezifikation.
- **Beleg:** `bdd/features/transactions/startup_saga_recovery.feature:1,40-62,68-166`; `bdd/features/deployment/projected_config_reload.feature:96-109,132-160,178-193`; `bdd/features/policy/whitelist_folder_mode.feature:54-71`; `bdd/features/tool_surface/folder_path_contract.feature:68-82,102-115`; `bdd/features/providers/imap_mailbox_names_utf8.feature:30-43,103-122`; `bdd/features/transactions/draft_revision_recovery.feature:19-26`.
- **Beweis:** Startup-Background verwendet `whitelist/FULL`, Projection-Erfolgsfälle `whitelist/ENVELOPE` bzw. BODY – exakt die Kombinationen, die ein anderes Feature als Start-/Reload-Fehler verlangt. Startup hat außerdem keine sichtbaren Move-Capabilities. Pending canonical-Drafts erwartet Count 1 ohne Nachricht und ohne FolderPolicy. Insgesamt 34 expandierte Pending-Szenarien ohne gültige LIM; der zusätzliche Draft-Delete-Refusal bleibt ganz ohne Szenario. Bei neuen Proposed-Features ist Red-first legitim, nicht aber die Coverage-Behauptung oder das Abschalten früherer Accepted-Garantien.
- **Korrektur:** Erst fachlich gültige Inputs und klare Geltung, danach Implementierungsarbeit; Defekte nicht durch LIM-Scheduling legitimieren. `pending` muss Owner/Scope/entscheidbare Abnahmekriterien besitzen, und darf nicht als „covered“ zählen.

#### G-07 — Erfolg wird mehrfach ohne richtigen Persistenznachweis akzeptiert

- **Kategorie:** Guidelines §§4.3/13.2 Prüfung 1.
- **Beleg:** vollständiges Ledger §2.8; besonders `bdd/features/tool_surface/replace_attachment.feature:21-29`, `bulk_mark_seen.feature:21-28,47-54`, `draft_identity.feature:34-49,119-132`, `bdd/features/policy/draft_edit_capability.feature:110-131`, `bdd/features/tool_surface/fetch_attachment_sink.feature:56-69`.
- **Beweis:** Ein Stub, der OK/Count/Revision/mechanism zurückgibt, besteht diese Erfolge ohne den versprochenen Schreibeffekt. WAL-Commit allein beweist keinen korrekten neuen Mailinhalt. Leere Listen lassen universelle Bulk-Assertions bestehen. Attachment-Rewrites werden mit ungültig gewordener alter UID nachgelesen, ohne explizite Nachfolgeadresse.
- **Korrektur:** Je erfolgreicher Mutation Source-/Target-Endzustand und unveränderte Nachbarobjekte unabhängig lesen; Bytes gegen sichtbare Fixture oder direkt gefetchte Quelle vergleichen. Positive Mengenbehauptung vor „every/no“. Absichtlich rein diagnostische Szenarien als solche aus Coverage herausnehmen.

#### G-08 — Konkrete Then-Werte beruhen auf unsichtbaren Daten oder falschen Voraussetzungen

- **Kategorie:** Guidelines §§2.1/2.2/13.2 Prüfung 2.
- **Beleg:** `bdd/features/tool_surface/search_criteria_and_pagination.feature:23,131-147`; `bulk_mark_seen.feature:25-28,34-37`; `list_folders_message_count.feature:17-25,40-46`; `folder_path_contract.feature:119-128`; `bdd/features/providers/gmail_label_semantics.feature:66-83`; `bdd/features/policy/policy_reload.feature:128-136`.
- **Beweis:** Ein gepinntes Datum bestimmt nicht 12:00 Uhr für 60s/30m/6h. „10 messages“ bestimmt weder example.com-Sender noch Zeitfenster; „5 messages“ schließt nobody@nowhere.com nicht aus. Drei aufgezählte Folder minus ein sichtbarer ergeben zwei, nicht den erwarteten Hidden-Count 3; eine zusätzliche implizite INBOX-Regel fehlt im fachlichen Setup. Der „Existing/But/Hidden“-Ordner wird nicht angelegt: zwei nicht existierende Namen beweisen keine Exists/Absent-Ununterscheidbarkeit. Canonical UID, X-GM-MSGID und UID 505 werden teils durch Konvention gleichgesetzt. Mid-saga-Setup liefert weder Ziel noch ursprünglich gesetzte Capabilities sichtbar.
- **Korrektur:** Uhrzeit/Zeitzone, Sender/Datum, vollständige Foldermenge bzw. ausdrücklich benannte automatische INBOX, getrennte Gmail-IDs und ganze Saga-Inputs in Givens. Kein fachliches Ergänzen durch Steps. Eine Datums-/ID-Konvention muss Bestandteil der Spezifikation sein, nicht nur eines Helpers.

#### G-09 — Die ausführbare Spezifikation enthält gegensätzliche Schema- und Reason-Verträge

- **Kategorie:** Guidelines §§1.1/1.3/12; ADR-Closure und SemVer.
- **Beleg:** `docs/adr/0026-tool-surface-consistency.md:114-116,282-296` vs `bdd/features/tool_surface/non_goal_rejection.feature:73-76` und `required_arguments.feature:25-26`; `docs/adr/0027-error-envelope-and-tool-surface-versioning.md:90-118` vs `bdd/features/transactions/intra_account_move.feature:62-98`, `cross_account_move_saga.feature:57-71,101-110`, `bdd/features/providers/gmail_label_semantics.feature:93-103`; `bdd/features/tool_surface/response_transparency.feature:59-76` vs `non_goal_rejection.feature:40-46`.
- **Beweis:** Unbekannte Argumente müssen zugleich abgewiesen und ignoriert werden. Entfernte `missing_capability`, `required_scope`, `granted_scope`, `error_type`, `imap_response` werden weiterhin aktiv verlangt. Source-/Target-DENY-Priorität ist unbestimmt. Reasons `missing_message_id`, `oauth_scope_insufficient`, `needs_rebootstrap`, `tool_not_applicable_for_provider`, `secret_decryption_failed` und interne Audit-Reasons stehen nicht vollständig in der kanonischen Tabelle; neue Alias-Regel ist dort ebenfalls nicht nachgeführt. `INFO saga_not_configured` passt nicht in den später zwingend ALLOW/DENY-Vertrag. `fetch_body` soll HTML liefern, ein Feature verbietet `<p>` im gesamten JSON (`fetch_body_html_fallback.feature:24-32`). Message-ID soll angle-bracket-stripped persistiert werden (ADR 0008:48–50), das Saga-Feature verlangt die Klammern (cross_account:55). 2.2 wird „additive and break nothing“ genannt (`mcp_tool_discovery.feature:11`), obwohl 0033:83–87 bisher gewährte Alias-Policies sperrt und Response-Namen ändert.
- **Korrektur:** Ein versionierter, vollständiger Input-/Output-/Fehlerkatalog pro Toolfamilie, klare Entscheidungspriorität, Major-Bump bei tatsächlichem Hard Cut. Alle Gegenverträge in einem beschlossenen Nachfolger auflösen; Steps dürfen sie nicht durch Feldaliasing unsichtbar kompatibel machen.

#### G-11 — Test-only-Steuerung ersetzt Produktionsverhalten und tarnt fehlende Beweise

- **Kategorie:** Guidelines §§1.2/4.3/7.2/13; unabhängige Beobachtung.
- **Beleg:** `docs/error_path_analysis.md:361-369`; `docs/ROADMAP.md:163-165`; `bdd/features/transactions/saga_crash_recovery.feature:37-38,50-51`; `bdd/features/audit/audit_retention.feature:29,39,46`; `bdd/features/tool_surface/search_blacklist_fast_path.feature:33-39`; `docs/limitations/0007-http-transport-deferred.md:148-155`.
- **Beweis:** Recovery wird durch versteckten Testaufruf oder Status-Poll vorangetrieben; Start allein nicht. Retention läuft laut ROADMAP nur über `_test_run_audit_rotation`. Kein `tool=fetch_envelope` im Audit beweist **keinen** internen IMAP-FETCH. LIM-0007 beschreibt eine automatische Änderung der Auth-Konfiguration im Harness. Die bloße Abwesenheit versteckter Tools aus Discovery beweist nicht deren Ablehnung in Produktion.
- **Korrektur:** Produktionsauslöser ohne Recovery-/Rotation-Testtool auslösen und extern beobachten; Wire-Log statt falschem Audit-Proxy für interne FETCHes. Separate Harness-/Mock-Kontrakte, keine unsichtbare fachliche Auth-Promotion. RFC-Bytes im Adaptertest sind sinnvoll; interne Helfernamen und Test-only-Aufrufe sind kein Beleg für Fachlichkeit.

#### G-12 — Behauptete unabhängige Spec-Audits sind im verpflichtenden Archiv nicht nachweisbar

- **Kategorie:** Governance, Guidelines §13, Evidenzprovenienz.
- **Beleg:** `docs/spec-audit/README.md:9-16,47-59,86-97`; `docs/limitations/0001-reason-code-symmetry-in-bdd.md:10-13,134-138`; `docs/ROADMAP.md:9-13,201`; Glob-Bestand in §1.2.
- **Beweis:** Major-Releases und LIM-Auflösungen verlangen einen archivierten Zyklus. LIM-0001 beruft sich ausdrücklich auf einen Zyklus 2026-05-06, vorhanden sind nur TEMPLATE und README. Gleichzeitig soll dessen Mitigation den Produktivcode prüfen, während das Spec-Audit-README diesen bewusst ausschließt (`:38-42`). Aus `green` folgt keine Prüfung-3-Datensymmetrie; unter diesem Auftrag ist sie mangels erlaubtem Step-Code-Zugriff ausdrücklich nicht bewiesen.
- **Korrektur:** Fehlende Audit-Artefakte mit Range, Agententrennung und Owner-Bewertung vorlegen, andernfalls Erledigungsbegründung nicht darauf stützen. Spec-Critic, Step-Audit und Code-Sicherheitsaudit als getrennte Evidenzen führen.

### MITTEL

#### G-02 — LIM-0011 ist keine eindeutige Referenz

- **Kategorie:** Nummerierung/Rückverfolgbarkeit.
- **Beleg:** `docs/limitations/README.md:134-156`; `bdd/features/tool_surface/list_messages.feature:7`; `bdd/features/tool_surface/search_criteria_and_pagination.feature:7`; `docs/adr/0036-search-window-transparency-and-calendar-dates.md:12,69,77`.
- **Beweis:** Dieselbe Nummer steht für einen Resolved-Performance-Defekt und einen Accepted-Bulk-Move-Aufschub. Unqualifiziertes „LIM-0011“ kann nicht eindeutig erklären, welche Ausnahme gilt.
- **Korrektur:** Ownerentscheid zur historisch nachvollziehbaren neuen eindeutigen Nummer mit Redirect-/Supersession-Verweis; alle Referenzen qualifizieren. Eine README-Notiz macht die Kollision nicht regelkonform.

#### G-04 — ADR-Form wird als Sammelrefactor- und Implementierungsprotokoll benutzt

- **Kategorie:** eine Entscheidung, <500 Zeilen, kein echter Code, Präsens/Aktiv, belastbare Begründung.
- **Beleg:** `docs/adr/0029-kubernetes-sidecar-deployment-topology.md:189-258,466-519`; `docs/adr/0026-tool-surface-consistency.md:58-60,146-151,213-231`; `docs/adr/0027-error-envelope-and-tool-surface-versioning.md:51-53`; `docs/adr/0011-pluggable-secret-store-backend.md:39-44`; `docs/adr/0024-duration-grammar-single-source.md:64-68,91-95`.
- **Beweis:** 0029 hat 519 Zeilen und bündelt Topologie, Health-API, Recovery und Auslieferung. 0026 bündelt Tool-Split, neues Tool, Schemas, Python-TypedDict, Scope und Description-Regel; 0027 Envelope und Versionierungsoberfläche. Eine konkrete Python-Protocol-Klasse und module-/helperbezogene Umbauanweisungen gehören nicht in einen unveränderlichen Entscheidungsrecord. Etwa „becomes/is converted“ ersetzt keine eindeutige geltende Gegenwartsregel. Security-Abschnitte behaupten „keine Exposition“ trotz Counts/verschiebbarer Policy-Attribute; 0028 verwirft Caller-Isolation als Bequemlichkeitskosten (286–290).
- **Korrektur:** getrennte Entscheidungen für getrennt supersedierbare Normen, Mechanik in verlinkte Spezifikation; Präsens mit eindeutigem Subjekt. JSON-/Policy-Beispiele als externe Verträge dürfen bleiben. Ob Alternatives die echte damalige Diskussion spiegeln, ist ohne Diskussionsartefakte nicht beweisbar; es wird kein erfundener Gesprächsverlauf unterstellt.

#### G-10 — Varianz wird als Behauptung, nicht als adversarielles Testdesign durchgesetzt

- **Kategorie:** Guidelines §2.3, ADR 0017 §2.2.
- **Beleg:** `docs/adr/0017-response-transparency-for-filtered-data.md:92-98`; `bdd/features/tool_surface/reason_code_contract.feature:182-198`; `bdd/features/policy/sender_rule_matcher.feature:87-97`; `bdd/features/providers/localized_gmail_resolution.feature:189-205`; `bdd/features/tool_surface/create_reply_draft.feature:361-380`.
- **Beweis:** Eine Then-Aussage „has variance discipline“ liefert selbst keine Szenariozuordnung. `policy_alias_conflict` hat nur einen konkret erreichten Fall; `saga_not_configured` keinen konkreten Reachability-Fall. Die NFC-Probe variiert nicht kanonisch äquivalente Unicode-Darstellungen. Visibilities werden nicht über alle sieben Levels/heterogene Nachrichten variiert; BODY-Reply-Gate nur an global METADATA-Folder. Forbidden-Flags testen überwiegend `\Deleted`, nicht `\Recent`/andere Systemflags oder `replace`-Erhalt.
- **Korrektur:** explizite Condition-/Code-Matrix mit wenigstens zwei materiell verschiedenen Inputs pro tatsächlich emittiertem Code; positive und negative Grenzfälle, Permutationen, leere Mengen, identische Namen mit verschiedenen Identitäten und niedrige Sicht in hohem Folder.

#### G-13 — Usability-Rubriken können fachlich falsche Erfolge und irreführende Effizienzwerte erzeugen

- **Kategorie:** Guidelines §§1/2/12; Messvertrag ADR 0039.
- **Beleg:** `usability/features/01_draft_format_decision.feature:40-44,57-60,76-78`; `02_draft_revision_and_discard.feature:41-45,68-86`; `03_invoice_search.feature:53,71,83-95`; `07_run_metrics.feature:21-39`; `usability/README.md:264-297`.
- **Beweis:** Ein beliebiger einzelner HTML-Draft kann E2 erfüllen, ohne geforderten Empfänger/Inhalt zu treffen. R1 prüft nicht die verlangte Absatzkürzung. S3 erwartet „bezahlt“ = Summe von Rechnungen, obwohl kein Zahlungseingang/-ausgang in den Givens steht; 451,00 ist als **Rechnungssumme** korrekt addiert, als Zahlungssumme nicht ableitbar. SEARCH-basierte Idealpfade enthalten nicht notwendigerweise Subjects, Attachment-Part-IDs oder die Invoice-Klassifikation. R3 besitzt keinen Nenner für die verpflichtende ideal_ratio. „response_token_share“ dividiert Bytes durch Tokens ohne definierte Umrechnung. Rejections ohne Audit können die Toolzählung unterschätzen. README entscheidet nachträglich Judged-/Question-Heuristiken, die die Features nicht vollständig tragen.
- **Korrektur:** Rubriken unabhängig auf Aufgabe, genaue Endobjekte, Inhaltsanforderungen und Schadensfreiheit prüfen; Bezahlung entweder als Datenquelle bereitstellen oder Frage korrekt auf Rechnungsbetrag begrenzen. Idealpfade aus realen autorisierten Responses herleiten; fehlende Nenner/Nullfälle und Einheiten definieren. Keine dieser Rubriken ist eine serverseitige Sicherheitskontrolle.

### NIEDRIG

#### G-14 — Veraltete Verweise und Statuszahlen verhindern reproduzierbare Navigation

- **Kategorie:** Dokumentationskonsistenz.
- **Beleg:** `docs/adr/0001-default-deny-hierarchical-policy.md:67-69`; `docs/adr/0023-test-only-control-surface.md:230-232`; `docs/limitations/0004-imap-fault-injection-via-env-var.md:10-12`; `README.md:13,506,521`; `docs/ROADMAP.md:12-13,58-59,79,93-99`; `docs/spec-audit/README.md:99`.
- **Beweis:** Mehrere ADR-/LIM-Links zeigen auf nicht vorhandene ältere Dateinamen. README nennt 33 ADRs und 241/0 Szenarien; Bestand enthält 40 ADRs und 34 pending, ROADMAP nennt sowohl 419/34 als auch erwartete 525/34. Als erledigt indexierte Mock-/LIM-Arbeit steht später noch als offen/leeres Verzeichnis. Der relative Guidelines-Link im tieferen Spec-Audit-Verzeichnis löst nicht auf den vorgegebenen Referenzpfad auf.
- **Korrektur:** Pfade, Status und versionierte Inventurzahlen nachvollziehbar synchronisieren; behauptete Laufzahlen mit Datum/Commit/Artefakt verbinden. Dieser Audit bestätigt ausdrücklich keine der angegebenen Passed-Zahlen.

## 4. Befunde Berechtigungskonzept & API-Sicherheit

### KRITISCH

#### S-01 — Suchtransparenz ist ein adaptives Prädikatorakel über verborgene Nachrichten

- **Kategorie/Beweisart:** Vertraulichkeit, Non-Interference; durch ADR und Gherkin ausdrücklich vorgeschrieben.
- **Beleg:** `docs/adr/0002-linear-visibility-levels.md:33-41`; `docs/adr/0017-response-transparency-for-filtered-data.md:39-50,165-168,191-195`; `docs/adr/0026-tool-surface-consistency.md:118-140,175-193`; `docs/adr/0036-search-window-transparency-and-calendar-dates.md:26-30`; `bdd/features/policy/default_deny.feature:64-71`; `bdd/features/tool_surface/list_messages.feature:82-97`; `bdd/features/tool_surface/bulk_mark_seen.feature:25-28`; `bdd/features/tool_surface/bulk_mark_tagged.feature:28-32`.
- **Angriffspfad:** Ein Caller darf den Folder grundsätzlich durchsuchen, hat aber für die gesuchte Nachricht `NONE`. Er wählt `criteria` und erhält `matched_total=1, matched_visible=0, filtered_out=1`. Variieren des Prädikats verändert die Antwort, obwohl keine sichtbare Nachricht verändert wurde. `scope=all` entfernt die Zeitbegrenzung; `outside_scope_total` eröffnet denselben Kanal zusätzlich für das vom Server gewählte Defaultfenster. Bulk-Tools besitzen zumindest queryabhängige `marked_count`/`tagged_count`; ob sie weitere Suchzähler zurückgeben und ob N-Treffer mitgeschrieben werden, ist nicht vollständig entschieden. Jede ungefilterte betroffene Anzahl ist dasselbe Orakel mit zusätzlichem Seiteneffekt.
- **Exakt ableitbare Information bei einer isolierbaren verborgenen Nachricht:**
  - `from`/`from_domain`: Wahrheit der gewählten Absender-/Domain-Gleichheit.
  - `to`: Empfänger-Mitgliedschaft; nach ADR 0004 auch Cc. `to_contains`: Wahrheit jeder gewählten Teilzeichenfolge.
  - `subject_contains`: Wahrheit jedes gewählten Subject-Substrings, obwohl M nicht einmal den Betreff sehen darf.
  - `has_attachment` und `flagged`: die jeweiligen Bits.
  - `size_gt`/`size_lt`: durch binäre Eingrenzung die Bytegröße in einem bekannten endlichen Bereich.
  - `newer_than`/`older_than`: Schwellenwerte des spezifizierten Header-Datums bis zur spezifizierten Zeitauflösung; `since`/`before`: Kalendergrenzen des INTERNALDATE-Vertrags.
- **Grenze der Rekonstruktion:** Ohne isolierende Query oder Vorwissen verrät eine Zahl zunächst die Anzahl der passenden verborgenen Objekte, nicht automatisch, welche Merkmale derselben Nachricht gehören. Exakte Gleichheits-/Substring-Antworten sind dennoch bereits Datenabfluss. Eine vollständige beliebige Adresse oder Betreffzeichenfolge folgt nicht voraussetzungslos aus einem einzigen Count; Kandidatenräume, Differenzabfragen und stabile Zustände bestimmen die weitere Rekonstruierbarkeit. Body-Inhalt ohne entsprechendes Prädikat folgt daraus nicht.
- **Auditmechanismus:** Der Toolaufruf selbst ist autorisiert; die untergeordneten verborgenen Matches erzeugen keinen notwendig sichtbaren DENY-Trail. ADR 0017 sagt ausdrücklich, Hidden-Counts würden nicht geloggt. Ein `ALLOW / OK` plus Query-Digest macht den Datenabfluss weder sicher noch für einen DENY-basierten Alarm sichtbar. Der Userbericht ist damit textlich erklärbar, ohne eine ausgelieferte Implementierung zu unterstellen.
- **Korrektur:** Zählen, paginieren und Prädikate nur auf einer autorisierten Sicht auswerten. Keine `matched_total`/`filtered_out`/Outside-Zahl über N oder über unzulässige Prädikatfelder. Stattdessen konstante Scope-Erklärung „vollständig innerhalb Ihrer autorisierten Sicht“ und explizites Zeitfenster; sichere sichtbare Counts separat. Operator-Diagnostik darf einen anderen, privilegierten Vertrag besitzen. Ein Query-Limit allein repariert das Orakel nicht.

#### S-02 — Copy/Move und Labelmutation sind implizite Declassification-Tools

- **Kategorie/Beweisart:** Privilege Escalation/Exfiltration; Vertragskomposition, teilweise durch N-Erfolgsszenarien festgeschrieben.
- **Beleg:** `docs/adr/0005-per-folder-write-capabilities.md:34-53,82-85,106-109`; `docs/adr/0016-mcp-tool-set.md:58-60`; `docs/adr/0019-gmail-label-semantics.md:67-76,144-153`; `bdd/features/transactions/cross_account_move_saga.feature:27-30,34-55,125-134`; `bdd/features/transactions/intra_account_move.feature:27-43`; `bdd/features/providers/gmail_label_semantics.feature:39-45,137-146`; `bdd/features/providers/localized_gmail_resolution.feature:151-167`.
- **Angriffspfad:** `V(c,src,m)=NONE` oder BODY, Source besitzt `move_out` (bei `copy` nicht einmal das), Ziel besitzt `accept_incoming` und gewährt FULL. `move`/`copy` transferiert das vollständige RFC822-Objekt. Anschließendes Lesen im Ziel liefert Body und Attachments, die an der Quelle nicht lesbar waren. Der Caller benötigt nur einen bekannten/geratenen Source-UID, nicht den Inhalt. Die N→N-Saga-Happy-Paths zeigen, dass der Source-Read-Floor kein Gate ist; der Wechsel des Zielgrants zu FULL ist nirgends verboten.
- **Archiv-Gegenbeweis:** ADR 0005 verspricht für `NONE + accept_incoming` ohne `move_out`, dass nichts wieder lesbar oder entfernbar sei. `copy` benötigt aber kein `move_out`: mit einem offenen zweiten Ziel kann dieselbe Nachricht herauskopiert werden. Deposit-only ist damit kein belastbarer Informationsflussvertrag.
- **Gmail-Variante:** Die Saga liest ausdrücklich aus `[Gmail]/All Mail`, obwohl die Feature-Policy diesem Folder gar keinen Grant gibt. ADR 0019 erklärt zugleich „There is no implicit access“ und dass `move_out` auf irgendeinem Label faktisch All-Mail-Vollzugriff zum Kopieren impliziere. `mark_tagged` darf außerdem Labels hinzufügen/entfernen, ohne Ziel-`accept_incoming` beziehungsweise Entfernungsgates für diese Labels festzulegen. Ein offenes Label kann dadurch direkt als Lesesicht geschaffen werden.
- **Korrektur:** Bestehenden Inhalt transferieren heißt exportieren: Source-Objektberechtigung auf **alle transferierten Teile**, explizites `export/copy_out` bzw. `relocate`, Ziel-Importrecht und Informationsflussprüfung. Niedrigere Sicht darf nur eine ausdrücklich definierte, neu erzeugte redigierte Ableitung exportieren. Transportwahl All Mail darf Rechte nicht erweitern; ein ausschließlich intern verwendeter Zugriff muss an die autorisierte Objektidentität gebunden sein und darf keine versteckte Folder-Mitgliedschaft offenlegen.

#### S-03 — Caller können die Daten ändern, aus denen ihre eigenen Grants berechnet werden

- **Kategorie/Beweisart:** Selbst-Eskalation durch veränderliche Policyattribute; normative Komposition.
- **Beleg:** `docs/adr/0004-sender-rule-matcher-grammar.md:31-43,94-101`; `docs/adr/0005-per-folder-write-capabilities.md:39-40,49-53`; `docs/adr/0017-response-transparency-for-filtered-data.md:86`; `README.md:268,314-324`; `bdd/features/policy/flagged_predicate.feature:22-34,80-93`; `docs/adr/0031-draft-revision-via-rewrite-saga.md:33-41,58-64,110-111`; `bdd/features/policy/modify_message_capability.feature:3-5`.
- **Angriffspfad A:** Whitelist-Nachricht erfüllt zunächst keine Regel. Der Folder erlaubt `mark_tagged`; Regel `flagged=true → FULL` besteht. Der Caller setzt `\Flagged`, danach erfüllt dieselbe Nachricht den Grant. ADR 0005 **erlaubt** `\Flagged` ausdrücklich, ADR 0017 verbietet in seinem Beispiel nur `\Deleted`/`\Recent`. Damit ist „Stern = vom Menschen kuratiert“ nicht mehr wahr. Bei einer Blacklist-Regel `flagged=false → NONE` beseitigt das Sternsetzen ebenso den Cap.
- **Angriffspfad B:** `update_draft` ändert unter `draft_edit` auch bei N/M einen erlaubten `subject` oder `to`, lässt Body/Attachments unangetastet und aktiviert damit eine großzügigere Subject-/Recipient-Regel. Ein zuvor nur als Handle bekannter Draft wird anschließend FULL-lesbar. `add_attachment`/`delete_attachment` verändern `has_attachment`; alle Rewrites können Größen-/Datumsregeln verändern. Positive oder negative Regeln auf diese Merkmale sind manipulierbar, wenn derselbe Caller sie schreiben darf.
- **Gmail-Verstärkung:** Ein Label ist keine eigene Kopie des Objekts (`docs/adr/0019-gmail-label-semantics.md:12-19,71-73`). Ein über eine erlaubte Ansicht gesetzter Stern kann deshalb eine `flagged`-Regel einer anderen Ansicht aktivieren; das Fehlen von `mark_tagged` gerade auf Sent Mail begrenzt eine andernorts erlaubte objektweite Flagmutation nicht.
- **Beweisgrenze:** `_FORBIDDEN_SYSTEM_FLAGS` wurde nicht gelesen; eine Aussage über dessen Codeinhalt wäre unzulässig. Der **veröffentlichte** Vertrag reicht bereits für den Eskalationspfad. Die gezeigten Tests prüfen nur vorher gesetzte Sterne, nicht das Zusammenspiel aus Caller-Mutation und höherem Grant.
- **Korrektur:** Autorisierungsmerkmale nach Herkunft und Schreibbarkeit trennen. Menschliche Freigabe als server-/operatorgeschütztes Approval-Artefakt, nicht als gewöhnliches IMAP-Flag. Policyvalidierung muss Kombinationen „Caller darf Grant-/Cap-Prädikat umschalten“ ablehnen oder die Mutation als privilegierte Freigabe behandeln. `replace` darf andere Systemflags nicht stillschweigend entfernen. Rewrites müssen gegen alte **und** neue Klassifikation geprüft werden.

#### S-04 — get_transaction_status ist ein unautorisiertes, schreibendes Fremdtransaktionswerkzeug

- **Kategorie/Beweisart:** Cross-caller-Zugriff/Broken Object Authorization; ausdrücklich dokumentierter Defekt.
- **Beleg:** `README.md:73-80`; `docs/ROADMAP.md:128,140-146`; `docs/error_path_analysis.md:361-369`; `docs/adr/0031-draft-revision-via-rewrite-saga.md:138`; `bdd/features/tool_surface/mcp_tool_discovery.feature:115-122`.
- **Angriffspfad:** Caller B erhält eine `tx_id` von A. Der Statushandler prüft laut Dokumentation nicht gegen den im WAL gespeicherten `caller_id`. B liest A's Transaktion; Polling kann deren Recovery und damit APPEND/DELETE unter A's Credentials auslösen. Besitz der ID ersetzt Autorisierung. Das trifft Move-/Copy- und neue Draft-Rewrite-Transaktionen.
- **Warum die Kompensation nicht reicht:** „Ein Caller je Instanz“ steht in Proposed ADR 0029 und einer README-Warnung, nicht in einer Startvalidierung. Der HTTP-Transport akzeptiert laut ROADMAP mehrere Caller und ein gemeinsames WAL. Keine Zwei-Caller-Gegenprobe existiert. Auch hohe Entropie der ID wäre nur ein erschwerter Fund, keine Rechteprüfung.
- **Korrektur:** Ownership und Tenant/Account-Scope vor jeder Rückgabe; unbekannte/fremde IDs gleich behandeln. Statusabfrage muss rein lesend werden. Recovery durch separaten, autorisierten Worker aus dauerhaftem Intent; eigene Resume-/Cancel-Rechte nur falls wirklich benötigt. Bis dahin Mehr-Caller-Konfiguration technisch ablehnen und State/Sink pro Caller isolieren.

#### S-05 — Der gemeinsame Attachment-Sink entfernt die Caller-Grenze nach erfolgreicher Autorisierung

- **Kategorie/Beweisart:** Cross-caller-Exfiltration; ausdrücklich gewählte Architektur.
- **Beleg:** `docs/adr/0028-attachment-file-sink-delivery.md:61-79,247-258,286-290`; `docs/limitations/0013-single-attachment-sink-path.md:35-45,94-104`; `docs/limitations/0014-attachment-sink-requires-caller-filesystem-access.md:21-29`.
- **Angriffspfad:** A darf vertrauliche Finanzanhänge mit FULL abrufen und schreibt sie in den gemeinsamen Sink. B hat zwar für diese Mail NONE, benötigt aber Filesystemzugriff auf denselben Sink für eigene erlaubte Downloads. B listet/liest A's Datei direkt, ohne `fetch_attachment`, PDP oder einen neuen Audit-Eintrag. Der öffentliche Sinkpfad erleichtert die Auffindbarkeit, ist aber nicht die eigentliche Autorisierungslücke.
- **Beweis:** Der ADR sagt selbst, dass Filesystemrechte **die** Zugriffskontrolle für gespeicherte Dateien sind und jeder Prozess mit Read-Recht alles sieht. Das widerspricht einer callergebundenen Schutzgarantie außerhalb der MCP-Antwort. Die vier Sink-LIMs benennen es, ändern den Informationsfluss aber nicht.
- **Korrektur:** Caller-/Tenant-isolierte Artefakte mit autorisiertem Download bzw. wirklich getrennten OS-/Containerrechten. Ein bloßer Unterordnername bei identischer lesender OS-Identität reicht nicht. Keine globale tool-description-Entdeckung eines gemeinsamen sensitiven Scratchspaces. Single-caller nur zusammen mit tatsächlicher Filesystemtrennung als Kompensation zulassen.

#### S-06 — Die empfohlene Same-user-Topologie besitzt keine durchsetzbare Policy-/Credential-Grenze

- **Kategorie/Beweisart:** vollständige externe Umgehung; dokumentierter Vorfall, topologische Grenze.
- **Beleg:** `docs/limitations/0019-policy-integrity-requires-out-of-process-trust-boundary.md:48-63,72-91,109-147,176-192`; `docs/adr/0014-policy-as-git-versioned-yaml.md:141-149`; `README.md:39-64`; `usability/README.md:122-130,247-260`.
- **Angriffspfad:** Ein mit Shell/File-Tools ausgestatteter Agent läuft als derselbe OS-Principal wie Server und Konfiguration. Er schreibt `default: FULL` in die geladene Arbeitsdatei, startet neu oder löst Reload aus. Git-Review wird nicht konsultiert, `git blame` sieht einen nicht committeten Eingriff nicht. Laut LIM wurde genau das am 2026-06-22 getan. Derselbe Principal kann grundsätzlich auch die zugänglichen Credentials, den Prozess oder dessen Umgebung angreifen; ein in-process Signaturcheck schließt diese Grenze nicht.
- **Einordnung:** Das ist kein mit einem zusätzlichen PDP-if reparierbarer Bug. Ein reiner MCP-Caller ohne solche OS-Rechte hat diesen **externen** Pfad nicht; für ihn bleiben die in-band-Befunde S-01 bis S-05 dennoch bestehen. Die Usability-README beschreibt ausdrücklich keinen Container-Sandboxbetrieb; Auto-approval plus unterschiedliche Harness-Guards ist keine allgemeine Sicherheitsgrenze.
- **Korrektur:** Schutzversprechen an zwingende Deployment-Voraussetzungen binden: Agent darf weder Servercode, Config, Secrets, WAL, Audit noch dessen Prozess beeinflussen; getrennte Principals/Container und read-only operatorgesteuerte Mounts. Kein Kubernetes-Token mit workload-/Secret-Mutationsrecht im Agent. Same-user-stdio nur als Komfortbetrieb **ohne adversarielle Isolationsgarantie** kennzeichnen.

#### S-07 — Message-ID-/5-Tupel-Dedup kann das falsche Ziel bestätigen und die Quelle löschen

- **Kategorie/Beweisart:** Datenverlust/Integrität; interner ADR-Widerspruch plus unzureichender Witness.
- **Beleg:** `docs/adr/0008-idempotency-via-message-id-and-hash.md:41-73,115-122`; `docs/adr/0006-cross-account-move-via-saga.md:55-59,107-109`; `bdd/features/transactions/saga_crash_recovery.feature:82-94,110-119`; `docs/limitations/0006-5-tuple-fallback-deferred.md:7-14`.
- **Angriffspfad:** Eine andere Nachricht liegt am Ziel mit derselben angreifbaren Message-ID. ADR 0008 §Decision erklärt bei genau einem SEARCH-Treffer ohne weitere Prüfung „message is present“ und lässt nachfolgend DELETE zu. Derselbe ADR verlangt im Security-Abschnitt dagegen **alle** Fallback-Komponenten vor „already there“. Der spezifizierte Einzel-ID-Test stellt weder Gleichheit der Bytes noch des Fallback-Tupels her.
- **Auch die strengere Lesart genügt nicht:** Zwei unterschiedliche Messages können dieselben From-/Datum-/Subjectprefix-/Size-Werte und dieselben ersten 4 KiB haben, sich aber hinter Byte 4096 unterscheiden. Der vollständige SHA-256 ist ausdrücklich nur forensisch, nicht Entscheidungsgrundlage. Ein passendes Präfix ist kein Beweis der ganzen Zielnachricht; Löschen der Quelle verliert den abweichenden Rest.
- **Korrektur:** Vor Source-Delete eine eindeutig dem Intent zugeordnete Zielinstanz und vollständige Integrität bestätigen. Providerbedingte Headeränderungen benötigen eine eng definierte sichere Kanonisierung; andernfalls konservativ `needs_operator/uncertain`, nicht „already present“. Mindestens Einzel-ID mit anderem Body, gleicher Größe/gleichem Präfix mit anderem Suffix, Mehrfach-ID, verlorene APPENDUID und Restart müssen gegen tatsächliche Source-/Target-Bytes geprüft werden.

### HOCH

#### S-08 — Antwortprojektionen unterlaufen die sieben Sichtstufen auch ohne Count-Query

- **Kategorie/Beweisart:** Objekt-/Feldinformation; teils expliziter Widerspruch, teils fehlende Projektion.
- **Beleg:** `docs/adr/0002-linear-visibility-levels.md:35-48`; `docs/adr/0031-draft-revision-via-rewrite-saga.md:45-52`; `bdd/features/tool_surface/mcp_tool_discovery.feature:79-95`; `bdd/features/policy/whitelist_folder_mode.feature:42-52`; `bdd/features/policy/flagged_predicate.feature:80-93`; `docs/adr/0017-response-transparency-for-filtered-data.md:100-104,158-168,205-218`; `bdd/features/providers/gmail_label_semantics.feature:39-63`; `bdd/features/providers/gmail_search_and_counts.feature:61-63,131-149`.
- **Mechanismen:**
  1. Hidden-Account-/Folder-/Message-Counts verändern sich bei ausschließlich verborgenen Objekten und widersprechen N. `describe_policy` zeigt eigene Policyabsichten zulässigerweise, aber nicht daraus folgend beliebige tatsächliche Hidden-Objektzahlen.
  2. `list_drafts` ist M-gegated, sein Vertrag enthält `subject`; M verbietet Betreff/Absender. `list_messages` ist ebenfalls M-gegated, seine Envelope-Projektion auf M ist nicht festgelegt. Die positiven Tests mit E/F ersetzen keine M-Negativprobe.
  3. `has_attachment` wird in `list_messages` bereits auf E gefordert, obwohl ADR 0002/0026 Attachment-Struktur erst B zuordnen. Das kann beschlossen werden, darf aber nicht gleichzeitig als andere Stufengrenze gelten.
  4. `matched_rule_index` wird ausdrücklich erwartet, obwohl ADR 0017 **jeden** Rule-Identifier in Responses verbietet. Ein Listenindex ist ebenso ein identifizierender Policy-Sidechannel.
  5. `canonical_all_mail_uid=10001` wird verlangt, obwohl der Policy-Background All Mail nicht freigibt. Er offenbart eine konkrete versteckte Mitgliedschaft/Adresse, nicht nur Identität der in INBOX erlaubten Mail. `gm_msgid` wäre nur als bewusst freigegebene, nicht folderverratende Identität zu beurteilen, nicht automatisch wie diese UID.
- **Korrektur:** Vollständige Feld-Allowlist je Level, Tool und Objektklasse; nie am höchsten möglichen Folder-Level statt am konkreten Message-Level orientieren. Deduplizierbare, caller-scoped Objekt-IDs statt einer Adresse in einem verborgenen Folder. Redaktionsflags allein aus Vertragsprojektion, keine Rule-Indices, keine Hidden-Peer-Zahlen. N-Ordner mit erlaubter Deposit-Funktion als eigenständige Sink-Adresse modellieren, nicht gleichzeitig als lesbare Count-Ansicht.

#### S-09 — Fehler, Revisionen und Mutationsergebnisse bilden Existenz- und Zustandsorakel

- **Kategorie/Beweisart:** Non-Interference über kontrollflussabhängige Antworten.
- **Beleg:** `docs/adr/0017-response-transparency-for-filtered-data.md:75-86`; `bdd/features/transactions/intra_account_move.feature:27-43,62-68`; `bdd/features/tool_surface/create_reply_draft.feature:187-202,326-335`; `bdd/features/tool_surface/replace_attachment.feature:31-38`; `bdd/features/tool_surface/delete_attachment.feature:34-41`; `docs/adr/0031-draft-revision-via-rewrite-saga.md:68-72,95-102`; `docs/adr/0034-discard-draft-by-handle.md:40-44`.
- **Angriffspfad:** Für bekannte/geratene UIDs oder Handles verraten unterschiedliche Antworten „nicht vorhanden“, „vorhanden, Sender gesperrt“, „Attachmentname fehlt“, „Revision aktuell N“ oder eine positive Anzahl interne Zustände. N-Move-Erfolg versus `uid_not_found` ist direkt spezifiziert. Blind-Rewrite/Discard kann außerdem bei N einen vorhandenen Handle und Anzahl/Revisionen bestätigen. Ein erfolgreicher Write-only-Create-Receipt ist dagegen zunächst Information über die eigene gerade ausgeführte Anlage; das ist nicht dasselbe wie allgemeine Leseberechtigung auf alle Objekte des Folders.
- **Abgrenzung:** `folder_hidden` unabhängig von tatsächlicher Existenz ist als Ansatz ausdrücklich spezifiziert (`docs/adr/0025-folder-path-contract-and-error-taxonomy.md:119-122`). Es fehlt die entsprechende vollständige Message-/Part-/Draft-Regel. Nicht jeder `attachment_not_found` bei einem FULL-berechtigten Caller ist ein Leak; problematisch ist die Unterscheidung **vor ausreichender Objekt-/Part-Autorisierung**.
- **Korrektur:** Unbekannt und unautorisiert von außen einheitlich behandeln; Validierungsfehler nur mailboxunabhängig. Existenz/Revision/Part-Fehler erst nach passender Objektberechtigung. Blindoperationen, wenn wirklich benötigt, ausschließlich als eng begrenzte eigene Sealed-Handles ohne beliebige UID-/Dateinamen-Probes spezifizieren.

#### S-10 — Gmail-Mutationen sind weder nur ordnerlokal noch als STORE-Paar atomar

- **Kategorie/Beweisart:** unautorisierte globale Seiteneffekte/Teiltransaktionen.
- **Beleg:** `docs/adr/0019-gmail-label-semantics.md:63-66,133-137`; `bdd/features/providers/gmail_draft_lifecycle.feature:21-27,180-203`; `bdd/features/providers/gmail_label_semantics.feature:76-106`; `docs/adr/0034-discard-draft-by-handle.md:27-46,73-76`.
- **Angriffspfad:** Ein Caller darf aus INBOX entfernen und nach Trash aufnehmen lassen. Gmail-`+\Trash` entfernt laut Spezifikation die Nachricht zugleich aus anderen Mailboxes; dadurch kann ein geschütztes weiteres Label/Sent-/All-Mail-Vorkommen betroffen sein, für das kein `move_out` geprüft ist. Ein normales User-Label-Move lässt die weiteren Labels dagegen bestehen. Diese zwei Zustandsdeltas besitzen nicht dieselbe Autorisierung.
- **Atomizitätsbruch:** Zwei STOREs sind ein geordnetes Paar, keine spezifizierte Transaktion. Der Test injiziert Fehler beim **ersten** Add; der Fehler zwischen erfolgreichem Add und Remove bleibt offen. Beim Discard über mehrere Revisionen verspricht §Decision „Nothing is moved on any error“, §Security räumt ausdrücklich Teilverschiebungen ein. Beide Aussagen können nicht gelten.
- **Korrektur:** Provideradapter muss einen vollständigen Mutationsplan liefern: alle neu sichtbaren und entfernten Mitgliedschaften, Flagänderungen, Auto-Expunge-Folgen. Policy prüft das gesamte Delta oder eine ausdrücklich objektweite Trash-/Archive-Aktion. Batch-Outcome `partial/uncertain` mit autorisierten Receipts statt falscher Alles-oder-nichts-Garantie. Fehler nach jedem einzelnen Seiteneffekt und Nebenlabel-Unverändertheit testen.

#### S-11 — Reply hat eine BODY-Grenze, aber keine lückenlose Absicherung ihrer entscheidenden Stelle

- **Kategorie/Beweisart:** potenzieller serverseitiger Exfiltrationspfad; **Spezifikations-/Testlücke**, kein nachgewiesener pauschaler Blind-Reply.
- **Beleg:** `bdd/features/tool_surface/create_reply_draft.feature:3-29,51-56,357-380`; `docs/adr/0035-draft-body-format-choice.md:43-47`; `README.md:361`; `bdd/features/tool_surface/mcp_tool_discovery.feature:108`.
- **Mechanismus:** Der Server liest das Original und schreibt ein Zitat in einen anderen Folder. Der Negativtest verwendet einen **insgesamt METADATA**-Folder ohne Rules. Er entdeckt keinen Handler, der lediglich den maximalen Folder-Level statt der effektiven Sicht der ausgewählten Mail prüft. In einem FULL-Folder mit einer Cap-Regel auf HEADERS/NONE könnte so Body in einen FULL-Drafts-Folder gelangen und anschließend gelesen werden. Dieses konkrete Gegenbeispiel ist nirgends abgesichert.
- **Weitere Grenze:** Bei Source BODY ist das Zitieren der wirklich freigegebenen Body-Projektion an denselben Caller kein zusätzlicher Body-Leak. Wenn der Quotierer aber `text/plain`-Attachments, eingebettete Nachrichten oder sonstige MIME-Teile aus dem Voll-Fetch übernimmt, würden F-geschützte Inhalte hochgestuft. Auch das ist nicht getestet. Ein Ziel mit weiteren Lesern ist zusätzlich ein Informationsfluss-/Audience-Thema.
- **Korrektur:** Read-Object-Gate ≥ BODY für genau die gewählte Message vor Zitaterstellung, anschließend Quote ausschließlich aus der autorisierten Projektion; kein Voll-RFC822-Reuse. Mixed-Whitelist/Blacklist-Tests auf N/C/M/E/H, MIME-Part-Negativzeugen sowie direkte Kontrolle, dass weder Draft noch Zitat existiert. Public Tool-Metadata muss beide Gates statt nur `draft_append` beschreiben.

#### S-12 — Draft-Header beweisen weder Besitzer noch Draft-Lifecycle; Rewrite-Befugnisse sind zu breit

- **Kategorie/Beweisart:** Integrität/Ownership/Blind Writes; belegte Modelllücke.
- **Beleg:** `docs/adr/0030-draft-identity-and-revisions.md:23-42,65-75`; `docs/adr/0031-draft-revision-via-rewrite-saga.md:58-64,78-87,133-136`; `bdd/features/tool_surface/draft_identity.feature:54-81`; `bdd/features/tool_surface/discard_draft.feature:23-27,184-201`; `docs/limitations/0022-externally-edited-drafts-lose-their-handle.md:37-55,76-98,102-111`.
- **Angriffspfad:** Die API behandelt „trägt X-SC-Draft-Id/Rev“ als editierbar, nicht „wurde von diesem Caller angelegt, ist weiterhin Draft und hat diesen authentischen Stand“. Ein anderer berechtigter Caller mit demselben Folder-Capability kann über bekannten Handle fremde Revisionen ändern oder verwerfen. Ein kopiertes/importiertes Mailobjekt kann Header tragen, die kein vertrauenswürdiger Provenienznachweis sind. Höhere eingeschleuste Revisionen werden als autoritativ gewählt; Discard sammelt alle Handle-Träger im benannten Folder, unabhängig von deren Ursprung.
- **Wichtige Korrektur der Aufgabenannahme:** Der dokumentierte Header heißt **`X-SC-Draft-Id`**, nicht `X-Imap-Mcp-Draft-Id`. Einen einzelnen eingebrachten X-SC-Handle überschreibt `create_draft` nach dem konkreten Test bereits. Deshalb wird **kein** einfacher „create_draft lässt beliebige ID durch“-Exploit behauptet. Nicht abgesichert sind Fremdimport/Kopie, doppelte/gefaltete Varianten, authentische Ownership und die anderen Rewrite-Pfade.
- **Attachments:** `modify_message` darf vollständige vorhandene Messages ohne Lesefloor umschreiben. Eindeutigkeit bei zwei gleichen Dateinamen, gezielte Part-ID, Erhalt unbekannter Header/Flags/INTERNALDATE/Labels und Wechselwirkung mit Draft-Revision/Change-Hash sind nicht geschlossen. Draft-Edit benötigt laut ADR „eigene“ Drafts, die Auflösung enthält jedoch keinen Caller-Beweis.
- **LIM-0022:** Das grundsätzliche Stale-Counter-Problem bei Fremdedits ist real. Das konkrete Beispiel „Mensch ändert Absatz, Agent ändert nur Subject, Absatz verschwindet“ folgt aber **nicht** aus ADR 0031: unberührter Body soll übernommen werden. Ebenso macht Write-only-Visibility einen Folder nicht zu einem Folder, den kein Mensch editiert. Die als unvermeidbar/permanent begründete Grenze ist so zu weit und falsch mitigiert.
- **Korrektur:** Serverseitige, nicht aus frei kopierbaren Mailheadern abgeleitete Ownership/Provenienz; unverwechselbare Objekt-/Part-IDs; `state=draft` als überprüfte Voraussetzung. Explizite Rechte auf eigenen/fremden Draft, Revision, Attachments und Trash. Mehrdeutige/importierte/gesendete Objekte nicht stillschweigend als eigene editierbare Drafts behandeln. Header-Injection-Tests müssen auch strukturierte Create-/Reply- und Attachment-Parameter abdecken; der Update-CRLF-Test allein genügt nicht.

#### S-13 — Revision-Zahl und UID ohne Epochen-/Atomizitätsvertrag verhindern weder Race noch falsches Objekt

- **Kategorie/Beweisart:** Concurrency/TOCTOU/Adressintegrität; logischer Gegenlauf.
- **Beleg:** `docs/adr/0031-draft-revision-via-rewrite-saga.md:68-87,114,146-148`; `docs/adr/0030-draft-identity-and-revisions.md:36-40,49`; `bdd/features/tool_surface/update_draft_errors.feature:229-247`; `bdd/features/transactions/intra_account_move.feature:90-98`; `docs/adr/0026-tool-surface-consistency.md:89-111`; `bdd/features/deployment/projected_config_reload.feature:18-42`.
- **Race:** Zwei Prozesse lösen denselben Handle gleichzeitig auf Revision r auf; beide prüfen `base_revision=r` erfolgreich, beide APPENDen unterschiedliche r+1, beide löschen r. Ein einfacher Vergleich vor APPEND ist kein CAS. Das Ergebnis sind zwei angeblich erfolgreiche gleiche Revisionen oder spätere Ambiguität. Die Tests nennen „first/second caller“, führen aber **seriell denselben Caller** aus. ADR 0030 nennt ausdrücklich zweite MCP-Instanzen als unterstützten Fall.
- **UID-Epoche:** Die öffentliche Source enthält Account/Folder/UID, nicht UIDVALIDITY oder einen gebundenen Objektbezug. Nach einer Folder-Neuanlage zwischen früherem SEARCH und späterem Write kann dieselbe Zahl eine andere Nachricht meinen. Ein Check auf UIDVALIDITY-Wechsel **innerhalb** eines Calls erkennt diesen Fall nicht. Rewrites ändern UIDs ebenfalls; Features verwenden trotzdem die alte Zahl.
- **Policygeneration:** Das Projected-Config-Feature behauptet, erneutes Öffnen der Pfade über den atomar umgeschalteten `..data`-Symlink garantiere eine vollständige konsistente Generation ohne Lock/Retry. Das gilt pro Pfadauflösung, nicht für mehrere nacheinander geöffnete Dateien. Ein Swap zwischen `callers` und `policies` kann die alte Caller→Policy-Zuordnung mit einem neuen weitergehenden Policyinhalt kombinieren, obwohl jede vollständige Generation diese Kombination ausschließt und die Hybridreferenzen syntaktisch gültig bleiben. Ein atomarer In-memory-Swap heilt einen bereits hybrid eingelesenen Baum nicht.
- **Korrektur:** Opaque Referenzen mit Account, kanonischer Objektidentität, UID-Epoche und Content-/Revision-Witness; bei Mutation atomare, über Instanzen koordinierte Reservation/CAS. Konfiguration aus einer nachweislich einzigen Generation lesen, diese bis zum Abschluss sichern oder Wechsel erkennen und den gesamten Load wiederholen. Externe Änderungen konservativ als Konflikt behandeln. Gleichzeitige gleiche/verschiedene Requests, Cross-instance-Races, UIDVALIDITY zwischen zwei Toolcalls, Config-Swap zwischen zwei Dateiöffnungen und Mutable-Predicate-Wechsel zwischen PDP und Write prüfen.

#### S-14 — Audit-Fail-closed geschieht nach dem Seiteneffekt; Wiederholung kann doppelt schreiben

- **Kategorie/Beweisart:** Auditvollständigkeit/Transaktionssicherheit; dokumentierter Defekt.
- **Beleg:** `docs/adr/0021-audit-log-format.md:43-44,178-194`; `docs/adr/0032-shared-audit-chain-across-local-instances.md:59-61`; `docs/ROADMAP.md:163-169`; `bdd/features/audit/audit_multi_instance.feature:102-108`.
- **Angriff-/Fehlerpfad:** Handler führt APPEND/MOVE/Rewrite aus; nachgelagerter Audit-Write scheitert. Caller bekommt Fehler, Mail ist bereits verändert, dauerhafter Tool-Audit fehlt. Retry kann erneut anlegen. Unerwartete Handler-Exceptions erzeugen laut ROADMAP ebenfalls keinen Eintrag. Der vorhandene „audit unwritable“-Test verwendet `get_caller_identity`, also gerade keinen Mail-Schreibeffekt. Er kann die zentrale Nichtausführungsgarantie nicht prüfen.
- **Korrektur:** Dauerhaftes, prüfbares Write-Intent vor jedem externen Seiteneffekt, mit Caller, Policygeneration, Objektbezug und idempotentem Request-Key. Nicht schreibbares Audit/WAL muss die Aktion vorab verhindern. Nachher Outcome/Uncertain-Zustand und Recovery/Reconciliation. Ein intent/outcome-Paar ist nicht automatisch eine atomare Transaktion mit IMAP; Crash zwischen Effekt und Outcome bleibt ausdrücklich aufzulösen. Unbekannte Exceptions immer kategoriell auditieren, ohne potenziell vertraulichen Exception-Text.

#### S-15 — WAL-Datenklassifikation widerspricht dem Recovery-Vertrag

- **Kategorie/Beweisart:** sensitive Persistenz/Retention; expliziter Dokumentwiderspruch.
- **Beleg:** `docs/adr/0007-sqlite-as-wal-store.md:94-106`; `docs/adr/0008-idempotency-via-message-id-and-hash.md:126-128`; `bdd/features/transactions/saga_crash_recovery.feature:43-54`; `docs/adr/0031-draft-revision-via-rewrite-saga.md:17,78-85`; `docs/adr/0022-audit-retention-and-access-model.md:97-104`.
- **Mechanismus:** ADR 0007 verspricht „no message bodies“, bezeichnet IDs/Accountnamen als nonsensitiv und plant 90 Tage terminale Aufbewahrung. Das Recovery-Feature verlangt Re-APPEND **aus WAL-held RFC822 bytes** nach Crash. Das sind Body und Attachments, nicht nur Metadaten. Draft-/Attachment-Rewrites benötigen ebenfalls dauerhafte Transform-/Zielinformation, deren Speicher- und Zugriffskontrakt nicht geschlossen ist. Eine Policy-/WAL-Weitergabe unter der Annahme „nur Metadaten“ würde Vollinhalt preisgeben.
- **Beweisgrenze:** Ob die aktuelle SQLite-Datei tatsächlich Rohbytes enthält oder die Implementation anders recoveriert, wurde nicht gelesen. Beide Dokumente können jedenfalls nicht gleichzeitig unverändert erfüllt sein. Dass `get_transaction_status` diese Bytes zurückgibt, wird mangels Response-Schema **nicht** behauptet.
- **Korrektur:** Persistenzmodell explizit festlegen: sensible Payloads/Witnesses, verschlüsselt und principal-isoliert, strikte minimale Retention und Löschbeweise nach sicheren Terminalzuständen. WAL-/Snapshot-/Backup-Zugriff wie Mailinhalt behandeln, nicht wie harmlose IDs. Statusausgabe nur gesondert freigegebene Felder; Content niemals implizit aus WAL serialisieren.

#### S-16 — Sink-Dateinamen begrenzen weder Symlink-Angriffe noch Kollisionen und Überschreibungen

- **Kategorie/Beweisart:** Filesystemintegrität; spezifizierte Kollision plus fehlende sichere Öffnungsregeln.
- **Beleg:** `docs/adr/0028-attachment-file-sink-delivery.md:83-113,120-149,199-209,240-258`; `docs/adr/0029-kubernetes-sidecar-deployment-topology.md:75-84,359-366`; `bdd/features/tool_surface/fetch_attachment_sink.feature:42-79`.
- **Angriffspfad:** Zielname ist aus MIME-Name und erstem 32-Bit-MD5-Präfix vorhersehbar. Ein Caller mit Schreibrecht auf `/sink` kann dort einen Symlink unter diesem Namen anlegen. Falls der Writer normal folgend öffnet, kann der Link auf einen nur dem Server sichtbaren schreibbaren Pfad zeigen; Zeichenersetzung im Namen und `stat/os.access` auf dem Verzeichnis verhindern das nicht. Nofollow/dirfd/atomisches exklusives Schreiben werden nicht verlangt oder getestet. Das ist eine nachzuweisende Implementierungsgrenze, kein hier ausgeführter Symlink-Exploit.
- **Unbedingter Namensfehler:** Für gleiche sanitized base/extension haben unterschiedliche Inhalte nur 2^32 Namenssuffixe. Die Behauptung „different attachments … two distinct files because their hashes differ“ ist falsch; bei ungefähr 77.000 verschiedenen Inhalten desselben Basenamens liegt die Birthday-Kollisionswahrscheinlichkeit etwa bei 50 %. Das ist eine 32-Bit-Verkürzungsfrage, kein Bedarf nach einem vollständigen MD5-Kollisionsangriff. „Overwrite on refetch“ überschreibt dann eine andere Datei.
- **Weitere offene Grenzen:** nur Base-Truncation, keine adversarielle sehr lange Extension; keine Regeln gegen Hardlinks, atomare Ersatzschritte, Partial Files, Ownership-/Mode-Fehler oder konkurrierende Writers. Der Caller beeinflusst MIME-Namen und bekannte Bytes durchaus indirekt, entgegen „cannot influence … filename“.
- **Korrektur:** Serverprivater Write-Bereich, callerlesbare aber nicht callerbeschreibbare Bereitstellung; `openat`/dirfd und No-follow-/Ownership-Regeln, sichere atomare Veröffentlichung. Starke vollständige Content-ID oder zufällige opaque Artefakt-ID, Kollisionserkennung vor jedem Ersetzen. Names sind Anzeige, nicht Autorisierung oder eindeutige Speicheridentität. Symlink/Hardlink/Rename-Race/ENOSPC/lange Extension explizit testen.

#### S-17 — Zeit- und Outside-Count-Vertrag versprechen Ergebnisse, die die vorgezogene IMAP-Suche nicht garantieren kann

- **Kategorie/Beweisart:** falsche Selektionsmenge/untererfasste Bulk-Operationen; mathematisches Gegenbeispiel.
- **Beleg:** `docs/adr/0024-duration-grammar-single-source.md:70-95`; `bdd/features/tool_surface/search_criteria_and_pagination.feature:79-95,131-147`; `bdd/features/providers/gmail_search_and_counts.feature:19-24`; `docs/adr/0036-search-window-transparency-and-calendar-dates.md:26-37`; `docs/limitations/0011-search-performance-large-mailbox.md:47-60`; `docs/adr/0004-sender-rule-matcher-grammar.md:33-43`.
- **Gegenbeispiel:** Jetzt sei 2026-05-07 12:00 UTC, `older_than=60s`, Nachricht von 09:00 desselben Tages. Sie ist älter als 60 Sekunden. ADR 0024 setzt aber `BEFORE = today - floor(60/86400) = 2026-05-07`, also exklusiv vor Tagesbeginn; die Nachricht wird vorher entfernt. Kein nachgelagerter Filter kann sie wieder hinzufügen. Auch bei 30h fehlen passende Nachrichten zwischen Mitternacht und der tatsächlichen Schwelle des Vortags. Die behauptete Superset-Garantie ist falsch.
- **Zweite Inkonsistenz:** relative Prädikate vergleichen `Date:`, IMAP `SINCE/BEFORE` vergleicht INTERNALDATE. Die Fixtures setzen beide absichtlich gleich. Reimportierte/verzögert zugestellte Nachrichten widerlegen ohne entsprechende Invariante selbst eine korrigierte reine Tagesrundung.
- **Outside-Count:** „same criteria“ plus exakter Count, aber „one extra UID SEARCH; no message is fetched“ ist für MIME-basierte `has_attachment` und exakte domain-/normalisierte Adressprädikate nicht allgemein erreichbar. Der dokumentierte Pushdown `FROM "@domain"` ist ein gröberer Stringfilter; `LARGER 0` ist kein Attachmentnachweis. Ein ungeprüfter Pushdown-Superset-Count ist nicht der Count desselben exakten Prädikats.
- **Korrektur:** Zeitbasis und Grenzinklusion explizit trennen (`received_at` vs `sent_at`); sicheren Pushdown aus tatsächlicher Schwelle ableiten, vollständigen zugelassenen Postfilter vor **allen** sichtbaren Counts ausführen. Für unbekannte oder nicht exakt pushdownfähige Counts kein exakter Wert erfinden. Boundary-/Timezone-/Date-vs-INTERNALDATE-Varianz und gleiche Regeln auf Bulk-Writes prüfen.

#### S-18 — Das Domänen-/Richtungsmodell ist asymmetrisch und semantisch nicht geschlossen

- **Kategorie/Beweisart:** fehlende Fachlichkeit/fehlleitende Autorisierung.
- **Beleg:** `docs/adr/0004-sender-rule-matcher-grammar.md:31-43,98-105`; `README.md:291-324`; `docs/adr/0017-response-transparency-for-filtered-data.md:77-78`; `bdd/features/policy/sender_rule_matcher.feature:25-48`; `docs/adr/0030-draft-identity-and-revisions.md:57-58`; `docs/ROADMAP.md:193`.
- **Mechanismus:** Inbound kann durch `from_domain` exakt gruppiert werden. Für Outbound gibt es nur `to` und `to_contains`, kein `to_domain`, keine eigenständigen Cc-/Bcc-Prädikate. ADR 0004 erklärt `to`/`to_contains` als To **oder Cc**, Features variieren nur To. `from` darf SMTP MAIL FROM **oder** Header-From bedeuten; die Auswahl bei Widerspruch ist nicht entschieden. Substring `allianz.de` trifft auch `person@evil-allianz.de` und `person@allianz.de.attacker.com`; eine Domainfreigabe daraus ist falsch.
- **Lifecycle:** Erstellen in Drafts/FULL garantiert nach externem Versand keine Sicht in Sent Mail/Whitelist/NONE. Weder Caller-Urheberschaft noch ein grantbewahrender Draft→Sent-Übergang ist modelliert. Ein Stern hilft nur, wenn eine wirksame passende Regel in der relevanten Sicht existiert; in einer Blacklist kann er strengere Caps nicht per Max-Grant überstimmen. Die konkrete geladene Sent-Policy des Uservorfalls wurde nicht gelesen, daher wird ihr genauer Fehler nicht erfunden.
- **Weitere Wirkung:** `sender_not_whitelisted` bei Recipient-/Flagged-Regeln behauptet die falsche Ursache. Raw-Create erlaubt bewusst Headerwahl, Structured-Create hat nach ROADMAP kein From; `create_reply_draft` setzt From dagegen auf Accountidentität. Policyentscheidungen auf diesen verschiedenen Datenursprüngen sind nicht symmetrisch. NFC beseitigt keine kyrillischen Lookalikes; die pauschale Confusables-Begründung ist unzureichend.
- **Korrektur:** Richtung und Akteursrolle aus vertrauenswürdiger Herkunft modellieren; typisierte Address-/Domain-Prädikate über parsed addr-spec, exakte oder explizit subdomainfähige Grenzen, definierte IDNA-/Unicode-Normalisierung. To/Cc/Bcc jeweils bewusst erfassbar, inklusive „nicht bekannt“ bei nicht verfügbaren Bcc-Daten. Neutrale externe Reason-Kategorie; interne Policyregeln operatorseitig. Sent-Zugriff auf eigene Entwürfe ist eine ausdrückliche Ownerentscheidung mit authentischer Provenienz, kein automatisch aus From abzuleitender Grant.

#### S-19 — Diagnostik- und Trace-Verträge widersprechen dem No-content-leak-Versprechen

- **Kategorie/Beweisart:** sekundäre Datenkanäle; ausdrücklicher Widerspruch und unvollständige Sanitizer-Anforderungen.
- **Beleg:** `docs/adr/0021-audit-log-format.md:98-112,175-188`; `docs/adr/0028-attachment-file-sink-delivery.md:159-175,264-268`; `docs/adr/0027-error-envelope-and-tool-surface-versioning.md:97-100,223-230`; `bdd/features/tool_surface/create_draft_error_visibility.feature:23-29,96-138`; `docs/adr/0037-trace-payload-modes.md:26-41,60-66`; `docs/adr/0038-traces-for-usage-efficiency.md:28-33,65-67`; `bdd/features/observability/tracing_payload_mode.feature:80-137`.
- **Konkreter Widerspruch:** Audit darf nie Attachment-Dateinamen enthalten; `saved_to_absolute` enthält ausdrücklich den aus dem ursprünglichen Dateinamen abgeleiteten Namen plus Pfad. Sanitizing entfernt keine semantischen Namen wie `Kuendigung_Mitarbeiter_Meier.pdf`. Dieser Audit-Eintrag muss entweder verboten oder als explizite, anders geschützte Datenklasse beschlossen werden.
- **Error-Reflection:** `error.detail` darf keinen caller-kontrollierten Echo und keine internen Pfade enthalten; zugleich sollen IMAP-NO/BAD-Texte verbatim zurückkommen. Upstreamtexte können gerade solche Werte zitieren. Eine Begrenzung auf 256 Zeichen ist keine Sanitization. Das eine Poison-Folder-Szenario prüft einen lokal klassifizierten Absence-Fehler, nicht einen fremden Status mit sensitiver Reflection.
- **Tracing:** `full` ist ausdrücklich beschlossenes Opt-in und wird hier nicht allein deshalb als Bug bezeichnet. Es macht den Trace-Store aber zu einer Kopie berechtigter Mailinhalte. `redacted`/`off` lassen weiterhin Größen/Counts/ClientInfo zu; diese sind korrelierbare Metadaten, nicht pauschal „nicht personenbezogen“. Der Client wählt `clientInfo`; keine Bound-/PII-Regel ist beschrieben. Die Negativtests decken einen Raw-Draft, Envelope und einen Account-Passwort-Canary ab, nicht alle 30 Responses, strukturierte Changes, Dateinamen, private `_matched_sender`-Kontexte, OAuth-Fehler und verschachtelte Payloads.
- **Korrektur:** Typisierte Allowlist für Audit, Fehler und jeden Trace-Modus, inklusive aller Nested-Felder und Upstreamtexte. Privilegierte Diagnostik getrennt vom Caller-Fehler; keine pauschale Rohstatus-Ausgabe. Vollpayload-Sinks nur isoliert, mit dokumentierter Retention/Zugriffskontrolle. Hashes über geringe Kandidatenräume sind keine Anonymisierung; für korrelierbare Query-Digests ggf. operatorgeschützte HMACs statt öffentlicher SHA-256-Wörterbuchwerte.

#### S-22 — Intra-account-Fallback und APPEND-Fehler behandeln unsichere Zustände als entschieden

- **Kategorie/Beweisart:** Datenverlust/Duplikate/fehlende Transaktionsgrenzen.
- **Beleg:** `docs/adr/0006-cross-account-move-via-saga.md:41-44,55-59`; `docs/adr/0018-non-goal-tool-surface.md:29-39`; `bdd/features/transactions/intra_account_move.feature:46-60`; `bdd/features/tool_surface/create_draft_error_visibility.feature:35-44,151-197`; `docs/limitations/0004-imap-fault-injection-via-env-var.md:127-136`.
- **Pfad A:** Ohne MOVE folgt COPY, STORE Deleted, EXPUNGE **ohne WAL/Saga**. Crash nach COPY hinterlässt ein Duplikat ohne resumierbaren Vorgang. Ein unqualifiziertes IMAP EXPUNGE kann zudem andere bereits `\Deleted` markierte Nachrichten im ausgewählten Folder entfernen; für sie wurde keine bestätigte Zielkopie nachgewiesen. Das ist nicht die versprochene ausschließlich gezielte Move-Destruktivität. Es gibt keinen Negativzeugen einer zweiten, fremd markierten Nachricht.
- **Pfad B:** APPEND kann serverseitig abgeschlossen sein, während Antwort/Verbindung verloren geht. Der Feature-Header verlangt bei **jedem** Fehler „draft did NOT land“. Die dokumentierte Delay-Fixture lässt den APPEND-Befehl dagegen gar nicht durch und ist damit kein Beweis für „Response verspätet nach tatsächlichem APPEND“. Ein Retry nach unbekanntem Erfolg kann zwei Drafts mit verschiedenen Handles erzeugen.
- **Korrektur:** Fallback nur mit sicherer UID-begrenzter Löschung und Journal/Recovery oder ausdrücklich als nicht unterstützt ablehnen. `unknown/uncertain` von „definitiv nicht ausgeführt“ unterscheiden. Idempotenter Create-Request-Key an Caller/Intent binden, auch wenn der Receipt verloren geht. Crash nach jedem Fallback-Schritt, erfolgreich gespeicherte Mail mit verlorener ACK und Nachbar-Deleted-Nachricht extern prüfen.

### MITTEL

#### S-20 — OAuth-Zustands-, Scope- und Bootstrap-Garantien sind nur teilweise beweisbar

- **Kategorie/Beweisart:** Credential-Lifecycle/Defence-in-depth/Operator-Remediation.
- **Beleg:** `docs/adr/0009-oauth2-authorization-code-with-scope-minimization.md:32-59,71-80,103-105`; `docs/adr/0010-configurable-token-cache-strategy.md:67-79`; `docs/adr/0014-policy-as-git-versioned-yaml.md:73-83`; `bdd/features/auth/oauth2_bootstrap.feature:44-72`; `docs/limitations/0017-expired-oauth-token-not-proactively-or-durably-detectable.md:40-64`; `docs/limitations/0018-oauth-reauth-not-initiable-from-mcp-no-remediation-guidance.md:50-84`; `docs/limitations/0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md:41-53,77-101`; `docs/error_path_analysis.md:483-492`.
- **Mechanismus:** Nach Restart/Reload ist ein bereits als tot erkannter Refresh-Token laut LIM wieder „active“ bis zur nächsten Verwendung. ADR/Feature behaupten zugleich proaktiven Refresh. Read-only-Scopes werden im Feature **vor STORE lokal** abgewiesen; das beweist nicht die behauptete unabhängige Provider-Enforcement-Schicht. Auch soll der Loader unpassende Write-Capabilities bei OAuth-Scope verwerfen, während das Runtime-Szenario genau diese Konfiguration voraussetzt. Bootstrap-Listener/Backend-Abstraktion und dokumentierte Paste-CLI/file_dir-only unterscheiden sich. Read-only Pod-Secrets können Rotation nicht dauerhaft speichern.
- **Abgrenzung:** Kein unbelegter Claim über das aktuelle Google-/Microsoft-Protokollverhalten wird aus dem Mock abgeleitet. Genau die unterstützten realen IMAP-Scopes und Rotationseigenschaften müssen unabhängig bestätigt werden.
- **Korrektur:** Accountgesundheit als `unknown/verified_active/reauth_required/unavailable` mit persistiertem bekanntem Negativzustand; kontrollierte Hintergrundprüfung statt irreführendem active. Pro Provider Scope-/Rotation-/Backend-Matrix, State-/PKCE-/TLS-Negativpfade und sichere Out-of-band-Remediation. Operator-Auth nicht ungeprüft als neue MCP-Admin-Capability öffnen.

#### S-21 — Anfrage-, Speicher- und Instanzbudgets sind keine durchsetzbaren Grenzen

- **Kategorie/Beweisart:** Verfügbarkeit/Resource isolation; dokumentierte Grenzen und offene Verträge.
- **Beleg:** `docs/adr/0013-hybrid-connection-pool.md:36-43,132-135`; `docs/ROADMAP.md:196,200`; `docs/adr/0024-duration-grammar-single-source.md:115-120,132-136`; `docs/limitations/0015-attachment-sink-cleanup-out-of-scope.md:35-48,103-109`; `docs/error_path_analysis.md:98-127,483-494`.
- **Mechanismus:** 4 Verbindungen je Prozess und etwa 15 gleichzeitige lokale Sitzungen erlauben rechnerisch 60 Verbindungen zu einem Account, dessen dokumentierte Providergrenze ungefähr 15 ist. Acquire-Timeout hat keinen beschlossenen Caller-Vertrag. `scope=all`, Sub-day-Overfetch, große RFC822-/MIME-Payloads, Taglisten und Sink-Dateien besitzen keine lückenlos spezifizierte Budgetmatrix. Ein voller gemeinsamer Datenträger kann Audit/WAL blockieren. Der Regex für Duration hat keine eigene Längen-/Wertgrenze; ein behaupteter Transportcap ist kein hier sichtbarer Vertrag.
- **Korrektur:** maximale Bytes, Parts, Tiefe, Listenlängen, Dauerwerte, Ergebnisarbeit und Query-/Write-Raten spezifizieren; Fehler vor Wirkung, keine stillen Teilresultate. Quoten pro Caller und Account, globaler Provider-Verbindungsplan über Instanzen, klarer Timeout-/Retry-Vertrag. Sink von Audit/WAL-Quota trennen; Retention-/Free-space-Signale betrieblich erzwingen.

#### S-23 — Attachment-„OK“ besagt nur serverseitige Speicherung, nicht abrufbare Auslieferung

- **Kategorie/Beweisart:** API-/Deployment-Vertrag und Silent Failure.
- **Beleg:** `docs/adr/0028-attachment-file-sink-delivery.md:151-175,213-224`; `docs/limitations/0014-attachment-sink-requires-caller-filesystem-access.md:34-48,85-100`; `docs/adr/0029-kubernetes-sidecar-deployment-topology.md:396-402`; `usability/features/04_filing_and_attachments.feature:43-52`.
- **Fehlerpfad:** HTTP-/Container-Caller ohne gemeinsamen Mount erhält OK plus Dateiname, kann die Datei aber nicht lesen. Ein fehlender Agent-Mount wird ausdrücklich nicht erkannt. Der Pfad steht nur in der Toolbeschreibung, nicht als stabiler Zugriffshandle mit Berechtigung und Lebensdauer in der Antwort. Server-Write-Erfolg, tatsächliche Fetchbarkeit und vom Menschen auffindbare Datei sind verschiedene Zustände.
- **Korrektur:** Abrufbare, callergebundene Artefakt-Referenz statt bloßem Filenamen; Ausgabe `stored` nicht „delivered“, wo kein Delivery-Nachweis existiert. Deployment-Preflight mit End-to-end-Leseprobe bzw. eindeutigem Downloadkanal. Lebensdauer, Re-Fetch, Quoten und Fehler nach Server-Neustart/Pod-Verlust ausdrücklich festlegen. Diese Prüfung darf die Isolation aus S-05 nicht durch ein gemeinsames lesbares Verzeichnis wieder aufheben.

## 5. Architekturvorschlag für ein sauberes Zielkonzept & API-Design

### 5.1 Urteil: semantischer Neuschnitt, kein begründeter Totalersatz der Implementierung

**Ein rein evolutionäres Hinzufügen von `to_domain`, einem fehlenden if oder zusätzlichen Reason-Codes reicht nicht.** Das Problem ist die Komposition der geltenden Entscheidungen: queryabhängige Hidden-Zahlen, leserechtsunabhängiger Vollinhaltstransfer, callerveränderliche Grants, nichtgebundene Handles und ein Filesystem, das Berechtigungen abstreift. Diese Entscheidungen müssen gemeinsam ersetzt werden. Die notwendige Änderung der Autorisierung und der Responses ist ein **Major-Vertrag**, nicht eine weitere „additive“ 2.x-Korrektur.

Folgende Mechanismen können unter einem neuen Vertrag weiterverwendet werden, ohne ihre gegenwärtigen Garantien zu übernehmen:

- der fail-closed Account-/Policy-Einstieg und die Trennung von Entscheidungslogik, Projektion und Provider-I/O;
- eine endliche, validierbare Prädikatgrammatik statt beliebiger Code-/Regexausführung;
- differenzierte Aktionen statt eines pauschalen write-Bits;
- Provideradapter einschließlich kanonischer Folderauflösung, sofern sie keine Policy umgehen;
- ein dauerhafter Transaktions-/Recovery-Mechanismus und gerichtete, prüfbare Zustandsübergänge;
- getrennte Caller-/Operator-Ebene, transparente **autorisierte** Sicht und keine Raw-IMAP-Hintertür;
- unabhängige IMAP-/Datei-/Collector-Zeugen, jedoch mit korrekten Givens und tatsächlichen Produktionsauslösern.

Nicht tragfähig sind die Zusätze „ein Tool = genau ein Gate“, „Folderrechte sind unabhängig voneinander hinreichend“, „Zähler verraten keinen Inhalt“, „Stern ist menschliches Approval“, „Headerhandle beweist eigenes Draft“ und „Status-Poll darf Recovery fremder Intents treiben“. Sprache, IMAP-Bibliothek und SQLite müssen nicht allein deshalb ersetzt werden; ihre Eignung ist außerhalb dieses Dokumentaudits separat zu prüfen.

### 5.2 Verbindliche Zielinvarianten

1. **Authentische Instanzgrenze:** Caller und Operator sind getrennte Principals. Config/Secrets/Code/WAL/Audit sind dem Agent nicht schreibbar oder direkt lesbar. Unterstützte Deploymentformen sind maschinell validiert.
2. **Objekt statt Zugriffsweg:** Rechte beziehen sich auf das Mailobjekt und seine vertrauenswürdige Klassifikation; Folder/Labels sind zusätzlich adressierbare Ansichten und Mutationsziele. Der Caller wählt nicht durch den Pfad seine maximalen Rechte.
3. **N bedeutet Nichtbeobachtbarkeit:** Existenz, Counts, Prädikatantworten, Revisionen, versteckte Memberships und Fehler unterscheiden keine ausschließlich N-Zustände. Eigene Write-only-Receipts sind eine ausdrücklich eng definierte Ausnahme, keine generelle Objektauflösung.
4. **Keine höheren Feldorakel:** Ein Caller darf Such-/Filterprädikate nur auf den Feldern auswerten, deren Werte oder bewusst freigegebene Aggregate er tatsächlich sehen darf. Der Check der Query-Feldrechte ist mailboxunabhängig, nicht abhängig davon, ob es passende geheime Treffer gibt.
5. **Autorisierung des gesamten Deltas:** Vor einer Mutation werden jedes gelesene Contentfeld, jede veränderte Nachricht/Revision/Part, jede betroffene Mitgliedschaft und jedes Ziel geprüft. Ein Write-Capability ist kein stiller Read-/Export-Grant.
6. **Kein Write-up der eigenen Rechte:** Caller-schreibbare Attribute erzeugen keine stärkeren Grants. Operatorapprovals und Klassifikation sind getrennt von `\Flagged`, frei wählbarem From/To/Subject und nichtauthentisierten Mailheadern.
7. **Kontrollierter Informationsfluss:** Ein vollständiger Transfer setzt Berechtigung zum Export genau dieses vollständigen Inhalts voraus. Ziel/Download müssen zu dessen Empfängerkreis passen oder eine ausdrückliche Declassification-Entscheidung besitzen.
8. **Gebundene Identität:** Message-/Draft-/Part-/Transaction-/Artifact-Referenzen sind an Caller/Tenant/Account/Objekt/Version gebunden. Raten oder Kopieren einer Kennung schafft kein Recht. Transport-UIDs und Epochen bleiben interne Auflösungshinweise.
9. **Nebenwirkungsfreie Reads:** Status-/Discovery-/Search-Aufrufe lösen keine Mailmutation/Recovery aus. Falls Fetch Attachment ein Artefakt erzeugt, ist diese Wirkung im Namen, Vertrag, Audit und Budget sichtbar.
10. **Durable intent before effect:** Nicht dauerhaft protokollierbare Writes werden nicht ausgeführt. Unknown outcomes sind explizite Zustände; Retry ist idempotent, nicht blind. Source-Delete erst nach eindeutig bestätigter Zielintegrität.
11. **Lineariserbare Servermutationen:** Revisions-/ETag-Vergleich und Reservation sind über alle schreibenden Instanzen koordiniert. Externe unkoordinierte Änderungen werden erkannt oder fail-closed eskaliert, nicht als durch einen Integer gelöst behauptet.
12. **Kanalübergreifende Projektion:** Response, Audit, Trace, Error, WAL und Artefaktdownload haben jeweils explizite Datenklassifikation und Allowlist. Eine Kopie in einem anderen Kanal hebt die Policy nicht auf.

### 5.3 Daten- und Policy-Modell

**Identität:** Intern `MessageObject(account_id, provider_object_id, content_version)` und `Membership(object_id, folder_id, uidvalidity, uid)`. Gmail `X-GM-MSGID` kann ein Provider-Identitätsbaustein sein; ein All-Mail-UID ist keine globale Objekt-ID. Nicht-Gmail benötigt einen konsistenten serverseitigen Referenz-/Witnessträger mit expliziter Behandlung von Copies und UIDVALIDITY. Öffentliche Referenzen sind opaque, nicht manipulierte Folder-/UID-Tupel.

**Autorisierung in zwei Stufen:**

1. Caller authentifizieren, Account-/Discovery-/View-Recht prüfen; nur dafür benötigte interne Auflösung zulassen. Fehlendes Accountrecht darf nicht schon eine externe Folder-Ermittlung auslösen.
2. Minimale erforderliche Metadaten unter serverinterner Zweckbindung ermitteln, Objekt-/Content-/Aktionsentscheidung auf einem Policy-/Objekt-Snapshot treffen. Providermechanismus erhält einen autorisierten Plan, nicht eine freie Kombination aus Caller-Strings.

**Visibility:** Die lineare Inhaltsprojektion kann zunächst als Kompatibilitätsvokabular bleiben, aber mit abschließender Feldtabelle:

| Zielstufe | Frei gegebene Beobachtung |
|---|---|
| NONE | kein Objektbezug, kein Countbeitrag, keine objektabhängige Error-/Redaktionsvarianz |
| COUNT | nur ausdrücklich autorisierte Aggregate; keine UID/Handle-/Predicate-Probes über unbekannte Inhaltsfelder |
| METADATA | opaque Objekt-ID, ausgewählte technische Metadaten; kein Subject/From/Recipient/Attachmentname |
| ENVELOPE | konkret entschiedene Headerprojektion; `has_attachment` entweder ausdrücklich hier aufnehmen oder hier konsequent nicht ausgeben |
| HEADERS | erlaubter Headerblock einschließlich deklarierter Fremdheaderbehandlung |
| BODY | Body-Projektion ohne Attachment-/eingebettete Message-Bytes; MIME-Metadaten bewusst klassifizieren |
| FULL | vollständig autorisierte Parts; nicht automatisch Write, Export oder Zugriff auf alle Labels |

**Gmail-Policy-Komposition:** Normale Navigationslabels dürfen keine Rechtequelle sein. Der Owner muss **ausdrücklich** festlegen, welche Klassifikationen/Security-Labels Sicherheitslabels sind; ihre Restriktionen gelten objektweit und sind deny-overrides/minimal. Ein verstecktes Navigationsfolder All Mail soll nicht automatisch alle über INBOX berechtigten Objekte global auf NONE ziehen. Umgekehrt darf ein sichtbares All Mail keinen restriktiven objektweiten Schutz aufheben. Das ist eine neue, explizite Unterscheidung zwischen **View-Discovery** und **Objektklassifikation**, nicht der alte beliebige Maximalgrant über alle Labels.

**Aktionen:** mindestens `read_projection`, `aggregate`, `mark_seen`, `set_user_keyword`, `create_owned_draft`, `edit_owned_draft`, `edit_foreign_draft` (falls gebraucht), `modify_part`, `copy_export`, `relocate`, `add_membership`, `remove_membership`, `trash_object`, `import`, `download_artifact`. Sie werden pro Objekt/Scope und vollständigem Zielplan entschieden. Eine technische APPEND-/DELETE-Umsetzung erbt keinen weiter reichenden fachlichen Grant.

**Prädikate:**

- strukturierte Felder für `direction=inbound|outbound|draft|unknown`, `actor/created_by` nur aus authentischer server-/providerseitiger Provenienz;
- `address_exact(role, mailbox)` und `domain(role, domain, include_subdomains=false)` für definierte Rollen `from`, `sender`, `reply_to`, `to`, `cc`, ggf. `bcc`;
- eindeutige Behandlung mehrerer/fehlender/ungültiger Header, IDNA/Unicode, casefold und trailing dot; Domainvergleich nicht als Substring;
- `sent_at` und `received_at` unterscheiden; Kalender- und relative Grenzen auf die jeweils genannte Zeitbasis, mit Zeitzone/Inklusivität;
- technische Flags/Größe/Attachment-Existenz nur aus korrekt klassifizierten, bekannten Fakten; `unknown` ist nicht stillschweigend false oder ein großzügiger Default;
- Policygrammar und Querygrammar dürfen dieselbe **Bedeutung** verwenden, müssen aber nicht dieselben **Rechte** geben: ein Policyadministrator darf auf Informationen entscheiden, auf denen der Caller nicht frei suchen darf;
- Validierung schließt positive Grants über callerveränderliche Merkmale, unerreichbare Regeln, unsichere Label-Transfers und Capability-/Provider-Konflikte vor dem Aktivieren aus.

### 5.4 Konkreter API-Schnitt für den Major-Vertrag

Die folgenden Namen sind Designvorschläge, keine behauptet vorhandenen Tools. Ein endgültiger ADR muss den Schnitt entscheiden, nicht diese Vorschläge automatisch übernehmen.

| Familie | Konsistenter Zielvertrag |
|---|---|
| Discovery | `list_accounts`, `list_views`, `describe_access`: nur zugängliche Ressourcen, erlaubte Aktionen, feste Sicht-/Window-Semantik; keine tatsächlichen Hidden-Counts oder Rule-Indices |
| Suche | `query_messages(view_ref, query, window, cursor, fields)`: ein gemeinsamer Selektions-/Projektionsvertrag statt zweier divergierender search/list-Flächen; Resultate und `has_more` ausschließlich nach Autorisierung |
| Aggregate | eigene autorisierte Aggregation auf festgelegtem Scope; keine beliebigen Prädikate über N-/höhere Felder; eventuell ganz weglassen, wenn COUNT keine sichere nützliche Bedeutung bekommt |
| Content | `get_message(message_ref, projection)` und `list_parts(message_ref)`: bewusste Projektion, keine Ersatzroute über Draft/Headers/Saga |
| Attachments | `prepare_attachment_download(message_ref, part_ref)` → `artifact_ref`, verifizierte Größe/Hash/Lebensdauer; autorisierter Download pro Caller, optional explizit isolierter lokaler Dateimodus |
| Flags/Labels | `set_seen`, `set_user_keywords`, explizite Membership-/Trash-Intents; kein `mark_tagged`, das je Provider zugleich Flag, Keyword und Sicherheitsfolder mutiert |
| Transfers | `copy_message`/`relocate_message` mit Source-Exportrecht und geprüftem Destination-Ref; Antwort referenziert dieselbe logische Nachricht/Operation, nicht impliziten All-Mail-Zugang |
| Drafts | strukturierte `create_draft`, `get_draft`, `revise_draft(if_match, changes)`, `discard_draft(if_match)` auf authentisch ownable Lifecycle-Objekten; Raw-Import separat und restriktiver |
| Reply | `create_reply_draft(source_ref, visible_quote_projection, body, destination_ref)`: keine Agentwahl einer höheren Projektion, serverseitig kontrollierter Quote, doppelte Read-/Write-Autorisierung |
| Bulk | gleiche individuelle Aktions-/Prädikatrechte wie Einzelaufruf; konkrete autorisierte Objektmenge einfrieren; pro Objekt Outcome oder explizit begrenzte Gesamttransaktion; niemals hidden matched counts |
| Operationen | `get_operation(operation_ref)` rein lesend, ownergebunden; `pending`, `staged`, `committed`, `partial`, `uncertain`, `needs_operator` jeweils mit definierter Bedeutung und erlaubter Sicht |

**Transparenz ohne Orakel:** Jede Liste erklärt konstant, dass sie nur die autorisierte Sicht wiedergibt. Sie nennt `applied_window`, angeforderte Projektion, autorisierte zurückgegebene Anzahl und ggf. `has_more`/opaque Cursor auf derselben erlaubten Menge. „Es könnten außerhalb Ihrer Sicht weitere Daten existieren“ kann eine konstante Caveat sein; „diese Query hat 1 verborgenen Treffer“ nicht. Ein optionaler `total_visible` darf nur eine genau definierte autorisierte Menge zählen, keine versteckten Rohmatches. `outside_scope_total` wird nicht mit einer zweiten ungefilterten Suche ersetzt, sondern entfernt oder allein innerhalb einer separat autorisierten Aggregatsicht angeboten.

**Fehler:** Eingabeschemafehler sind objektunabhängige `invalid_arguments`; unautorisierte/unbekannte Objekt-Refs erzeugen dieselbe sichere Außenantwort. Sichtbare, bereits autorisierte Objekte dürfen `conflict`, `not_editable`, `part_not_found`, `provider_unavailable`, `uncertain` liefern. Genau ein versionierter Envelope, kein paralleles `error_type`/`error.type`. Operator-Diagnostik erhält einen Correlation-Identifier, nicht rohen IMAP-/Exceptiontext im Caller-Response.

**Transaction lifecycle:** Receipt/Idempotency-Key sind principal-scoped und unabhängig von Mailheadern. Intents binden Policygeneration, Objekt-/Content-Witness, vollständigen Mutationsplan, Ziel und Caller. Nach Restart arbeitet ein echter Worker. Bei Policyrevokation darf nicht unbegrenzt eine alte Freigabe neue Exporte erlauben: vor einem neuen irreversiblen Expositionsschritt aktuelle Rechte prüfen; bereits gestagte Zustände erhalten einen konservativen Safe-stop/Reconciliation-Vertrag, der bestätigte Kopien nicht zerstört. Der Owner muss entscheiden, welche bloße Cleanup-Arbeit unter dem ursprünglichen Intent weiterlaufen darf.

### 5.5 Nötige Owner-Entscheidungen

| ID | Entscheidung | Empfohlene Festlegung / Konsequenz |
|---|---|---|
| O-01 | Bedeutet NONE Nichtexistenz oder nur „keine Inhaltsbytes“? | **Strikte Nichtbeobachtbarkeit**. Hidden-/Outside-Counts entfernen. Andernfalls Schutzversprechen ausdrücklich herabstufen; nicht weiter „invisible“ behaupten. |
| O-02 | Dürfen Write-Rechte Read-/Exportrechte implizieren? | **Nein als Default**. Copy/Move vollständiger Bytes nur mit vollständiger Source-/Exportberechtigung; Deposit-only und Reply getrennt. Bestehende Policies werden neu validiert. |
| O-03 | Ist `\Flagged` eine menschliche Freigabe? | Nicht, solange Agenten es ändern dürfen. Entweder unprivilegiertes Sternflag oder getrenntes authentisches Operatorapproval; kein gleichzeitiger Grant-Trigger. |
| O-04 | Welche Labels/Folders sind Sicherheitsklassifikation? | Navigationsansichten von objektweiten Restriktionen trennen; keine beliebige Pfadwahl zur Rechteerhöhung. Globaler Trash-Effekt braucht objektweite Zustimmung. |
| O-05 | Sind Drafts caller-owned, gemeinsam bearbeitbar oder nur append-only? | Caller-owned als Default, explizite Kollaborationsrechte. Serverseitige Provenienz und Version-Witness; übernommene Fremd-/Sent-Mails nicht anhand eines Headers editierbar. |
| O-06 | Wird Zugriff auf eigene gesendete Entwürfe benötigt? | Separat entscheiden und durch authentische Draft→Sent-Zuordnung begrenzen. Nicht aus `From` oder Domain-Substring ableiten; kein implizites Recht auf sämtliche Sent-Mail. |
| O-07 | Mehrere Caller je Instanz unterstützt? | Erst nach Ownership-/Sink-/State-Isolationsbeweisen. Bis dahin technische Startverweigerung für Mehr-Caller-Konfiguration, nicht nur README-Warnung. |
| O-08 | Welche Deployments erfüllen den Threat Model? | Separate Principals/Container verpflichtend für sensible Mail. Same-user-stdio ausdrücklich außerhalb adversarieller Isolation. |
| O-09 | Attachment-Delivery-Modell? | Authentifizierte Artefakte/Downloads, ggf. isolierter lokaler Adapter; kein globaler callerlesbarer/callerbeschreibbarer Sink. Die permanenten Sink-LIMs neu entscheiden. |
| O-10 | Verhalten bei Audit-/WAL-Ausfall und unbekanntem Write-Erfolg? | Kein Effekt ohne durable Intent; `uncertain`/idempotenter Retry/Reconciliation; niemals Fehler gleich „nichts passiert“. |
| O-11 | Alte Policies bei Recovery nach Revokation weiter gültig? | Genaue Übergangsregel: neue Exposition unter aktueller Policy, sichere Restarbeiten eng an alten Intent gebunden; sonst Safe-stop/Operatorfall. |
| O-12 | Unterstützte Provider-/TLS-/OAuth-/Legacy-IMAP-Garantien? | Tatsächliche Scope-/Rotation-/MOVE-/UIDPLUS-/EXPUNGE-Matrix beschließen. Nicht sicher ausführbare Schreibpfade verweigern statt sie unter einer allgemeinen atomaren MOVE-Garantie zu verstecken. |
| O-13 | Trace-/Audit-/WAL-Sensitivität und Retention? | Personal-data-/Content-Klassen je Kanal, erlaubte Sinks, TTL und Off-host-Witness festlegen; full-Traces nur explizit isoliert. |
| O-14 | Governance-/Release-Gate? | Proposed-Kernentscheidungen beschließen oder zurückziehen, LIMs formell reparieren, Fehlerpfad- und Triadenmatrix verbindlich machen. Sicherheits-/API-Neuschnitt als Major-Version mit Migrationsprüfung veröffentlichen. |

### 5.6 Priorisierte Umsetzung und erforderliche Beweisfälle

**P0 – Schaden begrenzen, vor weiterer breiter Freigabe:**

1. Keine ungefilterten Query-/Hidden-Zähler an Caller; insbesondere search/list/Bulk/Defaultwindow gemeinsam ändern.
2. Fremdtransaktionen sperren, Status-Poll entmutieren; Mehr-Caller-Betrieb technisch begrenzen.
3. Source-Export-/Informationsflussgate auf Copy/Move/Reply/Label-Wechsel; Selbstfreigaben durch Flag-/Header-/Attachment-Mutation blockieren.
4. Sink/Principals trennen; sichere Dateipublikation; Audit-Intent vor jedem Mail-/Dateischreibeffekt.
5. Source niemals aufgrund bloßer Message-ID-/Prefixgleichheit löschen; unsichere EXPUNGE-/Lost-ACK-Pfade stoppen oder explizit uncertain behandeln.

**P1 – Modell/Verträge beschließen und BDD red-first neu ordnen:**

- Mindestens folgende Gegenbeweise müssen als beobachtbare Szenarien existieren, bevor ein grüner Release als Sicherheitsnachweis dienen darf:

| Beweisfamilie | Given / When / Then – erforderlicher Beobachter |
|---|---|
| N-Nichtinterferenz | Zwei identische erlaubte Sichten, in einer zusätzlich/anders nur N-Objekte; adaptive gleiche Requests über alle betroffenen Tools; vollständige Responses/Fehler/Cursor und zugängliche Artefakte gleich. Nicht nur „kein hidden_subject-Feld“. |
| Höhere Feldstufen | M-Caller fragt Subject/To/Attachmentprädikate; Query wird unabhängig vom verborgenen Inhalt gesperrt oder auf explizit freigegebene Projektion begrenzt. Gleiche Tests auf C/E/H/B. |
| Write-Kreuzprodukt | Alle 13 Schreibtools × sieben Levels × fehlende/vorhandene Source-/Target-Rechte, plus unterschiedliche Caller; direkte Source-/Target-/Part-/Flag-Unverändertheit bei Ablehnung. |
| N/B→F-Transfer | Geschützte Quelle, offenes Ziel; Copy/Move/Label-Add verweigert oder nur bewusst redigierte Ableitung; danach echtes Lesen im Ziel kann keine verborgenen Bytes liefern. |
| Mutable Grants | Einzel-/Bulk-Stern add/remove/replace, Subject-/To-/Date-/Attachment-/Größenwechsel; effektive Rechte steigen nicht ohne getrenntes Approval. Alle anderen Systemflags unverändert. |
| Reply-Quote | FULL-Folder mit individueller N/HEADERS/BODY-Cap; kein Draft und kein Quote bei zu geringer Source-Sicht; Body mit text/plain-Attachment/embedded message kann diese Bytes nicht ins Quote ziehen. |
| Gmail-Homes | Dasselbe gm-Objekt in INBOX, Sent, All Mail, Trash und Userlabel mit verschiedenen Restriktionen; kein Cross-view-Escape, kein All-Mail-UID-Leak, explizit geprüfte globale Trash-/Spam-Folgen. |
| Draft-Provenienz | A/B mit bekanntem gleichem Handle, importierten/doppelten Headern, fremder höherer Revision, Sent-Zustand; nur autorisierte echte Draft-Objekte ändern/verwerfen. Alle gefundenen Revisionen einzeln geschützt. |
| Concurrency/UID | Zwei echte parallele Prozesse editieren denselben Stand; genau ein kompatibler Erfolg oder eindeutig definierte Konflikte. UIDVALIDITY zwischen SEARCH und Write, keine Mutation einer Ersatznachricht. |
| Idempotenz/No loss | Gleiche Message-ID mit anderem kompletten Body, gleichem 4-KiB-Präfix/Größe mit anderem Suffix; Source bleibt erhalten. Lost ACK nach realem erfolgreichen APPEND erzeugt keinen zweiten Draft/Move. |
| Audit vor Effekt | Audit/WAL unwritable/full vor allen effektiven Writes einschließlich Attachment-Sink; IMAP und Filesystem bleiben unverändert. Crash nach Effekt vor Outcome wird als uncertain rekonstruiert und nicht blind wiederholt. |
| Echte Lifecycle-Auslöser | Restart ohne `_test_run_recovery`, UTC-Tagwechsel ohne `_test_run_audit_rotation`; WAL/IMAP/Files beweisen Recovery und Retention. Kein Helper kann den Produktionspfad ersetzen. |
| Sink-Grenze | Zwei Caller, Symlink/Hardlink/rename race, gleicher Name mit kollidierendem Kurzsuffix, langer Extension, voller Datenträger; kein fremder Read, kein Outside-Write, keine fremde Überschreibung, keine Partial-Datei. |
| Diagnose/Trace | Sichtbare Canary-Werte in allen 30 Inputs/Outputs/Fehlern und verschachtelten Parts; redacted/off verbieten sämtliche Content-/Secret-Kopien, full bleibt auf expliziten Datenkanal begrenzt. |
| Grammatik/Zeit | Cc-only, Bcc-known/unknown, Displayname, Domain-Suffixfallen, Unicode-/IDNA-Varianten, Timestamp an beiden Seiten jeder Grenze, abweichendes Header-/INTERNALDATE, nicht-pushdownfähige Kriterien. |
| Ressourcen/Provider | Richtige Schema-, Quoten-, Timeout-, TLS-/OAuth-/Rotation- und Parserfehler mit gleichem sicheren Outcome; negative technische Fehler sind nicht einfach leere erfolgreiche Listen. |

**P2 – Evidenz und Freigabe:**

- Jedem gültigen Requirement eine ID, ADR-Stelle, Scenario/Examples-Zeile und gegebenenfalls gültige LIM zuordnen; ungeklärte Normen nicht durch Step-Defaults implementieren.
- Alle 34 Pending-Fälle fachlich bereinigen; neue unverbindliche Proposed-Funktionalität und nicht erfüllte Accepted-Garantie getrennt führen.
- Separates Step-Code-Audit für Guidelines §13.2 Prüfung 3 sowie ein Implementierungs-/Threat-Model-Audit durchführen. Dieser Bericht beansprucht beides nicht.
- Release-/Mock-/Provider-Abnahmebelege mit Commit und Datum archivieren; nicht nur Laufzahlen in README. Daten-/Policy-Migration muss unsichere Altpolicy-Kombinationen sichtbar zurückweisen.

### 5.7 Schlussentscheidung

Das aktuelle Berechtigungskonzept ist **nicht widerspruchsfrei**. Die beiden stärksten Gegenbeweise sind direkt spezifiziert: ein N-Treffer verändert eine erfolgreiche Suchantwort; eine unter N nicht lesbare Nachricht darf als Vollobjekt kopiert/verschoben werden. Gmail-Labelsemantik, mutable Grants, Draft-Header und gemeinsame Artefaktablage multiplizieren diese Brüche. Die Anzahl grüner Szenarien kann das nicht widerlegen, weil mehrere Szenarien genau die unsicheren Antworten verlangen und andere die entscheidenden Endzustände nicht prüfen.

Erforderlich ist ein **Neuschnitt des Autorisierungs-, Informationsfluss- und API-Vertrags mit Major-Version**, nicht zwingend ein Austausch sämtlicher Infrastruktur. Eine Ownerentscheidung zugunsten schwächerer Geheimhaltung ist möglich, muss aber den Begriff `NONE`, die behauptete Prompt-Injection-Isolation und den Multi-caller-/Deployment-Vertrag ehrlich ändern. Unveränderte starke Sicherheitsbehauptungen bei gleichzeitigem Beibehalten der hier belegten Orakel und Schreibpfade sind nicht freigabefähig.
