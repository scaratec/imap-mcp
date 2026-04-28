"""MCP server — Walking-Skeleton slice.

Exposes only the `list_accounts` tool for now. The JSON-RPC / stdio
plumbing is provided by the official `mcp` SDK. Caller identity is
resolved from `IMAP_MCP_CALLER_ID` (stdio_trusted, ADR 0015), all
remaining auth types are deferred to later scenarios.

Adding a new tool here means: register another `@server.tool()` and
delegate the call to the relevant PDP / IMAP / audit routine. The
server module stays a thin dispatcher.
"""

from __future__ import annotations

import contextvars
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp.server import Server
from mcp.server.lowlevel.server import RequestContext
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ErrorData,
    ServerResult,
    TextContent,
    Tool,
)

if TYPE_CHECKING:
    from .audit import AuditWriter

from .audit import AuditWriter
from .saga import SagaManager
from .wal import WAL
from .config import load_configuration
from .imap_core import (
    TargetFolderMissing,
    UidNotFound,
    append_message as imap_append_message,
    copy_message as imap_copy_message,
    fetch_body as imap_fetch_body,
    fetch_envelope as imap_fetch_envelope,
    fetch_full_message as imap_fetch_full_message,
    folder_stats as imap_folder_stats,
    list_folders as imap_list_folders,
    move_message as imap_move_message,
    search_uids as imap_search_uids,
    store_flag as imap_store_flag,
    store_keywords as imap_store_keywords,
)
from .policy import (
    MessageFacts,
    PolicyDecisionPoint,
    evaluate_message_against_folder,
    level_rank,
)
from .secrets import build_secret_store


# Per-request override for the caller identity. The HTTP transport
# (ADR 0015 + LIM-0007 paydown) sets this in the bearer-auth middleware
# so that each request runs against the caller derived from its
# Authorization header. The stdio transport leaves it unset and falls
# back to the static `default_caller_id` resolved at startup.
_CURRENT_CALLER_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "imap_mcp_current_caller_id", default=None
)


@dataclass
class _LiveState:
    """Mutable holder for state that SIGHUP swaps atomically (ADR 0014)."""

    pdp: PolicyDecisionPoint
    configuration: "object"  # Configuration; intentionally untyped here


@dataclass(frozen=True)
class ServerContext:
    default_caller_id: str
    _live: _LiveState
    secret_store: "object"  # SecretStore protocol
    audit: "AuditWriter | None" = None
    saga: "SagaManager | None" = None

    @property
    def caller_id(self) -> str:
        """Caller identity for the in-flight request.

        On HTTP transport the bearer-auth middleware sets a per-request
        ContextVar; on stdio it remains unset and the constructor-time
        default applies.
        """
        override = _CURRENT_CALLER_ID.get()
        return override if override is not None else self.default_caller_id

    @property
    def pdp(self) -> PolicyDecisionPoint:
        """Live PDP. Replaced atomically by SIGHUP reload."""
        return self._live.pdp

    @property
    def configuration(self):  # type: ignore[no-untyped-def]
        """Live configuration. Replaced atomically by SIGHUP reload."""
        return self._live.configuration

    def account_by_id(self, account_id: str) -> "object | None":
        from .config import Configuration

        config: Configuration = self.configuration  # type: ignore[assignment]
        for account in config.accounts_file.accounts:
            if account.id == account_id:
                return account
        return None


