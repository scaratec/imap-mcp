"""Shared helpers for tool handlers.

This module exposes pure helpers (`_facts_from_envelope`, alias
resolution, password lookup) plus the per-tool visibility /
capability tables that the dispatcher consults. Tool modules in
`handlers/` import from here; nothing here imports back from a
handler module.

The leading underscore on the file name signals: helpers, not tools.
Per-tool response builders + TypedDicts live in each handler module
since Phase C; this module no longer exports generic _deny / _ok /
_error.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal, TYPE_CHECKING

from ..imap_core import list_folders as imap_list_folders
from ..policy import MessageFacts

if TYPE_CHECKING:
    from ..context import ServerContext


_FORBIDDEN_SYSTEM_FLAGS = frozenset(["\\Deleted", "\\Draft", "\\Recent"])

TOOL_SET_VERSION = "2.0.0"
READ_TOOL_MIN_VIS = {
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
}
WRITE_TOOL_CAP = {
    "mark_seen": "mark_seen",
    "bulk_mark_seen": "mark_seen",
    "mark_tagged": "mark_tagged",
    "bulk_mark_tagged": "mark_tagged",
    "move": "move_out",
    "copy": "accept_incoming",
    "create_draft": "draft_append",
    "create_reply_draft": "draft_append",
    "add_attachment": "modify_message",
    "replace_attachment": "modify_message",
    "delete_attachment": "modify_message",
}


# --------------------------------------------------------------------- envelope


_ERROR_DETAIL_MAX_LEN = 256
_KNOWN_ERROR_TYPES = frozenset(
    {
        # Folder operations
        "folder_absent",
        "select_failed",
        # Append (drafts)
        "append_rejected",
        "append_timeout",
        "append_failed",
        # Reply construction
        "uid_not_found",
        "empty_reply_text",
        # Attachment access / modify
        "attachment_not_found",
        "rewrite_failed",
        # Move / copy
        "saga_aborted",
        "transient_imap_failure",
        # Attachment sink (ADR 0028)
        "sink_not_configured",
        "sink_not_writable",
    }
)


def error_envelope(
    *,
    error_type: str,
    detail: str = "",
    reason: str = "folder_default_applied",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ALLOW+ERROR response per ADR 0027.

    `error_type` must be in the closed enumeration of ADR 0027.  `detail`
    is bounded to 256 characters and is the caller-visible single line;
    callers must pre-sanitize the string (no reflected input).  `extra`
    holds the tool-specific identifying fields like account/folder/uid;
    those fields are merged at the top level of the envelope, not under
    the `error` block.
    """
    if error_type not in _KNOWN_ERROR_TYPES:
        raise ValueError(f"error_type {error_type!r} is not in the ADR 0027 enumeration")
    truncated = (detail or "")[:_ERROR_DETAIL_MAX_LEN]
    payload: dict[str, Any] = {
        "decision": "ALLOW",
        "result": "ERROR",
        "reason": reason,
        "error": {"type": error_type, "detail": truncated},
    }
    if extra:
        for key, value in extra.items():
            if key not in payload:
                payload[key] = value
    return payload


def _facts_from_envelope(envelope: Any) -> MessageFacts:
    """Build a MessageFacts record from the imap-core Envelope.

    Fields the ENVELOPE fetch already yields are passed through. Fields
    the ENVELOPE fetch does not expose yet (has_attachment, size) are
    given sentinel values that still let the Walking-Skeleton matchers
    work — every currently-live scenario that depends on them will be
    supplied with a proper RFC822.SIZE / BODYSTRUCTURE lookup before it
    turns green, so the sentinel is an honest "not measured yet" rather
    than a silent default. (BDD Guidelines §1.3)
    """
    return MessageFacts(
        from_address=envelope.from_address,
        to_addresses=tuple(envelope.to_addresses),
        subject=envelope.subject,
        has_attachment=envelope.has_attachment,
        flagged="\\Flagged" in envelope.flags,
        size_bytes=envelope.size_bytes,
        date_iso=envelope.date,
    )


def _is_google_provider(account: Any) -> bool:
    """True when the account uses Google/Gmail IMAP semantics."""
    return getattr(account, "provider", "imap-standard") in ("google", "google-mock")


def _get_folder_aliases(context: "ServerContext", account_id: str) -> dict[str, str]:
    """Return the cached folder alias map for a Google account.

    The map is populated by ``handle_list_folders`` as a side effect
    of its IMAP LIST call — no extra connection is needed.  If
    ``list_folders`` has not been called yet for this account the map
    is empty and canonical paths pass through unchanged.
    """
    if context._live.folder_aliases is None:
        return {}
    return context._live.folder_aliases.get(account_id, {})


