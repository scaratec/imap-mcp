# ADR 0003: Explicit Whitelist and Blacklist Folder Modes

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0001] prescribes default-deny and a hierarchical policy model.
[ADR 0002] fixes the set of visibility levels. Both leave open *how*
sender-level rules within a folder combine with the folder's default.

Two semantics naturally exist:

- **Whitelist:** start from nothing (`default: NONE`), and *grant* a higher
  level to specific senders.
- **Blacklist:** start from some bounded non-zero default, and *cap* specific
  senders to a lower level (including `NONE`).

Allowing a single folder to mix both styles ("some rules grant, others cap")
turns policy evaluation into a fixed-point search and makes review
intractable. Letting the mode be implicit (derived from whether `default` is
`NONE`) encourages silent drift when an operator later edits the default.

## Decision

Every folder policy declares an explicit `mode` field:

```yaml
- folder: INBOX/Rechnungen
  mode: whitelist
  default: NONE
  rules:
    - match: { from_domain: hornbach.de }
      grant: FULL

- folder: INBOX
  mode: blacklist
  default: ENVELOPE
  rules:
    - match: { from_domain: bank.de }
      cap: NONE
```

The two modes produce different effective-level formulas:

- `mode: whitelist` → rules carry a `grant`. Effective level =
  `max(default, max(grant over matching rules))`.
- `mode: blacklist` → rules carry a `cap`. Effective level =
  `min(default, min(cap over matching rules))`.

The policy loader rejects a folder that mixes `grant` and `cap` rules, or
that declares `mode: whitelist` with a non-`NONE` default, or `mode: blacklist`
with a `NONE` default. These are syntax errors, not runtime surprises.

Default-deny from [ADR 0001] still holds above the folder: a folder with no
matching `FolderPolicy` at all is `NONE` regardless of mode.

## Consequences

### Positive

- **Policy author commits to a direction.** The mental model "I am listing
  what is allowed" vs "I am listing what is forbidden" is never ambiguous.
- **Validator does real work.** A rule with the wrong operator for its mode
  fails at load time with a clear message, not at runtime with a silent
  wrong answer.
- **Audit is clearer.** A `DENY` decision in a whitelist folder is a
  "whitelist gap"; in a blacklist folder it is a "blacklist hit". The
  distinction is diagnostic.

### Negative

- **One extra required field per folder entry.** Operators must write the
  word `whitelist` or `blacklist` — a two-second cost per folder.
- **A folder cannot be converted between modes in place.** Changing mode
  requires rewriting all rules in the folder. This is the correct friction:
  a mode change is a policy redesign, not a tweak.

### Neutral

- The two modes are formally equivalent in expressive power (anything
  reachable with one is reachable with the other plus enough rules). The
  distinction is purely about policy author ergonomics.

## Security Implications

- **Intent encoded, not inferred.** A reviewer can read `mode: whitelist` and
  immediately know the baseline is "deny"; the absence of that line would
  force inference from the `default` value, which is a source of bugs.
- **Validator closes a class of bugs by construction.** The common mistake
  "I added a cap: NONE rule in a whitelist folder and wondered why it had
  no effect" becomes a parse error. This is the kind of bug that in a
  permissive system would be a silent data leak.
- **Audit reason codes are stable.** [ADR 0017] reserves distinct reason
  codes (`sender_not_whitelisted` vs `sender_blacklisted`) that depend on
  this mode distinction being carried through.

## Alternatives Considered

- **Implicit mode inferred from `default` value.** Rejected because an
  operator editing the default later would silently change the semantics of
  every rule in the folder.
- **Single unified operator** (`effective = f(default, rules)` with one
  merge function). Rejected: the two useful merge functions (max, min) are
  genuinely different and collapsing them would either lose a use case or
  introduce a more complex operator (weighted grants?) that no one wants.
- **Per-rule mode** (each rule declares `grant` or `cap` independently of
  the folder). Rejected; the mix case is exactly what we set out to
  prohibit, and per-rule mode would silently re-introduce it.

## References

- [ADR 0001] — the default-deny principle this ADR builds on.
- [ADR 0002] — the linear visibility scale used by `grant`/`cap`.
- [ADR 0017] — transparency reason codes that depend on the mode.

[ADR 0001]: 0001-default-deny-hierarchical-policy.md
[ADR 0002]: 0002-linear-visibility-levels.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md
