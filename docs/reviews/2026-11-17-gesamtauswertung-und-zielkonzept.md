# Gesamtauswertung & Zielkonzept `imap-mcp` 3.0

- **Datum:** 2026-11-17
- **Quellen (unabhängige Einzelprüfungen):**
  1. [`2026-11-17-vollpruefung-opus.md`](2026-11-17-vollpruefung-opus.md) — `spec-auditor-opus` (Claude Opus 5.5 High, nur `.md`/`.feature`): **45 Befunde** (12 KRITISCH · 16 HOCH · 13 MITTEL · 4 NIEDRIG)
  2. [`2026-11-17-vollpruefung-gpt6.md`](2026-11-17-vollpruefung-gpt6.md) — `spec-auditor-gpt6` (GPT-6 Astra Max, nur `.md`/`.feature`): **37 Befunde** (7 KRITISCH · 22 HOCH · 7 MITTEL · 1 NIEDRIG)
  3. [`2026-11-17-step-audit-sonnet.md`](2026-11-17-step-audit-sonnet.md) — `step-pruefer-sonnet` (Claude Sonnet 5.5, `.feature` + `bdd/**`, ohne `server/`): **88 Befunde** (5 BLOCKER · 33 HOCH · 33 MITTEL · 17 NIEDRIG)

---

## 1. Übergreifendes Gesamturteil

Alle drei unabhängigen Prüfungen kommen zum selben Befund:

1. **Das aktuelle Berechtigungskonzept ist im Kern widprüchlich und als Sicherheitsgrenze gebrochen.** Der reale Vorfall in `[Gmail]/Sent Mail` / `[Gmail]/All Mail` war kein bloßer Konfigurationsfehler, sondern das unmittelbare Ergebnis widersprüchlicher Architekturentscheidungen, die in den `.feature`-Dateien sogar als Soll-Verhalten festgeschrieben sind (`default_deny.feature:64-71`, `intra_account_move.feature:27-44`).
2. **Punktuelle Reparaturen (`to_domain` ergänzen, einzelne `if`-Abfragen nachziehen) reichen nicht.** Die Sicherheitsbrüche entstehen aus der *Komposition* von:
   - queryabhängigen Hidden-Zählern vor der Policy-Prüfung,
   - ordnergebundenen Policies auf mehrfach gelabelten Nachrichtenobjekten,
   - leserechtsunabhängigen Schreib- und Kopier-Capabilities,
   - vom Agenten selbst veränderbaren Grant-Merkmalen (`\Flagged`, Labels, Draft-Header, Anhänge) und
   - ungebundenen UIDs/Handles sowie einem gemeinsamen Dateisystem-Sink.
3. **Der technische Unterbau bleibt nutzbar, der Vertrag braucht einen Major-Neuschnitt (`Tool-Set 3.0.0`).** Python/MCP-Grundstruktur, striktes Default-Deny, endliche Prädikatgrammatik (ohne Regex/Code), Provider-Adapter, SQLite-WAL und Hash-verkettetes Audit bilden ein tragfähiges Fundament, sobald Autorisierungssemantik, API-Schnitt, Governance-Register und BDD-Suite neu geordnet sind.

---

## 2. Konsolidierte Sicherheitsbrüche (Kreuzabgleich Opus × GPT-6)

