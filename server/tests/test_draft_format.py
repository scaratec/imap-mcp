"""Unit tests for the draft body format choice (ADR 0035).

The BDD suite witnesses the stored message over IMAP. These tests pin
the composition edge cases that are cheaper to enumerate here: header
encoding and injection, the derived text alternative, the reported
format for message shapes the feature does not list, the input-mode
validation and what reaches the audit record.
"""

from __future__ import annotations

import email
from email.header import decode_header, make_header
from email.message import Message
from typing import Any

import pytest

from imap_mcp import reply as reply_builder
from imap_mcp.dispatch import (
    _build_tool_list,
    _sanitise_args,
    _validate_arguments_against_surface,
)
from imap_mcp.handlers import drafts

# ------------------------------------------------------------ helpers


def _parse(raw: bytes) -> Message:
    return email.message_from_bytes(raw)


def _header_block(raw: bytes) -> bytes:
    head, _ = drafts._split_header_block(raw)
    return head


def _leaves(message: Message) -> list[tuple[str, str | None]]:
    return [
        (part.get_content_type(), part.get_content_charset())
        for part in message.walk()
        if not part.is_multipart()
    ]


def _text_of(message: Message, content_type: str) -> str:
    for part in message.walk():
        if part.get_content_type() == content_type and not part.is_multipart():
            payload = part.get_payload(decode=True)
            assert isinstance(payload, bytes)
            return payload.decode(part.get_content_charset() or "utf-8")
    raise AssertionError(f"no {content_type} part")


def _decoded(value: str) -> str:
    return str(make_header(decode_header(value)))


# ------------------------------------------------------------ composition


def test_body_text_alone_is_a_single_utf8_text_plain_part() -> None:
    raw = drafts.compose_draft({"to": ["a@example.org"], "body_text": "Hallo"})
    message = _parse(raw)
    assert not message.is_multipart()
    assert _leaves(message) == [("text/plain", "utf-8")]
    assert _text_of(message, "text/plain").rstrip("\n") == "Hallo"
    assert drafts.stored_format(raw) == "text/plain"


def test_body_html_alone_gets_a_derived_text_alternative_first() -> None:
    html = "<p>Guten Tag,</p>\n<p>anbei das <b>Angebot</b>.</p>"
    raw = drafts.compose_draft({"body_html": html})
    message = _parse(raw)
    assert message.get_content_type() == "multipart/alternative"
    assert _leaves(message) == [("text/plain", "utf-8"), ("text/html", "utf-8")]
    plain = _text_of(message, "text/plain")
    assert "<" not in plain
    assert "Guten Tag," in plain
    assert "anbei das Angebot." in plain
    assert _text_of(message, "text/html").rstrip("\n") == html
    assert drafts.stored_format(raw) == "multipart/alternative"


def test_given_body_text_is_kept_beside_body_html() -> None:
    raw = drafts.compose_draft({"body_text": "Eigener Text", "body_html": "<p>HTML</p>"})
    message = _parse(raw)
    assert _text_of(message, "text/plain").rstrip("\n") == "Eigener Text"
    assert _text_of(message, "text/html").rstrip("\n") == "<p>HTML</p>"


def test_non_ascii_headers_are_encoded_and_the_header_block_is_ascii() -> None:
    raw = drafts.compose_draft(
        {
            "to": ["Jörg Müller <joerg@example.org>"],
            "cc": ["office@example.org"],
            "subject": "Rückfrage zur Kündigungsfrist",
            "body_html": "<p>Grüße aus Köln</p>",
        }
    )
    assert _header_block(raw).isascii()
    message = _parse(raw)
    assert _decoded(str(message["Subject"])) == "Rückfrage zur Kündigungsfrist"
    to_header = str(message["To"])
    # Only the display name is encoded; the addr-spec stays readable.
    assert "<joerg@example.org>" in to_header
    assert _decoded(to_header) == "Jörg Müller <joerg@example.org>"
    assert "Grüße aus Köln" in _text_of(message, "text/html")


def test_long_non_ascii_subject_is_folded_into_short_lines() -> None:
    subject = "Rückfrage zur Kündigungsfrist " * 6
    raw = drafts.compose_draft({"subject": subject, "body_text": "x"})
    head = _header_block(raw)
    assert head.isascii()
    assert max(len(line) for line in head.splitlines()) <= 78
    assert _decoded(str(_parse(raw)["Subject"])) == subject


def test_composed_draft_carries_date_message_id_and_in_reply_to() -> None:
    raw = drafts.compose_draft({"in_reply_to": "<orig@example.org>", "body_text": "x"})
    message = _parse(raw)
    assert message["Date"]
    assert message["Message-ID"]
    assert message["In-Reply-To"] == "<orig@example.org>"
    assert message["MIME-Version"] == "1.0"


