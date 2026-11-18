"""Parses and verifies the server's JSONL audit log.

Per ADR 0021 the audit log is append-only JSONL with a SHA-256 hash
chain, one file per UTC day, fsynced per record.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class AuditRecord:
    """One parsed audit log record."""

    raw_line: bytes
    record: dict[str, Any]
    is_corrupt: bool = False
    parse_error: str | None = None

    @property
    def ts(self) -> datetime:
        raw_ts = self.record.get("ts", "")
        return datetime.fromisoformat(raw_ts) if raw_ts else datetime.min.replace(tzinfo=UTC)

    @property
    def seq(self) -> int:
        return int(self.record.get("seq", -1))

    @property
    def prev_hash(self) -> str | None:
        return self.record.get("prev_hash")

    def matches(self, **criteria: Any) -> bool:
        if self.is_corrupt:
            return False
        for key, expected in criteria.items():
            if self.record.get(key) != expected:
                return False
        return True


@dataclass
class ChainVerificationResult:
    """Detailed outcome of a hash chain verification."""

    valid: bool
    first_broken_seq: int | None
    first_broken_reason: str | None
    record_statuses: list[dict[str, Any]] = field(default_factory=list)

    def __iter__(self) -> Iterator[Any]:
        yield self.valid
        yield self.first_broken_reason


class AuditReader:
    """Reads JSONL audit files from a directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def records(
        self, since: date | None = None, until: date | None = None
    ) -> Iterator[AuditRecord]:
        for path in self._sorted_files(since, until):
            yield from self._read_file(path)

    def records_today(self, pinned_date: date | str | None = None) -> list[AuditRecord]:
        if pinned_date is not None:
            day = date.fromisoformat(pinned_date) if isinstance(pinned_date, str) else pinned_date
            return list(self.records(since=day, until=day))

        active_day_files = sorted(
            [p for p in self.directory.glob("*.jsonl") if not p.name.endswith(".gz") and len(p.stem) == 10]
        )
        if active_day_files:
            return list(self._read_file(active_day_files[-1]))

        return list(self.records())

    def read_file(self, filename: str | Path) -> list[AuditRecord]:
        path = self.directory / filename if not isinstance(filename, Path) or not filename.is_absolute() else filename
        if not path.exists():
            return []
        return list(self._read_file(path))

    def find(self, **criteria: Any) -> list[AuditRecord]:
        """Return every record across every file matching criteria."""
        return [r for r in self.records() if r.matches(**criteria)]

    def verify_chain(
        self, since: date | None = None, until: date | None = None
    ) -> ChainVerificationResult:
        """Recompute the hash chain and record status for each entry."""
        expected_prev: str | None = None
        statuses: list[dict[str, Any]] = []
        is_all_valid = True
        first_broken_seq: int | None = None
        first_broken_reason: str | None = None

        for idx, record in enumerate(self.records(since=since, until=until)):
            if record.is_corrupt:
                status = {
                    "index": idx,
                    "seq": record.seq,
                    "valid": False,
                    "reason": "corrupt json",
                }
                if is_all_valid:
                    is_all_valid = False
                    first_broken_seq = record.seq
                    first_broken_reason = f"corrupt json at index {idx}: {record.parse_error}"
            elif expected_prev is not None and record.prev_hash != expected_prev:
                status = {
                    "index": idx,
                    "seq": record.seq,
                    "valid": False,
                    "reason": "broken prev_hash",
                }
                if is_all_valid:
                    is_all_valid = False
                    first_broken_seq = record.seq
                    first_broken_reason = f"broken at seq={record.seq}"
            else:
                status = {
                    "index": idx,
                    "seq": record.seq,
                    "valid": True,
                    "reason": None,
                }
                expected_prev = "sha256:" + hashlib.sha256(record.raw_line).hexdigest()

            statuses.append(status)

        return ChainVerificationResult(
            valid=is_all_valid,
            first_broken_seq=first_broken_seq,
            first_broken_reason=first_broken_reason,
            record_statuses=statuses,
        )

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
            day: date | None = None
            if name.endswith(".jsonl"):
                try:
                    day = date.fromisoformat(name[:-6])
                except ValueError:
                    day = None
            elif name.endswith(".jsonl.gz"):
                try:
                    day = date.fromisoformat(name[:-9])
                except ValueError:
                    day = None
            else:
                continue

            if day is not None:
                if since is not None and day < since:
                    continue
                if until is not None and day > until:
                    continue
            elif since is not None or until is not None:
                # Undated file excluded when date filtering is requested
                continue

            paths.append(path)
        paths.sort()
        return paths

    def _read_file(self, path: Path) -> Iterator[AuditRecord]:
        def _lines() -> Iterator[bytes]:
            if path.suffix == ".gz":
                with gzip.open(path, "rb") as fh:
                    yield from fh
            else:
                with path.open("rb") as fh:
                    yield from fh

        for line in _lines():
            stripped = line.rstrip(b"\r\n")
            if not stripped:
                continue
            try:
                record = json.loads(stripped.decode("utf-8"))
                yield AuditRecord(raw_line=line, record=record)
            except json.JSONDecodeError as exc:
                yield AuditRecord(raw_line=line, record={"seq": -1}, is_corrupt=True, parse_error=str(exc))


def iter_days(start: date, stop: date) -> Iterable[date]:
    current = start
    while current <= stop:
        yield current
        current += timedelta(days=1)
