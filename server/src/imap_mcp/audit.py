"""Audit log — ADR 0021, ADR 0022.

Writes JSONL records with a SHA-256 hash chain to a per-day file in
the configured audit directory. The writer is the single emitter for
every policy decision, tool invocation, and internal lifecycle event
the server needs to record. fsync per record by default; day rotation
on UTC midnight; strict no-content-leak rule (the writer refuses to
log a record that carries a forbidden field).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


FORBIDDEN_FIELDS = frozenset(
    [
        "body",
        "text_body",
        "html_body",
        "subject",  # hashed only, never raw
        "rfc822",
        "password",
        "access_token",
        "refresh_token",
    ]
)


@dataclass
class AuditWriter:
    directory: Path
    _lock: Lock = Lock()
    _seq: int = 0
    _current_day: str = ""
    _current_path: Path | None = None
    _prev_hash: str = "sha256:" + ("0" * 64)

    def __post_init__(self) -> None:  # type: ignore[override]
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass

    def write(self, record: dict[str, Any]) -> None:
        """Append one record. Returns after fsync."""
        with self._lock:
            self._rotate_if_needed()
            for field in record:
                if field in FORBIDDEN_FIELDS:
                    raise RuntimeError(
                        f"Audit writer refuses to log forbidden field {field!r} "
                        "(ADR 0021 no-content-leak rule)"
                    )
            now = datetime.now(tz=timezone.utc)
            self._seq += 1
            full = {
                "ts": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
                "seq": self._seq,
                "prev_hash": self._prev_hash,
                **record,
            }
            line = json.dumps(full, sort_keys=True, separators=(",", ":")) + "\n"
            assert self._current_path is not None
            with open(self._current_path, "ab") as fh:
                fh.write(line.encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.chmod(self._current_path, 0o600)
            except OSError:
                pass
            self._prev_hash = "sha256:" + hashlib.sha256(line.encode("utf-8")).hexdigest()

    def _rotate_if_needed(self) -> None:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        if today == self._current_day and self._current_path is not None:
            return
        if self._current_day and self._current_path and self._current_path.exists():
            try:
                os.chmod(self._current_path, 0o400)
            except OSError:
                pass
        self._current_day = today
        self._current_path = self.directory / f"{today}.jsonl"
        self._seq = 0