async def _resolve_imap_folder(
    context: "ServerContext", account_id: str, canonical_path: str
) -> str:
    """Resolve a canonical policy path to the actual IMAP folder path.

    For Google accounts with localized folder names the canonical path
    (e.g. ``[Gmail]/Drafts``) is mapped to the localized IMAP path
    (e.g. ``[Gmail]/Entwürfe``).  Non-Google accounts and paths
    without an alias entry pass through unchanged.
    """
    account = context.account_by_id(account_id)
    if account is None or not _is_google_provider(account):
        return canonical_path
    aliases = _get_folder_aliases(context, account_id)
    return aliases.get(canonical_path, canonical_path)


async def _known_folders_for(context: "ServerContext", account_id: str) -> list[str]:
    """Ask IMAP for the full folder list on a configured account.

    Returns an empty list when the account is not configured at all —
    the PDP will then produce `hidden_folders_count=0`, which is the
    correct answer for an unknown account because the caller should
    not learn about server-side state they have no grant for.
    """
    from ..config import Account

    account = context.account_by_id(account_id)
    if account is None:
        return []
    account_model: Account = account  # type: ignore[assignment]
    if account_model.auth is None:
        raise RuntimeError(
            f"Account {account_id!r} has no auth configuration; "
            "the Walking-Skeleton fixture must set auth.type=password "
            "and a secret_ref."
        )

    if account_model.auth.type == "xoauth2":
        password = await context.oauth_manager.get_access_token(account_model)
    else:
        password = context.secret_store.get(account_model.auth.password_secret_ref())

    if password is None:
        raise RuntimeError(
            f"Secret store could not resolve {account_model.auth.secret_ref!r} "
            f"for account {account_id!r}."
        )
    folder_infos = await imap_list_folders(account_model, password)
    return [fi.path for fi in folder_infos]


async def _password_for_account(context: "ServerContext", account_id: str) -> tuple[Any, str]:
    """Resolve account model and password. Raises on missing config."""
    from ..config import Account

    account = context.account_by_id(account_id)
    if account is None:
        raise RuntimeError(f"Account {account_id!r} not configured")
    account_model: Account = account  # type: ignore[assignment]
    if account_model.auth is None:
        raise RuntimeError(f"Account {account_id!r} has no auth configuration")
    if account_model.auth.type == "xoauth2":
        password = await context.oauth_manager.get_access_token(account_model)
    else:
        password = context.secret_store.get(account_model.auth.password_secret_ref())
    if password is None:
        raise RuntimeError(
            f"Secret store could not resolve {account_model.auth.secret_ref!r} "
            f"for account {account_id!r}."
        )
    return account_model, password


async def _password_for(context: "ServerContext", account_id: str) -> tuple[Any, str]:
    """Resolve (account_model, password) or raise with a clear error."""
    account = context.account_by_id(account_id)
    if account is None:
        raise RuntimeError(f"Account {account_id!r} is not configured")
    if account.auth is None:  # type: ignore[attr-defined]
        raise RuntimeError(f"Account {account_id!r} has no auth configuration")

    if account.auth.type == "xoauth2":  # type: ignore[attr-defined]
        password = await context.oauth_manager.get_access_token(account)
    else:
        password = context.secret_store.get(
            account.auth.password_secret_ref()  # type: ignore[attr-defined]
        )

    if password is None:
        raise RuntimeError(f"Password not resolvable for {account_id!r}")
    return account, password


__all__ = [
    "_FORBIDDEN_SYSTEM_FLAGS",
    "TOOL_SET_VERSION",
    "READ_TOOL_MIN_VIS",
    "WRITE_TOOL_CAP",
    "error_envelope",
    "sanitize_attachment_filename",
    "decode_mime_words",
    "check_attachment_sink",
    "describe_attachment_sink",
    "_facts_from_envelope",
    "_get_folder_aliases",
    "_is_google_provider",
    "_known_folders_for",
    "_password_for",
    "_password_for_account",
    "_resolve_imap_folder",
]


# --------------------------------------------------------------------- ADR 0028


