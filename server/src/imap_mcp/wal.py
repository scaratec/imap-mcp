"""SQLite-backed write-ahead log for sagas, provenance, and account health.

Adheres to:
- ADR 0042: Cryptographic draft provenance and WAL provenance registry
- ADR 0046: Content-verified transfer sagas, WAL leases, and immediate payload scrubbing
- ADR 0064: Durable account health and token lifecycle
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    tx_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    committed_at TEXT,
    caller_id TEXT,
    src_account TEXT,
    src_folder TEXT,
    src_uid INTEGER,
    dst_account TEXT,
    dst_folder TEXT,
    message_id TEXT,
    content_hash TEXT,
    raw_sha256 TEXT,
    canonical_sha256 TEXT,
    size_bytes INTEGER,
    target_uid INTEGER,
    retry_count INTEGER DEFAULT 0,
    last_error TEXT,
    staged_rfc822 BLOB,
    leased_by TEXT,
    leased_until TEXT
);

CREATE TABLE IF NOT EXISTS transaction_events (
    tx_id TEXT NOT NULL,
    step TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    outcome TEXT,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS provenance_records (
    draft_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    caller_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    message_id TEXT,
    canonical_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (draft_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_provenance_own
ON provenance_records (caller_id, account_id, message_id, canonical_digest);

CREATE TABLE IF NOT EXISTS account_health (
    account_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    secret_sha256 TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS draft_leases (
    account_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    leased_by TEXT NOT NULL,
    leased_until TEXT NOT NULL,
    PRIMARY KEY (account_id, draft_id)
);
"""


def _now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


