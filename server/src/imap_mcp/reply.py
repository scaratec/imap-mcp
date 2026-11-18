"""Pure helpers that build a top-posted reply from a source message (ADR 0035, ADR 0057, ADR 0063).

Adheres to:
- Quote only non-attachment inline text/plain / text/html parts (never attachments or message/rfc822 sub-parts)
- Strip <script> and executable tags inside HTML reply blockquote
- Paragraph-preserving HTML-to-text fallback
- Local wall-clock attribution
"""

from __future__ import annotations

import re
from email.message import EmailMessage
from email.utils import (
    formataddr,
    getaddresses,
    parseaddr,
    parsedate_to_datetime,
)
from html import escape


def strip_html_preserving_paragraphs(html: str) -> str:
    """Convert block tags (<p>, <br>, <div>, <li>, <tr>, <h1..h6>) to line breaks before stripping tags."""
    if not html:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</?(?:p|div|li|tr|h[1-6])(?:\s+[^>]*)?>", "\n", text)
    # Strip script and style blocks completely
    text = re.sub(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>", "", text)
    # Strip all remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Normalize multiple line breaks
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_reply_subject(source_subject: str) -> str:
    """Prepend `Re: ` unless the subject already starts with it."""
    if source_subject.lstrip().lower().startswith("re:"):
        return source_subject
    return f"Re: {source_subject}"


def derive_reply_to(source_reply_to: str | None, source_from: str) -> str:
    """Pick Reply-To over From."""
    if source_reply_to and source_reply_to.strip():
        return source_reply_to
    return source_from


def derive_reply_cc(
    source_to: str | None,
    source_cc: str | None,
    self_identity: str,
) -> str:
    """(source.To union source.Cc) minus self_identity, case-folded on addr-spec."""
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


def build_attribution(date_header: str | None, from_header: str) -> str:
    """Format single-line 'On YYYY-MM-DD HH:MM, Name <addr> wrote:'."""
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
    """Prefix lines with '> '."""
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


def build_reply_body(reply_text: str, attribution: str, quoted_original: str) -> str:
    return f"{reply_text.rstrip()}\n\n{attribution}\n{quoted_original}"


def build_threading_headers(
    source_message_id: str, source_references: str | None
) -> tuple[str, str]:
    in_reply_to = source_message_id
    if source_references and source_references.strip():
        references = f"{source_references} {source_message_id}"
    else:
        references = source_message_id
    return in_reply_to, references


def sanitize_reply_html(html: str) -> str:
    """Strip script and executable tags from HTML."""
    return re.sub(r"(?is)<script[^>]*>.*?</script>", "", html)


def build_reply_html(reply_html: str, attribution: str, source_body: str) -> str:
    clean_reply_html = sanitize_reply_html(reply_html)
    clean_source_body = sanitize_reply_html(source_body)

    lines = clean_source_body.rstrip("\n").split("\n")
    quoted = "<br>\n".join(escape(line) for line in lines)
    tail = f'<p>{escape(attribution)}</p>\n<blockquote type="cite">\n{quoted}\n</blockquote>\n'

    closing = re.search(r"</body\s*>", clean_reply_html, flags=re.IGNORECASE)
    if closing is not None:
        return clean_reply_html[: closing.start()] + tail + clean_reply_html[closing.start() :]
    return f"{clean_reply_html.rstrip()}\n{tail}"


def build_reply_message(
    *,
    self_identity: str,
    reply_to: str,
    cc: str,
    subject: str,
    in_reply_to: str,
    references: str,
    body: str,
    html_body: str | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = self_identity
    msg["To"] = reply_to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["In-Reply-To"] = in_reply_to
    msg["References"] = references
    msg.set_content(body, subtype="plain", charset="utf-8")
    if html_body is not None:
        msg.add_alternative(html_body, subtype="html", charset="utf-8")
    return msg.as_bytes()
