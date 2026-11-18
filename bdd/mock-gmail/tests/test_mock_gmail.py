"""Unit and integration tests for mock-gmail IMAP server."""

from __future__ import annotations

import asyncio
import base64

import pytest

from mock_gmail.server import start_gmail_mock
from mock_gmail.state import GmailState, Message


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _read_until_tagged(reader: asyncio.StreamReader, tag: str) -> list[str]:
    lines = []
    while True:
        line = await reader.readline()
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        lines.append(text)
        if text.startswith(f"{tag} "):
            break
    return lines


@pytest.mark.anyio
async def test_login_auth_and_failure() -> None:
    state = GmailState()
    server, port = await start_gmail_mock(state, host="127.0.0.1", port=0)
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        greeting = await r.readline()
        assert b"OK Gimap ready" in greeting

        # 1. Invalid password
        w.write(b'a01 LOGIN "test@example.com" "wrongpass"\r\n')
        await w.drain()
        lines = await _read_until_tagged(r, "a01")
        assert any("[AUTHENTICATIONFAILED]" in line for line in lines)

        # 2. Valid password
        w.write(b'a02 LOGIN "test@example.com" "test"\r\n')
        await w.drain()
        lines = await _read_until_tagged(r, "a02")
        assert any("a02 OK" in line for line in lines)

        w.close()
        await w.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_authenticate_xoauth2() -> None:
    state = GmailState()
    server, port = await start_gmail_mock(state, host="127.0.0.1", port=0)
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        await r.readline()

        # SASL-IR with valid token
        blob = b"user=test@example.com\x01auth=Bearer valid_token_123\x01\x01"
        b64 = base64.b64encode(blob).decode()
        w.write(f"a01 AUTHENTICATE XOAUTH2 {b64}\r\n".encode())
        await w.drain()
        lines = await _read_until_tagged(r, "a01")
        assert any("a01 OK" in line for line in lines)

        # Revoke next token once
        state.revoke_current_token_once()
        w.write(f"a02 AUTHENTICATE XOAUTH2 {b64}\r\n".encode())
        await w.drain()
        lines2 = await _read_until_tagged(r, "a02")
        assert any("a02 NO [AUTHENTICATIONFAILED]" in line for line in lines2)

        w.close()
        await w.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_strict_state_machine() -> None:
    state = GmailState()
    server, port = await start_gmail_mock(state, host="127.0.0.1", port=0)
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        await r.readline()

        # 1. Commands before auth are rejected
        w.write(b'a01 SELECT "INBOX"\r\n')
        await w.drain()
        lines = await _read_until_tagged(r, "a01")
        assert any("a01 BAD [CLIENTBUG] Command requires authenticated state" in line for line in lines)

        # 2. Authenticate
        w.write(b'a02 LOGIN "user" "test"\r\n')
        await w.drain()
        await _read_until_tagged(r, "a02")

        # 3. UID FETCH without SELECT is rejected
        w.write(b"a03 UID FETCH 1 (FLAGS)\r\n")
        await w.drain()
        lines = await _read_until_tagged(r, "a03")
        assert any("a03 BAD [CLIENTBUG] Command requires selected state" in line for line in lines)

        # 4. EXAMINE (Read-Only) mode rejects write operations
        w.write(b'a04 EXAMINE "INBOX"\r\n')
        await w.drain()
        lines = await _read_until_tagged(r, "a04")
        assert any("a04 OK [READ-ONLY]" in line for line in lines)

        w.write(b"a05 STORE 1 +FLAGS (\\Seen)\r\n")
        await w.drain()
        lines = await _read_until_tagged(r, "a05")
        assert any("a05 NO [READ-ONLY] Mailbox is read-only" in line for line in lines)

        w.close()
        await w.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_strict_mailbox_existence() -> None:
    state = GmailState()
    server, port = await start_gmail_mock(state, host="127.0.0.1", port=0)
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        await r.readline()
        w.write(b'a01 LOGIN "user" "test"\r\n')
        await w.drain()
        await _read_until_tagged(r, "a01")

        # 1. APPEND to non-existent folder
        w.write(b'a02 APPEND "NonExistentFolder" {10}\r\n')
        await w.drain()
        lines = await _read_until_tagged(r, "a02")
        assert any("a02 NO [TRYCREATE] Mailbox does not exist" in line for line in lines)

        # 2. CREATE folder
        w.write(b'a03 CREATE "Projects/2026"\r\n')
        await w.drain()
        lines = await _read_until_tagged(r, "a03")
        assert any("a03 OK CREATE completed" in line for line in lines)
        assert state.folder_exists("Projects/2026")

        # 3. Duplicate CREATE
        w.write(b'a04 CREATE "Projects/2026"\r\n')
        await w.drain()
        lines = await _read_until_tagged(r, "a04")
        assert any("a04 NO [ALREADYEXISTS]" in line for line in lines)

        w.close()
        await w.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_fetch_and_envelope() -> None:
    state = GmailState()
    server, port = await start_gmail_mock(state, host="127.0.0.1", port=0)
    w = None
    try:
        raw_email = (
            b"From: Alice <alice@example.org>\r\n"
            b"To: Bob <bob@example.org>\r\n"
            b"Subject: Test Subject\r\n"
            b"Date: Mon, 17 Nov 2026 10:00:00 +0000\r\n"
            b"Message-ID: <msg123@example.org>\r\n"
            b"\r\n"
            b"Hello world body."
        )
        msgid = state.new_msgid()
        msg = Message(
            gm_msgid=msgid,
            gm_thrid=msgid,
            rfc822=raw_email,
            labels={"\\Inbox"},
            subject="Test Subject",
        )
        state.add_message(msg)

        r, w = await asyncio.open_connection("127.0.0.1", port)
        await r.readline()
        w.write(b'a01 LOGIN "user" "test"\r\n')
        await w.drain()
        await _read_until_tagged(r, "a01")

        w.write(b'a02 SELECT "INBOX"\r\n')
        await w.drain()
        await _read_until_tagged(r, "a02")

        # 1. RFC822.SIZE does not return literal body
        w.write(b"a03 UID FETCH 1 (RFC822.SIZE)\r\n")
        await w.drain()
        lines = await _read_until_tagged(r, "a03")
        assert any(f"RFC822.SIZE {len(raw_email)}" in line for line in lines)
        assert not any("Hello world" in line for line in lines)

        # 2. ENVELOPE built from real headers
        w.write(b"a04 UID FETCH 1 (ENVELOPE)\r\n")
        await w.drain()
        lines = await _read_until_tagged(r, "a04")
        assert any('ENVELOPE ("Mon, 17 Nov 2026 10:00:00 +0000" "Test Subject"' in line for line in lines)
        assert any("<msg123@example.org>" in line for line in lines)

        # 3. Unknown FETCH item is rejected with BAD
        w.write(b"a05 UID FETCH 1 (UNKNOWN_ITEM)\r\n")
        await w.drain()
        lines = await _read_until_tagged(r, "a05")
        assert any("a05 BAD Could not parse command" in line for line in lines)
    finally:
        if w is not None:
            w.close()
            try:
                await w.wait_closed()
            except OSError:
                pass
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_close_expunges_deleted_including_all_mail_and_folder_reset() -> None:
    state = GmailState()
    server, port = await start_gmail_mock(state, host="127.0.0.1", port=0)
    w = None
    try:
        raw_email = (
            b"From: Alice <alice@example.org>\r\n"
            b"To: Bob <bob@example.org>\r\n"
            b"Subject: Test Close\r\n"
            b"Date: Mon, 17 Nov 2026 10:00:00 +0000\r\n"
            b"\r\n"
            b"Body"
        )
        msgid = state.new_msgid()
        msg = Message(
            gm_msgid=msgid,
            gm_thrid=msgid,
            rfc822=raw_email,
            labels={"\\Inbox"},
            subject="Test Close",
        )
        state.add_message(msg)

        r, w = await asyncio.open_connection("127.0.0.1", port)
        await r.readline()
        w.write(b'a01 LOGIN "user" "test"\r\n')
        await w.drain()
        await _read_until_tagged(r, "a01")

        # 1. In INBOX: STORE +FLAGS (\Deleted) + CLOSE removes message from INBOX but keeps it in All Mail
        w.write(b'a02 SELECT "INBOX"\r\n')
        await w.drain()
        await _read_until_tagged(r, "a02")

        w.write(b"a03 UID STORE 1 +FLAGS (\\Deleted)\r\n")
        await w.drain()
        await _read_until_tagged(r, "a03")

        w.write(b"a04 CLOSE\r\n")
        await w.drain()
        lines = await _read_until_tagged(r, "a04")
        assert any("a04 OK" in line for line in lines)

        # Verify INBOX is now empty
        w.write(b'a05 SELECT "INBOX"\r\n')
        await w.drain()
        lines = await _read_until_tagged(r, "a05")
        assert any("0 EXISTS" in line for line in lines)

        # 2. In [Gmail]/All Mail: STORE +FLAGS (\Deleted) + CLOSE expunges from All Mail without connection drop
        w.write(b'a06 SELECT "[Gmail]/All Mail"\r\n')
        await w.drain()
        lines = await _read_until_tagged(r, "a06")
        assert any("1 EXISTS" in line for line in lines)

        w.write(b"a07 UID STORE 1 +FLAGS (\\Deleted)\r\n")
        await w.drain()
        await _read_until_tagged(r, "a07")

        w.write(b"a08 CLOSE\r\n")
        await w.drain()
        lines = await _read_until_tagged(r, "a08")
        assert any("a08 OK" in line for line in lines)

        # Verify [Gmail]/All Mail is now empty
        w.write(b'a09 SELECT "[Gmail]/All Mail"\r\n')
        await w.drain()
        lines = await _read_until_tagged(r, "a09")
        assert any("0 EXISTS" in line for line in lines)
        assert len(state.messages) == 0

        # 3. Test remove_message_from_folder semantics for folder reset
        m1 = Message(gm_msgid=101, gm_thrid=101, rfc822=raw_email, labels={"\\Inbox"}, subject="M1")
        m2 = Message(gm_msgid=102, gm_thrid=102, rfc822=raw_email, labels={"\\Trash"}, subject="M2")
        state.add_message(m1)
        state.add_message(m2)

        # Folder reset on INBOX leaves m1 in All Mail
        state.remove_message_from_folder("INBOX", m1)
        assert len(state.messages_in_folder("INBOX")) == 0
        assert len(state.messages_in_folder("[Gmail]/All Mail")) == 1

        # Folder reset on Trash deletes permanently
        state.remove_message_from_folder("[Gmail]/Trash", m2)
        assert len(state.messages_in_folder("[Gmail]/Trash")) == 0
        assert len(state.messages) == 1

        # Folder reset on All Mail removes m1 completely
        state.remove_message_from_folder("[Gmail]/All Mail", m1)
        assert len(state.messages_in_folder("[Gmail]/All Mail")) == 0
        assert len(state.messages) == 0
    finally:
        if w is not None:
            w.close()
            try:
                await w.wait_closed()
            except OSError:
                pass
        server.close()
        await server.wait_closed()
