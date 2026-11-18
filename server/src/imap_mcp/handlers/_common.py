"""Shared helpers for tool handlers in Tool Surface 3.0.0.

Adheres to:
- ADR 0044: Normalized response envelope and closed error taxonomy
- ADR 0047: Isolated symlink-safe attachment delivery in per-caller 0700 directories
- ADR 0052: Tool Surface 3.0.0 (27 tools)
- ADR 0056: Canonical folder resolution and Modified UTF-7 wire boundary
- ADR 0062: Closed reason and error code registry
"""

from __future__ import annotations

import email.header
import hashlib
import os
import re
import stat
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..imap_core import build_folder_alias_map
from ..imap_core import list_mailboxes as imap_list_mailboxes
from ..policy import MessageFacts

if TYPE_CHECKING:
    from ..config import Account
    from ..context import ServerContext
    from ..saga import SagaManager

TOOL_SET_VERSION = "3.0.0"

READ_TOOL_MIN_VIS: dict[str, str | None] = {
    "list_accounts": None,
    "list_folders": "COUNT",
    "list_labels": "COUNT",
    "folder_stats": "COUNT",
    "search": "METADATA",
    "list_messages": "METADATA",
    "fetch_envelope": "ENVELOPE",
    "fetch_headers": "HEADERS",
    "fetch_body": "BODY",
    "list_attachments": "BODY",
    "fetch_attachment": "FULL",
    "list_drafts": "METADATA",
    "get_draft": "BODY",
}

# ADR 0052: Write tools required_capabilities list and minimum_visibility
WRITE_TOOL_METADATA: dict[str, dict[str, Any]] = {
    "mark_seen": {"required_capabilities": ["mark_seen"], "minimum_visibility": "METADATA"},
    "bulk_mark_seen": {"required_capabilities": ["mark_seen"], "minimum_visibility": "METADATA"},
    "mark_tagged": {"required_capabilities": ["mark_tagged"], "minimum_visibility": "ENVELOPE"},
    "bulk_mark_tagged": {
        "required_capabilities": ["mark_tagged"],
        "minimum_visibility": "ENVELOPE",
    },
    "move": {
        "required_capabilities": ["move_out", "accept_incoming"],
        "minimum_visibility": "ENVELOPE",
    },
    "copy": {
        "required_capabilities": ["copy_out", "accept_incoming"],
        "minimum_visibility": "ENVELOPE",
    },
    "create_draft": {"required_capabilities": ["draft_append"]},
    "create_reply_draft": {"required_capabilities": ["draft_append"], "minimum_visibility": "BODY"},
    "update_draft": {"required_capabilities": ["draft_edit"]},
    "discard_draft": {"required_capabilities": ["move_out", "accept_incoming"]},
}

# Compatibility map
WRITE_TOOL_CAP: dict[str, str] = {
    "mark_seen": "mark_seen",
    "bulk_mark_seen": "mark_seen",
    "mark_tagged": "mark_tagged",
    "bulk_mark_tagged": "mark_tagged",
    "move": "move_out",
    "copy": "copy_out",
    "create_draft": "draft_append",
    "create_reply_draft": "draft_append",
    "update_draft": "draft_edit",
    "discard_draft": "move_out",
}

META_TOOLS: frozenset[str] = frozenset(
    {
        "describe_policy",
        "get_transaction_status",
        "get_caller_identity",
        "tool_surface_info",
    }
)

# ADR 0062: Closed 22 caller_error_types
_KNOWN_ERROR_TYPES = frozenset(
    {
        "not_found",
        "folder_absent",
        "select_failed",
        "uidvalidity_changed",
        "append_rejected",
        "append_timeout",
        "append_failed",
        "empty_reply_text",
        "missing_message_id",
        "attachment_not_found",
        "sink_not_configured",
        "sink_not_writable",
        "draft_not_editable",
        "draft_revision_conflict",
        "draft_ambiguous",
        "trash_unavailable",
        "saga_aborted",
        "transient_imap_failure",
        "unsupported_imap_capability",
        "pool_exhausted",
        "secret_unavailable",
        "internal_error",
    }
)

_ERROR_DETAIL_MAX_LEN = 256