| # | Sicherheitsbruch | Opus | GPT-6 | Mechanismus & Konsequenz |
|---|---|---|---|---|
| **K-1** | **Adaptives Prädikat- & Zähl-Orakel über `NONE` und höhere Sichtstufen** | S-01, S-03, S-16 | S-01, S-08 | `search`, `list_messages` und `bulk_mark_*` zählen `matched_total`, `filtered_out`, `outside_scope_total`, `marked_count`/`tagged_count` **vor** dem PDP-Filter. Mit `to_contains`, `subject_contains`, `from_domain`, `size_gt`/`size_lt`, `since`/`before`, `has_attachment` lassen sich gesperrte (`NONE`) Mails sowie Felder oberhalb der eigenen Stufe (z. B. `subject` bei `METADATA`) Zeichen für Zeichen ausforschen — bei `ALLOW/OK` im Audit. `matched_rule_index` verrät zusätzlich die interne Regelnummer. |
| **K-2** | **Existenz- & Zustands-Orakel über Fehlercodes** | S-02 | S-09 | `uid_not_found` vs. `sender_not_whitelisted` vs. `visibility_below_*` vs. `attachment_not_found` verrät Existenz, UID-Belegung und Regelklassifikation selbst in komplett gesperrten Ordnern. |
| **K-3** | **Volltext-Exfiltration durch Umlagern (`copy`/`move`/Label-Swap/Saga)** | S-04, S-05 | S-02 | Schreib-Capabilities hängen nur am Ordner (`move` auf `NONE` ist spezifiziert; `copy` verlangt **nur** `accept_incoming` am Ziel und kein Quell-Recht!). Ein Caller kopiert/verschiebt eine `NONE`-Mail in einen offenen Ordner (`Drafts`, Ablage, Zweitkonto via `[Gmail]/All Mail`) und liest sie dort mit `FULL`. Deposit-only-Archive aus ADR 0005 sind damit wirkungslos. |
| **K-4** | **Selbst-Eskalation über schreibbare Policy-Eingaben** | S-06 | S-03 | `mark_tagged` darf `\Flagged` (und auf Gmail beliebige Labels) setzen → aktiviert `flagged: true → FULL` oder offene Label-Policies. `update_draft` (unter `NONE` erlaubt!) ändert `subject`/`to`, `add/delete_attachment` ändert `has_attachment`/`size_bytes` und schaltet darüber höhere Grants frei. |
| **K-5** | **Multi-Homed-Objekte & globale Gmail-Seiteneffekte** | S-08 | S-10 | In Gmail wählt der Caller über den Ordnerpfad faktisch das **Maximum** der Rechte über alle Labels einer Nachricht. Umgekehrt entfernt ein `move` nach `[Gmail]/Trash` oder `\Spam` die Mail global aus **allen** Labels (bricht Archiv-Unveränderlichkeit und umgeht `move_out` anderer Labels). `canonical_all_mail_uid` leakt Daten aus verstecktem `All Mail`. |
| **K-6** | **Draft-Übernahme, Reply-Exfiltration & Belegfälschung** | G-06, S-09, S-10, S-11 | S-11, S-12 | `X-SC-Draft-Id`/`Rev` ist ein unsignierter Klartext-Header (geht beim Versand an externe Empfänger, kein Caller-Ownership-Beweis). `update_draft` unter `NONE` erlaubt `bcc: angreifer@…`. `create_reply_draft` ist gegen per-Message-Caps in offenen Ordnern und MIME-Part-Leaks ins Zitat nicht abgesichert. `add/replace/delete_attachment` schreibt empfangene Mails ohne ADR um (EXPUNGE des Originals, Belegfälschung). |
| **K-7** | **Fremd-Transaktionen, gemeinsamer Attachment-Sink & Symlink-Escape** | S-07, S-12, S-18 | S-04, S-05, S-06, S-16, S-23 | `get_transaction_status` prüft `caller_id` nicht und triggert fremde IMAP-Writes. Der gemeinsame Attachment-Sink hebt die Caller-Trennung auf, nutzt ein kollisionsanfälliges 32-Bit-MD5-Suffix (~50 % Kollision ab ~77.000 Dateien gleichen Namens) und verlangt kein `O_NOFOLLOW`/`O_EXCL` (Symlink im geteilten Sink kann `policies/*.yaml` überschreiben). Im Same-User-Quickstart kann ein Agent mit Dateirechten seine Policy direkt ändern (LIM 0019). |
| **K-8** | **Datenverlust- & Concurrency-Fallen in Saga, Fallback, Zeitfilter & Config-Reload** | S-13, S-20 | S-07, S-13, S-17, S-22 | (1) `ADR 0008` löscht die Quellmail bei einfachem Message-ID-Treffer bzw. 4-KiB-Präfix-Match trotz abweichendem Rest-Body. (2) Intra-Account-Fallback ohne `MOVE` ruft unqualifiziertes `EXPUNGE` auf und löscht fremde `\Deleted`-Mails im Ordner mit. (3) `ADR 0024` rundet `older_than=60s` fehlerhaft auf `BEFORE <heute>` und verschluckt taggleiche Treffer. (4) Öffentliche `(folder, uid)`-Referenzen ohne `UIDVALIDITY`, fehlendes CAS bei parallelem `update_draft` und mehrteiliges Einlesen über Kubernetes-`..data`-Symlinks erzeugen Race-Conditions. |
| **K-9** | **Audit-Blindstellen, WAL-Leaks & Richtungs-Asymmetrie** | S-13, S-14, S-15 | S-14, S-15, S-18, S-19, S-20 | Audit schreibt erst *nach* dem IMAP-Effekt; `saved_to_absolute` loggt Dateinamen trotz No-Content-Leak-Regel; WAL hält laut Feature volle RFC822-Bytes entgegen `ADR 0007`; `IMAP_MCP_FAKE_NOW_*` ist nicht an `TEST_MODE` gebunden; `gmail.readonly` existiert für Gmail-IMAP nicht; Outbound-Regeln fehlen `to_domain`, `cc`/`bcc` und eigene Draft→Sent-Provenienz. |

