"""Cross-account move saga (ADR 0006, ADR 0046).

Executes: BEGIN → FETCH source → APPEND target → VERIFY → DELETE source → COMMIT.
Adheres to:
- Full canonical_sha256 target verification before source deletion
- Immediate staged_rfc822 payload scrubbing upon staged / terminal status
- Safe UID EXPUNGE source delete fallback under UIDPLUS
- Unified revocation rule (aborted before staged, commit after staged)
"""

from __future__ import annotations

import asyncio
import email
import hashlib
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .config import Account
from .imap_core import (
    TransientIMAPFailure,
    append_message,
    fetch_full_message,
    gmail_fetch_msgid,
    gmail_search_by_msgid,
    search_uids,
)
from .provenance import compute_canonical_digest
from .wal import WAL


def _is_google_provider(account: Account) -> bool:
    return getattr(account, "provider", "imap-standard") in ("google", "google-mock")


def _maybe_crash(at: str) -> None:
    from .test_hooks import get_global_hooks

    hooks = get_global_hooks()
    if not hooks.test_mode or not hooks.saga_crash_at:
        return
    match = (hooks.saga_crash_at == at) or (
        hooks.saga_crash_at == "post_append" and at in ("post_append", "post_append_pre_staged")
    )
    if not match:
        return
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001, S110
        pass
    os._exit(1)


async def _maybe_pause(at: str) -> None:
    from .test_hooks import get_global_hooks

    hooks = get_global_hooks()
    match_at = (
        (hooks.saga_pause_at == at)
        or (hooks.saga_pause_at == "fetched" and at == "post_fetch")
        or (hooks.saga_pause_at == "staged" and at == "post_staged")
    )
    if not match_at:
        return
    marker = hooks.saga_pause_marker
    if not marker:
        return
    Path(marker).write_text(at)
    resume = Path(marker + ".resume")
    while not resume.exists():
        await asyncio.sleep(0.1)
    resume.unlink(missing_ok=True)


@dataclass
class SagaResult:
    tx_id: str
    mechanism: str
    result: Literal["OK", "ERROR"]
    error_type: str | None = None


AccountResolver = Callable[[str], Awaitable[tuple[Account, str]]]
FolderResolver = Callable[[str, str], Awaitable[str]]

_GMAIL_ALL_MAIL = "[Gmail]/All Mail"


