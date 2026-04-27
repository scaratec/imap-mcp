# Spec-Audit: Verifikation der Implementierungstreue

Operationalisierung von [BDD Guidelines §13][gl] für `imap-mcp`. Jede LIM
nennt das Spec-Audit als Mitigation 5; dieses Verzeichnis liefert die
Vorlage und das Archiv der durchgeführten Zyklen.

## Wann läuft ein Zyklus

Ein Zyklus ist erforderlich, **bevor** eine der folgenden Aktionen
abgeschlossen wird:

- Ein Major-Version-Tag der Server-Komponente (`1.x.0` → `2.0.0`).
- Eine Pull-Request-Serie, die ≥ 3 neue Szenarien hinzufügt **und**
  Produktivcode für eine ihrer Antwort­logiken anfasst.
- Die Schließung einer must-resolve LIM, deren Mitigations das
  Spec-Audit explizit nennen (alle bisher angelegten LIMs tun das).

Außerhalb dieser Trigger ist ein Zyklus optional, aber empfohlen, wenn
der Hauptbetreuer eine Audit-Kadenz aus operationellen Gründen
beschließt (z. B. monatlich).

## Wer führt den Zyklus durch

Der Audit wird von einem **anderen Agenten** durchgeführt als dem
implementierenden — nicht weil ein einzelner Agent es technisch nicht
könnte, sondern weil Unabhängigkeit die Erkenntnisqualität erhöht.
Konkret:

- Implementierungs-Agent schreibt Produktiv- und Step-Code, lässt die
  BDD-Suite grün laufen und schließt sein Pull-Request.
- Audit-Agent erhält ausschließlich Zugriff auf
  - `bdd/features/**/*.feature`
  - `bdd/features/steps/**/*.py`
  - `bdd/support/**/*.py`
  - `docs/adr/**/*.md`
  - `docs/limitations/**/*.md`
  - `docs/error_path_analysis.md`
- Audit-Agent **liest den Produktivcode bewusst nicht**. Die Prüfung
  fragt: "Verifiziert der Step-Code das Verhalten ehrlich?" — nicht:
  "Macht der Produktivcode das Richtige?". Wer den Produktivcode liest,
  rationalisiert ihn als korrekt, statt die Lücke zwischen Spezifikation
  und Verifikation zu erkennen.

Der Mensch (Hauptbetreuer) bewertet die Findings und entscheidet, ob
Korrektur, Erfassung als neue LIM oder Verwerfen.

## Wie ein Zyklus angelegt wird

1. Datei `docs/spec-audit/YYYY-MM-DD-cycle-N.md` aus
   `TEMPLATE.md` kopieren. `N` zählt monoton hoch über alle Zyklen.
2. Header ausfüllen (Audit-Agent, Trigger, einbezogener Commit-Hash).
3. Die drei Prüfungen aus §13.2 der Reihe nach durcharbeiten:
   - Prüfung 1 — Persistence-Validierung
   - Prüfung 2 — Herkunftsanalyse für Then-Werte
   - Prüfung 3 — Daten-Symmetrie zwischen Szenario und Step-Code
   Plus den **reason-code-spezifischen Prüfauftrag** (siehe LIM-0001
   §Mitigations §5).
4. Jedes Finding wird einzeln nummeriert und mit Schwere klassifiziert.
5. Der Hauptbetreuer trägt am Ende eine Bewertung pro Finding ein.

## Schwere-Klassen

- **blocking** — Spezifikation ist nicht ehrlich verifiziert; das
  zugehörige Szenario darf nicht als Coverage-Beleg gelten, bis das
  Finding aufgelöst ist.
- **must-fix** — Das Finding ist legitim und sollte vor dem nächsten
  Major-Tag aufgelöst werden, blockiert aber nicht das aktuelle
  Release.
- **accept** — Der Hauptbetreuer akzeptiert die Lücke (z. B. weil das
  Szenario bewusst nur Statuscode prüft); das Finding wird mit
  Begründung dokumentiert und ggf. als LIM erfasst.
- **invalid** — Der Audit-Agent hat sich geirrt; Notiz wird
  protokolliert, das Finding fließt in den Trainings-Output für die
  nächste Iteration.

## Beziehung zu LIMs

Ein Spec-Audit-Finding kann zu einer neuen LIM führen, **muss aber
nicht**. Die Faustregel:

- Findings mit Schwere `accept`, deren Annahme nicht für jede neue
  Iteration neu zu treffen ist, **werden** als LIM erfasst.
- Findings mit Schwere `must-fix` oder `blocking` werden **nicht** als
  LIM erfasst — sie sind Arbeit, keine akzeptierte Schuld.

Die `Triggers for revisit`-Sektion jeder LIM zählt:
"Spec audit findings ≥ N attributable to this limitation across two
consecutive cycles" als Auflösungstrigger. Das stützt sich auf das
Audit-Archiv hier.

## Format der Archivdateien

Jeder Zyklus ist eine eigenständige Markdown-Datei mit dem Schema aus
`TEMPLATE.md`. Die Dateien sind unveränderlich nach Abschluss
(append-only); spätere Korrekturen entstehen als neue Zyklen, die auf
den vorherigen verweisen. Das spiegelt die Sliding-Window-Eigenschaft
der "≥ N findings across two cycles"-Trigger.

[gl]: ../../../privat/burn-your-code/BDD_GUIDELINES_v1.8.0_DE.md