---

## 3. Konsolidierte Governance-, Spezifikations- & Step-Audit-Lage

Warum wurden diese Brüche trotz hunderten grünen BDD-Szenarien nicht gefangen?

1. **Unsicheres Verhalten ist als Soll-Spezifikation codiert:** `default_deny.feature:64-71` verlangt das `NONE`-Suchorakel, `intra_account_move.feature:27-44` verlangt blinde Moves auf `NONE`, `whitelist_folder_mode.feature:52` verlangt `matched_rule_index`.
2. **5 BLOCKER und 33 HOCH-Befunde im Python-Step-Code (`step-pruefer-sonnet`):**
   - **No-op-Steps (`B-01`–`B-05`):** Der Taxonomie-Sweep in `reason_code_contract.feature`, die 3 Tool-Metadaten-Szenarien in `mcp_tool_discovery.feature`, der Pool-Drain in `policy_reload.feature`, der Mock-Gmail-Verbindungszähler auf Dovecot in `bulk_mark_tagged.feature` und der Prozess-Env-Check in `secret_store_backends.feature` können konstruktionsbedingt nicht fehlschlagen.
   - **Fehlende oder unehrliche Zweitkanäle (`H-01`, `H-02`, `H-29`):** `bulk_mark_seen` prüft nie `\Seen` per IMAP; `add/replace/delete_attachment` liest über denselben MCP-Server zurück und biegt im Glue-Code heimlich die alte UID auf `new_uid` um; `fetch_attachment` vergleicht die Sink-Datei nur gegen den vom Server gemeldeten Hash statt gegen die gesäten Anhang-Bytes.
   - **Test-Hooks & Wunschdenken-Mocks statt Produktionspfade (`Opus G-02`, `GPT-6 G-11`, `Sonnet H-03/H-13/H-28/H-32/H-33`):** Audit-Retention und Startup-Recovery laufen in Produktion überhaupt nicht (nur über `_test_run_*`-Tools, wobei der `Then`-Step selbst in einer Schleife `_test_run_recovery` aufruft); Proxy-`delay`/`close` leiten `APPEND` nie weiter; `mock-gmail` prüft keine Passwörter/Zustände und spricht kein XOAUTH2; `mock-oauth` besteht bei den Steuerfunktionen aus leeren `pass`-Stubs.
