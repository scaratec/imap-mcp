"""Per-account connection pool with time-to-live (ADR 0013).

The pool knows nothing about IMAP. It manages objects that satisfy
`PooledConnection`: a healthcheck run before every reuse (NOOP for
IMAP), a close (LOGOUT), and a cheap synchronous test whether the
connection may go back to the pool after a lease. `imap_core` supplies
the IMAP connection type and the opener.

Rules (ADR 0013 §Decision):

- one `AccountPool` per account id; no sharing across accounts;
- at most `max_size` connections per account, counting idle, leased
  and those being opened; a caller that finds the pool full waits up to
  `acquire_timeout` and then gets `PoolTimeout`;
- a connection is leased to one caller at a time and returns to the
  pool afterwards unless it is not reusable, older than `max_age`, or
  was drained while leased;
- an idle connection is closed after `idle_ttl` without use, and every
  connection after `max_age`;
- before reuse the healthcheck runs; a failing connection is closed and
  a new one replaces it;
- every connection is opened for a key (account settings plus a digest
  of the credential); an idle connection whose key no longer matches,
  for example after a token refresh, is closed instead of reused;
- `drain` closes the idle connections and marks the leased ones to be
  closed when they come back (token refresh, configuration reload).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

logger = logging.getLogger(__name__)


class PooledConnection(Protocol):
    async def healthcheck(self) -> bool:
        """True when the connection still works; run before reuse."""
        ...

    async def close(self) -> None:
        """Close the connection. Must not raise and must not block for long."""
        ...

    def reusable(self) -> bool:
        """True when the connection may return to the pool after a lease."""
        ...


C = TypeVar("C", bound=PooledConnection)


class PoolTimeout(TimeoutError):
    """No connection could be leased within `acquire_timeout`."""


class PoolClosed(RuntimeError):
    """The pool was closed (server shutdown); no new lease is handed out."""


_ENV_SETTINGS = {
    "max_size": "IMAP_MCP_POOL_MAX_SIZE",
    "idle_ttl": "IMAP_MCP_POOL_IDLE_TTL",
    "max_age": "IMAP_MCP_POOL_MAX_AGE",
    "acquire_timeout": "IMAP_MCP_POOL_ACQUIRE_TIMEOUT",
}


@dataclass(frozen=True)
class PoolSettings:
    """Pool limits. Durations are seconds."""

    max_size: int = 4
    idle_ttl: float = 600.0
    max_age: float = 3600.0
    acquire_timeout: float = 30.0

    def __post_init__(self) -> None:
        if self.max_size < 1:
            raise ValueError(f"pool max_size must be at least 1, got {self.max_size}")
        for name in ("idle_ttl", "max_age", "acquire_timeout"):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"pool {name} must be positive, got {value}")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> PoolSettings:
        """Read the `IMAP_MCP_POOL_*` variables; unset ones keep the defaults."""
        env = os.environ if environ is None else environ
        defaults = cls()
        max_size_raw = env.get(_ENV_SETTINGS["max_size"])
        idle_raw = env.get(_ENV_SETTINGS["idle_ttl"])
        age_raw = env.get(_ENV_SETTINGS["max_age"])
        acquire_raw = env.get(_ENV_SETTINGS["acquire_timeout"])
        try:
            return cls(
                max_size=int(max_size_raw) if max_size_raw else defaults.max_size,
                idle_ttl=float(idle_raw) if idle_raw else defaults.idle_ttl,
                max_age=float(age_raw) if age_raw else defaults.max_age,
                acquire_timeout=float(acquire_raw) if acquire_raw else defaults.acquire_timeout,
            )
        except ValueError as exc:
            names = ", ".join(_ENV_SETTINGS.values())
            raise ValueError(f"invalid connection pool setting ({names}): {exc}") from exc


@dataclass(eq=False)
class _Entry(Generic[C]):
    conn: C
    key: str
    created_at: float
    last_used: float
    generation: int


class AccountPool(Generic[C]):
    """The pool of one account. Not thread-safe; one event loop only."""

    def __init__(self, settings: PoolSettings, clock: Callable[[], float]) -> None:
        self._settings = settings
        self._clock = clock
        self._idle: list[_Entry[C]] = []
        self._leased: set[_Entry[C]] = set()
        self._opening = 0
        self._waiters: deque[asyncio.Future[None]] = deque()
        self._generation = 0
        self._closed = False

    @property
    def size(self) -> int:
        """Connections held: idle, leased and being opened."""
        return len(self._idle) + len(self._leased) + self._opening

    @property
    def idle_count(self) -> int:
        return len(self._idle)

    @property
    def leased_count(self) -> int:
        return len(self._leased)

    @contextlib.asynccontextmanager
    async def lease(self, key: str, opener: Callable[[], Awaitable[C]]) -> AsyncIterator[C]:
        entry = await self._acquire(key, opener)
        try:
            yield entry.conn
        finally:
            await self._release(entry)

    async def _acquire(self, key: str, opener: Callable[[], Awaitable[C]]) -> _Entry[C]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._settings.acquire_timeout
        while True:
            if self._closed:
                raise PoolClosed("connection pool is closed")
            await self._close_all(self._evict(key))
            entry = self._idle.pop() if self._idle else None
            if entry is not None:
                self._leased.add(entry)
                try:
                    healthy = await entry.conn.healthcheck()
                except BaseException:
                    self._leased.discard(entry)
                    self._wake()
                    await entry.conn.close()
                    raise
                if healthy:
                    return entry
                self._leased.discard(entry)
                await entry.conn.close()
                continue
            if self.size < self._settings.max_size:
                return await self._open(key, opener)
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise PoolTimeout(
                    f"no IMAP connection free within {self._settings.acquire_timeout:g} s "
                    f"(pool size {self._settings.max_size})"
                )
            await self._wait(remaining)

    async def _open(self, key: str, opener: Callable[[], Awaitable[C]]) -> _Entry[C]:
        self._opening += 1
        try:
            conn = await opener()
        except BaseException:
            self._opening -= 1
            self._wake()
            raise
        self._opening -= 1
        now = self._clock()
        entry = _Entry(
            conn=conn, key=key, created_at=now, last_used=now, generation=self._generation
        )
        self._leased.add(entry)
        return entry

    async def _wait(self, timeout: float) -> None:
        """Wait until capacity may have become free, or `timeout` passed."""
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter, timeout)
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                # Woken, but leaving: hand the wake to the next waiter.
                self._wake()
            raise
        finally:
            with contextlib.suppress(ValueError):
                self._waiters.remove(waiter)

    def _wake(self) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                return

    async def _release(self, entry: _Entry[C]) -> None:
        self._leased.discard(entry)
        now = self._clock()
        keep = (
            not self._closed
            and entry.generation == self._generation
            and now - entry.created_at < self._settings.max_age
            and entry.conn.reusable()
        )
        if keep:
            entry.last_used = now
            self._idle.append(entry)
            self._wake()
            return
        self._wake()
        await entry.conn.close()

    def _evict(self, key: str | None) -> list[_Entry[C]]:
        """Remove idle entries that must not be reused and return them."""
        now = self._clock()
        keep: list[_Entry[C]] = []
        evicted: list[_Entry[C]] = []
        for entry in self._idle:
            expired = (
                now - entry.last_used >= self._settings.idle_ttl
                or now - entry.created_at >= self._settings.max_age
            )
            stale_key = key is not None and entry.key != key
            if expired or stale_key or entry.generation != self._generation:
                evicted.append(entry)
            else:
                keep.append(entry)
        self._idle = keep
        if evicted:
            self._wake()
        return evicted

    @staticmethod
    async def _close_all(entries: list[_Entry[C]]) -> None:
        if entries:
            await asyncio.gather(*(entry.conn.close() for entry in entries))

    def reap(self) -> list[_Entry[C]]:
        """Take every idle entry past `idle_ttl` or `max_age` out of the pool."""
        return self._evict(None)

    def drain(self) -> list[_Entry[C]]:
        """Take every idle entry out; leased ones are closed when they return."""
        self._generation += 1
        drained, self._idle = self._idle, []
        self._wake()
        return drained

    def close(self) -> list[_Entry[C]]:
        """Like `drain`, and refuse new leases from now on."""
        self._closed = True
        drained = self.drain()
        for waiter in self._waiters:
            if not waiter.done():
                waiter.set_result(None)
        self._waiters.clear()
        return drained


class ConnectionPools(Generic[C]):
    """One `AccountPool` per account id, plus the idle reaper."""

    _CLOSE_ALL_TIMEOUT = 5.0

    def __init__(
        self, settings: PoolSettings | None = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.settings = settings or PoolSettings()
        self._clock = clock
        self._pools: dict[str, AccountPool[C]] = {}
        self._background: set[asyncio.Task[None]] = set()
        self._reaper: asyncio.Task[None] | None = None
        self._closed = False

    def pool(self, account_id: str) -> AccountPool[C]:
        pool = self._pools.get(account_id)
        if pool is None:
            pool = AccountPool(self.settings, self._clock)
            if self._closed:
                pool.close()
            self._pools[account_id] = pool
        return pool

    @contextlib.asynccontextmanager
    async def lease(
        self, account_id: str, key: str, opener: Callable[[], Awaitable[C]]
    ) -> AsyncIterator[C]:
        try:
            async with self.pool(account_id).lease(key, opener) as conn:
                yield conn
        finally:
            self._ensure_reaper()

    def drain(self, account_id: str) -> None:
        """Close the idle connections of one account in the background;
        its leased connections are closed when they return."""
        pool = self._pools.get(account_id)
        if pool is None:
            return
        self._close_in_background(pool.drain())

    async def reap(self) -> None:
        """Close idle connections past `idle_ttl` or `max_age`, in every pool."""
        expired = [entry for pool in self._pools.values() for entry in pool.reap()]
        if expired:
            await asyncio.gather(*(entry.conn.close() for entry in expired))

    async def close_all(self) -> None:
        """Shutdown: refuse new leases, close every idle connection and
        wait for closes still running, bounded by a few seconds."""
        self._closed = True
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        entries = [entry for pool in self._pools.values() for entry in pool.close()]
        closes: list[Awaitable[object]] = [entry.conn.close() for entry in entries]
        closes.extend(self._background)
        if not closes:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*closes, return_exceptions=True), self._CLOSE_ALL_TIMEOUT
            )
        except TimeoutError:
            logger.warning("connection pool: closing connections timed out at shutdown")

    def _close_in_background(self, entries: list[_Entry[C]]) -> None:
        if not entries:
            return

        async def _close() -> None:
            await asyncio.gather(*(entry.conn.close() for entry in entries))

        task = asyncio.get_running_loop().create_task(_close())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _reap_interval(self) -> float:
        shortest = min(self.settings.idle_ttl, self.settings.max_age)
        return min(60.0, max(0.1, shortest / 4))

    def _ensure_reaper(self) -> None:
        if self._closed or (self._reaper is not None and not self._reaper.done()):
            return
        self._reaper = asyncio.get_running_loop().create_task(self._reap_forever())

    async def _reap_forever(self) -> None:
        from .tracing import tracer

        interval = self._reap_interval()
        while True:
            await asyncio.sleep(interval)
            if not any(pool.idle_count for pool in self._pools.values()):
                continue
            try:
                with tracer.start_as_current_span("imap.pool.reap"):
                    await self.reap()
            except Exception:
                logger.exception("connection pool: reaping idle connections failed")