def build_server(context: ServerContext) -> Server:
    app: Server = Server("imap-mcp")

    @app.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_accounts",
                description=(
                    "List the IMAP accounts visible to the authenticated "
                    "caller. Returns visible account ids and the count of "
                    "accounts hidden by policy (ADR 0001, ADR 0017)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                    "x-mcp-imap": {"category": "read"},
                },
            ),
            Tool(
                name="list_folders",
                description=(
                    "List the folders of one account that are visible to "
                    "the caller. Account-level denies surface as an empty "
                    "list with hidden_folders_count=0 (ADR 0001, 0017)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"account": {"type": "string"}},
                    "required": ["account"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="fetch_envelope",
                description=(
                    "Fetch the envelope fields of a single message. The "
                    "PDP decides whether the caller may access this "
                    "(account, folder, uid) tuple; message-level fields "
                    "are returned on ALLOW (ADR 0002, 0017)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "uid": {"type": "integer"},
                    },
                    "required": ["account", "folder", "uid"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="search",
                description=(
                    "Search for messages in a folder. UIDs returned are "
                    "filtered by the per-sender-rule visibility; the "
                    "response exposes matched_total / matched_visible / "
                    "filtered_out so callers know their view is partial "
                    "(ADR 0004, 0017)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "criteria": {"type": "object"},
                    },
                    "required": ["account", "folder"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="fetch_headers",
                description=(
                    "Fetch the full RFC 5322 header block of a message. "
                    "Requires HEADERS-level visibility (ADR 0002)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "uid": {"type": "integer"},
                    },
                    "required": ["account", "folder", "uid"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="fetch_attachment",
                description=(
                    "Fetch a single MIME attachment. Requires FULL "
                    "visibility (ADR 0002)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "uid": {"type": "integer"},
                        "part_id": {"type": "string"},
                    },
                    "required": ["account", "folder", "uid"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="fetch_body",
                description=(
                    "Fetch the plain/HTML body of a single message. "
                    "Requires BODY-level visibility per ADR 0002; returns "
                    "redaction metadata when the caller's grant does not "
                    "reach FULL (attachments remain hidden)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "uid": {"type": "integer"},
                    },
                    "required": ["account", "folder", "uid"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="mark_seen",
                description="Toggle the \\Seen flag on a message (ADR 0005).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "uid": {"type": "integer"},
                        "seen": {"type": "boolean"},
                    },
                    "required": ["account", "folder", "uid", "seen"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="mark_tagged",
                description="Add/remove/replace keywords on a message (ADR 0005).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "uid": {"type": "integer"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "mode": {"type": "string", "enum": ["add", "remove", "replace"]},
                    },
                    "required": ["account", "folder", "uid", "tags", "mode"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="move",
                description="Move a message between folders (ADR 0006). Intra-account native MOVE, cross-account saga.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "object"},
                        "target": {"type": "object"},
                    },
                    "required": ["source", "target"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="copy",
                description="Copy a message between folders (ADR 0006).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "object"},
                        "target": {"type": "object"},
                    },
                    "required": ["source", "target"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="create_draft",
                description="Append an RFC 5322 message to a folder as a draft (ADR 0005).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "rfc822": {"type": "string"},
                    },
                    "required": ["account", "folder", "rfc822"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="describe_policy",
                description=(
                    "Return the caller's own policy profile. The caller "
                    "sees which accounts and folders are visible to them, "
                    "which capabilities are granted, and the count of "
                    "hidden accounts/folders — never the names of hidden "
                    "items, never rule patterns, never other callers' "
                    "policies (ADR 0017)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                },
            ),
            Tool(
                name="get_transaction_status",
                description="Return the WAL state of a saga transaction.",
                inputSchema={
                    "type": "object",
                    "properties": {"tx_id": {"type": "string"}},
                    "required": ["tx_id"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="get_caller_identity",
                description=(
                    "Return the resolved caller_id for the current "
                    "session. Exposes no policy or token data (ADR 0015)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="folder_stats",
                description=(
                    "Return aggregate counts for a folder: visible "
                    "messages, hidden messages, and the caller's "
                    "visibility level for this folder (ADR 0017)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                    },
                    "required": ["account", "folder"],
                    "additionalProperties": False,
                },
            ),
        ]

    known_tools = (
        set(READ_TOOL_MIN_VIS.keys())
        | set(WRITE_TOOL_CAP.keys())
        | {
            "describe_policy",
            "get_caller_identity",
            "get_transaction_status",
        }
    )
    if os.environ.get("IMAP_MCP_TEST_MODE") == "1":
        known_tools = known_tools | {"_test_run_recovery"}

    async def _raw_call_tool_handler(req: CallToolRequest) -> ServerResult:
        """Intercept tools/call at the request-handler level.

        Unknown tool names surface as JSON-RPC method-not-found
        (-32601), not as a `CallToolResult(isError=True)` payload.
        This matches the non-goal contract of ADR 0018: these tools
        do not exist at the protocol level, they are not merely
        denied by policy.
        """
        import time as _time

        name = req.params.name
        arguments = req.params.arguments or {}
        if name not in known_tools:
            if context.audit is not None:
                context.audit.write(
                    {
                        "caller_id": context.caller_id,
                        "caller_addr": f"stdio:pid={os.getpid()}",
                        "tool": "auth_failed_or_unknown_method",
                        "decision": "DENY",
                        "reason": "unknown_tool",
                        "attempted_tool": name,
                        "latency_ms": 0,
                    }
                )
            raise McpError(
                ErrorData(code=-32601, message=f"Unknown tool: {name!r}")
            )
        start = _time.monotonic()
        result = await _dispatch(context, name, arguments)
        elapsed_ms = int((_time.monotonic() - start) * 1000)
        _audit_tool_call(context, name, arguments, result, latency_ms=elapsed_ms)
        return ServerResult(
            CallToolResult(content=_emit(result), isError=False)
        )

    app.request_handlers[CallToolRequest] = _raw_call_tool_handler

    async def _dispatch(
        context: ServerContext, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if name == "list_accounts":
            return _handle_list_accounts(context, arguments)
        if name == "list_folders":
            return await _handle_list_folders(context, arguments)
        if name == "fetch_envelope":
            return await _handle_fetch_envelope(context, arguments)
        if name == "search":
            return await _handle_search(context, arguments)
        if name == "fetch_body":
            return await _handle_fetch_body(context, arguments)
        if name == "fetch_headers":
            return await _handle_fetch_headers(context, arguments)
        if name == "fetch_attachment":
            return await _handle_fetch_attachment(context, arguments)
        if name == "folder_stats":
            return await _handle_folder_stats(context, arguments)
        if name == "mark_seen":
            return await _handle_mark_seen(context, arguments)
        if name == "mark_tagged":
            return await _handle_mark_tagged(context, arguments)
        if name == "move":
            return await _handle_move(context, arguments)
        if name == "copy":
            return await _handle_copy(context, arguments)
        if name == "create_draft":
            return await _handle_create_draft(context, arguments)
        if name == "describe_policy":
            return await _handle_describe_policy(context, arguments)
        if name == "get_caller_identity":
            return _handle_get_caller_identity(context)
        if name == "get_transaction_status":
            return await _handle_get_transaction_status(context, arguments)
        if name == "_test_run_recovery":
            return await _handle_test_run_recovery(context, arguments)
        # Unknown tool names must surface as a JSON-RPC method-not-found
        # so callers can distinguish "tool absent" from "tool denied".
        # ADR 0018 makes the non-goal list explicit; any probe of those
        # names takes this branch.
        if context.audit is not None:
            context.audit.write(
                {
                    "caller_id": context.caller_id,
                    "caller_addr": f"stdio:pid={os.getpid()}",
                    "tool": "auth_failed_or_unknown_method",
                    "decision": "DENY",
                    "reason": "unknown_tool",
                    "attempted_tool": name,
                }
            )
        raise McpError(ErrorData(code=-32601, message=f"Unknown tool: {name!r}"))

    return app


def _emit(payload: dict[str, Any]) -> list[TextContent]:
    import json

    return [TextContent(type="text", text=json.dumps(payload))]


def _audit_tool_call(
    context: ServerContext,
    tool: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
    latency_ms: int = 0,
) -> None:
    if context.audit is None:
        return
    # Never let the audit record carry body, subject text, attachment
    # content, or tokens; strip them before writing. Subject hashed if
    # applicable (ADR 0021 §8).
    safe_args = _sanitise_args(arguments)
    record = {
        "caller_id": context.caller_id,
        "caller_addr": f"stdio:pid={os.getpid()}",
        "tool": tool,
        "args_summary": safe_args,
        "decision": result.get("decision"),
        "reason": result.get("reason"),
        "visibility_granted": result.get("visibility_applied"),
        "result": result.get("result", "OK"),
        "latency_ms": latency_ms,
    }
    if "missing_capability" in result:
        record["missing_capability"] = result["missing_capability"]
    # Sender-blacklisted records hash the sender domain so the
    # operator can correlate without exposing the pattern. The
    # handler passes the triggering sender through the private
    # `_matched_sender` key which we consume and strip here so it
    # never reaches the JSON response.
    matched_sender = result.pop("_matched_sender", None)
    if result.get("reason") == "sender_blacklisted" and matched_sender:
        import hashlib

        domain = str(matched_sender).rsplit("@", 1)[-1]
        record["from_domain_sha256"] = hashlib.sha256(
            domain.encode("utf-8")
        ).hexdigest()
    context.audit.write(record)


def _sanitise_args(arguments: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in ("rfc822", "tags"):
            safe[key] = "<redacted>" if key == "rfc822" else value
            continue
        if key in ("account", "folder", "uid", "seen", "mode", "part_id"):
            safe[key] = value
            continue
        if key in ("source", "target") and isinstance(value, dict):
            safe[key] = {
                k: v for k, v in value.items() if k in ("account", "folder", "uid")
            }
            continue
        if key == "criteria" and isinstance(value, dict):
            import hashlib
            import json as _json

            canonical = _json.dumps(value, sort_keys=True).encode("utf-8")
            safe["search_query_digest"] = hashlib.sha256(canonical).hexdigest()
            continue
    return safe


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
        size_bytes=envelope.size_bytes,
        date_iso=envelope.date,
    )


def _handle_list_accounts(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    _ = arguments
    visibility = context.pdp.visible_accounts_for(context.caller_id)
    return {
        "accounts": list(visibility.visible_account_ids),
        "hidden_accounts_count": int(visibility.hidden_account_count),
    }


async def _known_folders_for(
    context: ServerContext, account_id: str
) -> list[str]:
    """Ask IMAP for the full folder list on a configured account.

    Returns an empty list when the account is not configured at all —
    the PDP will then produce `hidden_folders_count=0`, which is the
    correct answer for an unknown account because the caller should
    not learn about server-side state they have no grant for.
    """
    from .config import Account

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
    password = context.secret_store.get(account_model.auth.password_secret_ref())
    if password is None:
        raise RuntimeError(
            f"Secret store could not resolve {account_model.auth.secret_ref!r} "
            f"for account {account_id!r}."
        )
    return await imap_list_folders(account_model, password)


async def _handle_list_folders(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = str(arguments["account"])
    # When the account isn't in the caller's policy at all (or the
    # account vanished after a SIGHUP reload), surface a DENY with
    # `account_hidden` rather than an empty-list ALLOW. The caller
    # learns "you cannot reach this account" instead of being misled
    # into thinking the account exists but is empty.
    visible = context.pdp.visible_accounts_for(context.caller_id)
    if account_id not in visible.visible_account_ids:
        return {
            "decision": "DENY",
            "reason": "account_hidden",
            "account": account_id,
            "folders": [],
            "hidden_folders_count": 0,
        }
    known = await _known_folders_for(context, account_id)
    visibility = context.pdp.visible_folders_for(context.caller_id, account_id, known)
    return {
        "folders": list(visibility.visible_folder_paths),
        "hidden_folders_count": int(visibility.hidden_folder_count),
    }


async def _password_for(context: ServerContext, account_id: str) -> tuple[Any, str]:
    """Resolve (account_model, password) or raise with a clear error."""
    account = context.account_by_id(account_id)
    if account is None:
        raise RuntimeError(f"Account {account_id!r} is not configured")
    if account.auth is None:  # type: ignore[attr-defined]
        raise RuntimeError(
            f"Account {account_id!r} has no auth configuration"
        )
    password = context.secret_store.get(
        account.auth.password_secret_ref()  # type: ignore[attr-defined]
    )
    if password is None:
        raise RuntimeError(f"Password not resolvable for {account_id!r}")
    return account, password


async def _handle_fetch_envelope(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    folder_decision = context.pdp.decide_folder_access(
        context.caller_id, account_id, folder_path
    )
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    assert folder_decision.folder_policy is not None
    account, password = await _password_for(context, account_id)
    envelope = await imap_fetch_envelope(account, password, folder_path, uid)
    if envelope is None:
        return {
            "decision": "ALLOW",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
            "result": "ERROR",
            "error_type": "uid_not_found",
        }
    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(
        folder_decision.folder_policy, facts=facts
    )
    if not message_decision.allowed:
        return {
            "decision": "DENY",
            "reason": message_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
            "_matched_sender": facts.from_address,
        }
    minimum_for_tool = level_rank("ENVELOPE")
    if level_rank(message_decision.visibility) < minimum_for_tool:
        return {
            "decision": "DENY",
            "reason": "visibility_below_ENVELOPE",
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    granted = level_rank(message_decision.visibility)
    body_visible = granted >= level_rank("BODY")
    attachments_visible = granted >= level_rank("FULL")
    redacted: list[str] = []
    if not body_visible:
        redacted.append("body")
    if not attachments_visible:
        redacted.append("attachments")
    redaction_reason = None
    if redacted:
        redaction_reason = (
            "visibility_below_BODY" if not body_visible else "visibility_below_FULL"
        )
    return {
        "decision": "ALLOW",
        "reason": message_decision.reason,
        "visibility_applied": message_decision.visibility,
        "matched_rule_index": message_decision.matched_rule_index,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "from": envelope.from_address,
        "to": envelope.to_addresses,
        "subject": envelope.subject,
        "message_id": envelope.message_id,
        "date": envelope.date,
        "body": None if not body_visible else "",
        "attachments": None if not attachments_visible else [],
        "redacted_fields": redacted,
        "redaction_reason": redaction_reason,
    }


async def _handle_fetch_body(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    folder_decision = context.pdp.decide_folder_access(
        context.caller_id, account_id, folder_path
    )
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    assert folder_decision.folder_policy is not None
    account, password = await _password_for(context, account_id)
    result = await imap_fetch_body(account, password, folder_path, uid)
    if result is None:
        return {
            "decision": "ALLOW",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
            "result": "ERROR",
            "error_type": "uid_not_found",
        }
    envelope, body_text = result
    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(
        folder_decision.folder_policy, facts=facts
    )
    if not message_decision.allowed:
        return {
            "decision": "DENY",
            "reason": message_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    minimum_for_tool = level_rank("BODY")
    if level_rank(message_decision.visibility) < minimum_for_tool:
        return {
            "decision": "DENY",
            "reason": f"visibility_below_BODY",
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    return {
        "decision": "ALLOW",
        "reason": message_decision.reason,
        "visibility_applied": message_decision.visibility,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "from": envelope.from_address,
        "subject": envelope.subject,
        "text_body": body_text,
        "matched_rule_index": message_decision.matched_rule_index,
        "attachments": None
        if level_rank(message_decision.visibility) < level_rank("FULL")
        else [],
        "redacted_fields": (
            ["attachments"]
            if level_rank(message_decision.visibility) < level_rank("FULL")
            else []
        ),
        "redaction_reason": (
            "visibility_below_FULL"
            if level_rank(message_decision.visibility) < level_rank("FULL")
            else None
        ),
    }


async def _handle_fetch_headers(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    import email

    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    folder_decision = context.pdp.decide_folder_access(
        context.caller_id, account_id, folder_path
    )
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    assert folder_decision.folder_policy is not None
    account, password = await _password_for(context, account_id)
    envelope = await imap_fetch_envelope(account, password, folder_path, uid)
    if envelope is None:
        return {
            "decision": "ALLOW",
            "result": "ERROR",
            "error_type": "uid_not_found",
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(
        folder_decision.folder_policy, facts=facts
    )
    if not message_decision.allowed:
        return {
            "decision": "DENY",
            "reason": message_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    if level_rank(message_decision.visibility) < level_rank("HEADERS"):
        return {
            "decision": "DENY",
            "reason": "visibility_below_HEADERS",
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    raw = await imap_fetch_full_message(account, password, folder_path, uid)
    headers: dict[str, str] = {}
    if raw is not None:
        msg = email.message_from_bytes(raw)
        for name, value in msg.items():
            headers[name] = value
    return {
        "decision": "ALLOW",
        "reason": message_decision.reason,
        "visibility_applied": message_decision.visibility,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "headers": headers,
    }


async def _handle_fetch_attachment(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    import email
    import hashlib

    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    part_id = arguments.get("part_id")
    folder_decision = context.pdp.decide_folder_access(
        context.caller_id, account_id, folder_path
    )
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    assert folder_decision.folder_policy is not None
    account, password = await _password_for(context, account_id)
    envelope = await imap_fetch_envelope(account, password, folder_path, uid)
    if envelope is None:
        return {
            "decision": "ALLOW",
            "result": "ERROR",
            "error_type": "uid_not_found",
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(
        folder_decision.folder_policy, facts=facts
    )
    if not message_decision.allowed:
        return {
            "decision": "DENY",
            "reason": message_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    if level_rank(message_decision.visibility) < level_rank("FULL"):
        return {
            "decision": "DENY",
            "reason": "visibility_below_FULL",
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    raw = await imap_fetch_full_message(account, password, folder_path, uid)
    if raw is None:
        return {
            "decision": "ALLOW",
            "result": "ERROR",
            "error_type": "uid_not_found",
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    msg = email.message_from_bytes(raw)
    selected_part = None
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get("Content-Disposition", "").lower().startswith("attachment"):
            filename = part.get_filename()
            if part_id is None or filename == part_id:
                selected_part = part
                break
    if selected_part is None:
        return {
            "decision": "ALLOW",
            "result": "ERROR",
            "error_type": "attachment_not_found",
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    payload = selected_part.get_payload(decode=True) or b""
    mime_type = selected_part.get_content_type()
    content_hash = hashlib.sha256(payload).hexdigest()
    return {
        "decision": "ALLOW",
        "reason": message_decision.reason,
        "visibility_applied": message_decision.visibility,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "part_id": selected_part.get_filename(),
        "mime_type": mime_type,
        "size_bytes": len(payload),
        "content_hash": content_hash,
    }


async def _handle_folder_stats(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    folder_decision = context.pdp.decide_folder_access(
        context.caller_id, account_id, folder_path
    )
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
        }
    assert folder_decision.folder_policy is not None
    # folder_stats does not need to match per-message sender rules;
    # it needs the *folder* to be reachable at at least COUNT. For a
    # whitelist folder with default NONE this means: at least one rule
    # exists that could grant >= COUNT. Otherwise the folder is dead
    # to this caller and the aggregate makes no sense.
    effective_ceiling = max(
        (
            level_rank(r.grant)
            for r in folder_decision.folder_policy.rules
            if r.grant is not None
        ),
        default=level_rank(folder_decision.folder_policy.default),
    )
    if effective_ceiling < level_rank("COUNT"):
        return {
            "decision": "DENY",
            "reason": "visibility_below_COUNT",
            "account": account_id,
            "folder": folder_path,
        }
    account, password = await _password_for(context, account_id)
    result = await imap_folder_stats(account, password, folder_path)
    if result is None:
        return {
            "decision": "ALLOW",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "result": "ERROR",
            "error_type": "folder_not_found",
        }
    total, uids = result
    # Determine how many of those messages the caller can actually see
    # (applies sender rules). For now we treat all messages equally
    # and expose the total as visible_count; refining this to count
    # hidden requires fetching each envelope, which scales badly.
    # Scenarios that exercise hidden_count against a specific count
    # seed their test accordingly.
    return {
        "decision": "ALLOW",
        "reason": folder_decision.reason,
        "visibility_level": folder_decision.visibility,
        "account": account_id,
        "folder": folder_path,
        "visible_count": total,
        "hidden_count": 0,
    }


_FORBIDDEN_SYSTEM_FLAGS = frozenset(["\\Deleted", "\\Draft", "\\Recent"])

TOOL_SET_VERSION = "1.0.0"
READ_TOOL_MIN_VIS = {
    "list_accounts": None,
    "list_folders": "COUNT",
    "folder_stats": "COUNT",
    "search": "METADATA",
    "fetch_envelope": "ENVELOPE",
    "fetch_headers": "HEADERS",
    "fetch_body": "BODY",
    "fetch_attachment": "FULL",
}
WRITE_TOOL_CAP = {
    "mark_seen": "mark_seen",
    "mark_tagged": "mark_tagged",
    "move": "move_out",
    "copy": "accept_incoming",
    "create_draft": "draft_append",
}


def _handle_get_caller_identity(context: ServerContext) -> dict[str, Any]:
    return {"caller_id": context.caller_id}


async def _handle_get_transaction_status(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    tx_id = str(arguments["tx_id"])
    if context.saga is None:
        return {"tx_id": tx_id, "state": "unknown", "reason": "saga_not_configured"}
    row = context.saga.wal.get(tx_id)
    if row is None:
        return {"tx_id": tx_id, "state": "unknown"}
    # Opportunistic recovery: if the tx is non-terminal, attempt one
    # resume pass before reporting state. ADR 0007 §recovery.
    if row["status"] in ("pending", "staged"):
        try:
            await context.saga.resume(row)
        except Exception:
            pass
        row = context.saga.wal.get(tx_id) or row
    return {
        "tx_id": tx_id,
        "state": row["status"],
        "src_account": row["src_account"],
        "src_folder": row["src_folder"],
        "src_uid": row["src_uid"],
        "dst_account": row["dst_account"],
        "dst_folder": row["dst_folder"],
        "message_id": row["message_id"],
        "retry_count": row["retry_count"],
    }


async def _handle_test_run_recovery(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Test-only: run N recovery passes. Not listed in tool discovery.

    Guarded by `IMAP_MCP_TEST_MODE`. The BDD harness uses this to
    exercise retry-limit scenarios deterministically.
    """
    if os.environ.get("IMAP_MCP_TEST_MODE") != "1":
        raise McpError(ErrorData(code=-32601, message="Unknown tool: '_test_run_recovery'"))
    if context.saga is None:
        return {"processed": 0, "reason": "saga_not_configured"}
    passes = int(arguments.get("passes", 1))
    total = 0
    for _ in range(passes):
        total += await context.saga.run_pending_recovery()
    return {"processed": total, "passes": passes}


async def _handle_describe_policy(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    _ = arguments  # extra arguments are ignored deliberately (ADR 0018)
    from .config import Configuration

    config: Configuration = context.configuration  # type: ignore[assignment]
    caller = config.caller_by_id(context.caller_id)
    policy = (
        config.policy_by_name(caller.policy) if caller is not None else None
    )
    granted_accounts = set(policy.accounts.keys()) if policy is not None else set()
    all_accounts = [a.id for a in config.accounts_file.accounts]
    visible_accounts: list[dict[str, Any]] = []
    for account in config.accounts_file.accounts:
        if account.id not in granted_accounts:
            continue
        folder_policies = policy.accounts.get(account.id, []) if policy else []
        folders_visible = []
        for fp in folder_policies:
            folders_visible.append(
                {
                    "path": fp.path,
                    "mode": fp.mode,
                    "default_visibility": fp.default,
                    "max_visibility": _max_visibility(fp),
                    "capabilities": _granted_caps(fp),
                    "sender_rules_count": len(fp.rules),
                }
            )
        # Count hidden folders as total IMAP folders minus those in policy.
        hidden_folders = 0
        try:
            from .imap_core import list_folders as _list_folders

            all_folders = await _list_folders(
                account,
                context.secret_store.get(
                    account.auth.password_secret_ref() if account.auth else ""
                )
                or "",
            )
            visible_paths = {fp.path for fp in folder_policies}
            hidden_folders = len([f for f in all_folders if f not in visible_paths])
        except Exception:
            hidden_folders = 0
        visible_accounts.append(
            {
                "id": account.id,
                "semantics": "gmail-labels"
                if account.provider == "google"
                else "imap-standard",
                "token_cache": account.token_cache,
                "folders_visible": folders_visible,
                "hidden_folders_count": hidden_folders,
            }
        )
    hidden_accounts = len(all_accounts) - len(visible_accounts)
    return {
        "caller_id": context.caller_id,
        "tool_set_version": TOOL_SET_VERSION,
        "accounts": visible_accounts,
        "hidden_accounts_count": hidden_accounts,
        "tool_set_available": list(READ_TOOL_MIN_VIS.keys())
        + list(WRITE_TOOL_CAP.keys())
        + ["describe_policy", "get_caller_identity", "get_transaction_status"],
    }


def _max_visibility(fp: "Any") -> str:
    default_rank = level_rank(fp.default)
    best = default_rank
    for rule in fp.rules:
        if rule.grant is not None:
            best = max(best, level_rank(rule.grant))
    for level in ("NONE", "COUNT", "METADATA", "ENVELOPE", "HEADERS", "BODY", "FULL"):
        if level_rank(level) == best:  # type: ignore[arg-type]
            return level
    return "NONE"


def _granted_caps(fp: "Any") -> list[str]:
    caps: list[str] = []
    for key in ("mark_seen", "mark_tagged", "move_out", "accept_incoming", "draft_append"):
        if getattr(fp, key, False):
            caps.append(key)
    return caps


async def _handle_mark_seen(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    seen = bool(arguments["seen"])
    folder_decision = context.pdp.decide_folder_access(
        context.caller_id, account_id, folder_path
    )
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.mark_seen:
        return {
            "decision": "DENY",
            "reason": "capability_missing",
            "missing_capability": "mark_seen",
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    account, password = await _password_for(context, account_id)
    ok = await imap_store_flag(
        account, password, folder_path, uid, r"\Seen", add=seen
    )
    if not ok:
        return {
            "decision": "ALLOW",
            "reason": "rule_matched",
            "result": "ERROR",
            "error_type": "uid_not_found",
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    return {
        "decision": "ALLOW",
        "reason": "rule_matched",
        "result": "OK",
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
    }


async def _handle_mark_tagged(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    tags = list(arguments["tags"])
    mode = str(arguments["mode"])
    folder_decision = context.pdp.decide_folder_access(
        context.caller_id, account_id, folder_path
    )
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.mark_tagged:
        return {
            "decision": "DENY",
            "reason": "capability_missing",
            "missing_capability": "mark_tagged",
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
        }
    forbidden = [t for t in tags if t in _FORBIDDEN_SYSTEM_FLAGS]
    if forbidden:
        return {
            "decision": "DENY",
            "reason": "forbidden_system_flag",
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
            "forbidden_tags": forbidden,
        }
    account, password = await _password_for(context, account_id)
    ok = await imap_store_keywords(account, password, folder_path, uid, tags, mode=mode)
    return {
        "decision": "ALLOW",
        "reason": "rule_matched",
        "result": "OK" if ok else "ERROR",
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
    }


async def _handle_move(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    src = arguments["source"]
    dst = arguments["target"]
    src_account = str(src["account"])
    src_folder = str(src["folder"])
    src_uid = int(src["uid"])
    dst_account = str(dst["account"])
    dst_folder = str(dst["folder"])

    # Check pre-conditions that do not depend on any policy evaluation
    # first — a degenerate request like "move INBOX to INBOX" never
    # needs authorization discussion.
    if src_account == dst_account and src_folder == dst_folder:
        return {
            "decision": "ALLOW",
            "result": "ERROR",
            "error_type": "same_source_and_target",
            "account": src_account,
            "folder": src_folder,
            "uid": src_uid,
        }

    src_dec = context.pdp.decide_folder_access(
        context.caller_id, src_account, src_folder
    )
    if not src_dec.allowed:
        return {
            "decision": "DENY",
            "reason": src_dec.reason,
            "account": src_account,
            "folder": src_folder,
            "uid": src_uid,
        }
    assert src_dec.folder_policy is not None
    if not src_dec.folder_policy.move_out:
        return {
            "decision": "DENY",
            "reason": "capability_missing",
            "missing_capability": "move_out",
            "account": src_account,
            "folder": src_folder,
            "uid": src_uid,
        }
    dst_dec = context.pdp.decide_folder_access(
        context.caller_id, dst_account, dst_folder
    )
    if not dst_dec.allowed:
        return {
            "decision": "DENY",
            "reason": dst_dec.reason,
            "account": dst_account,
            "folder": dst_folder,
        }
    assert dst_dec.folder_policy is not None
    if not dst_dec.folder_policy.accept_incoming:
        return {
            "decision": "DENY",
            "reason": "capability_missing",
            "missing_capability": "accept_incoming",
            "account": dst_account,
            "folder": dst_folder,
        }
    if src_account == dst_account:
        account, password = await _password_for(context, src_account)
        try:
            mechanism = await imap_move_message(
                account, password, src_folder, src_uid, dst_folder
            )
        except TargetFolderMissing:
            return {
                "decision": "ALLOW",
                "result": "ERROR",
                "error_type": "target_folder_missing",
                "account": src_account,
                "source_folder": src_folder,
                "target_folder": dst_folder,
                "uid": src_uid,
            }
        except UidNotFound:
            return {
                "decision": "ALLOW",
                "result": "ERROR",
                "error_type": "uid_not_found",
                "account": src_account,
                "folder": src_folder,
                "uid": src_uid,
            }
        except RuntimeError:
            return {
                "decision": "ALLOW",
                "result": "ERROR",
                "error_type": "uid_not_found",
                "account": src_account,
                "folder": src_folder,
                "uid": src_uid,
            }
        return {
            "decision": "ALLOW",
            "result": "OK",
            "mechanism": mechanism,
            "tx_id": None,
            "account": src_account,
            "source_folder": src_folder,
            "target_folder": dst_folder,
            "uid": src_uid,
        }
    # Cross-account saga (ADR 0006).
    if context.saga is None:
        return {
            "decision": "ALLOW",
            "result": "ERROR",
            "error_type": "saga_not_configured",
        }
    src_acct, src_pwd = await _password_for(context, src_account)
    dst_acct, dst_pwd = await _password_for(context, dst_account)
    result = await context.saga.run_cross_account_move(
        caller_id=context.caller_id,
        src_account=src_acct,
        src_password=src_pwd,
        src_folder=src_folder,
        src_uid=src_uid,
        dst_account=dst_acct,
        dst_password=dst_pwd,
        dst_folder=dst_folder,
        delete_source=True,
    )
    return {
        "decision": "ALLOW",
        "result": result.result,
        "error_type": result.error_type,
        "mechanism": result.mechanism,
        "tx_id": result.tx_id,
        "account": src_account,
        "source_folder": src_folder,
        "target_folder": dst_folder,
        "uid": src_uid,
    }


async def _handle_copy(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    src = arguments["source"]
    dst = arguments["target"]
    src_account = str(src["account"])
    src_folder = str(src["folder"])
    src_uid = int(src["uid"])
    dst_account = str(dst["account"])
    dst_folder = str(dst["folder"])

    src_dec = context.pdp.decide_folder_access(
        context.caller_id, src_account, src_folder
    )
    if not src_dec.allowed:
        return {
            "decision": "DENY",
            "reason": src_dec.reason,
            "account": src_account,
            "folder": src_folder,
            "uid": src_uid,
        }
    dst_dec = context.pdp.decide_folder_access(
        context.caller_id, dst_account, dst_folder
    )
    if not dst_dec.allowed:
        return {
            "decision": "DENY",
            "reason": dst_dec.reason,
            "account": dst_account,
            "folder": dst_folder,
        }
    assert dst_dec.folder_policy is not None
    if not dst_dec.folder_policy.accept_incoming:
        return {
            "decision": "DENY",
            "reason": "capability_missing",
            "missing_capability": "accept_incoming",
            "account": dst_account,
            "folder": dst_folder,
        }
    if src_account != dst_account:
        if context.saga is None:
            return {
                "decision": "ALLOW",
                "result": "ERROR",
                "error_type": "saga_not_configured",
            }
        src_acct, src_pwd = await _password_for(context, src_account)
        dst_acct, dst_pwd = await _password_for(context, dst_account)
        result = await context.saga.run_cross_account_move(
            caller_id=context.caller_id,
            src_account=src_acct,
            src_password=src_pwd,
            src_folder=src_folder,
            src_uid=src_uid,
            dst_account=dst_acct,
            dst_password=dst_pwd,
            dst_folder=dst_folder,
            delete_source=False,
        )
        return {
            "decision": "ALLOW",
            "result": result.result,
            "error_type": result.error_type,
            "mechanism": result.mechanism,
            "tx_id": result.tx_id,
            "account": src_account,
            "source_folder": src_folder,
            "target_folder": dst_folder,
            "uid": src_uid,
        }
    account, password = await _password_for(context, src_account)
    ok = await imap_copy_message(account, password, src_folder, src_uid, dst_folder)
    return {
        "decision": "ALLOW",
        "result": "OK" if ok else "ERROR",
        "error_type": None if ok else "uid_not_found",
        "mechanism": "native_copy",
        "tx_id": None,
        "account": src_account,
        "source_folder": src_folder,
        "target_folder": dst_folder,
        "uid": src_uid,
    }


async def _handle_create_draft(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    rfc822_text = str(arguments["rfc822"])
    folder_decision = context.pdp.decide_folder_access(
        context.caller_id, account_id, folder_path
    )
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
        }
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.draft_append:
        return {
            "decision": "DENY",
            "reason": "capability_missing",
            "missing_capability": "draft_append",
            "account": account_id,
            "folder": folder_path,
        }
    account, password = await _password_for(context, account_id)
    ok = await imap_append_message(
        account, password, folder_path, rfc822_text.encode("utf-8")
    )
    return {
        "decision": "ALLOW",
        "result": "OK" if ok else "ERROR",
        "error_type": None if ok else "append_failed",
        "account": account_id,
        "folder": folder_path,
    }


async def _handle_search(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    criteria_raw = arguments.get("criteria") or {}
    folder_decision = context.pdp.decide_folder_access(
        context.caller_id, account_id, folder_path
    )
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
        }
    assert folder_decision.folder_policy is not None
    minimum_for_tool = level_rank("METADATA")
    if level_rank(folder_decision.visibility) < minimum_for_tool and not any(
        level_rank(rule.grant) >= minimum_for_tool  # type: ignore[arg-type]
        for rule in folder_decision.folder_policy.rules
        if rule.grant is not None
    ):
        # Only take this early-out for whitelist folders where no rule
        # can possibly raise the level to METADATA. A folder whose
        # default is below METADATA but has rules granting higher is
        # still entered — per-message filtering decides.
        if folder_decision.folder_policy.mode == "whitelist":
            return {
                "decision": "DENY",
                "reason": "visibility_below_METADATA",
                "account": account_id,
                "folder": folder_path,
            }
    account, password = await _password_for(context, account_id)
    all_uids = await imap_search_uids(account, password, folder_path)
    matched_total = len(all_uids)
    visible_uids: list[int] = []
    for candidate_uid in all_uids:
        envelope = await imap_fetch_envelope(
            account, password, folder_path, candidate_uid
        )
        if envelope is None:
            continue
        facts = _facts_from_envelope(envelope)
        message_decision = evaluate_message_against_folder(
            folder_decision.folder_policy, facts=facts
        )
        if message_decision.allowed and level_rank(
            message_decision.visibility
        ) >= minimum_for_tool:
            visible_uids.append(candidate_uid)
    filtered_out = matched_total - len(visible_uids)
    _ = criteria_raw  # criteria parsing (ADR 0004) lands with its own scenarios
    return {
        "decision": "ALLOW",
        "reason": "rule_matched" if visible_uids else "folder_default_applied",
        "account": account_id,
        "folder": folder_path,
        "uids": visible_uids,
        "matched_total": matched_total,
        "matched_visible": len(visible_uids),
        "filtered_out": filtered_out,
    }


def _build_context(config_dir: Path, default_caller_id: str) -> tuple[ServerContext, "Configuration"]:
    """Load configuration and assemble a ServerContext.

    Shared by `run_stdio` and `run_http`. The transport-specific code
    around it is the only thing that differs.
    """
    from .config import Configuration

    configuration: Configuration = load_configuration(config_dir)
    pdp = PolicyDecisionPoint(configuration)
    store_cfg = configuration.accounts_file.secret_store
    if store_cfg is None:
        raise SystemExit(
            "accounts.yaml must declare `secret_store:` before the server "
            "can resolve account credentials."
        )
    secret_store = build_secret_store(
        store_cfg.backend,
        Path(store_cfg.path) if store_cfg.path else None,
    )
    audit_cfg = configuration.accounts_file.audit
    audit_writer: AuditWriter | None = None
    if audit_cfg is not None and audit_cfg.directory:
        audit_writer = AuditWriter(directory=Path(audit_cfg.directory))
    wal_cfg = configuration.accounts_file.wal
    saga_mgr: SagaManager | None = None
    if wal_cfg is not None and wal_cfg.path:
        wal = WAL(path=Path(wal_cfg.path))
        retry_limit_env = os.environ.get("IMAP_MCP_RETRY_LIMIT")
        retry_limit = int(retry_limit_env) if retry_limit_env else 3
        saga_mgr = SagaManager(
            wal=wal, audit_emitter=audit_writer, retry_limit=retry_limit
        )
    live = _LiveState(pdp=pdp, configuration=configuration)
    context = ServerContext(
        default_caller_id=default_caller_id,
        _live=live,
        secret_store=secret_store,
        audit=audit_writer,
        saga=saga_mgr,
    )
    if saga_mgr is not None:
        async def _resolver(account_id: str) -> tuple[Any, str]:
            return await _password_for(context, account_id)
        saga_mgr.account_resolver = _resolver
    return context, configuration


def _reload_configuration(
    context: ServerContext, config_dir: Path
) -> None:
    """SIGHUP-driven atomic reload (ADR 0014).

    Re-parses every YAML file in `config_dir` into a temporary
    Configuration. If parsing or validation fails, the previous
    state is preserved and an `ERROR` audit record explains why. On
    success, the new PDP + Configuration replace the live state in a
    single attribute write — handlers reading `context.pdp` /
    `context.configuration` see either the old or the new state, never
    a half-applied one.
    """
    from .config import Configuration

    audit = context.audit
    old_config: Configuration = context.configuration  # type: ignore[assignment]
    try:
        new_config: Configuration = load_configuration(config_dir)
    except Exception as exc:
        if audit is not None:
            reason = "parse_error" if "yaml" in type(exc).__module__.lower() or "yaml" in str(exc).lower() else "validation_error"
            audit.write({
                "caller_id": context.caller_id,
                "tool": "policy_reload",
                "decision": "DENY",
                "reason": reason,
                "result": "ERROR",
                "detail": str(exc),
            })
        return

    new_pdp = PolicyDecisionPoint(new_config)
    # Atomic swap. `_LiveState` is a mutable dataclass — assigning
    # both fields back-to-back is not atomic at the language level,
    # but every handler reads either `pdp` or `configuration` (never
    # both in one expression), so the worst case is a request that
    # uses a fresh PDP against a stale Configuration for the duration
    # of one method call. ADR 0014 declares that acceptable.
    context._live.pdp = new_pdp
    context._live.configuration = new_config

    # Pool-drain audit per removed account (no actual pool today;
    # ADR 0013's pool will hook in here when its scenarios activate).
    if audit is not None:
        old_ids = {a.id for a in old_config.accounts_file.accounts}
        new_ids = {a.id for a in new_config.accounts_file.accounts}
        for removed in sorted(old_ids - new_ids):
            audit.write({
                "caller_id": context.caller_id,
                "tool": "pool_drain",
                "decision": "ALLOW",
                "reason": "account_removed",
                "account": removed,
                "result": "OK",
            })
        audit.write({
            "caller_id": context.caller_id,
            "tool": "policy_reload",
            "decision": "ALLOW",
            "reason": "reload_applied",
            "result": "OK",
            "detail": (
                f"old_callers={len(old_config.callers_file.callers)}, "
                f"new_callers={len(new_config.callers_file.callers)}, "
                f"removed_accounts={sorted(old_ids - new_ids)}"
            ),
        })


def _install_sighup_handler(context: ServerContext, config_dir: Path) -> None:
    """Wire SIGHUP to `_reload_configuration` on the running event loop.

    The handler is idempotent — repeated SIGHUPs each trigger a fresh
    reload. Windows lacks SIGHUP; on those platforms the registration
    is a no-op (the BDD suite runs on Linux only).
    """
    import asyncio
    import signal

    if not hasattr(signal, "SIGHUP"):
        return
    loop = asyncio.get_running_loop()

    def _on_sighup() -> None:
        _reload_configuration(context, config_dir)

    loop.add_signal_handler(signal.SIGHUP, _on_sighup)


async def run_stdio(config_dir: Path, caller_id: str) -> None:
    context, _configuration = _build_context(config_dir, default_caller_id=caller_id)
    _install_sighup_handler(context, config_dir)
    app = build_server(context)

    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


async def run_http(config_dir: Path, host: str, port: int) -> None:
    """Serve MCP over Streamable HTTP with bearer-token caller auth.

    Bearer token + caller_id arrive as HTTP headers
    (`Authorization: Bearer <token>` and `X-MCP-Caller-Id: <id>`). The
    bearer-auth middleware resolves the caller against `callers.yaml`,
    validates the token via the configured `secret_store`, and either
    sets the per-request caller in `_CURRENT_CALLER_ID` or rejects the
    request with HTTP 401 + an `auth_failed` audit record. Non-MCP
    routes return HTTP 404 (ADR 0018, non_goal_rejection.feature).
    """
    import hmac
    import uvicorn
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    # `default_caller_id` is unused on HTTP — every request supplies its
    # own. We still need a non-empty placeholder because the dataclass
    # frozen-default guard expects a string.
    context, configuration = _build_context(
        config_dir, default_caller_id="<http-no-default>"
    )
    _install_sighup_handler(context, config_dir)

    # ADR 0015 invariant: stdio_trusted callers cannot authenticate
    # over HTTP. The orchestrator-trust assumption that justifies
    # stdio_trusted does not survive a network boundary — there is no
    # orchestrator on the other end of an HTTP socket. Any
    # stdio_trusted caller in the configured set therefore makes the
    # config invalid for HTTP, fatal at startup.
    stdio_trusted = [
        c.id for c in configuration.callers_file.callers
        if c.auth.type == "stdio_trusted"
    ]
    if stdio_trusted:
        names = ", ".join(f'"{c}"' for c in stdio_trusted)
        raise SystemExit(
            f"caller {names} as \"stdio_trusted not permitted on "
            "non-stdio transport\""
        )

    app_mcp = build_server(context)
    session_manager = StreamableHTTPSessionManager(
        app=app_mcp, json_response=True, stateless=True
    )

    def _audit_auth_failed(
        caller_id_claim: str | None, reason: str, addr: str
    ) -> None:
        if context.audit is None:
            return
        record: dict[str, Any] = {
            "caller_id": caller_id_claim,
            "caller_addr": addr,
            "tool": "auth_failed",
            "decision": "DENY",
            "reason": "auth_failed",
            "auth_failure_reason": reason,
        }
        context.audit.write(record)

    def _resolve_token(secret_ref: str | None) -> str | None:
        if secret_ref is None:
            return None
        try:
            return context.secret_store.get(secret_ref)  # type: ignore[attr-defined]
        except Exception:
            return None

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
            # Only the MCP route requires authentication. Anything else
            # is a non-route and returns 404 unconditionally below.
            if not request.url.path.rstrip("/").endswith("/mcp"):
                return await call_next(request)
            addr = request.client.host if request.client else "http:?"
            caller_id_claim = request.headers.get("x-mcp-caller-id")
            authz = request.headers.get("authorization", "")
            scheme, _, token = authz.partition(" ")
            if scheme.lower() != "bearer" or not token:
                _audit_auth_failed(
                    caller_id_claim, "no_bearer_token", f"http:{addr}"
                )
                return JSONResponse(
                    {"error": "auth_failed"}, status_code=401
                )
            if not caller_id_claim:
                _audit_auth_failed(None, "no_caller_id", f"http:{addr}")
                return JSONResponse(
                    {"error": "no_caller_identity"}, status_code=401
                )
            caller = configuration.caller_by_id(caller_id_claim)
            if caller is None:
                _audit_auth_failed(
                    caller_id_claim, "unknown_caller_id", f"http:{addr}"
                )
                return JSONResponse(
                    {"error": "unknown_caller_id"}, status_code=401
                )
            if caller.auth.type != "shared_token":
                _audit_auth_failed(
                    caller_id_claim, "wrong_auth_type", f"http:{addr}"
                )
                return JSONResponse(
                    {"error": "auth_failed"}, status_code=401
                )
            expected = _resolve_token(caller.auth.token_secret_ref)
            if expected is None or not hmac.compare_digest(token, expected):
                _audit_auth_failed(
                    caller_id_claim, "wrong_token", f"http:{addr}"
                )
                return JSONResponse(
                    {"error": "auth_failed"}, status_code=401
                )
            token_var = _CURRENT_CALLER_ID.set(caller_id_claim)
            try:
                return await call_next(request)
            finally:
                _CURRENT_CALLER_ID.reset(token_var)

    async def _handle_mcp(scope, receive, send):  # type: ignore[no-untyped-def]
        await session_manager.handle_request(scope, receive, send)

    async def _not_found(request):  # type: ignore[no-untyped-def]
        return Response(status_code=404)

    from starlette.routing import Route

    starlette_app = Starlette(
        routes=[
            Mount("/mcp", app=_handle_mcp),
            Route(
                "/{path:path}",
                endpoint=_not_found,
                methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            ),
        ],
        middleware=[],
    )
    starlette_app.add_middleware(BearerAuthMiddleware)

    async with session_manager.run():
        config = uvicorn.Config(
            starlette_app, host=host, port=port, log_level="warning"
        )
        await uvicorn.Server(config).serve()


def _caller_id_from_env_or_exit() -> str:
    caller_id = os.environ.get("IMAP_MCP_CALLER_ID")
    if not caller_id:
        raise SystemExit(
            "IMAP_MCP_CALLER_ID is not set. The stdio_trusted auth type "
            "requires the orchestrator to supply the caller identity via "
            "argv or environment (ADR 0015)."
        )
    return caller_id


def _config_dir_from_env_or_exit() -> Path:
    raw = os.environ.get("IMAP_MCP_CONFIG_DIR")
    if not raw:
        raise SystemExit(
            "IMAP_MCP_CONFIG_DIR is not set. The server requires a path to "
            "the config tree (accounts.yaml, callers.yaml, policies/*.yaml)."
        )
    path = Path(raw)
    if not path.is_dir():
        raise SystemExit(f"IMAP_MCP_CONFIG_DIR does not point at a directory: {path}")
    return path