class WAL:
    """Synchronous SQLite wrapper with 0600 mode and WAL journaling."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        with self._conn() as conn:
            conn.executescript(SCHEMA)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.row_factory = sqlite3.Row
        return conn

    # -------------------------------------------------------- Sagas & Leases

    def begin(
        self,
        caller_id: str,
        src_account: str,
        src_folder: str,
        src_uid: int,
        dst_account: str,
        dst_folder: str,
    ) -> str:
        tx_id = f"tx-{uuid.uuid4().hex[:16]}"
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO transactions "
                "(tx_id, status, created_at, caller_id, "
                "src_account, src_folder, src_uid, dst_account, dst_folder) "
                "VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?)",
                (
                    tx_id,
                    _now(),
                    caller_id,
                    src_account,
                    src_folder,
                    src_uid,
                    dst_account,
                    dst_folder,
                ),
            )
            self._event(conn, tx_id, "begin", "OK", None)
        return tx_id

    def record_fetch(
        self,
        tx_id: str,
        message_id: str | None,
        content_hash: str,
        *,
        raw_sha256: str | None = None,
        canonical_sha256: str | None = None,
        size_bytes: int | None = None,
        staged_rfc822: bytes | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE transactions SET status = 'fetched', message_id = ?, content_hash = ?, "
                "raw_sha256 = ?, canonical_sha256 = ?, size_bytes = ?, staged_rfc822 = ? "
                "WHERE tx_id = ?",
                (
                    message_id,
                    content_hash,
                    raw_sha256 or content_hash,
                    canonical_sha256 or content_hash,
                    size_bytes,
                    staged_rfc822,
                    tx_id,
                ),
            )
            self._event(conn, tx_id, "fetched", "OK", None)

    def mark_staged(self, tx_id: str, target_uid: int | None) -> None:
        with self._conn() as conn:
            # ADR 0046: Scrub staged_rfc822 to NULL upon reaching staged
            conn.execute(
                "UPDATE transactions SET status = 'staged', target_uid = ?, staged_rfc822 = NULL "
                "WHERE tx_id = ?",
                (target_uid, tx_id),
            )
            self._event(conn, tx_id, "staged", "OK", None)

    def mark_deleted(self, tx_id: str) -> None:
        with self._conn() as conn:
            self._event(conn, tx_id, "deleted", "OK", None)

    def commit(self, tx_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE transactions SET status = 'committed', committed_at = ?, staged_rfc822 = NULL, "
                "leased_by = NULL, leased_until = NULL "
                "WHERE tx_id = ?",
                (_now(), tx_id),
            )
            self._event(conn, tx_id, "commit", "OK", None)

    def bump_retry(self, tx_id: str, error: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT retry_count FROM transactions WHERE tx_id = ?", (tx_id,)
            ).fetchone()
            new = (row["retry_count"] if row else 0) + 1
            conn.execute(
                "UPDATE transactions SET retry_count = ?, last_error = ? WHERE tx_id = ?",
                (new, error, tx_id),
            )
            self._event(conn, tx_id, "retry", "ERROR", error)
            return new

    def mark_needs_operator(self, tx_id: str, reason: str = "retry_limit_reached") -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE transactions SET status = 'needs_operator', "
                "last_error = COALESCE(last_error, ?), staged_rfc822 = NULL, "
                "leased_by = NULL, leased_until = NULL WHERE tx_id = ?",
                (reason, tx_id),
            )
            self._event(conn, tx_id, "escalated", None, reason)

    def abort(self, tx_id: str, reason: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE transactions SET status = 'aborted', last_error = ?, staged_rfc822 = NULL, "
                "leased_by = NULL, leased_until = NULL "
                "WHERE tx_id = ?",
                (reason, tx_id),
            )
            self._event(conn, tx_id, "aborted", "ERROR", reason)

    def release_lease(self, tx_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE transactions SET leased_by = NULL, leased_until = NULL WHERE tx_id = ?",
                (tx_id,),
            )

    def get(self, tx_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM transactions WHERE tx_id = ?", (tx_id,)).fetchone()
            return dict(row) if row else None

    def pending_transactions(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM transactions "
                "WHERE status IN ('pending', 'fetched', 'staged') "
                "ORDER BY created_at"
            ).fetchall()
            return [dict(r) for r in rows]

    def claim_and_get_pending(
        self,
        caller_id: str | None = None,
        instance_id: str = "",
        ttl_seconds: float = 30.0,
    ) -> list[dict[str, Any]]:
        from datetime import datetime, timedelta

        now = _now()
        now_dt = datetime.fromisoformat(now)
        until = (now_dt + timedelta(seconds=ttl_seconds)).isoformat()
        claimed: list[dict[str, Any]] = []
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if caller_id is not None:
                rows = conn.execute(
                    "SELECT * FROM transactions "
                    "WHERE caller_id = ? AND status IN ('pending', 'fetched', 'staged') "
                    "AND (leased_until IS NULL OR leased_until < ?) "
                    "ORDER BY created_at",
                    (caller_id, now),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM transactions "
                    "WHERE status IN ('pending', 'fetched', 'staged') "
                    "AND (leased_until IS NULL OR leased_until < ?) "
                    "ORDER BY created_at",
                    (now,),
                ).fetchall()

            for r in rows:
                tx_id = r["tx_id"]
                cur = conn.execute(
                    "UPDATE transactions SET leased_by = ?, leased_until = ? "
                    "WHERE tx_id = ? AND (leased_until IS NULL OR leased_until < ?)",
                    (instance_id, until, tx_id, now),
                )
                if cur.rowcount > 0:
                    claimed.append(dict(r))
        return claimed

    def _event(
        self,
        conn: sqlite3.Connection,
        tx_id: str,
        step: str,
        outcome: str | None,
        detail: str | None,
    ) -> None:
        conn.execute(
            "INSERT INTO transaction_events (tx_id, step, timestamp, outcome, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (tx_id, step, _now(), outcome, detail),
        )

    # ---------------------------------------------------- Provenance Records (ADR 0042)

    def record_provenance(
        self,
        draft_id: str,
        revision: int,
        caller_id: str,
        account_id: str,
        message_id: str | None,
        canonical_digest: str,
        state: str = "active",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO provenance_records "
                "(draft_id, revision, caller_id, account_id, message_id, canonical_digest, state, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    draft_id,
                    revision,
                    caller_id,
                    account_id,
                    message_id,
                    canonical_digest,
                    state,
                    _now(),
                ),
            )

    def discard_provenance(self, draft_id: str, account_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE provenance_records SET state = 'discarded' "
                "WHERE draft_id = ? AND account_id = ?",
                (draft_id, account_id),
            )

    def is_caller_draft_owner(self, draft_id: str, caller_id: str, account_id: str) -> bool:
        """True if draft_id is registered in WAL and owned by caller_id on account_id."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM provenance_records WHERE draft_id = ? AND caller_id = ? AND account_id = ?",
                (draft_id, caller_id, account_id),
            ).fetchone()
            return row is not None

    def match_own_provenance(
        self, caller_id: str, account_id: str, message_id: str | None, canonical_digest: str
    ) -> bool:
        """Check if message matches provenance: 'own' via WAL fallback."""
        if not message_id:
            return False
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM provenance_records "
                "WHERE caller_id = ? AND account_id = ? AND message_id = ? AND canonical_digest = ?",
                (caller_id, account_id, message_id, canonical_digest),
            ).fetchone()
            return row is not None

    def acquire_draft_lease(
        self,
        account_id: str,
        draft_id: str,
        instance_id: str,
        ttl_seconds: float = 30.0,
        timeout: float = 10.0,
    ) -> bool:
        import time
        from datetime import timedelta

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            now = _now()
            now_dt = datetime.fromisoformat(now)
            until = (now_dt + timedelta(seconds=ttl_seconds)).isoformat()
            with self._conn() as conn:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        conn.execute(
                            "INSERT INTO draft_leases (account_id, draft_id, leased_by, leased_until) VALUES (?, ?, ?, ?)",
                            (account_id, draft_id, instance_id, until),
                        )
                        return True
                    except sqlite3.IntegrityError:
                        cur = conn.execute(
                            "UPDATE draft_leases SET leased_by = ?, leased_until = ? "
                            "WHERE account_id = ? AND draft_id = ? AND (leased_until < ? OR leased_by = ?)",
                            (instance_id, until, account_id, draft_id, now, instance_id),
                        )
                        if cur.rowcount > 0:
                            return True
                except sqlite3.OperationalError:
                    pass
            time.sleep(0.05)
        return False

    def release_draft_lease(
        self,
        account_id: str,
        draft_id: str,
        instance_id: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM draft_leases WHERE account_id = ? AND draft_id = ? AND leased_by = ?",
                (account_id, draft_id, instance_id),
            )

    # ------------------------------------------------------ Account Health (ADR 0064)

    def get_account_health(self, account_id: str) -> tuple[str, str | None] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT state, secret_sha256 FROM account_health WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row is None:
                return None
            return str(row["state"]), (str(row["secret_sha256"]) if row["secret_sha256"] else None)

    def set_account_health(
        self, account_id: str, state: str, secret_sha256: str | None = None
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO account_health (account_id, state, secret_sha256, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (account_id, state, secret_sha256, _now()),
            )

    def clear_account_health(self, account_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM account_health WHERE account_id = ?", (account_id,))
