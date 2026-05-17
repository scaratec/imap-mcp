"""Audit log — ADR 0021 (format) and ADR 0022 (retention).

Writes JSONL records with a SHA-256 hash chain to a per-day file in
the configured audit directory. Day rotation on UTC midnight; closing
a day emits an `eof_day` record carrying the day's `final_hash` and
optionally invokes an external root-hash hook (ADR 0022). Files older
than `hot_days` are gzipped to `<day>.jsonl.gz`; files older than
`hot_days + warm_days` are deleted, with a `retention_delete` audit
record naming the filename and observed age.

Time mocking for tests: `IMAP_MCP_FAKE_NOW_UTC` (ISO 8601) is read on
every `_now()` call so a scenario can advance the clock between
operations without restarting the server.

Strict no-content-leak rule (ADR 0021): the writer refuses to log a
record carrying any field in `FORBIDDEN_FIELDS`.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import shutil
import subprocess
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


def _now_utc() -> datetime:
    """Wall-clock helper that honours ``TestHooks.fake_now_utc`` so
    BDD scenarios can advance the clock without restarting the
    server. Falls back to ``datetime.now`` in production."""
    from .test_hooks import get_global_hooks

    raw = get_global_hooks().fake_now_utc
    if raw:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(raw).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(tz=timezone.utc)


@dataclass
class AuditWriter:
    directory: Path
    hot_days: int = 90
    warm_days: int = 275
    delete_after_days: int = 365
    external_root_hook: str | None = None
    _lock: Lock = field(default_factory=Lock)
    _seq: int = 0
    _current_day: str = ""
    _current_path: Path | None = None
    _prev_hash: str = "sha256:" + ("0" * 64)
    _missing_file_reported: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        self._close_stale_files_on_startup()

    def _close_stale_files_on_startup(self) -> None:
        """If a previous run left an open `*.jsonl` whose date is in
        the past relative to today, append the eof_day trailer + lock
        permissions to 0400 — the day-roll that should have happened
        on UTC midnight if the server had been running. Without this,
        a server restart after a date change leaves the prior day's
        file un-closed and the chain hash-trailer absent.

        Files whose last line is not a chain record (i.e. lacks
        `seq`+`prev_hash`) are not real audit logs — they may be
        placeholders staged by the BDD harness for retention tests.
        Skipped to avoid mutating their bytes (and breaking the
        round-trip SHA-256 the test asserts).

        The original mtime is restored after the trailer write so
        retention rotation continues to classify the file by its
        intended date, not by the wall-clock at restart."""
        today = _now_utc().strftime("%Y-%m-%d")
        for path in sorted(self.directory.glob("*.jsonl")):
            day = path.stem
            if day >= today:
                continue
            try:
                stat_before = path.stat()
                was_readonly = stat_before.st_mode & 0o200 == 0
                if was_readonly:
                    try:
                        os.chmod(path, 0o600)
                    except OSError:
                        continue
                if not self._restore_chain_state_from(path):
                    if was_readonly:
                        try:
                            os.chmod(path, stat_before.st_mode & 0o777)
                        except OSError:
                            pass
                    continue
                self._current_path = path
                self._current_day = day
                self._emit_eof_day(path)
                self._current_path = None
                self._current_day = ""
                self._seq = 0
                try:
                    os.chmod(path, 0o400)
                except OSError:
                    pass
                try:
                    os.utime(path, (stat_before.st_atime, stat_before.st_mtime))
                except OSError:
                    pass
                if self.external_root_hook:
                    self._invoke_hook(self._prev_hash)
            except Exception:
                continue

    def _restore_chain_state_from(self, path: Path) -> bool:
        """Read the last record of `path` and seed `_prev_hash`/`_seq`
        from it so the next emit chains correctly.

        Returns True if a real chain record was found (last line
        carries both `seq` and `prev_hash`). Returns False otherwise —
        the file is likely a placeholder, not a chained audit log."""
        last_line: bytes | None = None
        with open(path, "rb") as fh:
            for raw in fh:
                if raw.strip():
                    last_line = raw
        if last_line is None:
            return False
        try:
            record = json.loads(last_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if "seq" not in record or "prev_hash" not in record:
            return False
        self._prev_hash = "sha256:" + hashlib.sha256(last_line).hexdigest()
        seq = record.get("seq")
        self._seq = int(seq) + 1 if isinstance(seq, int) else 0
        return True

    # ----------------------------------------------------------- writing

    def write(self, record: dict[str, Any]) -> None:
        """Append one record. Returns after fsync."""
        with self._lock:
            self._rotate_if_needed()
            self._reject_forbidden(record)
            self._detect_missing_active_file()
            self._append_record(record)

    def _append_record(self, record: dict[str, Any]) -> None:
        now = _now_utc()
        # Post-increment: each file's first record has seq=0 (ADR 0021 §day_roll).
        seq = self._seq
        self._seq += 1
        full = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
            "seq": seq,
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

    def _reject_forbidden(self, record: dict[str, Any]) -> None:
        for f in record:
            if f in FORBIDDEN_FIELDS:
                raise RuntimeError(
                    f"Audit writer refuses to log forbidden field {f!r} "
                    "(ADR 0021 no-content-leak rule)"
                )

    def _detect_missing_active_file(self) -> None:
        """If the active day's file disappeared out-of-band, emit one
        `audit_file_missing` record per missing filename — to the next
        successful write target — before continuing.

        This must only fire if at least one record has already been
        written to the active path; the `_rotate_if_needed` helper
        sets `_current_path` *before* the first write, so on the
        very first append of a new day the file legitimately does
        not yet exist."""
        if self._current_path is None:
            return
        if self._seq == 0:
            return
        if self._current_path.exists():
            return
        missing_name = self._current_path.name
        if missing_name in self._missing_file_reported:
            return
        self._missing_file_reported.add(missing_name)
        _log.critical("Active audit file disappeared: %s", missing_name)
        # Recreate the file (as if from scratch) and mark the missing event.
        self._current_path.touch()
        try:
            os.chmod(self._current_path, 0o600)
        except OSError:
            pass
        # Use a direct line write so this record is the very first
        # entry in the recreated file.
        now = _now_utc()
        self._seq += 1
        full = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
            "seq": self._seq,
            "prev_hash": self._prev_hash,
            "tool": "audit_file_missing",
            "decision": "DENY",
            "reason": "audit_file_missing",
            "result": "ERROR",
            "filename": missing_name,
        }
        line = json.dumps(full, sort_keys=True, separators=(",", ":")) + "\n"
        with open(self._current_path, "ab") as fh:
            fh.write(line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        self._prev_hash = "sha256:" + hashlib.sha256(line.encode("utf-8")).hexdigest()

    # ---------------------------------------------------------- rotation

    def _rotate_if_needed(self) -> None:
        today = _now_utc().strftime("%Y-%m-%d")
        if today == self._current_day and self._current_path is not None:
            return
        # Close prior file: emit eof_day with final_hash, set 0400,
        # invoke external_root_hook with the final_hash.
        if self._current_path is not None and self._current_path.exists():
            self._emit_eof_day(self._current_path)
            try:
                os.chmod(self._current_path, 0o400)
            except OSError:
                pass
            if self.external_root_hook:
                self._invoke_hook(self._prev_hash)
        self._current_day = today
        self._current_path = self.directory / f"{today}.jsonl"
        self._seq = 0
        # `prev_hash` is intentionally not reset: it carries forward
        # across the day boundary so the chain remains verifiable.

    def _emit_eof_day(self, path: Path) -> None:
        """Append a single eof_day record carrying `final_hash`.

        `final_hash` is the chain hash up to and including the last
        content record — exactly the value the next day's first
        record will use as its `prev_hash`. The eof_day itself is NOT
        chained into the next day; it is a self-describing trailer
        that names the closing hash so an offline verifier can
        cross-file the chain without scanning the entire archive."""
        now = _now_utc()
        final_hash = self._prev_hash
        seq = self._seq
        self._seq += 1
        full = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
            "seq": seq,
            "prev_hash": self._prev_hash,
            "tool": "eof_day",
            "decision": "ALLOW",
            "reason": "day_roll",
            "result": "OK",
            "final_hash": final_hash,
        }
        line = json.dumps(full, sort_keys=True, separators=(",", ":")) + "\n"
        with open(path, "ab") as fh:
            fh.write(line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        # Carry `final_hash` (NOT the eof_day's own line hash) forward
        # so the next day's first record's prev_hash equals what the
        # eof_day declared.
        self._prev_hash = final_hash

    def _invoke_hook(self, final_hash: str) -> None:
        cmd = (self.external_root_hook or "").replace("%FINAL_HASH%", final_hash)
        try:
            subprocess.run(cmd, shell=True, check=False, timeout=5)
        except Exception:
            # Hook failures must not break audit writing.
            pass

    # ---------------------------------------------------------- retention

    def rotate(self) -> dict[str, int]:
        """Run a full retention pass: day-roll if needed, gzip files
        older than `hot_days`, delete files older than
        `hot_days + warm_days`. Returns a small summary for tests.
        """
        with self._lock:
            self._rotate_if_needed()
            gz_count = self._compress_old()
            del_count = self._delete_expired()
        return {"gzipped": gz_count, "deleted": del_count}

    def _compress_old(self) -> int:
        """Compress every `*.jsonl` whose mtime is older than
        `hot_days`, except the currently-active day's file."""
        cutoff = _now_utc() - timedelta(days=self.hot_days)
        n = 0
        for path in sorted(self.directory.glob("*.jsonl")):
            if self._current_path is not None and path == self._current_path:
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

    def _delete_expired(self) -> int:
        cutoff = _now_utc() - timedelta(days=self.delete_after_days)
        n = 0
        candidates = list(self.directory.glob("*.jsonl.gz")) + list(self.directory.glob("*.jsonl"))
        for path in sorted(candidates):
            if self._current_path is not None and path == self._current_path:
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if mtime < cutoff:
                age_days = int((_now_utc() - mtime).total_seconds() / 86400)
                filename = path.name
                path.unlink()
                # Emit retention_delete *after* the file is removed so
                # the audit record reflects the action taken, not an
                # intent. This call goes through the normal write()
                # path (which honours day-roll itself).
                self._lock.release()
                try:
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
                finally:
                    self._lock.acquire()
                n += 1
        return n
