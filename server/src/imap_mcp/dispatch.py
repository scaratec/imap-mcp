"""MCP tool dispatcher.

`build_server` constructs the MCP `Server` instance: the static tool
list (descriptions + JSON-Schema), the call-tool request handler that
gates unknown tools at the JSON-RPC level (ADR 0018), and the dispatch
table that routes each known tool to its handler module.

`_emit`, `_audit_tool_call`, `_sanitise_args` shape the wire response
and the audit record around each call.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server import Server
from mcp.shared.exceptions import McpError
from mcp.types import (
    BlobResourceContents,
    CallToolRequest,
    CallToolResult,
    EmbeddedResource,
    ErrorData,
    ServerResult,
    TextContent,
    Tool,
)

from .context import ServerContext, _package_version
from .handlers._common import READ_TOOL_MIN_VIS, WRITE_TOOL_CAP
from .handlers.accounts import (
    handle_list_accounts,
    handle_list_folders,
    handle_list_labels,
)
from .handlers.attachments import (
    handle_add_attachment,
    handle_delete_attachment,
    handle_replace_attachment,
)
from .handlers.drafts import handle_create_draft, handle_create_reply_draft
from .handlers.fetch import (
    handle_fetch_attachment,
    handle_fetch_body,
    handle_fetch_envelope,
    handle_fetch_headers,
)
from .handlers.folder import handle_folder_stats
from .handlers.introspection import (
    handle_describe_policy,
    handle_get_caller_identity,
    handle_get_transaction_status,
)
from .handlers.mark import (
    handle_bulk_mark_seen,
    handle_mark_seen,
    handle_mark_tagged,
)
from .handlers.move import handle_copy, handle_move
from .handlers.search import handle_list_messages, handle_search
from .handlers.test_only import (
    handle_test_run_audit_rotation,
    handle_test_run_recovery,
)


def build_server(context: ServerContext) -> Server:
    app: Server = Server("imap-mcp", version=_package_version())

    @app.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_accounts",
                description=(
                    "List available email accounts. Call this FIRST to "
                    "discover which accounts you can access. Returns "
                    "account ids and their state (active/needs_rebootstrap)."
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
                    "List visible folders in an email account. "
                    "Returns folder names and hidden_folders_count."
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
                    "Fetch from/to/subject/date for a single message by "
                    "UID. Use list_messages instead when you need multiple "
                    "messages — it is much faster."
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
                    "Search for message UIDs in a folder. Returns UIDs "
                    "only (no envelope data). Use list_messages instead "
                    "if you need from/subject/date. This tool is for "
                    "counting or for feeding UIDs into fetch_envelope."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "criteria": {"type": "object"},
                        "limit": {"type": "integer", "minimum": 1, "default": 50},
                        "offset": {"type": "integer", "minimum": 0, "default": 0},
                    },
                    "required": ["account", "folder"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="list_messages",
                description=(
                    "THE PRIMARY TOOL for reading emails. Returns from, "
                    "subject, date for each message in one call. "
                    "Use this for: 'show me my emails', 'what arrived "
                    "today', 'recent messages'. Supports criteria: "
                    '{"newer_than": "1d"} for today, '
                    '{"from_domain": "example.com"} for sender filter, '
                    '{"subject_contains": "invoice"} for subject search. '
                    "Always call list_accounts first to get the account id, "
                    "then call this with account, folder='INBOX'."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "criteria": {"type": "object"},
                        "limit": {"type": "integer", "minimum": 1, "default": 20},
                        "offset": {"type": "integer", "minimum": 0, "default": 0},
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
                    "Fetch a single MIME attachment. Requires FULL visibility (ADR 0002)."
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
                    "Only works if your visibility for this message is "
                    "BODY or FULL. Will return DENY if the sender is "
                    "not in your whitelist or your visibility is only "
                    "ENVELOPE. Use list_messages first to see which "
                    "messages you can access."
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
                name="bulk_mark_seen",
                description=(
                    "Mark all messages matching criteria as read (or "
                    "unread) in one call. Use for 'mark all alerts as "
                    "read'. Searches by criteria, then sets \\Seen on "
                    "all matches in a single IMAP session. Returns "
                    "marked_count."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "criteria": {"type": "object"},
                        "seen": {"type": "boolean"},
                    },
                    "required": ["account", "folder", "criteria", "seen"],
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
                description=(
                    "Append an RFC 5322 message to a folder as a draft. "
                    "Call list_folders first to get the correct folder "
                    "path. Gmail uses '[Gmail]/Drafts', not 'Drafts'."
                ),
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
                name="create_reply_draft",
                description=(
                    "Build a top-posted reply to <account/source_folder/uid>"
                    " and APPEND it as a draft to drafts_folder. The agent "
                    "supplies only reply_text; the server derives Re:-subject"
                    ", In-Reply-To and References headers, reply-all "
                    "recipients (with the account identity removed from Cc),"
                    " and the quoted original body."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "source_folder": {"type": "string"},
                        "uid": {"type": "integer"},
                        "drafts_folder": {"type": "string"},
                        "reply_text": {"type": "string"},
                    },
                    "required": [
                        "account",
                        "source_folder",
                        "uid",
                        "drafts_folder",
                        "reply_text",
                    ],
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
                name="add_attachment",
                description=(
                    "Add an attachment to an existing message. The message "
                    "is rewritten via FETCH-APPEND-DELETE (WAL-backed). "
                    "Requires modify_message capability and FULL visibility."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "uid": {"type": "integer"},
                        "filename": {"type": "string"},
                        "mime_type": {"type": "string"},
                        "content": {"type": "string", "description": "Base64-encoded attachment content"},
                    },
                    "required": ["account", "folder", "uid", "filename", "mime_type", "content"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="replace_attachment",
                description=(
                    "Replace an existing attachment identified by filename. "
                    "The message is rewritten via FETCH-APPEND-DELETE (WAL-backed). "
                    "Requires modify_message capability and FULL visibility."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "uid": {"type": "integer"},
                        "filename": {"type": "string", "description": "Name of the attachment to replace"},
                        "new_content": {"type": "string", "description": "Base64-encoded new content"},
                        "new_mime_type": {"type": "string"},
                        "new_filename": {"type": "string"},
                    },
                    "required": ["account", "folder", "uid", "filename", "new_content"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="delete_attachment",
                description=(
                    "Remove an attachment identified by filename from a message. "
                    "The message is rewritten via FETCH-APPEND-DELETE (WAL-backed). "
                    "Requires modify_message capability and FULL visibility."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                        "folder": {"type": "string"},
                        "uid": {"type": "integer"},
                        "filename": {"type": "string", "description": "Name of the attachment to delete"},
                    },
                    "required": ["account", "folder", "uid", "filename"],
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
            Tool(
                name="list_labels",
                description=(
                    "List Gmail labels for a Google account. Returns "
                    "label names, flags, and hierarchy separators. "
                    "Only applicable for google/google-mock providers; "
                    "returns DENY for non-Google accounts."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string"},
                    },
                    "required": ["account"],
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
        known_tools = known_tools | {
            "_test_run_recovery",
            "_test_run_audit_rotation",
        }

    async def _raw_call_tool_handler(req: CallToolRequest) -> ServerResult:
        """Intercept tools/call at the request-handler level.

        Unknown tool names surface as JSON-RPC method-not-found
        (-32601), not as a `CallToolResult(isError=True)` payload.
        This matches the non-goal contract of ADR 0018: these tools
        do not exist at the protocol level, they are not merely
        denied by policy.
        """
        import time as _time

        from .tracing import tracer

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
            raise McpError(ErrorData(code=-32601, message=f"Unknown tool: {name!r}"))
        with tracer.start_as_current_span(
            f"tool.{name}",
            attributes={
                "mcp.tool": name,
                "mcp.caller_id": context.caller_id,
                "mcp.account": arguments.get("account", ""),
                "mcp.folder": arguments.get("folder", ""),
            },
        ) as span:
            start = _time.monotonic()
            result = await _dispatch(context, name, arguments)
            elapsed_ms = int((_time.monotonic() - start) * 1000)
            span.set_attribute("mcp.decision", result.get("decision", ""))
            span.set_attribute("mcp.reason", result.get("reason", ""))
            span.set_attribute("mcp.latency_ms", elapsed_ms)
            import json as _json

            _safe = {
                k: v
                for k, v in result.items()
                if k not in ("messages", "body", "headers", "attachment", "rfc822", "_blob", "_blob_mime_type", "_blob_uri")
            }
            span.set_attribute("mcp.response", _json.dumps(_safe, default=str))
            span.set_attribute("mcp.request", _json.dumps(arguments, default=str))
            _audit_tool_call(context, name, arguments, result, latency_ms=elapsed_ms)
            return ServerResult(CallToolResult(content=_emit(result), isError=False))

    app.request_handlers[CallToolRequest] = _raw_call_tool_handler

    async def _dispatch(
        context: ServerContext, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if name == "list_accounts":
            return handle_list_accounts(context, arguments)
        if name == "list_folders":
            return await handle_list_folders(context, arguments)
        if name == "list_labels":
            return await handle_list_labels(context, arguments)
        if name == "fetch_envelope":
            return await handle_fetch_envelope(context, arguments)
        if name == "search":
            return await handle_search(context, arguments)
        if name == "list_messages":
            return await handle_list_messages(context, arguments)
        if name == "fetch_body":
            return await handle_fetch_body(context, arguments)
        if name == "fetch_headers":
            return await handle_fetch_headers(context, arguments)
        if name == "fetch_attachment":
            return await handle_fetch_attachment(context, arguments)
        if name == "folder_stats":
            return await handle_folder_stats(context, arguments)
        if name == "mark_seen":
            return await handle_mark_seen(context, arguments)
        if name == "bulk_mark_seen":
            return await handle_bulk_mark_seen(context, arguments)
        if name == "mark_tagged":
            return await handle_mark_tagged(context, arguments)
        if name == "move":
            return await handle_move(context, arguments)
        if name == "copy":
            return await handle_copy(context, arguments)
        if name == "create_draft":
            return await handle_create_draft(context, arguments)
        if name == "create_reply_draft":
            return await handle_create_reply_draft(context, arguments)
        if name == "add_attachment":
            return await handle_add_attachment(context, arguments)
        if name == "replace_attachment":
            return await handle_replace_attachment(context, arguments)
        if name == "delete_attachment":
            return await handle_delete_attachment(context, arguments)
        if name == "describe_policy":
            return await handle_describe_policy(context, arguments)
        if name == "get_caller_identity":
            return handle_get_caller_identity(context)
        if name == "get_transaction_status":
            return await handle_get_transaction_status(context, arguments)
        if name == "_test_run_recovery":
            return await handle_test_run_recovery(context, arguments)
        if name == "_test_run_audit_rotation":
            return await handle_test_run_audit_rotation(context, arguments)
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


def _emit(payload: dict[str, Any]) -> list[TextContent | EmbeddedResource]:
    import json

    blob = payload.pop("_blob", None)
    blob_mime = payload.pop("_blob_mime_type", None)
    blob_uri = payload.pop("_blob_uri", None)
    result: list[TextContent | EmbeddedResource] = [
        TextContent(type="text", text=json.dumps(payload))
    ]
    if blob is not None:
        result.append(
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    uri=blob_uri or "attachment://unknown",
                    mimeType=blob_mime,
                    blob=blob,
                ),
            )
        )
    return result


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
        record["from_domain_sha256"] = hashlib.sha256(domain.encode("utf-8")).hexdigest()
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
            safe[key] = {k: v for k, v in value.items() if k in ("account", "folder", "uid")}
            continue
        if key == "criteria" and isinstance(value, dict):
            import hashlib
            import json as _json

            canonical = _json.dumps(value, sort_keys=True).encode("utf-8")
            safe["search_query_digest"] = hashlib.sha256(canonical).hexdigest()
            continue
    return safe
