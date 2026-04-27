# mock-gmail — Gmail IMAP protocol mock

A test fixture that speaks enough of Gmail's dialect of IMAP that an
unmodified Gmail-aware client cannot tell it apart from the real
thing, within a deliberately bounded set of operations.

The mock exists to close an honesty gap in the BDD suite: the server's
Gmail adapter (see [ADR 0019](../../docs/adr/0019-gmail-label-semantics.md))
cannot be verified against our Dovecot fixture, because Dovecot is a
standard IMAP server and Gmail is not. The seven scenarios in
`../features/providers/gmail_label_semantics.feature` would otherwise
live on paper only.

The accepted technical debt that motivates this subproject is recorded
in [LIM-0002](../../docs/limitations/0002-gmail-scenarios-not-runnable.md).

## What this mock is

- A Python IMAP server, built from scratch, that implements:
  - RFC 3501 IMAPv4rev1 to the extent required,
  - Gmail's private extensions `X-GM-MSGID`, `X-GM-LABELS`, `X-GM-THRID`,
  - Gmail's folder projection of labels (a message with labels
    `[INBOX, Rechnungen]` appears in both the `INBOX` and `Rechnungen`
    folder listings, with separate per-folder UIDs, and exactly once
    in `[Gmail]/All Mail` with a canonical UID),
  - Gmail's `MOVE` semantics as a label swap (remove source label,
    add target label; the underlying object is not relocated),
  - Gmail's `EXPUNGE` semantics (removes the label, not the message;
    only `MOVE` into `[Gmail]/Trash` truly detaches the object),
  - the `[Gmail]/*` special-use folders (`All Mail`, `Drafts`, `Sent
    Mail`, `Spam`, `Starred`, `Trash`) with their respective semantics.

## What this mock is not

- It is not a faithful re-implementation of Gmail. It implements
  only the subset that the two reference clients (below) exercise,
  plus the specific additional calls the `imap-mcp` server's own
  Gmail adapter issues. Anything beyond that is out of scope until
  explicitly identified as a gap.
- It does not speak SMTP, Gmail REST API, IDLE push at scale, search
  across accounts, or any of Gmail's non-IMAP surfaces.
- It does not emulate Gmail's rate limits, throttling, or error
  classes beyond what is necessary for the test scenarios.

## Why a separate subproject

Three boundaries make this a distinct module:

1. **Isolation from the BDD harness.** The harness under `../`
   imports nothing from here. The mock is reached over the wire on
   a configured host:port.
2. **Isolation from the server.** Likewise, no cross-imports with
   `../../server/`. The mock does not share types, parsers, or
   constants with the code under test — otherwise a bug that affects
   both would not be caught.
3. **Own dependency set.** The mock's dependencies (`anyio`,
   `pydantic`) are narrow and unrelated to either the BDD harness
   dependencies or the server dependencies. Own `pyproject.toml`,
   own `.venv`.

## Validation strategy — the key property of this subproject

A mock built purely to our own understanding of Gmail would reinforce
whatever blind spots we have. To avoid that, the mock is **validated
against two independent Gmail-aware clients** that are already in
production use against the real Gmail:

- **`mbsync`** (isync, C) — primary validator. Protocol-level
  behaviour, clean IMAP command traces, strong `[Gmail]/All Mail`
  deduplication.
- **`imapsync`** (Perl) — secondary validator. Gmail-specific sync
  matrix: label collision handling, label preservation across moves,
  multi-label messages.

If **both** clients run their intended workloads against the mock
without protocol errors, semantic mismatches, or silently wrong
outcomes, the mock has *independently corroborated approximation* of
Gmail — it has not merely been built to our expectations.

Pathways that neither client exercises are explicitly out of the
validation envelope and are tracked in a subsidiary Limitation Record
when and if they matter for `imap-mcp`'s Gmail adapter.

## Phased approach

The subproject is delivered in three phases; each phase is a task in
the project tracker.

### Phase 1 — Command-trace capture

