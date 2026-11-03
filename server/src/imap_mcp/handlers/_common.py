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
from typing import TYPE_CHECKING, Any, Literal

from ..imap_core import build_folder_alias_map
from ..imap_core import list_folders as imap_list_folders
from ..imap_core import list_mailboxes as imap_list_mailboxes
from ..policy import MessageFacts

if TYPE_CHECKING:
    from ..config import Account
    from ..context import ServerContext
    from ..saga import SagaManager


_FORBIDDEN_SYSTEM_FLAGS = frozenset(["\\Deleted", "\\Draft", "\\Recent"])

TOOL_SET_VERSION = "2.1.0"
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
    "list_drafts": "METADATA",
    "get_draft": "BODY",
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
    "update_draft": "draft_edit",
    "discard_draft": "move_out",
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
        # Draft identity and revisions (ADR 0030, ADR 0031)
        "draft_not_found",
        "draft_not_editable",
        "draft_ambiguous",
        "draft_revision_conflict",
        # Discarding a draft (ADR 0034)
        "trash_unavailable",
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


def _walk_attachment_parts(msg: Any) -> list[Any]:
    """Walk the MIME tree and return parts that count as an
    attachment from the agent's perspective: any non-multipart part
    whose Content-Disposition is ``attachment``, or which is
    ``inline`` but carries a filename, or which is a non-text part
    with a filename. The same rule is used by the fetch handlers and
    by ``get_draft``."""
    all_parts: list[Any] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        # str() the header value before .lower(): a raw 8-bit (non-ASCII,
        # non-RFC-2047) Content-Disposition parses to an email.header.Header,
        # which has no .lower(). str(Header) decodes it to a plain string.
        disposition = str(part.get("Content-Disposition") or "").lower()
        is_attachment = disposition.startswith("attachment")
        is_inline_file = disposition.startswith("inline") and part.get_filename() is not None
        is_named_binary = (
            part.get_content_maintype() not in ("text", "multipart")
            and part.get_filename() is not None
        )
        if is_attachment or is_inline_file or is_named_binary:
            all_parts.append(part)
    return all_parts


def _is_google_provider(account: Any) -> bool:
    """True when the account uses Google/Gmail IMAP semantics."""
    return getattr(account, "provider", "imap-standard") in ("google", "google-mock")


def _get_folder_aliases(context: ServerContext, account_id: str) -> dict[str, str]:
    """Return the cached folder alias map for a Google account.

    Empty when no map has been built yet in this configuration
    generation; `_ensure_folder_aliases` builds it on demand.
    """
    return context.pdp.folder_aliases(account_id) or {}


async def _ensure_folder_aliases(context: ServerContext, account_id: str) -> dict[str, str]:
    """Return the alias map of a Google account, building it on first use.

    ADR 0033: the first call that needs a Gmail folder on an account
    issues one IMAP LIST and derives `{canonical_path: wire_path}` from
    the RFC 6154 special-use flags. The map is cached on the live PDP,
    so a configuration reload (which replaces the PDP) discards it and
    `list_folders` refreshes it. Non-Google accounts get an empty map
    without any IMAP traffic. A failing LIST propagates: guessing a
    name would address the wrong folder.
    """
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
    """Resolve a canonical policy path to the actual IMAP folder path.

    For Google accounts with localized folder names the canonical path
    (e.g. ``[Gmail]/Drafts``) is mapped to the localized IMAP path
    (e.g. ``[Gmail]/Entwürfe``).  Non-Google accounts and paths
    without an alias entry pass through unchanged.
    """
    if _never_aliased(canonical_path):
        return canonical_path
    aliases = await _ensure_folder_aliases(context, account_id)
    return aliases.get(canonical_path, canonical_path)


def _never_aliased(folder_path: str) -> bool:
    """INBOX has one protocol-fixed name (RFC 3501 §5.1) on every
    server and in every locale, so it never needs the alias map; this
    spares the LIST on the most common path."""
    return folder_path.upper() == "INBOX"


# Top-level argument names that carry a folder path of `account`.
_FOLDER_ARGUMENT_KEYS = ("folder", "source_folder", "drafts_folder")


async def canonicalize_folder_arguments(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Return a copy of `arguments` with every Gmail folder path canonical.

    ADR 0033: a caller may name a Gmail special-use folder by its
    canonical or its localized path; both must reach policy evaluation
    and the response as the one canonical path. Runs before the
    handler, so every tool sees canonical names. Accounts outside the
    caller's grant, accounts awaiting OAuth re-bootstrap and
    non-Google accounts are left untouched; the handler's own checks
    answer for those.
    """
    granted = set(context.pdp.visible_accounts_for(context.caller_id).visible_account_ids)

    async def canonical(account_id: object, folder: object) -> object:
        if not isinstance(account_id, str) or not isinstance(folder, str):
            return folder
        if _never_aliased(folder):
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


async def _known_folders_for(context: ServerContext, account_id: str) -> list[str]:
    """Ask IMAP for the full folder list on a configured account.

    Returns an empty list when the account is not configured at all —
    the PDP will then produce `hidden_folders_count=0`, which is the
    correct answer for an unknown account because the caller should
    not learn about server-side state they have no grant for.
    """
    account_model = context.account_by_id(account_id)
    if account_model is None:
        return []
    if account_model.auth is None:
        raise RuntimeError(
            f"Account {account_id!r} has no auth configuration; "
            "the Walking-Skeleton fixture must set auth.type=password "
            "and a secret_ref."
        )

    password: str | None
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


def _require_saga(context: ServerContext, tool: str) -> SagaManager:
    """Return the saga manager, or fail loudly if no WAL is configured.

    Message rewrites and draft escalations are only defined on top of
    the WAL (ADR 0007), so their absence is a provisioning error.
    """
    if context.saga is None:
        raise RuntimeError(f"{tool} requires the saga WAL; set wal.path in accounts.yaml")
    return context.saga


async def _password_for_account(context: ServerContext, account_id: str) -> tuple[Account, str]:
    """Resolve account model and password. Raises on missing config."""
    account_model = context.account_by_id(account_id)
    if account_model is None:
        raise RuntimeError(f"Account {account_id!r} not configured")
    if account_model.auth is None:
        raise RuntimeError(f"Account {account_id!r} has no auth configuration")
    password: str | None
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


async def _password_for(context: ServerContext, account_id: str) -> tuple[Account, str]:
    """Resolve (account_model, password) or raise with a clear error."""
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


__all__ = [
    "READ_TOOL_MIN_VIS",
    "TOOL_SET_VERSION",
    "WRITE_TOOL_CAP",
    "_FORBIDDEN_SYSTEM_FLAGS",
    "_ensure_folder_aliases",
    "_facts_from_envelope",
    "_get_folder_aliases",
    "_is_google_provider",
    "_known_folders_for",
    "_password_for",
    "_password_for_account",
    "_resolve_imap_folder",
    "_walk_attachment_parts",
    "canonicalize_folder_arguments",
    "check_attachment_sink",
    "decode_mime_words",
    "describe_attachment_sink",
    "error_envelope",
    "sanitize_attachment_filename",
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
    except Exception:  # noqa: BLE001 - untrusted header, keep raw
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
    sink: Path | None,
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
            (
                "attachment_sink_directory is not set in the server config "
                "(accounts.yaml -> attachment_sink.directory)"
            ),
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


def describe_attachment_sink(sink: Path | None) -> str:
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