3. **Formale Triaden-Brüche (`Opus G-01..G-16`, `GPT-6 G-01..G-12`):**
   - Kein einziger archivierter Spec-Audit-Zyklus unter `docs/spec-audit/` (trotz Behauptung in `LIM-0001`).
   - `ADR 0029–0032` stehen auf `Proposed`, sind aber ausgelieferte Kern-API; 7 `Proposed`-LIMs liegen ungenehmigt im Bestand; alle 23 LIMs ohne `Scope`, alle 10 `Resolved`-LIMs ohne `Resolution commit`, Nummernkollision `LIM-0011`, fehlende Freigaben bei `LIM-0010`/`0011`.
   - Aktive Feature-Files widersprechen `Accepted`-ADRs (`error_type`/`imap_response`/`missing_capability` vs. `ADR 0027`, ignorierte Extra-Args vs. `ADR 0026`, mindestens 15 undeklarierte Reason-Codes und ~8 Error-Types außerhalb der „geschlossenen“ Tabellen).
   - 34 expandierte `@pending`-Szenarien ohne LIM, darunter syntaktisch verbotene `whitelist + default: FULL/ENVELOPE/BODY`-Policies in `startup_saga_recovery.feature` und `projected_config_reload.feature`.

---

## 4. Gemeinsamer Zielentwurf für `imap-mcp` 3.0

Beide Architektur-Prüfer schlagen im Kern dasselbe saubere Zielmodell vor:

