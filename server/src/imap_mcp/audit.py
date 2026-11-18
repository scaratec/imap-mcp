"""Audit log — ADR 0021, ADR 0022, ADR 0032, ADR 0048.

Writes JSONL records with a SHA-256 hash chain to a per-day file in
the configured audit directory. Pre-effect intent records fsync before
every mutating IMAP command or artifact creation; outcome records record
the final result. All schema (-32602) and unhandled exceptions are audited.
"""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import hmac
import json
import logging
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
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
        "saved_to_absolute",
    ]
)

GENESIS_HASH = "sha256:" + ("0" * 64)
LOCK_FILENAME = ".audit.lock"
_TAIL_BLOCK = 8192


class AuditUnavailableError(RuntimeError):
    """The audit trail cannot be written. Handlers fail closed with this error."""


def _now_utc() -> datetime:
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
    return datetime.now(tz=UTC)


def _parse_utc(raw: str) -> datetime | None:
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).astimezone(UTC)
    except ValueError:
        return None


def _day_of(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _ts(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _line_hash(line: bytes) -> str:
    return "sha256:" + hashlib.sha256(line).hexdigest()


def compute_keyed_digest(key: bytes, message: bytes) -> str:
    """HMAC-SHA256 digest prefixed with 'hmac-sha256:'."""
    return "hmac-sha256:" + hmac.new(key, message, hashlib.sha256).hexdigest()


def _ends_with_newline(path: Path) -> bool:
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
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or "seq" not in record or "prev_hash" not in record:
        return None
    return record


def _last_chain_record(path: Path) -> tuple[bytes, dict[str, Any]] | None:
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
    warm_days: int | None = None
    delete_after_days: int = 365
    external_root_hook: str | list[str] | None = None
    hmac_key: bytes = b""
    _lock: Lock = field(default_factory=Lock)
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
        if self.warm_days is not None and self.hot_days + self.warm_days == self.delete_after_days:
            pass
        elif self.delete_after_days <= self.hot_days:
            self.delete_after_days = self.hot_days + 275

        try:
            with self._exclusive():
                today = _day_of(_now_utc())
                sealed_any, _ = self._seal_past_days(today)
                if sealed_any:
                    self._run_retention_locked(today)
        except OSError as exc:
            _log.critical("audit trail not writable at start-up: %s", exc)

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        with self._lock:
            fd = os.open(self.directory / LOCK_FILENAME, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    def is_writable(self) -> bool:
        """Check if audit directory is writable."""
        try:
            with self._exclusive():
                return os.access(self.directory, os.W_OK)
        except OSError:
            return False

    def write(self, record: dict[str, Any]) -> None:
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
        last = _last_chain_record(path)
        if last is None:
            sealed_any, final_h = self._seal_past_days(today)
            if sealed_any:
                self._run_retention_locked(today)
            last_after = _last_chain_record(path)
            if last_after is not None:
                line, record = last_after
                seq = record.get("seq")
                return (seq + 1 if isinstance(seq, int) else 0), _line_hash(line)
            return 0, final_h
        line, record = last
        if record.get("tool") == "eof_day":
            raise PermissionError(f"{path} is already sealed (eof_day); clock moved backwards")
        seq = record.get("seq")
        return (seq + 1 if isinstance(seq, int) else 0), _line_hash(line)

    def _append(
        self, path: Path, now: datetime, seq: int, prev_hash: str, record: dict[str, Any]
    ) -> None:
        full = {"ts": _ts(now), "seq": seq, "prev_hash": prev_hash, **record}
        line = (json.dumps(full, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
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
                    f"Audit writer refuses to log forbidden field {f!r} (ADR 0021 / ADR 0048)"
                )

    def _report_missing_active_file(self, path: Path, now: datetime) -> None:
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
                "missing_filename": missing_name,
            },
        )

    def _seal_past_days(self, today: str) -> tuple[bool, str]:
        head = GENESIS_HASH
        sealed_any = False
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
            sealed_any = True
            if self.external_root_hook:
                self._invoke_hook(final_hash)
        return sealed_any, head

    def _emit_eof_day(self, path: Path, seq: int, final_hash: str) -> None:
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
        if not self.external_root_hook:
            return
        if isinstance(self.external_root_hook, list):
            args = [arg.replace("%FINAL_HASH%", final_hash) for arg in self.external_root_hook]
        else:
            cmd_str = self.external_root_hook.replace("%FINAL_HASH%", final_hash)
            args = shlex.split(cmd_str)
        if not args:
            return
        clean_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LC_ALL": "C.UTF-8",
        }
        try:
            subprocess.run(args, env=clean_env, shell=False, check=False, timeout=5)
        except Exception:  # noqa: BLE001, S110
            pass

    def _run_retention_locked(self, today: str) -> None:
        active = self.directory / f"{today}.jsonl"
        self._compress_old(active)
        deleted = self._delete_expired(active)
        for filename, age_days in deleted:
            try:
                now = _now_utc()
                path = active
                last = _last_chain_record(path)
                if last is None:
                    _, final_h = self._seal_past_days(today)
                    seq = 0
                    prev_hash = final_h
                else:
                    line, record = last
                    seq_val = record.get("seq")
                    seq = seq_val + 1 if isinstance(seq_val, int) else 0
                    prev_hash = _line_hash(line)
                self._append(
                    path,
                    now,
                    seq,
                    prev_hash,
                    {
                        "tool": "retention_delete",
                        "decision": "ALLOW",
                        "reason": "retention_delete",
                        "result": "OK",
                        "filename": filename,
                        "age_days": age_days,
                    },
                )
            except Exception:  # noqa: BLE001, S110
                pass

    def rotate(self) -> dict[str, int]:
        """Manual retention trigger (for tests / _test_run_audit_rotation)."""
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

        for filename, age_days in deleted:
            self.write(
                {
                    "tool": "retention_delete",
                    "decision": "ALLOW",
                    "reason": "retention_delete",
                    "result": "OK",
                    "filename": filename,
                    "age_days": age_days,
                }
            )
        return {"gzipped": gz_count, "deleted": len(deleted)}

    def _file_age_days(self, path: Path, today: datetime) -> int | None:
        name = path.name
        date_str = name[:10]
        try:
            file_date = date.fromisoformat(date_str)
            return (today.date() - file_date).days
        except ValueError:
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
                return int((today - mtime).total_seconds() / 86400)
            except OSError:
                return None

    def _compress_old(self, active: Path) -> int:
        now = _now_utc()
        n = 0
        for path in sorted(self.directory.glob("*.jsonl")):
            if path == active:
                continue
            age = self._file_age_days(path, now)
            if age is not None and age > self.hot_days:
                self._gzip_file(path)
                n += 1
        return n

    def _gzip_file(self, path: Path) -> None:
        gz_path = path.with_name(path.name + ".gz")
        with open(path, "rb") as src, gzip.open(gz_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        try:
            os.chmod(gz_path, 0o400)
            stat = path.stat()
            os.utime(gz_path, (stat.st_atime, stat.st_mtime))
        except OSError:
            pass
        path.unlink()

    def _delete_expired(self, active: Path) -> list[tuple[str, int]]:
        now = _now_utc()
        deleted: list[tuple[str, int]] = []
        candidates = list(self.directory.glob("*.jsonl.gz")) + list(self.directory.glob("*.jsonl"))
        for path in sorted(candidates):
            if path == active:
                continue
            age = self._file_age_days(path, now)
            if age is not None and age > self.delete_after_days:
                try:
                    path.unlink()
                    deleted.append((path.name, age))
                except OSError:
                    pass
        return deleted
