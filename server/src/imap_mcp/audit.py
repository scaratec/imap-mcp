"""Audit log — ADR 0021 (format), ADR 0022 (retention), ADR 0032 (shared
writer).

Writes JSONL records with a SHA-256 hash chain to a per-day file in
the configured audit directory. The day boundary is UTC midnight;
closing a day emits an `eof_day` record carrying the day's
`final_hash` and optionally invokes an external root-hash hook
(ADR 0022). Files older than `hot_days` are gzipped to
`<day>.jsonl.gz`; files older than `hot_days + warm_days` are deleted,
with a `retention_delete` audit record naming the filename and
observed age.

Several server processes may share one audit directory (ADR 0032):
an MCP client typically starts one stdio server per session. Every
write therefore runs under an exclusive `flock` on `<dir>/.audit.lock`
and derives the target file, `seq` and `prev_hash` from what is on
disk at that moment, never from per-process memory. Whichever process
writes first on a new UTC day seals the previous day files. A file
whose last record is `eof_day` is sealed and is never written again.

Time mocking for tests: `IMAP_MCP_FAKE_NOW_FILE` (re-read on every
call) and `IMAP_MCP_FAKE_NOW_UTC` (fixed at start-up), see
`test_hooks.py`.

Strict no-content-leak rule (ADR 0021): the writer refuses to log a
record carrying any field in `FORBIDDEN_FIELDS`.
"""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

_log = logging.getLogger(__name__)


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

GENESIS_HASH = "sha256:" + ("0" * 64)
LOCK_FILENAME = ".audit.lock"
_TAIL_BLOCK = 8192


class AuditUnavailableError(RuntimeError):
    """The audit trail cannot be written. Every audited operation
    fails closed with this error; its message starts with
    ``audit trail not writable:`` and names the underlying cause."""


def _now_utc() -> datetime:
    """Wall-clock helper that honours the fake-clock test hooks so BDD
    scenarios can move the clock without restarting the server. Falls
    back to ``datetime.now`` in production."""
    from .test_hooks import get_global_hooks

    hooks = get_global_hooks()
    if hooks.fake_now_file:
        try:
            parsed = _parse_utc(Path(hooks.fake_now_file).read_text(encoding="utf-8").strip())
        except OSError:
            parsed = None
        if parsed is not None:
            return parsed
    if hooks.fake_now_utc:
        parsed = _parse_utc(hooks.fake_now_utc)
        if parsed is not None:
            return parsed
    return datetime.now(tz=timezone.utc)


def _parse_utc(raw: str) -> datetime | None:
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except ValueError:
        return None