### 4.1 Zwölf harte Sicherheitsinvarianten
1. **Objekt- statt Pfadschutz (`M-1`):** Schutzobjekt ist die logische Nachricht (bei Gmail objektweit über `X-GM-MSGID`, auf Standard-IMAP gebunden an `(account, mailbox, uidvalidity, uid)`). Der Zugriffsweg erhöht niemals Rechte; Sicherheitsrestriktionen auf Labels/Ordnern wirken **deny/cap-overrides** (Minimum).
2. **Strikte Non-Interference für `NONE` & Feldstufen (`N-1`):** `NONE`-Nachrichten beeinflussen weder Rückgabefelder noch Zähler (`matched_total`/`filtered_out`/`outside_scope_total`/`hidden_*_count` entfallen bzw. zählen ausschließlich autorisiert sichtbare Objekte; Transparenz erfolgt statisch aus der Policy als `completeness: "complete" | "policy_may_hide" | "window_limited"`). Suchprädikate dürfen nur Felder abfragen, deren Stufe für das Objekt freigegeben ist.
3. **Einheitliches Außenbild bei Nichtberechtigung (`N-2`):** Nicht existierende, fremde oder unter `METADATA` liegende Referenzen liefern identisch `not_found` (keine Unterscheidung `uid_not_found` vs. `sender_not_whitelisted` an den Caller; Detailgrund nur im Operator-Audit). `matched_rule_index` entfällt.
4. **Autorisierung des gesamten Zustandsdeltas & `Write ≤ Read` (`W-1`, `W-2`):** Jede Schreiboperation verlangt eine explizite Mindest-Lesestufe pro betroffener Nachricht (`mark_seen ≥ METADATA`, `mark_tagged/move ≥ ENVELOPE`, `create_reply_draft ≥ BODY` pro Quellmail, `copy` verlangt explizites Quell-Exportrecht `copy_out` + `ENVELOPE` bzw. `FULL` bei Anhängen) **und** Fluss-Monotonie (`eff_target(m) ≤ eff_source(m)`, kein „Read-up by Relocation“). Globale Operationen wie Gmail-Trash/-Spam prüfen alle betroffenen Mitgliedschaften.
5. **Kein Write-up eigener Grants (`W-3`):** Vom Agenten veränderbare Zustände (`\Flagged`, Keywords, Labels, Draft-Header, Anhangsanzahl/Größe) dürfen **niemals** positive `grant`-Regeln auslösen (Loader weist solche Kombinationen ab, oder das Flag ist für den Agenten schreibgeschützt).
6. **Kein Hard-Delete & keine Belegfälschung (`W-4`):** Empfangene Nachrichten sind unveränderlich (`add/replace/delete_attachment` auf empfangener Mail entfällt; Anhang-Bearbeitung gibt es nur auf eigenen Entwürfen). Kein unqualifiziertes `EXPUNGE`.
7. **Authentische Provenienz & Lifecycle für eigene Artefakte (`P-1`):** Serverseitiges Provenienz-Register (WAL/Store + optionaler HMAC), gebunden an `caller_id` und `Message-ID`/Content-Hash. Nur nachweislich eigene Entwürfe im `\Drafts`-Ordner sind per `update_draft`/`discard_draft` änderbar; optional macht `provenance: own` eigene Entwürfe auch nach Versand durch den Menschen in `Sent Mail` kontrolliert lesbar (löst den Draft→Sent-Bruch sauber ohne Substring-Regeln).
8. **Richtungs- & rollenbewusste Regelgrammatik (`R-1`):** Symmetrische Adress-/Domain-Matcher auf geparsten RFC-5322-Adressen (`sender.address_in`, `sender.domain_in`, `recipients.{to,cc,bcc,any}.{address_in,domain_in}` mit Quantoren `any`/`all`, optional `draft_recipients`-Allowlist gegen Bcc-Exfiltration); einheitliche, unmanipulierbare Zeitbasis (`INTERNALDATE` = `received_at`, korrekte Rundungsmathematik).
9. **Gebundene, epochensichere Referenzen & echtes CAS (`C-1`):** Öffentliche Referenzen (`message_ref`, `draft_ref`, `tx_id`, `artifact_ref`) sind caller-gebunden und kapseln `UIDVALIDITY`/Version-Witness (`if_match`); parallele Edits werden instanzübergreifend im WAL/Lock serialisiert. `get_transaction_status` ist rein lesend und caller-geprüft.
10. **Sichere Ziel-Verifikation vor Source-Delete (`I-1`):** Saga-Dedup löscht eine Quellmail nur bei vollständigem Byte-/SHA-256-Nachweis der Zielinstanz, nie aufgrund bloßer Message-ID oder eines 4-KiB-Präfixes; unklare Ausgänge nach Verbindungsabbruch enden in `uncertain`/`needs_operator`.
11. **Durable Intent vor Seiteneffekt & kanalsichere Sanitization (`A-1`):** Jeder Schreibvorgang schreibt vorab einen `intent`-Eintrag (schlägt das fehl, passiert nichts). Alle 30 Tools, Fehler (`-32602`, Exceptions), Traces und Audit-Felder unterliegen einer strikten Allowlist (keine Klartext-Dateinamen in `saved_to_absolute`, keine rohen IMAP-Echos an den Caller, HMAC statt ungesalzenem SHA-256 für Suchkriterien).
12. **Isolierte Attachment-Auslieferung & ehrliche Deployment-Grenze (`D-1`):** Kein gemeinsamer beschreibbarer Sink; pro Caller isolierte Bereitstellung mit `openat` + `O_CREAT|O_EXCL|O_NOFOLLOW` und zufälliger Artefakt-ID (volle SHA-256-Prüfsumme separat). Mehr-Caller-HTTP wird technisch abgewiesen, solange Isolation nicht vollständig aktiv ist; Same-User-stdio wird ehrlich als Komfortmodus ohne Schutz gegen Shell-fähige Agenten deklariert, getrennter OS-User/Container als Sicherheitsstandard.

---

## 5. Gebündelte Owner-Entscheidungen vor Spezifikations-Neubau

Beide Berichte benennen zusammen 12 konkrete Richtungsentscheidungen, die vor dem Schreiben der 3.0-ADRs und `.feature`-Dateien durch den Owner festgelegt werden müssen:

