"""Parses and verifies the server's JSONL audit log.

Per ADR 0021 the audit log is append-only JSONL with a SHA-256 hash
chain, one file per UTC day, fsynced per record. The reader:

- lists records from a given day or range,
- filters by any field combination,
- verifies the hash chain end-to-end or across a day boundary.

This is the second channel the harness uses for persistence
validation of write operations (BDD Guidelines §13.2 Prüfung 1).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


@dataclass
class AuditRecord:
    """One parsed audit log record."""

    raw_line: bytes
    record: dict[str, Any]

    @property
    def ts(self) -> datetime:
        return datetime.fromisoformat(self.record["ts"].replace("Z", "+00:00"))

    @property
    def seq(self) -> int:
        return int(self.record["seq"])

    @property
    def prev_hash(self) -> str | None:
        return self.record.get("prev_hash")

    def matches(self, **criteria: Any) -> bool:
        for key, expected in criteria.items():
            if self.record.get(key) != expected:
                return False
        return True


class AuditReader:
    """Reads JSONL audit files from a directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    # ------------------------------------------------------------- query

    def records(
        self, since: date | None = None, until: date | None = None
    ) -> Iterator[AuditRecord]:
        for path in self._sorted_files(since, until):
            yield from self._read_file(path)

    def records_today(self) -> list[AuditRecord]:
        today = datetime.now(tz=timezone.utc).date()
        return list(self.records(since=today, until=today))

    def find(self, **criteria: Any) -> list[AuditRecord]:
        """Return every record across every file matching criteria."""
        return [r for r in self.records() if r.matches(**criteria)]

    # -------------------------------------------------------- integrity

    def verify_chain(
        self, since: date | None = None, until: date | None = None
    ) -> tuple[bool, str | None]:
        """Recompute the hash chain. Returns (ok, first_broken_seq_or_None)."""
        expected_prev: str | None = None
        for record in self.records(since=since, until=until):
            if expected_prev is not None and record.prev_hash != expected_prev:
                return False, f"broken at seq={record.seq}"
            expected_prev = "sha256:" + hashlib.sha256(record.raw_line).hexdigest()
        return True, None

    # -------------------------------------------------------- internals

    def _sorted_files(
        self, since: date | None, until: date | None
    ) -> list[Path]:
        paths: list[Path] = []
        if not self.directory.exists():
            return paths
        for path in self.directory.iterdir():
            if not path.is_file():
                continue
            name = path.name
            if name.endswith(".jsonl"):
                day = date.fromisoformat(name[:-6])
            elif name.endswith(".jsonl.gz"):
                day = date.fromisoformat(name[:-9])
            else:
                continue
            if since is not None and day < since:
                continue
            if until is not None and day > until:
                continue
            paths.append(path)
        paths.sort()
        return paths

    def _read_file(self, path: Path) -> Iterator[AuditRecord]:
        if path.suffix == ".gz":
            import gzip

            opener = lambda: gzip.open(path, "rb")
        else:
            opener = lambda: path.open("rb")
        with opener() as fh:
            for line in fh:
                stripped = line.rstrip(b"\n")
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                yield AuditRecord(raw_line=line, record=record)


def iter_days(start: date, stop: date) -> Iterable[date]:
    """Inclusive day iterator; stop >= start."""
    current = start
    while current <= stop:
        yield current
        current += timedelta(days=1)
