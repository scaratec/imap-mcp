"""Cross-account move saga (ADR 0006).

Executes: BEGIN → FETCH source → APPEND target → VERIFY → DELETE source → COMMIT.
On any step failure, the WAL captures the incident and the saga enters
a retry loop bounded by `retry_limit` (default 3). Exceeding the limit
advances the transaction to `needs_operator`.

Idempotency: the target VERIFY step checks for an existing message
with the same Message-ID (primary) or 5-tuple fallback before
re-APPENDing on recovery. See ADR 0008.

The saga is invoked from the move/copy handlers; the happy path
runs synchronously. Retry recovery runs when a caller polls
`get_transaction_status` and finds a non-terminal state, or during
server startup via `run_pending_recovery()`.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .config import Account
from .imap_core import (
    append_message,
    fetch_full_message,
    search_uids,
)
from .wal import WAL


def _maybe_crash(at: str) -> None:
    """Test-only crash injection (LIM-0004 style, but for saga state).

    If `IMAP_MCP_CRASH_AT` matches `at`, flush stdio and terminate
    with `os._exit(1)`. Used by saga_crash_recovery.feature to exit
    the server at a known WAL state.
    """
    if os.environ.get("IMAP_MCP_CRASH_AT") != at:
        return
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(1)


@dataclass
class SagaResult:
    tx_id: str
    mechanism: str
    result: str  # "OK" or "ERROR"
    error_type: str | None = None


AccountResolver = Callable[[str], Awaitable[tuple[Account, str]]]


class SagaManager:
    """Pure orchestrator — IMAP I/O happens through the injected callables."""

    def __init__(
        self,
        wal: WAL,
        audit_emitter: Any | None,
        retry_limit: int = 3,
        account_resolver: AccountResolver | None = None,
    ) -> None:
        self.wal = wal
        self.audit = audit_emitter
        self.retry_limit = retry_limit
        self.account_resolver = account_resolver

    def _audit_step(
        self,
        tx_id: str,
        step: str,
        outcome: str | None = "OK",
        reason: str = "saga_step",
    ) -> None:
        if self.audit is None:
            return
        self.audit.write(
            {
                "caller_id": None,
                "tool": "saga_transition",
                "tx_id": tx_id,
                "step": step,
                "decision": "ALLOW",
                "reason": reason,
                "result": outcome or "OK",
            }
        )

    async def _search_target_for_existing(
        self,
        dst_account: Account,
        dst_password: str,
        dst_folder: str,
        *,
        message_id: str | None,
        raw: bytes | None,
        fallback: dict[str, Any] | None = None,
    ) -> "list[int] | str":
        """Idempotency lookup. Returns:
        - `[]`        if the message is not yet on target,
        - `[uid]`     if exactly one match (Message-ID or unique 5-tuple),
        - `"ambiguous"` if the 5-tuple matches ≥ 2 candidates with
          identical first-4-KiB SHA-256 (ADR 0008 §fallback ambiguity).
        """
        # Primary key: Message-ID.
        if message_id is not None:
            try:
                hits = await search_uids(
                    dst_account, dst_password, dst_folder,
                    f'HEADER "Message-Id" "{message_id}"',
                )
            except Exception:
                hits = []
            return list(hits)

        # Fallback key: 5-tuple. Read either from `raw` (live FETCH)
        # or from a pre-staged WAL row (`fallback` dict).
        key = fallback if fallback is not None else (
            _extract_fallback_key(raw) if raw is not None else None
        )
        if key is None or key.get("fallback_from") is None:
            return []
        sender = str(key["fallback_from"])
        sent_iso = str(key.get("fallback_date") or "")
        subject = str(key.get("fallback_subject") or "")
        size = int(key.get("fallback_size") or 0)
        sha = str(key.get("fallback_4kb_sha256") or "")
        # Build IMAP SEARCH for FROM + SUBJECT + (SENTON YYYY-MM-DD).
        terms: list[str] = []
        if sender:
            terms.append(f'FROM "{sender}"')
        if subject:
            terms.append(f'SUBJECT "{subject}"')
        sent_date = sent_iso[:10] if sent_iso else ""
        if sent_date:
            try:
                from datetime import datetime
                d = datetime.fromisoformat(sent_date)
                terms.append(f'SENTON "{d.strftime("%d-%b-%Y")}"')
            except ValueError:
                pass
        if not terms:
            return []
        criteria = " ".join(terms)
        try:
            candidates = await search_uids(
                dst_account, dst_password, dst_folder, criteria
            )
        except Exception:
            return []
        # Confirm size + 4-KiB hash for each candidate. Same size +
        # same 4-KiB SHA-256 = the same payload to the precision the
        # spec accepts; ≥ 2 such matches = ambiguous → escalate.
        confirmed: list[int] = []
        for uid in candidates:
            try:
                payload = await fetch_full_message(
                    dst_account, dst_password, dst_folder, uid
                )
            except Exception:
                continue
            if payload is None:
                continue
            if size and len(payload) != size:
                continue
            payload_sha = hashlib.sha256(payload[:4096]).hexdigest()
            if sha and payload_sha != sha:
                continue
            confirmed.append(uid)
        if len(confirmed) >= 2:
            return "ambiguous"
        return confirmed

    async def run_cross_account_move(
        self,
        *,
        caller_id: str,
        src_account: Account,
        src_password: str,
        src_folder: str,
        src_uid: int,
        dst_account: Account,
        dst_password: str,
        dst_folder: str,
        delete_source: bool = True,
    ) -> SagaResult:
        tx_id = self.wal.begin(
            caller_id=caller_id,
            src_account=src_account.id,
            src_folder=src_folder,
            src_uid=src_uid,
            dst_account=dst_account.id,
            dst_folder=dst_folder,
        )
        self._audit_step(tx_id, "begin")
        _maybe_crash("post_begin")
        return await self._run_from_fetch(
            tx_id=tx_id,
            src_account=src_account,
            src_password=src_password,
            src_folder=src_folder,
            src_uid=src_uid,
            dst_account=dst_account,
            dst_password=dst_password,
            dst_folder=dst_folder,
            delete_source=delete_source,
            known_message_id=None,
            known_target_uid=None,
            resume_from_status="pending",
        )

    async def _run_from_fetch(
        self,
        *,
        tx_id: str,
        src_account: Account,
        src_password: str,
        src_folder: str,
        src_uid: int,
        dst_account: Account,
        dst_password: str,
        dst_folder: str,
        delete_source: bool,
        known_message_id: str | None,
        known_target_uid: int | None,
        resume_from_status: str,
    ) -> SagaResult:
        """FETCH -> VERIFY idempotency -> APPEND -> VERIFY -> DELETE -> COMMIT."""

        message_id = known_message_id
        target_uid = known_target_uid

        # Skip FETCH/APPEND when resuming from `staged`: the message is
        # already on target; only the DELETE/COMMIT phase remains.
        if resume_from_status == "pending":
            # FETCH
            try:
                raw = await fetch_full_message(
                    src_account, src_password, src_folder, src_uid
                )
            except Exception as exc:
                count = self.wal.bump_retry(tx_id, f"fetch_failed: {exc}")
                if count >= self.retry_limit:
                    self.wal.mark_needs_operator(tx_id)
                    self._audit_step(tx_id, "escalated", outcome="ERROR")
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR",
                    error_type="fetch_failed",
                )
            if raw is None:
                self.wal.bump_retry(tx_id, "fetch_failed: uid_not_found")
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR",
                    error_type="uid_not_found",
                )
            content_hash = hashlib.sha256(raw).hexdigest()
            message_id = _extract_message_id(raw)
            self.wal.record_fetch(tx_id, message_id, content_hash)
            # When Message-ID is absent, capture the 5-tuple fallback
            # identity so recovery can re-locate the message on the
            # target without it (ADR 0008 §fallback_key).
            if message_id is None:
                fallback = _extract_fallback_key(raw)
                self.wal.record_fallback(tx_id, **fallback)
            self._audit_step(tx_id, "fetched")
            _maybe_crash("post_fetch")

            # VERIFY idempotency: is the message already at the target?
            existing = await self._search_target_for_existing(
                dst_account, dst_password, dst_folder,
                message_id=message_id, raw=raw,
            )
            if existing == "ambiguous":
                self.wal.mark_needs_operator(tx_id)
                self._audit_step(
                    tx_id, "escalated", outcome="ERROR",
                    reason="ambiguous_fallback_match",
                )
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR",
                    error_type="ambiguous_fallback_match",
                )
            if existing:
                target_uid = existing[0]
                self.wal.mark_staged(tx_id, target_uid)
                self._audit_step(tx_id, "staged")
                return await self._finish_delete_and_commit(
                    tx_id=tx_id,
                    src_account=src_account,
                    src_password=src_password,
                    src_folder=src_folder,
                    src_uid=src_uid,
                    delete_source=delete_source,
                )

            # APPEND target
            try:
                ok = await append_message(dst_account, dst_password, dst_folder, raw)
            except asyncio.TimeoutError:
                count = self.wal.bump_retry(tx_id, "append_failed: timeout")
                if count >= self.retry_limit:
                    self.wal.mark_needs_operator(tx_id)
                    self._audit_step(tx_id, "escalated", outcome="ERROR")
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR",
                    error_type="target_append_timeout",
                )
            except ConnectionRefusedError as exc:
                count = self.wal.bump_retry(tx_id, f"append_failed: {exc}")
                if count >= self.retry_limit:
                    self.wal.mark_needs_operator(tx_id)
                    self._audit_step(tx_id, "escalated", outcome="ERROR")
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR",
                    error_type="target_unreachable",
                )
            except Exception as exc:
                count = self.wal.bump_retry(tx_id, f"append_failed: {exc}")
                if count >= self.retry_limit:
                    self.wal.mark_needs_operator(tx_id)
                    self._audit_step(tx_id, "escalated", outcome="ERROR")
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR",
                    error_type="target_append_failed",
                )
            if not ok:
                count = self.wal.bump_retry(tx_id, "append_failed: server rejected APPEND")
                if count >= self.retry_limit:
                    self.wal.mark_needs_operator(tx_id)
                    self._audit_step(tx_id, "escalated", outcome="ERROR")
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR",
                    error_type="target_append_failed",
                )
            _maybe_crash("post_append_pre_staged")

            # VERIFY + record staged
            if message_id is not None:
                try:
                    hits = await search_uids(
                        dst_account,
                        dst_password,
                        dst_folder,
                        f'HEADER "Message-Id" "{message_id}"',
                    )
                    target_uid = hits[0] if hits else None
                except Exception:
                    target_uid = None
            self.wal.mark_staged(tx_id, target_uid)
            self._audit_step(tx_id, "staged")

        return await self._finish_delete_and_commit(
            tx_id=tx_id,
            src_account=src_account,
            src_password=src_password,
            src_folder=src_folder,
            src_uid=src_uid,
            delete_source=delete_source,
        )

    async def _finish_delete_and_commit(
        self,
        *,
        tx_id: str,
        src_account: Account,
        src_password: str,
        src_folder: str,
        src_uid: int,
        delete_source: bool,
    ) -> SagaResult:
        if delete_source:
            # Idempotent delete: if the source UID is already absent,
            # a prior saga run (or a post-DELETE crash) completed the
            # physical deletion; just record the transition.
            try:
                existing = await search_uids(
                    src_account, src_password, src_folder, f"UID {src_uid}"
                )
            except Exception:
                existing = [src_uid]
            if src_uid in existing:
                try:
                    await _delete_source(src_account, src_password, src_folder, src_uid)
                except Exception as exc:
                    count = self.wal.bump_retry(tx_id, f"delete_failed: {exc}")
                    if count >= self.retry_limit:
                        self.wal.mark_needs_operator(tx_id)
                        self._audit_step(tx_id, "escalated", outcome="ERROR")
                    return SagaResult(
                        tx_id=tx_id, mechanism="saga", result="ERROR",
                        error_type="source_delete_failed",
                    )
            self.wal.mark_deleted(tx_id)
            self._audit_step(tx_id, "deleted")
            _maybe_crash("post_delete")

        self.wal.commit(tx_id)
        self._audit_step(tx_id, "commit")
        return SagaResult(tx_id=tx_id, mechanism="saga", result="OK")

    async def resume(self, tx: dict) -> SagaResult | None:
        """Resume a single non-terminal transaction. Returns None if
        the resolver cannot produce credentials (e.g. the account was
        removed from config after the tx began)."""
        if self.account_resolver is None:
            return None
        # Crash-before-FETCH: the WAL has a BEGIN row but no
        # content_hash — meaning nothing was yet fetched from source.
        # There is no work to undo; mark the tx aborted and return.
        if tx["status"] == "pending" and not tx.get("content_hash"):
            self.wal.abort(tx["tx_id"], "crashed_before_fetch")
            self._audit_step(tx["tx_id"], "aborted", outcome="ERROR")
            return SagaResult(
                tx_id=tx["tx_id"], mechanism="saga", result="ERROR",
                error_type="crashed_before_fetch",
            )
        try:
            src_account, src_password = await self.account_resolver(tx["src_account"])
            dst_account, dst_password = await self.account_resolver(tx["dst_account"])
        except Exception:
            return None

        # Staged-but-no-target_uid: a Message-ID-less message whose
        # `target_uid` was never observed. Use the 5-tuple fallback
        # to locate it; ambiguity (two identical candidates on the
        # target) escalates per ADR 0008.
        if (
            tx["status"] == "staged"
            and not tx.get("target_uid")
            and not tx.get("message_id")
            and tx.get("fallback_from")
        ):
            fallback = {
                "fallback_from": tx.get("fallback_from"),
                "fallback_date": tx.get("fallback_date"),
                "fallback_subject": tx.get("fallback_subject"),
                "fallback_size": tx.get("fallback_size"),
                "fallback_4kb_sha256": tx.get("fallback_4kb_sha256"),
            }
            existing = await self._search_target_for_existing(
                dst_account, dst_password, tx["dst_folder"],
                message_id=None, raw=None, fallback=fallback,
            )
            if existing == "ambiguous":
                self.wal.mark_needs_operator(tx["tx_id"])
                self._audit_step(
                    tx["tx_id"], "escalated", outcome="ERROR",
                    reason="ambiguous_fallback_match",
                )
                return SagaResult(
                    tx_id=tx["tx_id"], mechanism="saga", result="ERROR",
                    error_type="ambiguous_fallback_match",
                )
            if existing:
                self.wal.mark_staged(tx["tx_id"], existing[0])

        return await self._run_from_fetch(
            tx_id=tx["tx_id"],
            src_account=src_account,
            src_password=src_password,
            src_folder=tx["src_folder"],
            src_uid=int(tx["src_uid"]),
            dst_account=dst_account,
            dst_password=dst_password,
            dst_folder=tx["dst_folder"],
            delete_source=True,
            known_message_id=tx.get("message_id"),
            known_target_uid=tx.get("target_uid"),
            resume_from_status=tx["status"],
        )

    async def run_pending_recovery(self) -> int:
        """One pass over every non-terminal tx. Returns count processed."""
        processed = 0
        for tx in self.wal.pending_transactions():
            if tx["status"] in ("committed", "aborted", "needs_operator"):
                continue
            await self.resume(tx)
            processed += 1
        return processed


def _extract_message_id(raw: bytes) -> str | None:
    """Parse Message-ID header from RFC822 bytes."""
    import email

    msg = email.message_from_bytes(raw)
    return msg.get("Message-ID")


def _extract_fallback_key(raw: bytes) -> dict[str, Any]:
    """Extract the 5-tuple fallback identity (ADR 0008) from RFC822
    bytes: From, Date (parsed to ISO 8601), Subject, byte size,
    SHA-256 of the first 4 KiB."""
    import email
    from datetime import timezone
    from email.utils import parsedate_to_datetime

    msg = email.message_from_bytes(raw)
    sender = msg.get("From") or ""
    subject = msg.get("Subject") or ""
    date_iso: str | None = None
    raw_date = msg.get("Date")
    if raw_date:
        try:
            parsed = parsedate_to_datetime(raw_date)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            date_iso = parsed.astimezone(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
        except (TypeError, ValueError):
            date_iso = None
    return {
        "fallback_from": sender,
        "fallback_date": date_iso,
        "fallback_subject": subject,
        "fallback_size": len(raw),
        "fallback_4kb_sha256": hashlib.sha256(raw[:4096]).hexdigest(),
    }


async def _delete_source(
    account: Account, password: str, folder: str, uid: int
) -> None:
    """Detach the message at `uid` from `folder`.

    Uses MOVE-to-trash-folder-emulation via STORE \\Deleted + EXPUNGE,
    which is the DELETE component of the saga.
    """
    from .fault_injection import get_registry
    from .imap_core import _imap_user_for, _open_imap

    imap = await _open_imap(account)
    await imap.login(_imap_user_for(account), password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            raise RuntimeError(f"SELECT {folder!r} failed: {status}")
        status, _ = await imap.uid("store", str(uid), "+FLAGS", r"(\Deleted)")
        if status != "OK":
            raise RuntimeError(f"STORE \\Deleted failed: {status}")
        await get_registry().check_expunge(account.id)
        status, _ = await imap.expunge()
        if status != "OK":
            raise RuntimeError(f"EXPUNGE failed: {status}")
    finally:
        try:
            await imap.logout()
        except Exception:
            pass