1. **Schnitt & Versionierung:** Hard Cut auf **Tool-Set `3.0.0`** (empfohlen von beiden Prüfern) statt inkrementellem 2.x-Flickwerk?
2. **Strikte `NONE`-Nichtbeobachtbarkeit (Hidden Counts):** Alle datenabhängigen Hidden-/Outside-/Raw-Folder-Counts für Caller streichen und durch statische Policy-Transparenz (`completeness: "complete" | "policy_may_hide" | "window_limited"`) ersetzen (empfohlen) — oder pro Ordner optionales Opt-in `disclose_hidden_counts`?
3. **Gmail-Label-Komposition:** Trennung zwischen reinen Navigationsansichten (`INBOX`, `Sent Mail`, `All Mail`) und objektweiten Sicherheitsregeln mit **Deny/Cap-Overrides** (kein Rechtegewinn durch Ordnerwahl; Trash/Spam prüft alle betroffenen Labels)?
4. **Umgang mit `\Flagged` („Sternchen“-Workflow):** Soll `flagged: true` weiterhin als **menschliches Freigabesignal** dienen? Falls ja, muss dem Agenten das Schreiben von `\Flagged` (und sicherheitsrelevanten Labels) in `mark_tagged`/`bulk_mark_tagged` komplett entzogen werden (empfohlen, damit dein Gmail-Stern-Workflow aus Bild 1 sicher bleibt!) — oder soll `flagged` nur noch in `cap`-Regeln erlaubt sein?
5. **Eigene Entwürfe nach Versand (`Drafts → Sent Mail`):** Soll der Server eigene, signierte/registrierte Entwürfe per Policy-Option (z. B. `grant_own_sent: FULL` oder `provenance: own`) auch nach dem Versand in `Sent Mail` für denselben Caller lesbar halten dürfen?
6. **Ausgehende Empfänger-Leitplanke (`draft_recipients`):** Sollen Ordner/Policies optional oder verpflichtend einschränken können, an welche Empfänger/Domains ein Agent Entwürfe adressieren (`to`/`cc`/`bcc`) darf?
7. **Schicksal von `add/replace/delete_attachment` (`modify_message`):** Umschreiben empfangener Nachrichten **komplett abschaffen** (empfohlen) und Anhang-Operationen ausschließlich für eigene Entwürfe (`Drafts`) erlauben?
8. **Transfer-Rechte (`copy` / `move`):** Bestätigung von `Write ≤ Read` + Fluss-Monotonie (`eff_target ≤ eff_source`) + explizite Quell-Capability `copy_out` für `copy` (damit Deposit-only-Archive wirklich einbahnig sind)?
9. **Attachment-Auslieferung:** Pro Caller isoliertes, serverseitig mit `O_NOFOLLOW|O_EXCL` geschriebenes Artefaktverzeichnis (für den Agenten read-only!) mit opaker UUID statt vorhersagbarem Namen?
10. **Mehr-Caller & lokale Deployment-Grenze:** Mehr-Caller pro Server-Instanz hart beim Start verweigern (1 Caller = 1 Instanz) und im README getrennten OS-User / Container als einzigen sicherheitswirksamen Modus dokumentieren?
11. **OAuth-Realität bei Gmail (`ADR 0009`):** Schein-Verteidigung `gmail.readonly` für IMAP streichen und ehrlich dokumentieren, dass Gmail-IMAP technisch immer den vollen `https://mail.google.com/`-Scope verlangt und die Policy daher die einzige Schutzschicht ist?
12. **Governance-Bereinigung:** Alle offenen `Proposed`-ADRs (0029–0032) und `Proposed`-LIMs (0012, 0017–0022) im Rahmen des 3.0-Schnitts sauber auflösen/ablösen, `LIM-0011` entkollidieren, maschinenlesbare Code-Registry einführen und alle 5 BLOCKER / 33 HOCH aus dem Step-Audit beseitigen?
