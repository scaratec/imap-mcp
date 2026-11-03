"""Unit tests for the per-account connection pool (ADR 0013).

The BDD suite (performance/connection_pool.feature) witnesses reuse,
separate pools, the NOOP replacement, the size bound and the shutdown
LOGOUT against the mock-gmail server and the IMAP proxy. These tests
pin what needs a controllable clock or exact interleavings: idle TTL,
maximum age, acquire timeout, drain, key changes, cancellation, and the
IMAP side's decision which failures keep a connection.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from imap_mcp import imap_core
from imap_mcp.config import Account, AccountAuth
from imap_mcp.pool import ConnectionPools, PoolClosed, PoolSettings, PoolTimeout


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeConn:
    def __init__(self, ident: int) -> None:
        self.ident = ident
        self.healthy = True
        self.usable = True
        self.closed = False
        self.healthchecks = 0

    async def healthcheck(self) -> bool:
        self.healthchecks += 1
        return self.healthy and not self.closed

    async def close(self) -> None:
        self.closed = True

    def reusable(self) -> bool:
        return self.usable and not self.closed


class Opener:
    def __init__(self) -> None:
        self.opened: list[FakeConn] = []
        self.fail_next = False

    async def __call__(self) -> FakeConn:
        await asyncio.sleep(0)
        if self.fail_next:
            self.fail_next = False
            raise ConnectionRefusedError("refused")
        conn = FakeConn(len(self.opened))
        self.opened.append(conn)
        return conn


def make_pools(
    clock: FakeClock | None = None, **settings: Any
) -> tuple[ConnectionPools[FakeConn], FakeClock, Opener]:
    clock = clock or FakeClock()
    pools: ConnectionPools[FakeConn] = ConnectionPools(PoolSettings(**settings), clock=clock)
    return pools, clock, Opener()


# ---------------------------------------------------------------- lease / return


async def test_sequential_leases_reuse_one_connection_after_a_healthcheck() -> None:
    pools, _clock, opener = make_pools()
    for _ in range(5):
        async with pools.lease("acct", "k", opener) as conn:
            assert conn is opener.opened[0]
    assert len(opener.opened) == 1
    assert opener.opened[0].healthchecks == 4
    assert pools.pool("acct").idle_count == 1
    await pools.close_all()


async def test_accounts_have_separate_pools() -> None:
    pools, _clock, opener = make_pools()
    async with pools.lease("a", "k", opener) as first:
        pass
    async with pools.lease("b", "k", opener) as second:
        assert second is not first
    async with pools.lease("a", "k", opener) as again:
        assert again is first
    assert len(opener.opened) == 2
    await pools.close_all()


async def test_connection_failing_the_healthcheck_is_closed_and_replaced() -> None:
    pools, _clock, opener = make_pools()
    async with pools.lease("acct", "k", opener):
        pass
    stale = opener.opened[0]
    stale.healthy = False
    async with pools.lease("acct", "k", opener) as conn:
        assert conn is not stale
    assert stale.closed
    assert pools.pool("acct").size == 1
    await pools.close_all()


async def test_connection_that_is_not_reusable_is_closed_on_return() -> None:
    pools, _clock, opener = make_pools()
    async with pools.lease("acct", "k", opener) as conn:
        conn.usable = False
    assert conn.closed
    assert pools.pool("acct").size == 0
    await pools.close_all()


async def test_failed_open_frees_its_slot() -> None:
    pools, _clock, opener = make_pools(max_size=1)
    opener.fail_next = True
    with pytest.raises(ConnectionRefusedError):
        async with pools.lease("acct", "k", opener):
            pass
    assert pools.pool("acct").size == 0
    async with pools.lease("acct", "k", opener):
        pass
    await pools.close_all()


async def test_changed_key_closes_the_idle_connection_instead_of_reusing_it() -> None:
    pools, _clock, opener = make_pools()
    async with pools.lease("acct", "token-1", opener):
        pass
    async with pools.lease("acct", "token-2", opener) as conn:
        assert conn is opener.opened[1]
    assert opener.opened[0].closed
    await pools.close_all()


# ---------------------------------------------------------------- size bound


async def test_concurrent_leases_never_exceed_max_size() -> None:
    pools, _clock, opener = make_pools(max_size=4)
    in_use = 0
    peak = 0

    async def call() -> None:
        nonlocal in_use, peak
        async with pools.lease("acct", "k", opener):
            in_use += 1
            peak = max(peak, in_use)
            await asyncio.sleep(0.01)
            in_use -= 1

    await asyncio.gather(*(call() for _ in range(12)))
    assert peak == 4
    assert len(opener.opened) == 4
    await pools.close_all()


async def test_acquire_times_out_when_every_connection_is_leased() -> None:
    pools, _clock, opener = make_pools(max_size=1, acquire_timeout=0.05)
    async with pools.lease("acct", "k", opener):
        with pytest.raises(PoolTimeout):
            async with pools.lease("acct", "k", opener):
                pass
    assert pools.pool("acct").size == 1
    await pools.close_all()


async def test_waiter_gets_the_connection_released_before_its_timeout() -> None:
    pools, _clock, opener = make_pools(max_size=1, acquire_timeout=5)
    released = asyncio.Event()

    async def holder() -> None:
        async with pools.lease("acct", "k", opener):
            await asyncio.sleep(0.02)
        released.set()

    async def waiter() -> FakeConn:
        await asyncio.sleep(0.005)
        async with pools.lease("acct", "k", opener) as conn:
            assert released.is_set()
            return conn

    _, conn = await asyncio.gather(holder(), waiter())
    assert conn is opener.opened[0]
    assert len(opener.opened) == 1
    await pools.close_all()


async def test_cancelled_waiter_does_not_swallow_the_wake_up() -> None:
    pools, _clock, opener = make_pools(max_size=1, acquire_timeout=5)
    holding = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with pools.lease("acct", "k", opener):
            holding.set()
            await release.wait()

    async def lease_once() -> None:
        async with pools.lease("acct", "k", opener):
            pass

    hold = asyncio.create_task(holder())
    await holding.wait()
    doomed = asyncio.create_task(lease_once())
    survivor = asyncio.create_task(lease_once())
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.sleep(0)
    doomed.cancel()
    await asyncio.wait_for(survivor, 1)
    await hold
    with pytest.raises(asyncio.CancelledError):
        await doomed
    assert pools.pool("acct").leased_count == 0
    await pools.close_all()


# ---------------------------------------------------------------- expiry


async def test_idle_connection_past_idle_ttl_is_closed_and_replaced() -> None:
    pools, clock, opener = make_pools(idle_ttl=600, max_age=3600)
    async with pools.lease("acct", "k", opener):
        pass
    clock.now += 599
    async with pools.lease("acct", "k", opener) as conn:
        assert conn is opener.opened[0]
    clock.now += 600
    async with pools.lease("acct", "k", opener) as conn:
        assert conn is opener.opened[1]
    assert opener.opened[0].closed
    await pools.close_all()


async def test_connection_past_max_age_is_not_returned_to_the_pool() -> None:
    pools, clock, opener = make_pools(idle_ttl=600, max_age=3600)
    async with pools.lease("acct", "k", opener):
        clock.now += 3600
    assert opener.opened[0].closed
    assert pools.pool("acct").size == 0
    await pools.close_all()


async def test_idle_connection_past_max_age_is_not_reused() -> None:
    pools, clock, opener = make_pools(idle_ttl=600, max_age=3600)
    for _ in range(7):
        async with pools.lease("acct", "k", opener):
            pass
        clock.now += 500
    # 3500 s after opening the first connection is still in use; at
    # 3600 s it is too old even though it was used 100 s ago.
    clock.now += 100
    async with pools.lease("acct", "k", opener) as conn:
        assert conn is opener.opened[1]
    assert opener.opened[0].closed
    await pools.close_all()


async def test_reap_closes_idle_connections_past_their_ttl() -> None:
    pools, clock, opener = make_pools(idle_ttl=600)
    async with pools.lease("acct", "k", opener):
        pass
    clock.now += 601
    await pools.reap()
    assert opener.opened[0].closed
    assert pools.pool("acct").size == 0
    await pools.close_all()


async def test_background_reaper_closes_idle_connections() -> None:
    clock = FakeClock()
    pools, _clock, opener = make_pools(clock, idle_ttl=0.4, max_age=3600)
    async with pools.lease("acct", "k", opener):
        pass
    clock.now += 1
    for _ in range(50):
        if opener.opened[0].closed:
            break
        await asyncio.sleep(0.02)
    assert opener.opened[0].closed
    await pools.close_all()


# ---------------------------------------------------------------- drain / shutdown


async def test_drain_closes_idle_and_leased_connections_on_return() -> None:
    pools, _clock, opener = make_pools()
    async with pools.lease("acct", "k", opener):
        pass
    idle = opener.opened[0]
    async with pools.lease("acct", "k", opener) as leased:
        assert leased is idle
        async with pools.lease("acct", "k", opener) as second:
            pools.drain("acct")
        assert second.closed
        assert not leased.closed
    assert leased.closed
    await asyncio.sleep(0)
    async with pools.lease("acct", "k", opener) as fresh:
        assert fresh is opener.opened[2]
    await pools.close_all()


async def test_drain_closes_idle_connections_of_that_account_only() -> None:
    pools, _clock, opener = make_pools()
    async with pools.lease("a", "k", opener):
        pass
    async with pools.lease("b", "k", opener):
        pass
    pools.drain("a")
    for _ in range(10):
        await asyncio.sleep(0)
    assert opener.opened[0].closed
    assert not opener.opened[1].closed
    await pools.close_all()


async def test_close_all_closes_idle_connections_and_refuses_new_leases() -> None:
    pools, _clock, opener = make_pools()
    async with pools.lease("acct", "k", opener) as leased:
        async with pools.lease("acct", "k", opener) as idle:
            pass
        await pools.close_all()
        assert idle.closed
        assert not leased.closed
        with pytest.raises(PoolClosed):
            async with pools.lease("acct", "k", opener):
                pass
    assert leased.closed


# ---------------------------------------------------------------- settings


def test_settings_from_environment() -> None:
    settings = PoolSettings.from_environment(
        {"IMAP_MCP_POOL_MAX_SIZE": "2", "IMAP_MCP_POOL_IDLE_TTL": "30"}
    )
    assert settings == PoolSettings(max_size=2, idle_ttl=30.0, max_age=3600.0, acquire_timeout=30.0)
    assert PoolSettings.from_environment({}) == PoolSettings()


@pytest.mark.parametrize(
    "environ",
    [
        {"IMAP_MCP_POOL_MAX_SIZE": "0"},
        {"IMAP_MCP_POOL_MAX_SIZE": "four"},
        {"IMAP_MCP_POOL_ACQUIRE_TIMEOUT": "-1"},
    ],
)
def test_invalid_settings_are_refused(environ: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        PoolSettings.from_environment(environ)


# ---------------------------------------------------------------- IMAP side


class _FakeTransport:
    def __init__(self) -> None:
        self.closing = False

    def is_closing(self) -> bool:
        return self.closing

    def abort(self) -> None:
        self.closing = True


class _FakeResponse:
    def __init__(self, result: str) -> None:
        self.result = result
        self.lines: list[bytes] = []


class _FakeIMAP:
    """Stands in for TracedIMAP (and the client underneath it)."""

    def __init__(self) -> None:
        self.protocol = type("P", (), {})()
        self.protocol.transport = _FakeTransport()
        self.state = "AUTH"
        self.timeout = 10
        self.commands: list[str] = []
        self.noop_result = "OK"

    @property
    def client(self) -> _FakeIMAP:
        return self

    def get_state(self) -> str:
        return self.state

    async def noop(self) -> _FakeResponse:
        self.commands.append("NOOP")
        return _FakeResponse(self.noop_result)

    async def logout(self) -> _FakeResponse:
        self.commands.append("LOGOUT")
        self.state = "LOGOUT"
        return _FakeResponse("OK")


def _pooled(fake: _FakeIMAP) -> tuple[imap_core._PooledIMAP, asyncio.Event]:
    lost = asyncio.Event()
    return imap_core._PooledIMAP(fake, lost), lost  # type: ignore[arg-type]


async def test_healthcheck_sends_noop_on_a_live_connection() -> None:
    fake = _FakeIMAP()
    conn, _lost = _pooled(fake)
    assert await conn.healthcheck()
    assert fake.commands == ["NOOP"]


async def test_healthcheck_fails_without_noop_once_the_server_closed_the_socket() -> None:
    fake = _FakeIMAP()
    conn, lost = _pooled(fake)
    lost.set()
    assert not await conn.healthcheck()
    assert fake.commands == []
    await conn.close()
    assert fake.commands == []
    assert fake.protocol.transport.closing


async def test_healthcheck_fails_on_a_non_ok_noop_and_close_skips_logout() -> None:
    fake = _FakeIMAP()
    fake.noop_result = "BAD"
    conn, _lost = _pooled(fake)
    assert not await conn.healthcheck()
    await conn.close()
    assert fake.commands == ["NOOP"]


async def test_close_logs_out_a_healthy_connection() -> None:
    fake = _FakeIMAP()
    conn, _lost = _pooled(fake)
    await conn.close()
    assert fake.commands == ["LOGOUT"]
    assert fake.protocol.transport.closing


async def test_unauthenticated_connection_is_not_reusable() -> None:
    fake = _FakeIMAP()
    fake.state = "NONAUTH"
    conn, _lost = _pooled(fake)
    assert not conn.reusable()


def _account(password_user: str = "alice") -> Account:
    return Account(
        id="acct",
        host="imap.example.org",
        port=143,
        user=password_user,
        auth=AccountAuth(type="password", secret_ref="secret://acct/password"),
    )


def test_pool_key_changes_with_the_credential_and_hides_it() -> None:
    account = _account()
    key = imap_core._pool_key(account, "s3cret")
    assert key == imap_core._pool_key(account, "s3cret")
    assert key != imap_core._pool_key(account, "rotated")
    assert key != imap_core._pool_key(_account("bob"), "s3cret")
    assert "s3cret" not in key


@pytest.fixture
def fake_connections(monkeypatch: pytest.MonkeyPatch) -> list[_FakeIMAP]:
    opened: list[_FakeIMAP] = []

    async def connect(account: Account, password: str, timeout: int) -> imap_core._PooledIMAP:
        fake = _FakeIMAP()
        opened.append(fake)
        return imap_core._PooledIMAP(fake, asyncio.Event())  # type: ignore[arg-type]

    monkeypatch.setattr(imap_core, "_connect", connect)
    imap_core.configure_connection_pools(PoolSettings())
    return opened


async def test_session_keeps_the_connection_after_a_domain_error(
    fake_connections: list[_FakeIMAP],
) -> None:
    account = _account()
    with pytest.raises(imap_core.UidNotFound):
        async with imap_core._imap_session(account, "pw"):
            raise imap_core.UidNotFound(7)
    async with imap_core._imap_session(account, "pw"):
        pass
    assert len(fake_connections) == 1
    await imap_core.close_connection_pools()


async def test_session_closes_the_connection_after_any_other_error(
    fake_connections: list[_FakeIMAP],
) -> None:
    account = _account()
    with pytest.raises(TimeoutError):
        async with imap_core._imap_session(account, "pw"):
            raise TimeoutError
    assert fake_connections[0].protocol.transport.closing
    async with imap_core._imap_session(account, "pw"):
        pass
    assert len(fake_connections) == 2
    await imap_core.close_connection_pools()


async def test_session_applies_and_restores_the_command_timeout(
    fake_connections: list[_FakeIMAP],
) -> None:
    account = _account()
    async with imap_core._imap_session(account, "pw", timeout=60) as imap:
        assert imap.client.timeout == 60
    assert fake_connections[0].timeout == imap_core._COMMAND_TIMEOUT
    await imap_core.close_connection_pools()


async def test_shutdown_logs_out_pooled_connections(
    fake_connections: list[_FakeIMAP],
) -> None:
    async with imap_core._imap_session(_account(), "pw"):
        pass
    await imap_core.close_connection_pools()
    assert fake_connections[0].commands == ["LOGOUT"]