def test_composed_draft_takes_the_draft_handle_like_an_rfc822_draft() -> None:
    raw = drafts._inject_draft_headers(
        drafts.compose_draft({"body_html": "<p>x</p>"}), draft_id="d-1", revision=1
    )
    record = drafts._parse_draft_headers(1, _header_block(raw))
    assert (record.draft_id, record.revision) == ("d-1", 1)
    assert drafts.stored_format(raw) == "multipart/alternative"


def test_update_draft_encodes_only_the_display_name_of_a_recipient() -> None:
    original = drafts.compose_draft({"to": ["a@example.org"], "body_text": "x"})
    revised = drafts.apply_draft_changes(
        original,
        changes={"to": ["Jörg <joerg@example.org>", "b@example.org"]},
        revision=2,
        request_hash="h",
    )
    assert _header_block(revised).isascii()
    to_header = str(_parse(revised)["To"])
    assert "<joerg@example.org>" in to_header
    assert "b@example.org" in to_header


# ------------------------------------------------------------ reported format


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"Subject: s\r\n\r\nno content type", "text/plain"),
        (b"Content-Type: text/plain; charset=utf-8\r\n\r\nx", "text/plain"),
        (b"Content-Type: text/html; charset=utf-8\r\n\r\n<p>x</p>", "text/html"),
        (
            (
                b'Content-Type: multipart/alternative; boundary="b"\r\n\r\n'
                b"--b\r\nContent-Type: text/plain\r\n\r\nx\r\n"
                b"--b\r\nContent-Type: text/html\r\n\r\n<p>x</p>\r\n--b--\r\n"
            ),
            "multipart/alternative",
        ),
        (
            (
                b'Content-Type: multipart/mixed; boundary="m"\r\n\r\n'
                b'--m\r\nContent-Type: multipart/alternative; boundary="a"\r\n\r\n'
                b"--a\r\nContent-Type: text/plain\r\n\r\nx\r\n"
                b"--a\r\nContent-Type: text/html\r\n\r\n<p>x</p>\r\n--a--\r\n"
                b"--m\r\nContent-Type: application/pdf\r\n"
                b"Content-Disposition: attachment\r\n\r\nPDF\r\n--m--\r\n"
            ),
            "multipart/alternative",
        ),
        (
            (
                b'Content-Type: multipart/mixed; boundary="m"\r\n\r\n'
                b"--m\r\nContent-Type: text/html\r\n\r\n<p>x</p>\r\n"
                b"--m\r\nContent-Type: application/pdf\r\n"
                b"Content-Disposition: attachment\r\n\r\nPDF\r\n--m--\r\n"
            ),
            "text/html",
        ),
    ],
)
def test_stored_format_reads_the_body_of_the_message(raw: bytes, expected: str) -> None:
    assert drafts.stored_format(raw) == expected


# ------------------------------------------------------------ validation


def _create(**fields: Any) -> dict[str, Any]:
    return {"account": "acct", "folder": "Drafts", **fields}


@pytest.mark.parametrize(
    "arguments",
    [
        _create(rfc822="Subject: x\r\n\r\ny"),
        _create(body_text="x"),
        _create(body_html="<p>x</p>"),
        _create(body_text="", to=["Mara <mara@example.org>"], subject="Ä"),
    ],
)
def test_valid_create_draft_input_passes(arguments: dict[str, Any]) -> None:
    assert _validate_arguments_against_surface("create_draft", arguments) is None


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        (_create(rfc822="Subject: A\r\n\r\nB", body_text="C"), "not both"),
        (_create(rfc822="x", subject="s"), "not both"),
        (_create(to=["a@example.org"], subject="Leer"), "body_text"),
        (_create(), "body_text"),
        (_create(rfc822=42), "must be a string"),
        (_create(body_text=None), "must be a string"),
        (_create(body_text="x", to="a@example.org"), "array of strings"),
        (_create(body_text="x", to=["not an address"]), "malformed address"),
        (_create(body_text="x", to=["jörg@example.org"]), "outside ASCII"),
        (_create(body_text="x", subject="x\r\nBcc: evil@example.org"), "line break"),
        (_create(body_text="x", in_reply_to="<a@b>\nX-Evil: 1"), "line break"),
        (_create(body_text="x", cc=["<a@b.example>\r\nBcc: evil@example.org"]), "cc"),
    ],
)
def test_malformed_create_draft_input_is_refused(arguments: dict[str, Any], fragment: str) -> None:
    error = _validate_arguments_against_surface("create_draft", arguments)
    assert error is not None
    assert fragment in error


