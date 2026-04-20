# Architecture Decision Records

This directory contains the architectural decision records (ADRs) for `imap-mcp`.

An ADR captures a single architectural decision, the context that forced it,
and its consequences. ADRs are **immutable once accepted** — a later decision
does not edit an earlier one; it supersedes it and links back.

## Format

All ADRs in this repository follow the format defined in
[`TEMPLATE.md`](TEMPLATE.md). The format is based on
[Michael Nygard's original proposal][nygard], extended with two mandatory
sections:

- **Security Implications** — required because this project's entire purpose is
  access control over sensitive data. An ADR that does not discuss its security
  impact is incomplete.
- **Alternatives Considered** — required so a future reader (or a public
  contributor) can see what was rejected and why, without having to reconstruct
  it from discussion.

[nygard]: https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions

## Numbering & naming

- ADRs are numbered sequentially, zero-padded to four digits: `0001`, `0002`, …
- File name: `NNNN-short-kebab-case-title.md`
- `0000` is reserved for the meta-ADR that establishes the ADR process itself.
- Numbers are never reused. A deprecated or superseded ADR keeps its number.

## Lifecycle

```
Proposed  →  Accepted  →  (Deprecated | Superseded by ADR-XXXX)
```

- **Proposed** — authored, under discussion, not yet binding.
- **Accepted** — merged to `main`. Binding until deprecated or superseded.
- **Deprecated** — decision no longer applies, but no replacement is in force.
- **Superseded** — replaced by a named later ADR. Both remain in the repository.

Status transitions are themselves recorded: when an ADR moves to Superseded,
edit its Status line and link the replacement. Do not delete the file.

## Writing rules

1. **One decision per ADR.** If you are tempted to describe two, write two.
2. **Present tense, active voice.** "We use X" not "X will be used".
3. **Stand on its own.** A reader should not need prior ADRs to understand
   this one — link explicitly to anything required.
4. **Keep it short.** Under 500 lines. If it grows larger, split it or move
   detail into a linked specification document.
5. **No code.** ADRs describe decisions, not implementations. Pseudo-code for
   illustration is fine; real code belongs in the implementation.
6. **Write for a public reader.** This repository may become public. Avoid
   internal shorthand, named individuals, or private links.

## Index

| #    | Title                                   | Status   |
|------|-----------------------------------------|----------|
| 0000 | [Record Architecture Decisions](0000-record-architecture-decisions.md) | Accepted |
