"""SQLite-backed write-ahead log for cross-account sagas (ADR 0007).

The WAL is a single-writer, file-backed SQLite database at the path
configured in accounts.yaml. Schema:

  transactions (
    tx_id          PRIMARY KEY,
    status         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    committed_at   TEXT,
    caller_id      TEXT,
    src_account    TEXT, src_folder TEXT, src_uid INTEGER,
    dst_account    TEXT, dst_folder TEXT,
    message_id     TEXT,
    content_hash   TEXT,
    target_uid     INTEGER,
    retry_count    INTEGER DEFAULT 0,
    last_error     TEXT
  )

  transaction_events (
    tx_id          NOT NULL,
    step           TEXT NOT NULL,
    timestamp      TEXT NOT NULL,
    outcome        TEXT,
    detail         TEXT
  )

States advance:
  pending -> staged -> committed
  pending -> pending (retry)
  pending -> aborted (crash before fetch)
  pending -> needs_operator (retry exhausted)
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


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
    target_uid INTEGER,
    retry_count INTEGER DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS transaction_events (
    tx_id TEXT NOT NULL,
    step TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    outcome TEXT,
    detail TEXT
);
"""


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


class WAL:
    """Synchronous SQLite wrapper. Callers invoke from async contexts via
    `asyncio.to_thread` if ever blocking matters; for the scenarios
    we exercise the SQLite operations are microseconds."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.row_factory = sqlite3.Row
        return conn

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
                (tx_id, _now(), caller_id, src_account, src_folder, src_uid, dst_account, dst_folder),
            )
            self._event(conn, tx_id, "begin", "OK", None)
        return tx_id

    def record_fetch(
        self, tx_id: str, message_id: str | None, content_hash: str
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE transactions SET message_id = ?, content_hash = ? WHERE tx_id = ?",
                (message_id, content_hash, tx_id),
            )
            self._event(conn, tx_id, "fetched", "OK", None)

    def mark_staged(self, tx_id: str, target_uid: int | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE transactions SET status = 'staged', target_uid = ? WHERE tx_id = ?",
                (target_uid, tx_id),
            )
            self._event(conn, tx_id, "staged", "OK", None)

    def mark_deleted(self, tx_id: str) -> None:
        with self._conn() as conn:
            self._event(conn, tx_id, "deleted", "OK", None)

    def commit(self, tx_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE transactions SET status = 'committed', committed_at = ? WHERE tx_id = ?",
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

    def mark_needs_operator(self, tx_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE transactions SET status = 'needs_operator' WHERE tx_id = ?",
                (tx_id,),
            )
            self._event(conn, tx_id, "escalated", None, "retry_limit_reached")

    def abort(self, tx_id: str, reason: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE transactions SET status = 'aborted', last_error = ? WHERE tx_id = ?",
                (reason, tx_id),
            )
            self._event(conn, tx_id, "aborted", "ERROR", reason)

    def get(self, tx_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM transactions WHERE tx_id = ?", (tx_id,)
            ).fetchone()
            return dict(row) if row else None

    def pending_transactions(self) -> list[dict]:
        """Return txs that have not reached a terminal state.

        Used by the saga recovery loop. Terminal states are
        `committed`, `aborted`, and `needs_operator`.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM transactions "
                "WHERE status IN ('pending','staged') "
                "ORDER BY created_at"
            ).fetchall()
            return [dict(r) for r in rows]

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