def _day_of(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _ts(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _line_hash(line: bytes) -> str:
    return "sha256:" + hashlib.sha256(line).hexdigest()


def _ends_with_newline(path: Path) -> bool:
    """True for an absent or empty file, or one whose last byte is a newline."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                return True
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) == b"\n"
    except FileNotFoundError:
        return True


def _chain_record(raw: bytes) -> dict[str, Any] | None:
    """Parse one line; return it only if it is a chained audit record
    (carries both `seq` and `prev_hash`)."""
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or "seq" not in record or "prev_hash" not in record:
        return None
    return record


def _last_chain_record(path: Path) -> tuple[bytes, dict[str, Any]] | None:
    """Return (raw line incl. newline, record) of the last chained
    record in `path`, or None if the file holds none (absent, empty,
    or a placeholder staged by the BDD harness).

    Reads only the tail in the common case; falls back to a full scan
    when the last line is not a chain record."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            start = end
            body = b""
            while start > 0:
                start = max(0, start - _TAIL_BLOCK)
                fh.seek(start)
                body = fh.read(end - start).rstrip(b"\r\n")
                if b"\n" in body:
                    break
            if body:
                last_raw = body.rsplit(b"\n", 1)[-1]
                record = _chain_record(last_raw)
                if record is not None:
                    return last_raw + b"\n", record
            fh.seek(0)
            found: tuple[bytes, dict[str, Any]] | None = None
            for raw in fh:
                if not raw.strip():
                    continue
                record = _chain_record(raw)
                if record is not None:
                    found = (raw if raw.endswith(b"\n") else raw + b"\n", record)
            return found
    except FileNotFoundError:
        return None


@dataclass
class AuditWriter:
    directory: Path
    hot_days: int = 90
    warm_days: int = 275
    delete_after_days: int = 365
    external_root_hook: str | None = None
    _lock: Lock = field(default_factory=Lock)
    # What this process last wrote. Used only to detect an active file
    # that disappeared out-of-band; the chain itself is always read
    # from disk.
    _last_written_path: Path | None = None
    _last_written_seq: int = -1
    _last_written_hash: str = GENESIS_HASH
    _missing_file_reported: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        # A previous run may have left a past day unsealed. Sealing is
        # idempotent, so doing it here is harmless when another
        # instance already did; a failure surfaces on the first write.
        try:
            with self._exclusive():
                self._seal_past_days(_day_of(_now_utc()))
        except OSError as exc:
            _log.critical("audit trail not writable at start-up: %s", exc)

    # ----------------------------------------------------------- locking

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        """Serialise against other threads of this process and against
        every other process sharing the audit directory."""
        with self._lock:
            fd = os.open(self.directory / LOCK_FILENAME, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    # ----------------------------------------------------------- writing

    def write(self, record: dict[str, Any]) -> None:
        """Append one record. Returns after fsync.

        Raises `AuditUnavailableError` if the trail cannot be written."""
        self._reject_forbidden(record)
        try:
            with self._exclusive():
                now = _now_utc()
                today = _day_of(now)
                path = self.directory / f"{today}.jsonl"
                self._report_missing_active_file(path, now)
                seq, prev_hash = self._chain_head(today, path)
                self._append(path, now, seq, prev_hash, record)
        except OSError as exc:
            _log.critical("audit trail not writable: %s", exc)
            raise AuditUnavailableError(f"audit trail not writable: {exc}") from exc

    def _chain_head(self, today: str, path: Path) -> tuple[int, str]:
        """Return (seq, prev_hash) for the next record of `path`."""
        last = _last_chain_record(path)
        if last is None:
            # First record of the day: close the previous day(s) and
            # chain onto the most recent final hash.
            return 0, self._seal_past_days(today)
        line, record = last
        if record.get("tool") == "eof_day":
            raise PermissionError(f"{path} is already sealed (eof_day); the clock moved backwards")
        seq = record.get("seq")
        return (seq + 1 if isinstance(seq, int) else 0), _line_hash(line)

    def _append(
        self, path: Path, now: datetime, seq: int, prev_hash: str, record: dict[str, Any]
    ) -> None:
        full = {"ts": _ts(now), "seq": seq, "prev_hash": prev_hash, **record}
        line = (json.dumps(full, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        # A torn final line (crash mid-write) must not swallow the new
        # record; terminate it so the new record stays a line of its own.
        lead = b"\n" if not _ends_with_newline(path) else b""
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
            os.write(fd, lead + line)
            os.fsync(fd)
        finally:
            os.close(fd)
        self._last_written_path = path
        self._last_written_seq = seq
        self._last_written_hash = _line_hash(line)

    def _reject_forbidden(self, record: dict[str, Any]) -> None:
        for f in record:
            if f in FORBIDDEN_FIELDS:
                raise RuntimeError(
                    f"Audit writer refuses to log forbidden field {f!r} "
                    "(ADR 0021 no-content-leak rule)"
                )

    def _report_missing_active_file(self, path: Path, now: datetime) -> None:
        """If the file this process last wrote to is today's file and it
        disappeared out-of-band, emit one `audit_file_missing` record
        per missing filename as the first entry of the recreated file.
        It chains onto the last record this process wrote, which is the
        best surviving evidence of the lost file's head."""
        if self._last_written_path != path or path.exists():
            return
        missing_name = path.name
        if missing_name in self._missing_file_reported:
            return
        self._missing_file_reported.add(missing_name)
        _log.critical("Active audit file disappeared: %s", missing_name)
        self._append(
            path,
            now,
            self._last_written_seq + 1,
            self._last_written_hash,
            {
                "tool": "audit_file_missing",
                "decision": "DENY",
                "reason": "audit_file_missing",
                "result": "ERROR",
                "filename": missing_name,
            },
        )

    # ---------------------------------------------------------- day roll

    def _seal_past_days(self, today: str) -> str:
        """Seal every chained day file before `today` that is not sealed
        yet and return the hash the first record of `today` chains onto.

        Must be called with the lock held. A file is sealed when its
        last chained record is `eof_day`; such a file is left untouched.
        Files without any chained record (placeholders staged by the
        BDD harness) are skipped so their bytes stay intact."""
        head = GENESIS_HASH
        for path in sorted(self.directory.glob("*.jsonl")):
            if path.stem >= today:
                continue
            last = _last_chain_record(path)
            if last is None:
                continue
            line, record = last
            if record.get("tool") == "eof_day":
                head = record.get("final_hash") or _line_hash(line)
                continue
            seq = record.get("seq")
            final_hash = _line_hash(line)
            self._emit_eof_day(path, seq + 1 if isinstance(seq, int) else 0, final_hash)
            head = final_hash
            if self.external_root_hook:
                self._invoke_hook(final_hash)
        return head

    def _emit_eof_day(self, path: Path, seq: int, final_hash: str) -> None:
        """Append the `eof_day` trailer, then lock the file to 0400.

        `final_hash` is the chain hash up to and including the last
        content record — exactly the value the next day's first record
        uses as its `prev_hash`. The eof_day itself is NOT chained into
        the next day; it is a self-describing trailer that names the
        closing hash so an offline verifier can cross-file the chain
        without scanning the entire archive.

        A day file left read-only but without trailer by an earlier run
        is reopened once for the trailer. The original mtime is restored
        so retention keeps classifying the file by its own day."""
        stat_before = path.stat()
        if stat_before.st_mode & 0o200 == 0:
            os.chmod(path, 0o600)
        now = _now_utc()
        full = {
            "ts": _ts(now),
            "seq": seq,
            "prev_hash": final_hash,
            "tool": "eof_day",
            "decision": "ALLOW",
            "reason": "day_roll",
            "result": "OK",
            "final_hash": final_hash,
        }
        line = json.dumps(full, sort_keys=True, separators=(",", ":")) + "\n"
        lead = b"\n" if not _ends_with_newline(path) else b""
        with open(path, "ab") as fh:
            fh.write(lead + line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(path, 0o400)
        try:
            os.utime(path, (stat_before.st_atime, stat_before.st_mtime))
        except OSError:
            pass

    def _invoke_hook(self, final_hash: str) -> None:
        cmd = (self.external_root_hook or "").replace("%FINAL_HASH%", final_hash)
        try:
            subprocess.run(cmd, shell=True, check=False, timeout=5)
        except Exception:
            # Hook failures must not break audit writing.
            pass

    # ---------------------------------------------------------- retention

    def rotate(self) -> dict[str, int]:
        """Run a full retention pass: seal past days, gzip files older
        than `hot_days`, delete files older than
        `hot_days + warm_days`. Returns a small summary for tests.
        """
        try:
            with self._exclusive():
                today = _day_of(_now_utc())
                self._seal_past_days(today)
                active = self.directory / f"{today}.jsonl"
                gz_count = self._compress_old(active)
                deleted = self._delete_expired(active)
        except OSError as exc:
            _log.critical("audit trail not writable: %s", exc)
            raise AuditUnavailableError(f"audit trail not writable: {exc}") from exc
        # Emitted *after* the files are removed so each record reflects
        # the action taken, not an intent.
        for filename, age_days in deleted:
            self.write(
                {
                    "tool": "retention_delete",
                    "decision": "ALLOW",
                    "reason": "warm_period_elapsed",
                    "result": "OK",
                    "filename": filename,
                    "age_days": age_days,
                }
            )
        return {"gzipped": gz_count, "deleted": len(deleted)}

    def _compress_old(self, active: Path) -> int:
        """Compress every `*.jsonl` whose mtime is older than
        `hot_days`, except the currently-active day's file."""
        cutoff = _now_utc() - timedelta(days=self.hot_days)
        n = 0
        for path in sorted(self.directory.glob("*.jsonl")):
            if path == active:
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                self._gzip_file(path)
                n += 1
        return n

    def _gzip_file(self, path: Path) -> None:
        gz_path = path.with_name(path.name + ".gz")
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = 0o400
        with open(path, "rb") as src, gzip.open(gz_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        try:
            os.chmod(gz_path, mode)
            # Preserve mtime so the next rotation pass classifies it
            # by the original date, not the gzip operation's clock.
            stat = path.stat()
            os.utime(gz_path, (stat.st_atime, stat.st_mtime))
        except OSError:
            pass
        path.unlink()

    def _delete_expired(self, active: Path) -> list[tuple[str, int]]:
        cutoff = _now_utc() - timedelta(days=self.delete_after_days)
        deleted: list[tuple[str, int]] = []
        candidates = list(self.directory.glob("*.jsonl.gz")) + list(self.directory.glob("*.jsonl"))
        for path in sorted(candidates):
            if path == active:
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if mtime < cutoff:
                age_days = int((_now_utc() - mtime).total_seconds() / 86400)
                path.unlink()
                deleted.append((path.name, age_days))
        return deleted
