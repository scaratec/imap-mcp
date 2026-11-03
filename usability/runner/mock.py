"""mock-gmail in a background thread with its own event loop, one per run."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any

from mock_gmail.server import start_gmail_mock

from .fixtures import GmailState


class MockGmail:
    """Starts mock-gmail on a free loopback port; `stop()` shuts it down."""

    def __init__(self, state: GmailState) -> None:
        self.state = state
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Any = None
        self._thread: threading.Thread | None = None

    def start(self, timeout: float = 10.0) -> None:
        ready: Future[int] = Future()

        def _run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            try:
                server, port = loop.run_until_complete(start_gmail_mock(self.state, port=0))
            except BaseException as exc:  # noqa: BLE001 - surfaced through the future
                ready.set_exception(exc)
                return
            self._server = server
            ready.set_result(port)
            try:
                loop.run_forever()
            finally:
                server.close()
                loop.run_until_complete(server.wait_closed())
                loop.close()

        self._thread = threading.Thread(target=_run, name="mock-gmail", daemon=True)
        self._thread.start()
        self.port = ready.result(timeout=timeout)

    def call(self, fn: Any) -> Any:
        """Run `fn()` on the mock's loop thread (consistent state reads)."""
        if self._loop is None:
            return fn()
        fut: Future[Any] = Future()

        def _wrap() -> None:
            try:
                fut.set_result(fn())
            except BaseException as exc:  # noqa: BLE001
                fut.set_exception(exc)

        self._loop.call_soon_threadsafe(_wrap)
        return fut.result(timeout=10)

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._loop = None
