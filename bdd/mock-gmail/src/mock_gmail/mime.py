"""RFC 3501 §7.4.2 BODYSTRUCTURE of a stored message, as Gmail renders it.

Gmail upper-cases type, subtype, parameter names and the disposition
type; a single part carries the extension fields md5, disposition and
language, a multipart the parameters, disposition and language (no
location in either). Sizes are the octets of the encoded body as
stored, lines the number of lines of a text body.
"""

from __future__ import annotations

import email
import email.policy
from email.message import Message as EmailMessage
from email.utils import getaddresses


def bodystructure(rfc822: bytes) -> str:
    """The BODYSTRUCTURE value (without the item name) of `rfc822`."""
    return _part(email.message_from_bytes(rfc822, policy=email.policy.compat32))


def _quote(value: str | None) -> str:
    if value is None:
        return "NIL"
    # Header values parsed from bytes keep undecodable octets as
    # surrogates; send them as the UTF-8 they most likely were.
    text = value.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    text = text.replace("\r", " ").replace("\n", " ")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _params(pairs: list[tuple[str, str]]) -> str:
    if not pairs:
        return "NIL"
    return "(" + " ".join(f"{_quote(k.upper())} {_quote(v)}" for k, v in pairs) + ")"


def _header_params(part: EmailMessage, header: str) -> list[tuple[str, str]]:
    raw = part.get_params(header=header, unquote=True) or []
    # The first entry is the value itself (e.g. `text/plain`), not a parameter.
    out: list[tuple[str, str]] = []
    for name, value in raw[1:]:
        if isinstance(value, tuple):
            value = email.utils.collapse_rfc2231_value(value)
        out.append((str(name), str(value)))
    return out


def _disposition(part: EmailMessage) -> str:
    header = part.get("Content-Disposition")
    if header is None:
        return "NIL"
    kind = str(header).split(";", 1)[0].strip()
    if not kind:
        return "NIL"
    return f"({_quote(kind.upper())} {_params(_header_params(part, 'content-disposition'))})"


def _language(part: EmailMessage) -> str:
    header = part.get("Content-Language")
    return _quote(str(header).strip()) if header is not None else "NIL"


def _raw_body(part: EmailMessage) -> str:
    payload = part.get_payload(decode=False)
    if isinstance(payload, str):
        return payload
    return ""


def _part(part: EmailMessage) -> str:
    if part.is_multipart() and part.get_content_type() != "message/rfc822":
        children = part.get_payload()
        assert isinstance(children, list)
        inner = "".join(_part(child) for child in children if isinstance(child, EmailMessage))
        subtype = part.get_content_subtype().upper()
        return (
            f"({inner} {_quote(subtype)} {_params(_header_params(part, 'content-type'))} "
            f"{_disposition(part)} {_language(part)})"
        )
    maintype = part.get_content_maintype().upper()
    subtype = part.get_content_subtype().upper()
    params = _params(_header_params(part, "content-type"))
    content_id = part.get("Content-ID")
    description = part.get("Content-Description")
    encoding = str(part.get("Content-Transfer-Encoding") or "7BIT").strip().upper()
    nested: EmailMessage | None = None
    if maintype == "MESSAGE" and subtype == "RFC822":
        payload = part.get_payload()
        if isinstance(payload, list) and payload and isinstance(payload[0], EmailMessage):
            nested = payload[0]
        body = nested.as_string() if nested is not None else _raw_body(part)
    else:
        body = _raw_body(part)
    size = len(body.encode("utf-8", "surrogateescape"))
    fields = (
        f"{_quote(maintype)} {_quote(subtype)} {params} "
        f"{_quote(str(content_id) if content_id is not None else None)} "
        f"{_quote(str(description) if description is not None else None)} "
        f"{_quote(encoding)} {size}"
    )
    if maintype == "TEXT":
        fields += f" {_line_count(body)}"
    elif nested is not None:
        fields += f" {_envelope(nested)} {_part(nested)} {_line_count(body)}"
    extension = f"NIL {_disposition(part)} {_language(part)}"
    return f"({fields} {extension})"


def _line_count(body: str) -> int:
    if not body:
        return 0
    return body.count("\n") + (0 if body.endswith("\n") else 1)


def _address_list(message: EmailMessage, header: str) -> str:
    values = message.get_all(header) or []
    addresses = getaddresses([str(v) for v in values])
    if not addresses:
        return "NIL"
    rendered = []
    for name, address in addresses:
        local, _, domain = address.rpartition("@") if "@" in address else (address, "", "")
        rendered.append(f"({_quote(name) if name else 'NIL'} NIL {_quote(local)} {_quote(domain)})")
    return "(" + "".join(rendered) + ")"


def _envelope(message: EmailMessage) -> str:
    def header(name: str) -> str:
        value = message.get(name)
        return _quote(str(value)) if value is not None else "NIL"

    sender = _address_list(message, "From")
    return (
        f"({header('Date')} {header('Subject')} {sender} "
        f"{_address_list(message, 'Sender') if message.get('Sender') else sender} "
        f"{_address_list(message, 'Reply-To') if message.get('Reply-To') else sender} "
        f"{_address_list(message, 'To')} {_address_list(message, 'Cc')} "
        f"{_address_list(message, 'Bcc')} {header('In-Reply-To')} {header('Message-ID')})"
    )
