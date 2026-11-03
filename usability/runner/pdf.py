"""Deterministic invoice PDFs, generated from feature-table values.

No binary file is committed: an attachment such as `NL-2026-10.pdf` is
rendered from the row that names it (invoice number, issuer, date,
amount). The writer emits a minimal PDF 1.4 with fixed metadata and no
time stamps or random IDs, so the same row always yields the same bytes.
"""

from __future__ import annotations

from datetime import date


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _latin1(text: str) -> bytes:
    return text.encode("latin-1", errors="replace")


def format_eur(amount: str) -> str:
    """'89.40' → '89,40 EUR' (German notation, as on a German invoice)."""
    whole, _, cents = f"{float(amount):.2f}".partition(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{'.'.join(groups)},{cents} EUR"


def invoice_lines(
    *, number: str, issuer: str, issued: date, amount_eur: str | None, recipient: str
) -> list[str]:
    lines = [
        issuer,
        "",
        f"Rechnung {number}",
        f"Rechnungsdatum: {issued.strftime('%d.%m.%Y')}",
        f"Kunde: {recipient}",
        "",
    ]
    if amount_eur is not None:
        lines.append(f"Gesamtbetrag: {format_eur(amount_eur)}")
    else:
        lines.append("Gesamtbetrag: siehe Kundenkonto")
    lines += ["", "Zahlbar innerhalb von 14 Tagen ohne Abzug."]
    return lines


def render_pdf(lines: list[str], *, title: str, creation: date) -> bytes:
    """Render text lines on one A4 page; byte-for-byte reproducible."""
    content_ops = ["BT", "/F1 12 Tf", "14 TL", "72 770 Td"]
    for line in lines:
        content_ops.append(f"({_escape(line)}) Tj T*")
    content_ops.append("ET")
    content = _latin1("\n".join(content_ops) + "\n")
    stamp = creation.strftime("D:%Y%m%d000000Z")
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Title (" + _latin1(_escape(title)) + b") /Producer (imap-mcp usability fixtures) "
        b"/CreationDate (" + stamp.encode() + b") /ModDate (" + stamp.encode() + b") >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {len(objects)} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)