def error_envelope(
    *,
    error_type: str,
    detail: str = "",
    reason: str = "folder_default_applied",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build normalized ALLOW + ERROR response (ADR 0044, ADR 0062)."""
    if error_type not in _KNOWN_ERROR_TYPES:
        raise ValueError(f"error_type {error_type!r} is not in the ADR 0062 closed registry")
    truncated = (detail or "")[:_ERROR_DETAIL_MAX_LEN]
    payload: dict[str, Any] = {
        "decision": "ALLOW",
        "result": "ERROR",
        "reason": reason,
        "error": {"type": error_type, "detail": truncated},
    }
    if extra:
        for key, value in extra.items():
            if key not in payload and not key.startswith("_"):
                payload[key] = value
        for key, value in extra.items():
            if key.startswith("_"):
                payload[key] = value
    return payload


def _facts_from_envelope(envelope: Any) -> MessageFacts:

    internaldate = None
    if getattr(envelope, "date", None):
        try:
            from datetime import UTC, datetime
            from email.utils import parsedate_to_datetime

            raw_date = str(envelope.date)
            if "T" in raw_date:
                internaldate = datetime.fromisoformat(raw_date)
            else:
                internaldate = parsedate_to_datetime(raw_date)
            if internaldate.tzinfo is None:
                internaldate = internaldate.replace(tzinfo=UTC)
            else:
                internaldate = internaldate.astimezone(UTC)
        except Exception:  # noqa: BLE001
            internaldate = None

    return MessageFacts.from_envelope(
        from_raw=envelope.from_address or "",
        to_raws=envelope.to_addresses or [],
        cc_raws=getattr(envelope, "cc_addresses", None) or [],
        subject_raw=envelope.subject or "",
        has_attachment=bool(getattr(envelope, "has_attachment", False)),
        flagged="\\Flagged" in (getattr(envelope, "flags", None) or ()),
        size_bytes=int(getattr(envelope, "size_bytes", 0) or 0),
        internaldate_utc=internaldate,
        labels=getattr(envelope, "labels", ()) or (),
        x_gm_msgid=getattr(envelope, "x_gm_msgid", None),
    )


async def resolve_provenance_own(
    context: ServerContext,
    account: Any,
    password: str,
    imap_folder: str,
    uid: int,
    envelope: Any,
) -> bool:
    import email

    from ..imap_core import fetch_full_message
    from ..provenance import compute_canonical_digest, verify_provenance_mac

    raw = await fetch_full_message(account, password, imap_folder, uid)
    if not raw:
        return False
    msg = email.message_from_bytes(raw)
    canonical_digest = compute_canonical_digest(raw)

    prov_header = msg.get("X-SC-Provenance")
    draft_id = msg.get("X-SC-Draft-Id")
    draft_rev = msg.get("X-SC-Draft-Rev")
    if (
        prov_header
        and draft_id
        and draft_rev
        and draft_rev.isdigit()
        and context.provenance_hmac_key
    ):
        if context.saga is not None and not context.saga.wal.is_caller_draft_owner(
            draft_id, context.caller_id, account.id
        ):
            return False
        if verify_provenance_mac(
            context.provenance_hmac_key,
            prov_header,
            context.caller_id,
            account.id,
            draft_id,
            int(draft_rev),
            canonical_digest,
        ):
            return True

    if context.saga is not None:
        mid = getattr(envelope, "message_id", None) or msg.get("Message-ID")
        if mid and context.saga.wal.match_own_provenance(
            context.caller_id, account.id, mid, canonical_digest
        ):
            return True

    return False


def _walk_attachment_parts(msg: Any) -> list[Any]:
    """Walk MIME tree and classify attachments per ADR 0047 §1."""
    attachments: list[Any] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = str(part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        maintype = part.get_content_maintype()

        is_attachment = disp.startswith("attachment")
        is_inline_with_filename = disp.startswith("inline") and filename is not None
        is_non_text_with_filename = maintype != "text" and filename is not None

        if is_attachment or is_inline_with_filename or is_non_text_with_filename:
            attachments.append(part)
    return attachments


def _is_google_provider(account: Any) -> bool:
    return getattr(account, "provider", "imap-standard") in ("google", "google-mock")


async def _ensure_folder_aliases(context: ServerContext, account_id: str) -> dict[str, str]:
    account = context.account_by_id(account_id)
    if account is None or not _is_google_provider(account):
        return {}
    pdp = context.pdp
    cached = pdp.folder_aliases(account_id)
    if cached is not None:
        return cached
    account_model, password = await _password_for(context, account_id)
    alias_map = build_folder_alias_map(await imap_list_mailboxes(account_model, password))
    pdp.set_folder_aliases(account_id, alias_map)
    return alias_map


async def _resolve_imap_folder(context: ServerContext, account_id: str, canonical_path: str) -> str:
    if canonical_path.upper() == "INBOX":
        return canonical_path
    aliases = await _ensure_folder_aliases(context, account_id)
    return aliases.get(canonical_path, canonical_path)


_FOLDER_ARGUMENT_KEYS = ("folder", "source_folder", "drafts_folder")


async def canonicalize_folder_arguments(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    granted = set(context.pdp.visible_accounts_for(context.caller_id).visible_account_ids)

    async def canonical(account_id: object, folder: object) -> object:
        if not isinstance(account_id, str) or not isinstance(folder, str):
            return folder
        if folder.upper() == "INBOX":
            return folder
        if account_id not in granted or context.oauth_manager.is_rebootstrap_needed(account_id):
            return folder
        account = context.account_by_id(account_id)
        if account is None or not _is_google_provider(account):
            return folder
        await _ensure_folder_aliases(context, account_id)
        return context.pdp.canonical_folder(account_id, folder)

    result = dict(arguments)
    for key in _FOLDER_ARGUMENT_KEYS:
        if key in result:
            result[key] = await canonical(result.get("account"), result[key])
    for key in ("source", "target"):
        nested = result.get(key)
        if isinstance(nested, dict) and "folder" in nested:
            nested = dict(nested)
            nested["folder"] = await canonical(nested.get("account"), nested["folder"])
            result[key] = nested
    return result


async def _password_for(context: ServerContext, account_id: str) -> tuple[Account, str]:
    account = context.account_by_id(account_id)
    if account is None:
        raise RuntimeError(f"Account {account_id!r} is not configured")
    if account.auth is None:
        raise RuntimeError(f"Account {account_id!r} has no auth configuration")

    password: str | None
    if account.auth.type == "xoauth2":
        password = await context.oauth_manager.get_access_token(account)
    else:
        password = context.secret_store.get(account.auth.password_secret_ref())

    if password is None:
        raise RuntimeError(f"Password not resolvable for {account_id!r}")
    return account, password


def _require_saga(context: ServerContext, tool: str) -> SagaManager:
    if context.saga is None:
        raise RuntimeError(f"{tool} requires the saga WAL; set wal.path in accounts.yaml")
    return context.saga


def decode_mime_words(raw: str) -> str:
    """RFC 2047-decode MIME encoded-words."""
    try:
        return str(email.header.make_header(email.header.decode_header(raw)))
    except Exception:  # noqa: BLE001
        return raw


_SINK_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_attachment_filename(original: str, payload: bytes) -> str:
    """Sanitize attachment filename per ADR 0047: [A-Za-z0-9._-], strip leading dots."""
    if not original:
        original = "attachment"
    decoded = decode_mime_words(original)
    clean = decoded.lstrip(".")
    if not clean:
        clean = "attachment"
    sanitized = _SINK_FILENAME_SAFE_RE.sub("_", clean)
    sanitized = re.sub(r"\.{2,}", "_", sanitized)
    sanitized = sanitized.lstrip("._")
    if not sanitized:
        sanitized = "attachment"
    return sanitized


def deliver_attachment_to_sink(
    *,
    base_sink_dir: Path | None,
    caller_id: str,
    original_filename: str,
    payload: bytes,
) -> tuple[str, str, str, str, int]:
    """Per-caller symlink-safe attachment delivery (ADR 0047).

    Returns: (saved_to, saved_path, artifact_id, content_hash, size_bytes).
    Raises OSError / PermissionError on sink failure.
    """
    if base_sink_dir is None:
        raise FileNotFoundError("sink_not_configured")

    if base_sink_dir.is_symlink():
        raise PermissionError("Base sink is a symlink")
    if not base_sink_dir.is_dir():
        raise PermissionError(f"sink path {base_sink_dir} is not a directory")

    # Verify base descriptor with O_NOFOLLOW
    base_fd = os.open(base_sink_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        base_stat = os.fstat(base_fd)
        if not stat.S_ISDIR(base_stat.st_mode):
            raise PermissionError("Base sink is not a directory")
        if base_stat.st_uid != os.geteuid():
            raise PermissionError("Base sink directory not owned by current user")

        # Create/open per-caller directory
        caller_dir = base_sink_dir / caller_id
        if caller_dir.is_symlink():
            raise PermissionError("Caller sink is a symlink")
        caller_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(caller_dir, 0o700)
        except OSError:
            pass

        caller_fd = os.open(caller_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            caller_stat = os.fstat(caller_fd)
            if not stat.S_ISDIR(caller_stat.st_mode):
                raise PermissionError("Caller sink is not a directory")
            if caller_stat.st_uid != os.geteuid():
                raise PermissionError("Caller sink directory not owned by current user")

            artifact_id = uuid.uuid4().hex  # 32 lowercase hex chars (128-bit)
            sanitized_base = sanitize_attachment_filename(original_filename, payload)

            # Cap leaf filename to <= 255 bytes UTF-8 as <artifact_id>_<sanitized_base>.<ext>
            prefix = f"{artifact_id}_"
            stem, ext = os.path.splitext(sanitized_base)
            max_stem_len = 255 - len(prefix.encode("utf-8")) - len(ext.encode("utf-8"))
            if max_stem_len < 1:
                # If extension is absurdly long, fallback
                ext = ""
                max_stem_len = 255 - len(prefix.encode("utf-8"))

            stem_encoded = stem.encode("utf-8")
            if len(stem_encoded) > max_stem_len:
                stem = stem_encoded[:max_stem_len].decode("utf-8", errors="ignore") or "attachment"

            leaf_name = f"{prefix}{stem}{ext}"
            target_path = caller_dir / leaf_name

            # Open with O_CREAT | O_EXCL | O_NOFOLLOW, mode 0400 relative to caller_fd
            file_fd = os.open(
                leaf_name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o400,
                dir_fd=caller_fd,
            )
            try:
                os.write(file_fd, payload)
                os.fsync(file_fd)
            except Exception:
                try:
                    os.unlink(leaf_name, dir_fd=caller_fd)
                except OSError:
                    pass
                raise
            finally:
                os.close(file_fd)

            content_hash = hashlib.sha256(payload).hexdigest()
            saved_to = f"{caller_id}/{leaf_name}"
            saved_path = str(target_path.resolve())
            return saved_to, saved_path, artifact_id, content_hash, len(payload)
        finally:
            os.close(caller_fd)
    finally:
        os.close(base_fd)
