# LIM 0002: Gmail scenarios not runnable against current fixture

- **Status:** Accepted
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-04-21
- **Date approved:** 2026-04-21
- **Proposed by:** Claude (implementation agent)
- **Approved by:** Randy Nel Gupta
- **Related ADRs:** [ADR-0019](../adr/0019-gmail-label-semantics.md)
- **Related Guidelines:** BDD Guidelines §4.1 (Circular-Test Prohibition), §7.1 (Deterministic mocks), §6.1 (Isolation)

## Context

[ADR 0019] specifies that Gmail accounts are first-class providers
whose label semantics the server explicitly models: `X-GM-MSGID`,
`X-GM-LABELS`, the `canonical_all_mail_uid` exposed through `search`,
the Gmail-only `list_labels` tool, intra-account `move` implemented
as a label swap, and cross-account sagas deterministically fetched
from `[Gmail]/All Mail`.

The BDD suite encodes these requirements in
`bdd/features/providers/gmail_label_semantics.feature` (seven
scenarios).

The BDD test fixture is two Dovecot instances (`imap-a`, `imap-b`),
which implement standard IMAP and have no Gmail dialect. As a direct
consequence, every Gmail scenario currently asserts against fixture
behaviour the fixture cannot produce.

## Nature of the weakness

Concretely, the following Gmail properties are absent from the
fixture and cannot be added to Dovecot:

- **`X-GM-*` extensions** (`X-GM-MSGID`, `X-GM-LABELS`, `X-GM-THRID`)
  as first-class IMAP command parameters in FETCH, SEARCH, and STORE.
- **Label projection.** The same stored message cannot appear under
  multiple folder listings with distinct per-folder UIDs. Dovecot
  stores one file per folder; copying produces a distinct message
  with its own Message-ID handling, which breaks the single-object /
  multi-label semantics.
- **`[Gmail]/All Mail`** as the canonical deduplicated view with a
  persistent X-GM-MSGID to All-Mail-UID mapping.
- **Gmail-flavoured `MOVE`** (label swap, not physical move).
- **Gmail `EXPUNGE`** (detaches label, keeps object until
  `[Gmail]/Trash`).

A Dovecot-based fixture can *approximate* some of these through
shim layers, but the single-object / multi-label property is
structural: it cannot be faithfully shimmed because the storage
layer below is not shared.

The practical effect is that the seven Gmail scenarios either cannot
run at all, or — worse — pass against fixture behaviour that the
real Gmail IMAP dialect does not match, silently concealing adapter
bugs.

## Why the clean solution is not chosen (yet)

A clean solution exists: a dedicated Gmail-IMAP mock, validated
independently against real Gmail-aware clients. That solution is
approved and being built (see "Mitigations in place" below). The
limitation captures the **interim** state: at the moment this record
is written, the mock does not exist; the scenarios therefore have
no runnable target.

This is not an argument against the clean solution. It is an
acknowledgement that the clean solution is a multi-phase build and
that the interim weakness must be visible in the project's own
records rather than swept under the rug of "it will be fine when
the mock is done."

## Mitigations in place

The long-term plan is a dedicated, independently validated Gmail
IMAP mock at `bdd/mock-gmail/`:

1. **Subproject scaffolded.** `bdd/mock-gmail/` exists, is
   isolated from the BDD harness and from the server, has its own
   `pyproject.toml`, and carries the design rationale in its README.
2. **Validation strategy committed.** The mock will be validated
   against two independent Gmail-aware clients (`mbsync` as primary,
   `imapsync` as secondary) before it is used as a test fixture.
   A mock that satisfies both clients in their intended workloads
   has *independently corroborated* behaviour, not merely self-
   corroborated behaviour.
3. **Three-phase build plan.** Phase 1 captures command traces from
   the clients against real Gmail, Phase 2 implements the mock in
   layers against those traces, Phase 3 integrates the mock as a
   third `docker-compose` service and wires the seven Gmail
   scenarios to target it. Each phase is a tracked task in the
   project's task list.
4. **Out-of-scope paths identified up front.** Pathways the two
   validators do not exercise — but the server's Gmail adapter does
   — will not be silently validated against our own mock. Each such
   path becomes its own subsidiary Limitation Record, so the residual
   risk is visible at the right granularity.
5. **Interim suppression (explicit, not silent).** Until Phase 3
   lands, the seven scenarios in `gmail_label_semantics.feature`
   are tagged `@pending:LIM-0002` and excluded from CI runs. They
   remain in the repository and continue to be read by reviewers
   as specification; they simply do not run. Running the suite
   without the tag-filter yields no false positives from Gmail
   scenarios passing against the wrong fixture.
6. **Error-path analysis marked accurately.** The entries L14.1
   through L14.7 in `docs/error_path_analysis.md` — previously shown
   as `covered` — are downgraded to `covered_by_LIM-0002` until the
   mock is in place and the scenarios pass. This keeps the
   analysis's count of genuine coverage honest.

## Residual risk

Even after Phase 3 completes and all seven scenarios run green
against the mock, two categories of risk persist:

- **Operations the validators never exercise.** If the server's
  Gmail adapter uses a sequence that neither `mbsync` nor
  `imapsync` uses in their typical workloads — for example, a
  cross-account saga that specifically FETCHes from
  `[Gmail]/All Mail` with a particular UID sequence — the mock's
  response for that sequence has only *our own* assumptions to
  lean on. This corresponds directly to the spirit of LIM-0001
  (self-corroboration) applied to the fixture layer.
- **Gmail behavioural drift.** Gmail changes its IMAP dialect
  over time. A mock that correctly mirrored Gmail a year ago may
  silently diverge from current Gmail. The validation step
  (`mbsync` + `imapsync` ran successfully against the mock) is
  valid at the time of capture, not in perpetuity. Periodic
  re-capture is the only honest mitigation.

Both residuals will, at minimum, generate their own subsidiary
Limitation Records when they materialise, rather than being
absorbed into this record.

## Triggers for revisit

- **Mock V1 completes Phase 3.** At that point this record must be
  re-examined: remaining residuals get subsidiary records, and LIM-
  0002's status advances to `Mitigated` or `Resolved` depending on
  coverage.
- **A production or staging incident** is traced to a Gmail-adapter
  behaviour that the scenarios should have caught but the scenario
  was suppressed under this limitation.
- **`mbsync` or `imapsync` becomes unmaintained** or drops Gmail-
  specific code paths; the validation strategy then needs a
  replacement.
- **Gmail deprecates or changes a relevant extension** (e.g.
  `X-GM-MSGID` semantics shift): re-capture required; trace
  divergence against the existing mock is reviewed.
- **18 months since the last validator run against real Gmail**,
  regardless of other triggers. Drift is real; time-based review
  prevents quiet decay.

## References

- [ADR 0019] — Gmail label semantics in the server.
- `bdd/mock-gmail/README.md` — the dedicated subproject's design.
- `bdd/features/providers/gmail_label_semantics.feature` — the
  seven scenarios currently suppressed under this limitation.
- `docs/error_path_analysis.md` — error-path table, rows L14.*
  downgraded to `covered_by_LIM-0002`.
- Gmail IMAP extensions:
  <https://developers.google.com/gmail/imap/imap-extensions>
- `mbsync` / isync: <https://isync.sourceforge.io/>
- `imapsync`: <https://imapsync.lamiral.info/>

[ADR 0019]: ../adr/0019-gmail-label-semantics.md
