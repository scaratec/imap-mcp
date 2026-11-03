# ADR 0035: Draft Body Format Is a Deliberate, Equally Easy Choice

- **Status:** Accepted
- **Date:** 2026-11-03
- **Deciders:** Randy Gupta
- **Amends:** [ADR 0026] (create_draft input), [ADR 0031] (shared field set), the reply contract of `create_reply_draft`

## Context

Whether a draft is plain text or HTML is the mailbox owner's preference.
The server must not impose one, and it must not make one so much harder
than the other that the effort decides.

Today `create_draft` accepts only a complete RFC 822 message. A plain
text draft takes a few header lines; an HTML draft needs a
`multipart/alternative` tree, boundaries, charsets and an RFC 2047
encoded subject. `update_draft` already accepts `body_text` and
`body_html` as fields ([ADR 0031]). `create_reply_draft` accepts only
`reply_text`, so an HTML reply is impossible through it. Agents write
plain text with manual line breaks by default and rewrite the draft when
asked for HTML.

## Decision

**`create_draft` and `create_reply_draft` accept the body as structured
fields in either format, the tool description presents the format as a
choice, and the response reports the format that was stored.**

- **`create_draft` takes either `rfc822` or fields**, never both:
  `to`, `cc`, `bcc`, `subject`, `body_text`, `body_html`,
  `in_reply_to`, the set `update_draft` uses. At least one body field is
  required. Both inputs together, or neither body field nor `rfc822`,
  is an invalid-arguments error.
- **Composition:** `body_text` alone gives `text/plain; charset=utf-8`.
  `body_html`, with or without `body_text`, gives `multipart/alternative`
  with a `text/plain` and a `text/html` part, both UTF-8. Without
  `body_text` the plain part is derived from the HTML by the same tag
  stripping `fetch_body` uses. Non-ASCII header values are RFC 2047
  encoded; the stored header block is pure ASCII.
- **Response field `format`** on `create_draft`: `text/plain`,
  `text/html` or `multipart/alternative`, read from the message that was
  appended, for `rfc822` and field input alike.
- **`create_reply_draft` accepts `reply_html`** beside `reply_text`. At
  least one is required; a missing `reply_text` is derived from
  `reply_html`. With `reply_html` the reply is `multipart/alternative`;
  the HTML part quotes the original in a `<blockquote>`, the text part
  quotes it with `>` as before.
- **Tool description of `create_draft`** contains, in substance: "Choose
  the body format deliberately: plain text or HTML. If the user's
  preference is unknown, ask." It names no preference of its own.
- `rfc822` input remains for callers that need full control.

## Consequences

### Positive

- Text and HTML cost a caller the same effort; the choice follows the
  content and the owner.
- The caller sees what was stored and can tell the user.
- Create and update share one field vocabulary.

### Negative

- The server now composes MIME and must get encoding right for every
  combination; covered by BDD with non-ASCII cases.
- `create_reply_draft` grows a second body parameter.

### Neutral

- Whether agents actually decide deliberately is measured in the
  usability matrix ([ADR 0039]), not enforced.

## Security Implications

- **Attack surface:** new input fields; header values are validated and
  encoded, never concatenated raw into headers (no header injection).
- **Trust boundaries:** unchanged.
- **Data exposure:** draft content is never audited ([ADR 0021]); the
  field names changed are recorded as for `update_draft`.
- **Failure modes:** invalid input is rejected before any IMAP command.

## Alternatives Considered

- **Fields without the derived text alternative.** Rejected: text-only
  clients would show nothing readable for an HTML-only draft.
- **Only the description hint and the `format` field.** Rejected: the
  effort gap would remain the deciding factor.
- **Default to HTML.** Rejected: the server has no business holding a
  preference.

## References

- [ADR 0021], [ADR 0026], [ADR 0031], [ADR 0039]
- `bdd/features/tool_surface/create_draft_format.feature`
- `usability/features/01_draft_format_decision.feature`

[ADR 0021]: 0021-audit-log-format.md
[ADR 0026]: 0026-tool-surface-consistency.md
[ADR 0031]: 0031-draft-revision-via-rewrite-saga.md
[ADR 0039]: 0039-usability-matrix-for-agent-behaviour.md