# Filename safety bounds (ADR 0028 §2):
#  - 200 bytes for the sanitized base name (BEFORE underscore + hash + ext)
#  - 8 hex chars md5 prefix
#  - 1 underscore separator
#  - extension preserved through sanitization (no length cap on it alone;
#    the 255-byte total cap covers it)
#  - 255-byte total cap, the per-name limit on ext4/ext3/XFS/NTFS.
_SINK_FILENAME_BASE_MAX_BYTES = 200
_SINK_FILENAME_TOTAL_MAX_BYTES = 255
_SINK_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def decode_mime_words(raw: str) -> str:
    """RFC 2047-decode any MIME encoded-words in a header value.

    Some servers put a `=?charset?b?...?=` (B) or `=?charset?q?...?=`
    (Q) encoded-word directly in the Content-Disposition filename
    parameter. The default email.compat32 parser does not decode it,
    so `part.get_filename()` hands us the raw token. Decoding it here,
    before sanitization, is what turns `=?utf-8?b?ZmE...?=` into
    `fa 2026-6 Kuba.pdf`. Falls back to the raw input on any decode
    error so a malformed encoded-word degrades to sanitization rather
    than raising.
    """
    from email.header import decode_header, make_header

    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def sanitize_attachment_filename(original: str, payload: bytes) -> str:
    """Produce the on-disk filename per ADR 0028 §2.

    Steps:
      0. RFC 2047-decode any MIME encoded-word in the raw filename.
      1. Replace every character outside `[A-Za-z0-9._-]` with `_`.
      2. Strip leading dots so the result is not a hidden file.
      3. Split off the trailing `.ext` if present.
      4. Truncate the base to 200 bytes (byte-based, not chars).
      5. Append `_<md5(payload)[:8]>.<ext>`.
      6. As a defence in depth, truncate the final name to 255 bytes.

    The 8-hex prefix of md5(payload) makes re-fetch idempotent: the
    same bytes always land on the same filename and overwrite in
    place.
    """
    import hashlib as _hashlib

    if not original:
        original = "attachment"
    original = decode_mime_words(original)
    sanitized = _SINK_FILENAME_SAFE_RE.sub("_", original)
    # Collapse any "..", "..." etc. run to a single "_" so the final
    # name never carries a path-element token even though "." itself
    # is whitelisted (we need it for the extension separator).
    sanitized = re.sub(r"\.{2,}", "_", sanitized)
    sanitized = sanitized.lstrip(".")
    if not sanitized:
        sanitized = "attachment"
    if "." in sanitized:
        base, _, ext = sanitized.rpartition(".")
        if not base:
            base, ext = sanitized, ""
    else:
        base, ext = sanitized, ""
    base_bytes = base.encode("utf-8")
    if len(base_bytes) > _SINK_FILENAME_BASE_MAX_BYTES:
        base = base_bytes[:_SINK_FILENAME_BASE_MAX_BYTES].decode("utf-8", errors="ignore")
        if not base:
            base = "attachment"
    digest = _hashlib.md5(payload).hexdigest()[:8]
    if ext:
        name = f"{base}_{digest}.{ext}"
    else:
        name = f"{base}_{digest}"
    name_bytes = name.encode("utf-8")
    if len(name_bytes) > _SINK_FILENAME_TOTAL_MAX_BYTES:
        # Total cap is reached only when the extension itself is huge;
        # truncate from the end of the base, keeping the hash + ext
        # intact so the re-fetch idempotency story still holds.
        overshoot = len(name_bytes) - _SINK_FILENAME_TOTAL_MAX_BYTES
        keep = max(1, len(base.encode("utf-8")) - overshoot)
        base = base.encode("utf-8")[:keep].decode("utf-8", errors="ignore") or "a"
        name = f"{base}_{digest}" + (f".{ext}" if ext else "")
    return name


def check_attachment_sink(
    sink: "Path | None",
) -> tuple[Literal["ok", "not_configured", "missing", "not_writable"], str]:
    """Return (state, detail) for the configured attachment sink.

    Called from both `list_tools` (to render the tool description)
    and from `handle_fetch_attachment` (to gate the call). Per ADR
    0028 §3 this must be cheap — a stat + os.access is microseconds.
    The detail string is the human-readable diagnostic used either
    way (description text or error.detail).
    """
    if sink is None:
        return (
            "not_configured",
            "attachment_sink_directory is not set in the server config "
            "(accounts.yaml -> attachment_sink.directory)",
        )
    if not sink.exists():
        return (
            "missing",
            f"sink directory {sink} does not exist",
        )
    if not sink.is_dir():
        return (
            "not_writable",
            f"sink path {sink} is not a directory",
        )
    if not os.access(sink, os.W_OK):
        return (
            "not_writable",
            f"sink directory {sink} is not writable by user uid={os.geteuid()}",
        )
    return "ok", str(sink)


def describe_attachment_sink(sink: "Path | None") -> str:
    """Render the human-facing sink summary that gets pasted into the
    fetch_attachment tool description. Stable shape for both happy
    and error paths so scenarios can grep against it.
    """
    state, detail = check_attachment_sink(sink)
    if state == "ok":
        return f"Attachment bytes are written to: {detail}"
    if state == "not_configured":
        return f"Attachment sink not configured ({detail})"
    return f"Attachment sink not writable ({detail})"