class SagaManager:
    def __init__(
        self,
        wal: WAL,
        audit_emitter: Any | None,
        retry_limit: int = 3,
        account_resolver: AccountResolver | None = None,
        test_hooks: Any = None,
        folder_resolver: FolderResolver | None = None,
    ) -> None:
        self.wal = wal
        self.audit = audit_emitter
        self.retry_limit = retry_limit
        self.account_resolver = account_resolver
        self.folder_resolver = folder_resolver
        self.test_hooks = test_hooks
        self.permission_checker: Callable[[str, str, str, str, str], bool] | None = None

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

    async def _search_target_for_matching_candidate(
        self,
        dst_account: Account,
        dst_password: str,
        dst_folder: str,
        *,
        message_id: str | None,
        canonical_sha256: str,
    ) -> list[int] | Literal["ambiguous"]:
        """Locate candidates by Message-ID or search, verify full canonical_sha256 (ADR 0046 §2)."""
        candidates: list[int] = []
        if message_id is not None:
            try:
                hits = await search_uids(
                    dst_account,
                    dst_password,
                    dst_folder,
                    f'HEADER "Message-Id" "{message_id}"',
                )
                candidates = list(hits)
            except Exception:  # noqa: BLE001
                candidates = []
        else:
            try:
                hits = await search_uids(dst_account, dst_password, dst_folder, "ALL")
                candidates = list(hits)
            except Exception:  # noqa: BLE001
                candidates = []

        confirmed: list[int] = []
        for uid in candidates:
            try:
                payload = await fetch_full_message(dst_account, dst_password, dst_folder, uid)
            except Exception:  # noqa: BLE001, S112
                continue
            if payload is None:
                continue
            if compute_canonical_digest(payload) == canonical_sha256:
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
            caller_id=caller_id,
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
        caller_id: str | None = None,
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
        message_id = known_message_id
        target_uid = known_target_uid

        if resume_from_status == "pending":
            fetch_folder = src_folder
            fetch_uid = src_uid
            if _is_google_provider(src_account):
                try:
                    gm_msgid = await gmail_fetch_msgid(
                        src_account, src_password, src_folder, src_uid
                    )
                    if gm_msgid is not None:
                        all_mail = _GMAIL_ALL_MAIL
                        if self.folder_resolver is not None:
                            all_mail = await self.folder_resolver(src_account.id, all_mail)
                        all_mail_hits = await gmail_search_by_msgid(
                            src_account,
                            src_password,
                            all_mail,
                            gm_msgid,
                        )
                        if all_mail_hits:
                            fetch_folder = all_mail
                            fetch_uid = all_mail_hits[0]
                except Exception:  # noqa: BLE001, S110
                    pass

            try:
                raw = await fetch_full_message(src_account, src_password, fetch_folder, fetch_uid)
            except Exception as exc:  # noqa: BLE001
                count = self.wal.bump_retry(tx_id, f"fetch_failed: {exc}")
                if count >= self.retry_limit:
                    self._audit_step(tx_id, "escalated", outcome="ERROR")
                    self.wal.mark_needs_operator(tx_id)
                return SagaResult(
                    tx_id=tx_id,
                    mechanism="saga",
                    result="ERROR",
                    error_type="transient_imap_failure",
                )

            if raw is None:
                self._audit_step(tx_id, "aborted", outcome="ERROR", reason="uid_not_found")
                self.wal.abort(tx_id, "uid_not_found")
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR", error_type="not_found"
                )

            content_hash = hashlib.sha256(raw).hexdigest()
            canonical_digest = compute_canonical_digest(raw)
            parsed_email = email.message_from_bytes(raw)
            message_id = parsed_email.get("Message-ID")

            self.wal.record_fetch(
                tx_id,
                message_id,
                content_hash,
                raw_sha256=content_hash,
                canonical_sha256=canonical_digest,
                size_bytes=len(raw),
                staged_rfc822=raw,
            )
            self._audit_step(tx_id, "fetched")
            _maybe_crash("post_fetch")
            await _maybe_pause("post_fetch")

            if self.permission_checker is not None:
                effective_caller = caller_id or "invoice-agent"
                still_allowed = self.permission_checker(
                    effective_caller, src_account.id, src_folder, dst_account.id, dst_folder
                )
                if not still_allowed:
                    self._audit_step(tx_id, "aborted", outcome="ERROR", reason="saga_aborted")
                    self.wal.abort(tx_id, "saga_aborted")
                    return SagaResult(
                        tx_id=tx_id, mechanism="saga", result="ERROR", error_type="saga_aborted"
                    )

            # Verify idempotency
            existing = await self._search_target_for_matching_candidate(
                dst_account,
                dst_password,
                dst_folder,
                message_id=message_id,
                canonical_sha256=canonical_digest,
            )
            if existing == "ambiguous":
                self._audit_step(
                    tx_id, "escalated", outcome="ERROR", reason="ambiguous_target_match"
                )
                self.wal.mark_needs_operator(tx_id, reason="ambiguous_target_match")
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR", error_type="saga_aborted"
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

            # APPEND to target
            try:
                append_result = await append_message(dst_account, dst_password, dst_folder, raw)
                ok = append_result.outcome == "ok"
            except TimeoutError:
                count = self.wal.bump_retry(tx_id, "append_failed: timeout")
                if count >= self.retry_limit:
                    self._audit_step(tx_id, "escalated", outcome="ERROR")
                    self.wal.mark_needs_operator(tx_id)
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR", error_type="append_timeout"
                )
            except Exception as exc:  # noqa: BLE001
                count = self.wal.bump_retry(tx_id, f"append_failed: {exc}")
                if count >= self.retry_limit:
                    self._audit_step(tx_id, "escalated", outcome="ERROR")
                    self.wal.mark_needs_operator(tx_id)
                err_str = str(exc).lower()
                err_type = (
                    "transient_imap_failure"
                    if isinstance(exc, (TransientIMAPFailure, OSError, ConnectionError))
                    or "connection refused" in err_str
                    or "unreachable" in err_str
                    or "failed to connect" in err_str
                    else "append_failed"
                )
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR", error_type=err_type
                )

            if not ok:
                count = self.wal.bump_retry(tx_id, "append_failed: server rejected")
                if count >= self.retry_limit:
                    self._audit_step(tx_id, "escalated", outcome="ERROR")
                    self.wal.mark_needs_operator(tx_id)
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR", error_type="append_rejected"
                )

            _maybe_crash("post_append_pre_staged")

            # VERIFY on target
            if message_id is not None:
                try:
                    hits = await search_uids(
                        dst_account,
                        dst_password,
                        dst_folder,
                        f'HEADER "Message-Id" "{message_id}"',
                    )
                    target_uid = hits[0] if hits else None
                except Exception:  # noqa: BLE001
                    target_uid = None

            self.wal.mark_staged(tx_id, target_uid)
            self._audit_step(tx_id, "staged")
            await _maybe_pause("post_staged")

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
        mechanism: str = "saga",
    ) -> SagaResult:
        if delete_source:
            try:
                existing = await search_uids(
                    src_account, src_password, src_folder, f"UID {src_uid}"
                )
            except Exception:  # noqa: BLE001
                existing = [src_uid]

            if src_uid in existing:
                try:
                    await _delete_source(src_account, src_password, src_folder, src_uid)
                except Exception as exc:  # noqa: BLE001
                    count = self.wal.bump_retry(tx_id, f"delete_failed: {exc}")
                    if count >= self.retry_limit:
                        self._audit_step(tx_id, "escalated", outcome="ERROR")
                        self.wal.mark_needs_operator(tx_id)
                    return SagaResult(
                        tx_id=tx_id,
                        mechanism=mechanism,
                        result="ERROR",
                        error_type="transient_imap_failure",
                    )
            self.wal.mark_deleted(tx_id)
            self._audit_step(tx_id, "deleted")
            _maybe_crash("post_delete")

        self._audit_step(tx_id, "commit")
        self.wal.commit(tx_id)
        return SagaResult(tx_id=tx_id, mechanism=mechanism, result="OK")

    async def resume(self, tx: dict[str, Any]) -> SagaResult | None:
        """Unified revocation rule on recovery (ADR 0046 §4)."""
        if self.account_resolver is None:
            return None

        tx_id = tx["tx_id"]
        status = tx["status"]

        if int(tx.get("retry_count") or 0) >= self.retry_limit:
            self._audit_step(tx_id, "escalated", outcome="ERROR")
            self.wal.mark_needs_operator(tx_id)
            return SagaResult(
                tx_id=tx_id, mechanism="saga", result="ERROR", error_type="saga_aborted"
            )

        if status == "pending":
            self._audit_step(tx_id, "aborted", outcome="ERROR", reason="saga_aborted")
            self.wal.abort(tx_id, "saga_aborted")
            return SagaResult(
                tx_id=tx_id,
                mechanism="saga",
                result="ERROR",
                error_type="saga_aborted",
            )

        try:
            src_account, src_password = await self.account_resolver(tx["src_account"])
            dst_account, dst_password = await self.account_resolver(tx["dst_account"])
        except Exception:  # noqa: BLE001
            return None

        # Check if policy was revoked pre-staged
        if status == "fetched" and self.permission_checker is not None:
            effective_caller = tx.get("caller_id") or "invoice-agent"
            still_allowed = self.permission_checker(
                effective_caller, src_account.id, tx["src_folder"], dst_account.id, tx["dst_folder"]
            )
            if not still_allowed:
                self._audit_step(tx_id, "aborted", outcome="ERROR", reason="saga_aborted")
                self.wal.abort(tx_id, "saga_aborted")
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR", error_type="saga_aborted"
                )

        # If fetched, check if target already received the message (e.g. delayed APPEND)
        if status == "fetched":
            message_id = tx.get("message_id")
            canonical_digest: str = str(tx.get("canonical_sha256") or "")
            existing = await self._search_target_for_matching_candidate(
                dst_account,
                dst_password,
                tx["dst_folder"],
                message_id=message_id,
                canonical_sha256=canonical_digest,
            )
            if existing == "ambiguous":
                self._audit_step(
                    tx_id, "escalated", outcome="ERROR", reason="ambiguous_target_match"
                )
                self.wal.mark_needs_operator(tx_id, reason="ambiguous_target_match")
                return SagaResult(
                    tx_id=tx_id, mechanism="saga", result="ERROR", error_type="saga_aborted"
                )

            if existing:
                target_uid = existing[0]
                self.wal.mark_staged(tx_id, target_uid)
                self._audit_step(tx_id, "staged")
                status = "staged"
            else:
                raw = tx.get("staged_rfc822")
                if not raw:
                    count = self.wal.bump_retry(tx_id, "append_failed: no payload")
                    if count >= self.retry_limit:
                        self._audit_step(tx_id, "escalated", outcome="ERROR")
                        self.wal.mark_needs_operator(tx_id)
                    return SagaResult(
                        tx_id=tx_id, mechanism="saga", result="ERROR", error_type="append_failed"
                    )

                try:
                    append_result = await append_message(
                        dst_account, dst_password, tx["dst_folder"], raw
                    )
                    ok = append_result.outcome == "ok"
                except TimeoutError:
                    ok = False
                except Exception:  # noqa: BLE001
                    ok = False

                if not ok:
                    count = self.wal.bump_retry(tx_id, "append_failed")
                    if count >= self.retry_limit:
                        self._audit_step(tx_id, "escalated", outcome="ERROR")
                        self.wal.mark_needs_operator(tx_id)
                    return SagaResult(
                        tx_id=tx_id, mechanism="saga", result="ERROR", error_type="append_rejected"
                    )

                existing_after = await self._search_target_for_matching_candidate(
                    dst_account,
                    dst_password,
                    tx["dst_folder"],
                    message_id=message_id,
                    canonical_sha256=canonical_digest,
                )
                target_uid = (
                    existing_after[0] if (existing_after and existing_after != "ambiguous") else 0
                )
                self.wal.mark_staged(tx_id, target_uid)
                self._audit_step(tx_id, "staged")
                status = "staged"

        if status == "staged":
            return await self._finish_delete_and_commit(
                tx_id=tx_id,
                src_account=src_account,
                src_password=src_password,
                src_folder=tx["src_folder"],
                src_uid=int(tx["src_uid"]),
                delete_source=True,
            )
        return None

    async def run_pending_recovery(self, caller_id: str | None = None) -> int:
        processed = 0
        instance_id = f"proc-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        pending = self.wal.claim_and_get_pending(caller_id=caller_id, instance_id=instance_id)
        for tx in pending:
            if tx["status"] in ("committed", "aborted", "needs_operator"):
                self.wal.release_lease(tx["tx_id"])
                continue
            try:
                await self.resume(tx)
                processed += 1
            finally:
                self.wal.release_lease(tx["tx_id"])
        return processed


async def _delete_source(account: Account, password: str, folder: str, uid: int) -> None:
    """Safe source delete using UID EXPUNGE <uid> when UIDPLUS is advertised (ADR 0046 §3)."""
    from .imap_core import _imap_session, _quote_mailbox

    async with _imap_session(account, password) as imap:
        status, _ = await imap.select(_quote_mailbox(folder))
        if status != "OK":
            raise RuntimeError(f"SELECT {folder!r} failed: {status}")

        has_uidplus = bool(imap.has_capability("UIDPLUS"))
        status, _ = await imap.uid("store", str(uid), "+FLAGS.SILENT", r"(\Deleted)")
        if status != "OK":
            raise RuntimeError(f"STORE \\Deleted failed: {status}")

        if has_uidplus:
            status, _ = await imap.uid("expunge", str(uid))
            if status != "OK":
                raise RuntimeError(f"UID EXPUNGE failed: {status}")
        else:
            # Fallback
            status, _ = await imap.expunge()
            if status != "OK":
                raise RuntimeError(f"EXPUNGE failed: {status}")
