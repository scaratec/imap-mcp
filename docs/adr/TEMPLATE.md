# ADR NNNN: <Short Noun Phrase>

- **Status:** Proposed | Accepted | Deprecated | Superseded by [ADR-XXXX](XXXX-title.md)
- **Date:** YYYY-MM-DD
- **Deciders:** <names or roles>

## Context

What is the problem we are solving? What forces are at play — technical,
organizational, regulatory, performance, security? Describe the situation such
that a reader can understand *why a decision is needed now* without any prior
knowledge of the project.

Keep this section factual. Do not describe the decision here.

## Decision

What did we decide? State it as a single, unambiguous sentence, then elaborate
with the mechanism.

Use present tense and active voice: "We use X to achieve Y."

## Consequences

State the consequences honestly. A reader should be able to tell from this
section alone whether this decision is a good fit for their situation.

### Positive

- <what gets better>

### Negative

- <what gets worse, or what costs we now carry>

### Neutral

- <what changes without being clearly better or worse>

## Security Implications

**Required for every ADR in this repository.**

Describe how this decision affects the security posture:

- **Attack surface** — does this expose new endpoints, credentials, data flows?
- **Trust boundaries** — does this move or redefine any?
- **Data exposure** — what additional data can now be seen, by whom, and under
  which conditions?
- **Failure modes** — what happens on partial failure, race, or malicious input?
- **Auditability** — what is logged, what is not?

If the decision has no security implications, state that explicitly and argue
why (do not simply leave the section empty).

## Alternatives Considered

**Required for every ADR in this repository.**

List the options that were on the table and were rejected. For each, give:

- A one-line description.
- The reason it was not chosen.

This section exists so that a future reader (including a public contributor
years from now) does not have to re-litigate the decision from scratch.

## References

- RFCs, standards, prior art, related ADRs.
- Links to specifications or external documentation.
- Issue/MR links where the decision was discussed.
