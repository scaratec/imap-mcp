"""Pure helpers that build a top-posted reply from a source message.

The functions here are deterministic and have no I/O: they take parsed
header values and the agent-supplied reply text, and produce the strings
that go into the outgoing draft. Keeping them here (rather than inline
in `server.py`) means the BDD scenarios that constrain the behavior —
subject prefix, recipient derivation, attribution format, quoting —
can be reasoned about as data transformations.

See `bdd/features/tool_surface/create_reply_draft.feature` for the
contract these helpers implement.
"""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import (
    formataddr,
    getaddresses,
    parseaddr,
    parsedate_to_datetime,
)


def build_reply_subject(source_subject: str) -> str:
    """Prepend `Re: ` unless the subject already starts with it.

    Detection is case-insensitive and tolerates leading whitespace, but
    only on the literal `Re:` token — locale variants like `AW:`, `WG:`,
    `Rep:`, `Fwd:` are NOT recognised and DO get a `Re: ` prepended.
    """
    if source_subject.lstrip().lower().startswith("re:"):
        return source_subject
    return f"Re: {source_subject}"


def derive_reply_to(
    source_reply_to: str | None,
    source_from: str,
) -> str:
    """Pick the To-address for the reply: source Reply-To takes
    precedence over source From, both used verbatim."""
    if source_reply_to and source_reply_to.strip():
        return source_reply_to
    return source_from


def derive_reply_cc(
    source_to: str | None,
    source_cc: str | None,
    self_identity: str,
) -> str:
    """Build the reply Cc list as (source.To union source.Cc) minus
    the account's own identity, compared case-insensitively on the
    addr-spec only. Display-name is preserved on the surviving entries.

    Returns the empty string if no addresses survive."""
    seen: set[str] = set()
    self_lc = self_identity.lower().strip()
    out: list[tuple[str, str]] = []
    for raw in (source_to, source_cc):
        if not raw:
            continue
        for name, addr in getaddresses([raw]):
            if not addr:
                continue
            addr_lc = addr.lower().strip()
            if addr_lc == self_lc:
                continue
            if addr_lc in seen:
                continue
            seen.add(addr_lc)
            out.append((name, addr))
    return ", ".join(formataddr(p) for p in out)


def build_attribution(
    date_header: str | None,
    from_header: str,
) -> str:
    """Format the single-line `On YYYY-MM-DD HH:MM, Name <addr> wrote:`.

    The clock time of the source Date header is preserved verbatim — no
    timezone conversion. If `date_header` is absent or unparseable, the
    `On YYYY-MM-DD HH:MM, ` prefix is omitted entirely.

    If the From header has no display-name, the attribution shows only
    `<addr>` (the angle brackets are part of the literal output).
    """
    name, addr = parseaddr(from_header or "")
    who = f"{name} <{addr}>" if name else f"<{addr}>"
    if not date_header:
        return f"{who} wrote:"
    try:
        dt = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return f"{who} wrote:"
    return f"On {dt.strftime('%Y-%m-%d %H:%M')}, {who} wrote:"


def quote_body(plain_text: str) -> str:
    """Return the source body with each line prefixed for quoting:
    - empty lines become `>`
    - lines starting with `>` get an extra `>` (no separating space)
    - other lines get `> ` (one space).

    Trailing newlines on the source are dropped before quoting; the
    caller decides how to assemble the final reply body."""
    lines = plain_text.rstrip("\n").split("\n")
    quoted: list[str] = []
    for line in lines:
        if not line:
            quoted.append(">")
        elif line.startswith(">"):
            quoted.append(">" + line)
        else:
            quoted.append("> " + line)
    return "\n".join(quoted)


def build_reply_body(
    reply_text: str,
    attribution: str,
    quoted_original: str,
) -> str:
    """Assemble the top-posted reply body:
    <reply_text>
    <blank line>
    <attribution>
    <quoted original>
    """
    return f"{reply_text.rstrip()}\n\n{attribution}\n{quoted_original}"


def build_threading_headers(
    source_message_id: str,
    source_references: str | None,
) -> tuple[str, str]:
    """Return (in_reply_to, references) headers for the draft.

    `source_message_id` is used verbatim, including its angle brackets.
    The References chain is extended with the source Message-ID; if the
    source had no References header, the new chain is just the source
    Message-ID."""
    in_reply_to = source_message_id
    if source_references and source_references.strip():
        references = f"{source_references} {source_message_id}"
    else:
        references = source_message_id
    return in_reply_to, references


def build_reply_message(
    *,
    self_identity: str,
    reply_to: str,
    cc: str,
    subject: str,
    in_reply_to: str,
    references: str,
    body: str,
) -> bytes:
    """Construct the RFC822 bytes for the draft using stdlib EmailMessage.

    EmailMessage handles RFC 2047 encoding for non-ASCII subject and
    recipient display names automatically, and marks the body as
    UTF-8 — Cyrillic and other non-Latin scripts survive the round-trip
    through IMAP unchanged.
    """
    msg = EmailMessage()
    msg["From"] = self_identity
    msg["To"] = reply_to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["In-Reply-To"] = in_reply_to
    msg["References"] = references
    msg.set_content(body, subtype="plain", charset="utf-8")
    return msg.as_bytes()
