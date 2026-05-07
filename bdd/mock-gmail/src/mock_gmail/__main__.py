"""Standalone entry point for testing the mock outside the BDD harness."""

import asyncio
import sys

from .server import start_gmail_mock
from .state import GmailState, Message


async def main() -> None:
    state = GmailState()
    # Seed a demo message
    msg = Message(
        gm_msgid=state.new_msgid(),
        gm_thrid=state.new_msgid(),
        rfc822=b"From: demo@example.com\r\nSubject: Demo\r\n\r\nHello.\r\n",
        labels={"\\Inbox", "TestLabel"},
        message_id="<demo@example.com>",
        from_addr="demo@example.com",
        subject="Demo",
    )
    state.add_message(msg)
    server, port = await start_gmail_mock(state, port=13143)
    print(f"Gmail mock listening on 127.0.0.1:{port}", file=sys.stderr)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
