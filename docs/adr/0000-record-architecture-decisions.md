# ADR 0000: Record Architecture Decisions

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

`imap-mcp` is a security-sensitive project: it mediates LLM access to private
email. Decisions made during its design — about policy semantics, transaction
guarantees, authentication flows, audit behaviour — are load-bearing and will
outlive the original author's memory of why they were made.

The project is also intended to be published eventually. A future external
contributor must be able to understand not only *what* the architecture is but
*why* it is the way it is, and *what was rejected* along the way. Reconstructing
that history from commit messages and chat logs is unreliable.

A lightweight, in-repository decision log avoids both failure modes.

## Decision

We record every significant architectural decision as an **Architecture
Decision Record (ADR)** under [`docs/adr/`](.), following the format defined in
[`TEMPLATE.md`](TEMPLATE.md).

The template is based on Michael Nygard's original proposal, extended with two
mandatory sections: **Security Implications** and **Alternatives Considered**.

ADRs are numbered sequentially, zero-padded, and named
`NNNN-short-kebab-case-title.md`. Once accepted, an ADR is immutable: later
decisions supersede earlier ones through a new ADR that links back. The index
in [`README.md`](README.md) is kept up to date.

What counts as "significant" is any decision that:

- Constrains future implementation beyond the scope of a single module,
- Has non-obvious trade-offs worth justifying, or
- Affects security, compliance, operational cost, or public API.

Routine implementation choices do not require an ADR.

## Consequences

### Positive

- Every structural decision is traceable to a written rationale.
- Newcomers — including future public contributors — can onboard without
  interviewing the original author.
- The "Security Implications" section forces security reasoning to be explicit
  rather than implicit, which matches the project's threat model.
- The "Alternatives Considered" section prevents re-litigation of settled
  questions.

### Negative

- Writing an ADR adds friction to making a decision. This is intentional but
  real.
- Maintaining the index and superseded-by links requires discipline.

### Neutral

- ADRs live in the main repository rather than a separate documentation system.
  They follow the same review and merge process as code.

## Security Implications

This meta-ADR does not itself change the security posture of the system. It
does, however, establish that every subsequent ADR must explicitly reason about
security — which is the primary reason the template mandates a Security
Implications section. Absent that requirement, security trade-offs tend to be
made implicitly and invisibly.

## Alternatives Considered

- **No ADR process.** Rejected: an undocumented decision is indistinguishable
  from an arbitrary one, and the project is too security-sensitive for that.
- **MADR ([Markdown Architectural Decision Records][madr]).** A more elaborate
  template with pros/cons tables per alternative. Rejected as heavier than the
  project currently needs; the Nygard core plus two extensions covers the same
  ground more tersely.
- **Decision log in the project wiki.** Rejected because wikis drift out of
  sync with code, are not reviewed like code, and are lost if the hosting
  platform changes.
- **Reuse the `objbox` ADR style** from the internal scaratec mail server.
  Rejected because `imap-mcp` is intended for eventual public release; the
  Nygard-based template is more widely recognized outside scaratec.

[madr]: https://adr.github.io/madr/

## References

- Michael Nygard, *Documenting Architecture Decisions* (2011):
  <https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions>
- <https://adr.github.io/> — index of ADR approaches and tooling.