def test_update_draft_messages_are_unchanged_by_the_shared_check() -> None:
    assert (
        drafts.validate_changes({"subject": 1})
        == "update_draft `changes.subject` must be a string or null"
    )
    assert drafts.validate_changes({"cc": None, "body_html": None}) is None


@pytest.mark.parametrize("key", ["reply_text", "reply_html"])
def test_reply_bodies_must_be_strings(key: str) -> None:
    arguments = {"account": "a", "source_folder": "INBOX", "uid": 1, "drafts_folder": "D"}
    assert (
        _validate_arguments_against_surface("create_reply_draft", {**arguments, key: "x"}) is None
    )
    error = _validate_arguments_against_surface("create_reply_draft", {**arguments, key: 7})
    assert error is not None and key in error


# ------------------------------------------------------------ audit


def test_structured_create_draft_audits_field_names_only() -> None:
    safe = _sanitise_args(
        _create(
            to=["mara@example.org"],
            subject="Geheim",
            body_html="<p>Inhalt</p>",
        )
    )
    assert safe == {
        "account": "acct",
        "folder": "Drafts",
        "changed_fields": ["body_html", "subject", "to"],
    }


def test_reply_bodies_are_audited_by_name_only() -> None:
    safe = _sanitise_args({"account": "a", "uid": 3, "reply_text": "t", "reply_html": "<p>h</p>"})
    assert safe == {"account": "a", "uid": 3, "changed_fields": ["reply_html", "reply_text"]}


def test_rfc822_create_draft_audit_is_unchanged() -> None:
    assert _sanitise_args(_create(rfc822="Subject: x\r\n\r\ny")) == {
        "account": "acct",
        "folder": "Drafts",
        "rfc822": "<redacted>",
    }


# ------------------------------------------------------------ replies


def test_reply_bodies_derive_missing_text_from_html() -> None:
    assert drafts._reply_bodies({"reply_html": "<p>Danke, passt.</p>"}) == (
        "Danke, passt.",
        "<p>Danke, passt.</p>",
    )
    assert drafts._reply_bodies({"reply_text": "", "reply_html": "<p>x</p>"}) == ("x", "<p>x</p>")
    assert drafts._reply_bodies({"reply_text": "t", "reply_html": "  "}) == ("t", None)
    assert drafts._reply_bodies({}) == ("", None)


def test_html_reply_quotes_the_original_escaped_in_a_blockquote() -> None:
    html = reply_builder.build_reply_html(
        "<p>Danke.</p>", "On 2026-10-28 09:00, <m@example.org> wrote:", "a < b\n\n> old"
    )
    assert html.startswith("<p>Danke.</p>")
    assert "<p>On 2026-10-28 09:00, &lt;m@example.org&gt; wrote:</p>" in html
    assert '<blockquote type="cite">\na &lt; b<br>\n<br>\n&gt; old\n</blockquote>' in html


def test_html_reply_document_gets_the_quote_inside_its_body() -> None:
    html = reply_builder.build_reply_html("<html><body><p>Hi</p></body></html>", "X wrote:", "o")
    assert html.index("<blockquote") < html.index("</body>")
    assert html.endswith("</body></html>")


def test_reply_message_with_html_is_multipart_alternative() -> None:
    raw = reply_builder.build_reply_message(
        self_identity="me@example.org",
        reply_to="you@example.org",
        cc="",
        subject="Re: Grüße",
        in_reply_to="<o@example.org>",
        references="<o@example.org>",
        body="Danke.\n\nX wrote:\n> o",
        html_body="<p>Danke.</p>",
    )
    message = _parse(raw)
    assert message.get_content_type() == "multipart/alternative"
    assert _leaves(message) == [("text/plain", "utf-8"), ("text/html", "utf-8")]
    assert _header_block(raw).isascii()


# ------------------------------------------------------------ tool surface


def _tool(name: str) -> Any:
    return next(tool for tool in _build_tool_list() if tool.name == name)


def test_create_draft_schema_offers_both_input_modes() -> None:
    schema = _tool("create_draft").inputSchema
    assert schema["required"] == ["account", "folder"]
    assert set(schema["properties"]) == {
        "account",
        "folder",
        "rfc822",
        *drafts.CHANGE_FIELDS,
    }


def test_create_draft_description_names_the_choice_without_a_preference() -> None:
    description = _tool("create_draft").description
    assert "plain text or HTML" in description
    assert "If the user's preference is unknown, ask" in description
    assert "prefer HTML" not in description
    assert "prefer plain text" not in description


def test_create_reply_draft_takes_reply_html_and_requires_neither_body() -> None:
    tool = _tool("create_reply_draft")
    assert "reply_html" in tool.inputSchema["properties"]
    assert "reply_text" not in tool.inputSchema["required"]
    assert "reply_html" in tool.description