Both validator clients are run against a real Gmail test account in
verbose mode (`mbsync -V`, `imapsync --debug`). The resulting IMAP
command/response sequences become the **behavioural contract** that
the mock must satisfy. A representative workload includes:

- Full initial sync of a labelled mailbox.
- Delta sync with new messages, label changes, and moves.
- Trash / Spam / All-Mail interactions.
- Rare paths: empty labels, special characters in labels, labels that
  are Gmail system folders.

Captures are stored under `traces/` (git-annotated, redacted of any
user-specific content), with one file per client + workload.

### Phase 2 — Mock implementation

Built in layers, each with its own tests against the captured traces:

1. **IMAP skeleton** — connection, authentication (plain, for local
   tests only), SELECT, LIST, STATUS, LOGOUT. Passes the clients'
   connect-and-list workload.
2. **Core commands** — FETCH (standard RFC fields), SEARCH (standard
   keys), APPEND, STORE, EXPUNGE, UID variants, UIDPLUS, MOVE.
3. **Gmail extensions** — CAPABILITY advertises `X-GM-EXT-1`; FETCH
   and SEARCH accept `X-GM-MSGID`, `X-GM-LABELS`, `X-GM-THRID`;
   STORE `+X-GM-LABELS` / `-X-GM-LABELS` on messages; Gmail-flavoured
   MOVE rewrites labels rather than relocating.
4. **Pseudo-folders** — `[Gmail]/All Mail` as the canonical home of
   every labelled message, with a persistent `X-GM-MSGID` →
   `All Mail UID` mapping; `[Gmail]/Trash` as the only true
   detachment target.

Each layer is complete only when both clients' matching workload
runs against it without deviations from the captured trace.

### Phase 3 — Validation and container integration

Both clients run their full recorded workloads against the mock. Any
divergence is either fixed in the mock or recorded as a subsidiary
Limitation — never shrugged off. When the validation passes:

- The mock is packaged into `../docker/docker-compose.yml` as a
  third service next to `imap-a` and `imap-b`.
- The seven Gmail scenarios under
  `../features/providers/gmail_label_semantics.feature` are wired
  to target the mock.
- LIM-0002 is updated: `Mitigated` if residual risks remain,
  `Resolved` when the mock plus any subsidiary Limitations cover
  everything the server's Gmail adapter does.

## Layout (planned)

```
bdd/mock-gmail/
├── pyproject.toml         # isolated project
├── README.md              # this file
├── traces/                # Phase 1 capture (git-annotated, redacted)
│   ├── mbsync/
│   └── imapsync/
├── src/mock_gmail/
│   ├── __init__.py
│   ├── __main__.py        # mock-gmail-server entry point
│   ├── protocol/          # IMAP command parsing & serialisation
│   ├── state/             # labels, folders, UID mapping
│   ├── extensions/        # X-GM-MSGID, X-GM-LABELS, X-GM-THRID
│   └── server.py          # connection acceptor, session state
└── tests/                 # unit tests for each layer
```

None of this is implemented yet — Phase 1 has not started.

## Operator notes

- The mock is a test fixture. It is not authenticated in any way a
  production IMAP server would accept; it must never be bound to a
  non-loopback address.
- Persistence is ephemeral and process-local. Every test run starts
  from an empty mailbox; seeding is done by the BDD steps just like
  it is for `imap-a` and `imap-b`.
- The mock has no SMTP side. Messages are seeded only via IMAP
  APPEND from the test harness.

## References

- [ADR 0019](../../docs/adr/0019-gmail-label-semantics.md) — Gmail
  label semantics the server adapter supports.
- [LIM-0002](../../docs/limitations/0002-gmail-scenarios-not-runnable.md)
  — the technical debt this subproject pays down.
- [Gmail IMAP extensions](https://developers.google.com/gmail/imap/imap-extensions)
  — upstream reference for `X-GM-MSGID`, `X-GM-LABELS`, `X-GM-THRID`.
- `mbsync` / isync — <https://isync.sourceforge.io/>
- `imapsync` — <https://imapsync.lamiral.info/>
