"""Reads the server's saga WAL as a second verification channel.

The WAL schema is defined by ADR 0007; the reader inspects it via a
read-only SQLite connection so scenarios can assert on saga state
without trusting the server's self-reported `get_transaction_status`
output.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Transaction:
    tx_id: str
    status: str
    src_account: str
    src_folder: str
    src_uid: int
    dst_account: str
    dst_folder: str
    message_id: str | None
    retry_count: int
    content_hash: str | None
    target_uid: int | None
    last_error: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Transaction":
        return cls(
            tx_id=row["tx_id"],
            status=row["status"],
            src_account=row["src_account"],
            src_folder=row["src_folder"],
            src_uid=row["src_uid"],
            dst_account=row["dst_account"],
            dst_folder=row["dst_folder"],
            message_id=row["message_id"],
            retry_count=row["retry_count"],
            content_hash=row["content_hash"],
            target_uid=row["target_uid"],
            last_error=row["last_error"],
        )


class WALReader:
    """Read-only access to the saga WAL file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _open(self) -> sqlite3.Connection:
        uri = f"file:{self.path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def transaction(self, tx_id: str) -> Transaction | None:
        if not self.path.exists():
            return None
        with self._open() as conn:
            cursor = conn.execute(
                "SELECT * FROM transactions WHERE tx_id = ?", (tx_id,)
            )
            row = cursor.fetchone()
        return Transaction.from_row(row) if row else None

    def all_transactions(self) -> list[Transaction]:
        if not self.path.exists():
            return []
        with self._open() as conn:
            cursor = conn.execute("SELECT * FROM transactions ORDER BY created_at")
            rows = cursor.fetchall()
        return [Transaction.from_row(r) for r in rows]

    def events(self, tx_id: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._open() as conn:
            cursor = conn.execute(
                "SELECT step, timestamp, outcome, detail "
                "FROM transaction_events WHERE tx_id = ? ORDER BY timestamp",
                (tx_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def count_by_status(self) -> dict[str, int]:
        if not self.path.exists():
            return {}
        with self._open() as conn:
            cursor = conn.execute(
                "SELECT status, COUNT(*) AS n FROM transactions GROUP BY status"
            )
            return {row["status"]: int(row["n"]) for row in cursor.fetchall()}
