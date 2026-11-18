# Gherkin- und Spezifikationsabnahme `imap-mcp` 3.0.0 – Delta-Check (Runde 7)

- **Prüfer:** Gherkin-Prüfinstanz (Claude Opus 5.5, High)
- **Datum:** 2026-11-18
- **Bezug:** [`2026-11-18-gherkin-abnahme-opus-runde6.md`](2026-11-18-gherkin-abnahme-opus-runde6.md). Urteil dort: FREIGEGEBEN.
- **Umfang:** `bdd/features/auth/secret_store_backends.feature:114-126`. Gelesen habe ich nur `.feature` und `.md`.

---

## 1. Prüfung

| Fundstelle | Ergebnis |
|---|---|
| `secret_store_backends.feature:121-122` | ✓ Nach dem erfolgreichen Handshake (`:119-120`) folgt jetzt ein echter Meta-Aufruf `get_caller_identity` mit geprüftem Ergebnis `caller_id = "invoice-agent"`. Das Muster ist identisch mit dem `file_dir`-Szenario `:50-51`. Die Trennung von When und Then ist korrekt (§12). |
| Positivkontrolle (§4.3) | ✓ Laut ADR 0048 §1 schreibt jeder Read- oder Meta-Aufruf einen `phase="single"`-Record. Die Abwesenheitsprüfung `:123-126` (`correct-horse-battery`, `test-passphrase-correct`) läuft damit gegen eine Audit-Datei, die nachweislich einen Eintrag eines authentifizierten, gpg-entschlüsselten Aufrufs enthält. Das ist keine triviale Leermenge mehr. Der Eintrag wird über die Antwort belegt, nicht direkt im Audit; siehe den Hinweis unten. |
| §4.5-Header `:8-19` | ✓ Unverändert 10 = 10. Es wurden nur Schritte ergänzt, kein neues Szenario. |

**Hinweis (NIEDRIG, optional):** Wer die Positivkontrolle direkt im Audit statt indirekt über ADR 0048 belegen möchte, ergänzt nach `:122`:
`And the audit log contains an entry with tool "get_caller_identity", decision "ALLOW", reason "authenticated"`

---

## 2. Urteil

**FREIGEGEBEN: 0 BLOCKER, 0 HOCH, 0 MITTEL.**
