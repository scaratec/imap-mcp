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

from typing import Any, TYPE_CHECKING

from ..imap_core import list_folders as imap_list_folders
from ..policy import MessageFacts

if TYPE_CHECKING:
    from ..context import ServerContext


_FORBIDDEN_SYSTEM_FLAGS = frozenset(["\\Deleted", "\\Draft", "\\Recent"])

TOOL_SET_VERSION = "1.0.0"
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
    "fetch_attachment": "FULL",
}
WRITE_TOOL_CAP = {
    "mark_seen": "mark_seen",
    "bulk_mark_seen": "mark_seen",
    "mark_tagged": "mark_tagged",
    "move": "move_out",
    "copy": "accept_incoming",
    "create_draft": "draft_append",
    "create_reply_draft": "draft_append",
    "add_attachment": "modify_message",
    "replace_attachment": "modify_message",
    "delete_attachment": "modify_message",
}


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
    "_facts_from_envelope",
    "_get_folder_aliases",
    "_is_google_provider",
    "_known_folders_for",
    "_password_for",
    "_password_for_account",
    "_resolve_imap_folder",
]
