"""Unit tests verifying that saga terminal audit records precede WAL status updates.

Prevents the race condition where external observers see a transaction status
as 'committed', 'aborted', or 'needs_operator' in wal.db before the corresponding
saga_transition audit record has been fsynced to the audit log.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from imap_mcp.audit import AuditWriter
from imap_mcp.config import Account
from imap_mcp.saga import SagaManager
from imap_mcp.wal import WAL


class OrderingVerifyingWAL(WAL):
    """A WAL subclass that asserts the audit entry is already visible on disk before status update."""

    def __init__(self, path: Path, audit_dir: Path) -> None:
        super().__init__(path)
        self.audit_dir = audit_dir
        self.commit_called = False
        self.abort_called = False
        self.escalate_called = False

    def _assert_audit_contains(self, tx_id: str, step: str) -> None:
        found = False
        for p in self.audit_dir.glob("*.jsonl"):
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if (
                    rec.get("tool") == "saga_transition"
                    and rec.get("tx_id") == tx_id
                    and rec.get("step") == step
                ):
                    found = True
                    break
        assert found, (
            f"Audit log in {self.audit_dir} did not contain saga_transition "
            f"with step={step!r} for tx_id={tx_id!r} before WAL update!"
        )

    def commit(self, tx_id: str) -> None:
        self._assert_audit_contains(tx_id, "commit")
        self.commit_called = True
        super().commit(tx_id)

    def abort(self, tx_id: str, reason: str) -> None:
        self._assert_audit_contains(tx_id, "aborted")
        self.abort_called = True
        super().abort(tx_id, reason)

    def mark_needs_operator(self, tx_id: str, reason: str = "retry_limit_reached") -> None:
        self._assert_audit_contains(tx_id, "escalated")
        self.escalate_called = True
        super().mark_needs_operator(tx_id, reason)


def _setup_saga(tmp_path: Path) -> tuple[SagaManager, OrderingVerifyingWAL, AuditWriter]:
    wal_path = tmp_path / "wal.db"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit = AuditWriter(directory=audit_dir)
    wal = OrderingVerifyingWAL(wal_path, audit_dir)

    async def dummy_resolver(acct_id: str) -> tuple[Account, str]:
        return Account(id=acct_id, host="127.0.0.1", port=993, user="u"), "pw"

    saga = SagaManager(
        wal=wal,
        audit_emitter=audit,
        retry_limit=3,
        account_resolver=dummy_resolver,
    )
    return saga, wal, audit


@pytest.mark.anyio
async def test_saga_commit_audited_before_wal_status(tmp_path: Path) -> None:
    saga, wal, _ = _setup_saga(tmp_path)
    tx_id = wal.begin("caller1", "src", "INBOX", 100, "dst", "Archiv")
    src_acct = Account(id="src", host="127.0.0.1", port=993, user="u")

    # Finishing without delete_source triggers commit directly
    res = await saga._finish_delete_and_commit(
        tx_id=tx_id,
        src_account=src_acct,
        src_password="pw",
        src_folder="INBOX",
        src_uid=100,
        delete_source=False,
    )
    assert res.result == "OK"
    assert wal.commit_called is True

    # At this point WAL status is committed
    entry = wal.get(tx_id)
    assert entry is not None
    assert entry["status"] == "committed"


@pytest.mark.anyio
async def test_saga_abort_audited_before_wal_status(tmp_path: Path) -> None:
    saga, wal, _ = _setup_saga(tmp_path)
    tx_id = wal.begin("caller1", "src", "INBOX", 100, "dst", "Archiv")

    tx = {"tx_id": tx_id, "status": "pending", "retry_count": 0}
    res = await saga.resume(tx)
    assert res is not None
    assert res.result == "ERROR"
    assert res.error_type == "saga_aborted"
    assert wal.abort_called is True

    entry = wal.get(tx_id)
    assert entry is not None
    assert entry["status"] == "aborted"


@pytest.mark.anyio
async def test_saga_escalate_audited_before_wal_status(tmp_path: Path) -> None:
    saga, wal, _ = _setup_saga(tmp_path)
    tx_id = wal.begin("caller1", "src", "INBOX", 100, "dst", "Archiv")

    tx = {"tx_id": tx_id, "status": "pending", "retry_count": 3}
    res = await saga.resume(tx)
    assert res is not None
    assert res.result == "ERROR"
    assert res.error_type == "saga_aborted"
    assert wal.escalate_called is True

    entry = wal.get(tx_id)
    assert entry is not None
    assert entry["status"] == "needs_operator"
