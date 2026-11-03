"""Then-steps that read a stored message's MIME structure over IMAP.

ADR 0035 makes the body format of a draft observable. These witnesses
take the independent channel the feature names: a direct IMAP FETCH of
the newest message in a folder, parsed with the stdlib `email` package.
They never consult the tool response they are compared against and
never import the server.

"Newest" is the message with the highest UID in the folder, the one
the most recent APPEND produced.
"""

from __future__ import annotations

from email.header import decode_header, make_header
from email.message import Message

from behave import then
from behave.runner import Context

from support.imap_fixture import resolve_account


def _newest_uid(context: Context, location: str) -> tuple[str, str, str, int]:
    """Resolve "<account>:<folder>" to (instance, user, folder, uid)."""
    account_id, separator, folder = location.partition(":")
    if not separator or not account_id or not folder:
        raise AssertionError(
            f"Expected a location of the form \"account:folder\", got {location!r}"
        )
    instance, user = resolve_account(account_id)
    uids = context.imap.folder_uids(instance, user, folder)
    if not uids:
        raise AssertionError(f"Folder {location!r} holds no message to inspect.")
    return instance, user, folder, max(uids)


def _newest_message(context: Context, location: str) -> Message:
    instance, user, folder, uid = _newest_uid(context, location)
    return context.imap.fetch_message(instance, user, folder, uid)


def _leaf_parts(message: Message) -> list[Message]:
    return [part for part in message.walk() if not part.is_multipart()]


def _decoded_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        raise AssertionError(f"Part {part.get_content_type()} carries no byte payload.")
    charset = part.get_content_charset()
    if charset is None:
        raise AssertionError(f"Part {part.get_content_type()} declares no charset.")
    return payload.decode(charset)


@then(
    'a direct IMAP FETCH of the newest message in "{location}" shows the '
    'top-level content type "{expected}"'
)
def step_newest_message_top_level_type(
    context: Context, location: str, expected: str
) -> None:
    message = _newest_message(context, location)
    actual = message.get_content_type()
    if actual != expected:
        raise AssertionError(
            f"Newest message in {location!r} has top-level content type "
            f"{actual!r}, expected {expected!r}."
        )


@then(
    'a direct IMAP FETCH of the newest message in "{location}" shows the '
    'leaf parts in order "{expected}"'
)
def step_newest_message_leaf_parts(
    context: Context, location: str, expected: str
) -> None:
    """`expected` lists "<content type>;<charset>" per leaf, comma
    separated, in the order the parts appear in the message."""
    message = _newest_message(context, location)
    actual = [
        f"{part.get_content_type()};{part.get_content_charset() or ''}"
        for part in _leaf_parts(message)
    ]
    wanted = [entry.strip() for entry in expected.split(",")]
    if actual != wanted:
        raise AssertionError(
            f"Newest message in {location!r} has leaf parts {actual!r}, "
            f"expected {wanted!r}."
        )


@then(
    'a direct IMAP FETCH of the newest message in "{location}" shows the '
    'decoded subject "{expected}"'
)
def step_newest_message_decoded_subject(
    context: Context, location: str, expected: str
) -> None:
    message = _newest_message(context, location)
    raw = message.get("Subject")
    if raw is None:
        raise AssertionError(f"Newest message in {location!r} has no Subject header.")
    decoded = str(make_header(decode_header(str(raw))))
    if decoded != expected:
        raise AssertionError(
            f"Newest message in {location!r} has decoded subject {decoded!r} "
            f"(raw {str(raw)!r}), expected {expected!r}."
        )


@then(
    'a direct IMAP FETCH of the newest message in "{location}" shows a '
    'text/html part whose decoded text contains "{needle}"'
)
def step_newest_message_html_part_contains(
    context: Context, location: str, needle: str
) -> None:
    message = _newest_message(context, location)
    html_parts = [
        part for part in _leaf_parts(message) if part.get_content_type() == "text/html"
    ]
    if not html_parts:
        raise AssertionError(f"Newest message in {location!r} has no text/html part.")
    texts = [_decoded_text(part) for part in html_parts]
    if not any(needle in text for text in texts):
        raise AssertionError(
            f"No text/html part of the newest message in {location!r} "
            f"contains {needle!r}; decoded parts: {texts!r}"
        )


@then(
    'the raw header block of the newest message in "{location}" contains '
    "only ASCII characters"
)
def step_newest_message_header_block_ascii(context: Context, location: str) -> None:
    instance, user, folder, uid = _newest_uid(context, location)
    header_block = context.imap.fetch_header_block(instance, user, folder, uid)
    offending = sorted({byte for byte in header_block if byte > 0x7F})
    if offending:
        raise AssertionError(
            f"Header block of the newest message in {location!r} holds "
            f"non-ASCII bytes {[hex(b) for b in offending]}:\n"
            f"{header_block.decode('utf-8', errors='replace')}"
        )
