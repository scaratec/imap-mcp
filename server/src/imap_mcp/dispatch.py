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
from .handlers._common import READ_TOOL_MIN_VIS, TOOL_SET_VERSION, WRITE_TOOL_CAP
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
    handle_list_attachments,
)
from .handlers.folder import handle_folder_stats
from .handlers.introspection import (
    handle_describe_policy,
    handle_get_caller_identity,
    handle_get_transaction_status,
    handle_tool_surface_info,
)
from .handlers.mark import (
    handle_bulk_mark_seen,
    handle_bulk_mark_tagged,
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
    # The MCP SDK passes `instructions` verbatim into the Initialize
    # response.  ADR 0027 requires the tool-set version to be reachable
    # before any tools/call; embedding it as a JSON envelope inside
    # `instructions` keeps us SDK-clean and lets clients parse the
    # `serverInfo.metadata` they expect (the test client carries a
    # tiny parser that lifts the JSON back out — see `mcp_client.py`).
    import json as _json

    surface_metadata = {
        "tool_set_version": TOOL_SET_VERSION,
        "package_version": _package_version(),
    }
    instructions = "imap-mcp surface metadata: " + _json.dumps(surface_metadata)
    app: Server = Server("imap-mcp", version=_package_version(), instructions=instructions)

    @app.list_tools()
    async def _list_tools() -> list[Tool]:
        return _build_tool_list()

    known_tools = (
        set(READ_TOOL_MIN_VIS.keys())
        | set(WRITE_TOOL_CAP.keys())
        | {
            "describe_policy",
            "get_caller_identity",
            "get_transaction_status",
            "tool_surface_info",
        }
    )
    if context.test_hooks.test_mode:
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
        # Schema-layer validation that the MCP SDK does not enforce
        # server-side: criteria field grammar (ADR 0024) and the
        # required `part_id` on fetch_attachment (ADR 0026 §1).
        schema_error = _validate_arguments_against_surface(name, arguments)
        if schema_error is not None:
            raise McpError(ErrorData(code=-32602, message=schema_error))
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
                if k
                not in (
                    "messages",
                    "body",
                    "headers",
                    "attachment",
                    "rfc822",
                    "_blob",
                    "_blob_mime_type",
                    "_blob_uri",
                )
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
        if name == "list_attachments":
            return await handle_list_attachments(context, arguments)
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
        if name == "bulk_mark_tagged":
            return await handle_bulk_mark_tagged(context, arguments)
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
        if name == "tool_surface_info":
            return handle_tool_surface_info(context)
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


_DURATION_RE = None  # initialised lazily

_TOOLS_WITH_CRITERIA = frozenset({"search", "list_messages", "bulk_mark_seen", "bulk_mark_tagged"})
_DURATION_KEYS = ("newer_than", "older_than")


def _validate_arguments_against_surface(name: str, arguments: dict[str, Any]) -> str | None:
    """Reject the two argument shapes the in-handler code cannot
    distinguish from runtime errors: malformed duration strings and
    a missing `part_id` on fetch_attachment.

    Returns the JSON-RPC error message string when invalid, None when
    the call may proceed.
    """
    import re as _re

    global _DURATION_RE
    if _DURATION_RE is None:
        _DURATION_RE = _re.compile(_DURATION_PATTERN)

    if name in _TOOLS_WITH_CRITERIA:
        criteria = arguments.get("criteria") or {}
        if isinstance(criteria, dict):
            for key in _DURATION_KEYS:
                value = criteria.get(key)
                if value is None:
                    continue
                if not isinstance(value, str) or not _DURATION_RE.match(value):
                    return (
                        f"Invalid criteria.{key} {value!r}: must match "
                        f"the duration pattern {_DURATION_PATTERN}"
                    )
    if name == "fetch_attachment" and "part_id" not in arguments:
        return "fetch_attachment requires part_id (ADR 0026 §1)"
    return None


def _sanitise_args(arguments: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in ("rfc822", "tags"):
            safe[key] = "<redacted>" if key == "rfc822" else value
            continue
        if key in ("account", "folder", "uid", "seen", "mode", "part_id", "scope"):
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


# --------------------------------------------------------------------- schemas

# Single source for the V2 search-criteria grammar (ADR 0024, ADR 0026).
# Reused by search, list_messages, bulk_mark_seen, bulk_mark_tagged.
_DURATION_PATTERN = r"^[0-9]+[smhdwy]$"
_CRITERIA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "from": {"type": "string"},
        "from_domain": {"type": "string"},
        "to": {"type": "string"},
        "to_contains": {"type": "string"},
        "subject_contains": {"type": "string"},
        "has_attachment": {"type": "boolean"},
        "flagged": {"type": "boolean"},
        "newer_than": {"type": "string", "pattern": _DURATION_PATTERN},
        "older_than": {"type": "string", "pattern": _DURATION_PATTERN},
        "size_gt": {"type": "integer", "minimum": 1},
        "size_lt": {"type": "integer", "minimum": 1},
    },
    "additionalProperties": False,
}
_SCOPE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": ["recent", "all"],
    "default": "recent",
}
_SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "account": {"type": "string"},
        "folder": {"type": "string"},
        "uid": {"type": "integer", "minimum": 1},
    },
    "required": ["account", "folder", "uid"],
    "additionalProperties": False,
}
_TARGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "account": {"type": "string"},
        "folder": {"type": "string"},
    },
    "required": ["account", "folder"],
    "additionalProperties": False,
}


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
    *,
    category: str | None = None,
) -> Tool:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    if category is not None:
        schema["x-mcp-imap"] = {"category": category}
    return Tool(name=name, description=description, inputSchema=schema)


def _build_tool_list() -> list[Tool]:
    """Return the V2 tool surface (ADR 0026, 26 tools)."""
    account_field = {"account": {"type": "string"}}
    folder_field = {"folder": {"type": "string"}}
    uid_field = {"uid": {"type": "integer", "minimum": 1}}
    return [
        _tool(
            "list_accounts",
            "List accounts visible to the caller (ADR 0026).",
            {},
            category="read",
        ),
        _tool(
            "list_folders",
            "List folders the caller may see on an account (ADR 0025, 0026).",
            {**account_field},
            ["account"],
        ),
        _tool(
            "list_labels",
            "List Gmail labels for a Google account (ADR 0019, 0026).",
            {**account_field},
            ["account"],
        ),
        _tool(
            "folder_stats",
            "Return aggregate counts for a folder (ADR 0017, 0025, 0026).",
            {**account_field, **folder_field},
            ["account", "folder"],
        ),
        _tool(
            "search",
            "Search a folder for matching UIDs (ADR 0024, 0026).",
            {
                **account_field,
                **folder_field,
                "criteria": _CRITERIA_SCHEMA,
                "scope": _SCOPE_SCHEMA,
                "limit": {"type": "integer", "minimum": 1, "default": 50},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            ["account", "folder"],
        ),
        _tool(
            "list_messages",
            "List messages with envelope data (ADR 0024, 0026).",
            {
                **account_field,
                **folder_field,
                "criteria": _CRITERIA_SCHEMA,
                "scope": _SCOPE_SCHEMA,
                "limit": {"type": "integer", "minimum": 1, "default": 20},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            ["account", "folder"],
        ),
        _tool(
            "fetch_envelope",
            "Fetch envelope fields for one UID (ADR 0026).",
            {**account_field, **folder_field, **uid_field},
            ["account", "folder", "uid"],
        ),
        _tool(
            "fetch_headers",
            "Fetch full RFC 5322 headers (ADR 0026).",
            {**account_field, **folder_field, **uid_field},
            ["account", "folder", "uid"],
        ),
        _tool(
            "fetch_body",
            "Fetch the plain/HTML body parts (ADR 0026).",
            {**account_field, **folder_field, **uid_field},
            ["account", "folder", "uid"],
        ),
        _tool(
            "list_attachments",
            "List attachment metadata for one UID (ADR 0026).",
            {**account_field, **folder_field, **uid_field},
            ["account", "folder", "uid"],
        ),
        _tool(
            "fetch_attachment",
            "Fetch one attachment part by index (ADR 0026).",
            {
                **account_field,
                **folder_field,
                **uid_field,
                "part_id": {"type": "integer", "minimum": 0},
            },
            ["account", "folder", "uid", "part_id"],
        ),
        _tool(
            "mark_seen",
            "Toggle the \\Seen flag on a message (ADR 0005).",
            {**account_field, **folder_field, **uid_field, "seen": {"type": "boolean"}},
            ["account", "folder", "uid", "seen"],
        ),
        _tool(
            "bulk_mark_seen",
            "Mark every message matching criteria as seen/unseen (ADR 0026).",
            {
                **account_field,
                **folder_field,
                "criteria": _CRITERIA_SCHEMA,
                "scope": _SCOPE_SCHEMA,
                "seen": {"type": "boolean"},
            },
            ["account", "folder", "criteria", "seen"],
        ),
        _tool(
            "mark_tagged",
            "Add, remove, or replace keywords on a message (ADR 0005).",
            {
                **account_field,
                **folder_field,
                **uid_field,
                "tags": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string", "enum": ["add", "remove", "replace"]},
            },
            ["account", "folder", "uid", "tags", "mode"],
        ),
        _tool(
            "bulk_mark_tagged",
            "Tag/untag every message matching criteria (ADR 0026).",
            {
                **account_field,
                **folder_field,
                "criteria": _CRITERIA_SCHEMA,
                "scope": _SCOPE_SCHEMA,
                "tags": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string", "enum": ["add", "remove", "replace"]},
            },
            ["account", "folder", "criteria", "tags", "mode"],
        ),
        _tool(
            "move",
            "Move a message between folders (ADR 0006, 0026).",
            {"source": _SOURCE_SCHEMA, "target": _TARGET_SCHEMA},
            ["source", "target"],
        ),
        _tool(
            "copy",
            "Copy a message between folders (ADR 0006, 0026).",
            {"source": _SOURCE_SCHEMA, "target": _TARGET_SCHEMA},
            ["source", "target"],
        ),
        _tool(
            "create_draft",
            "Append an RFC 5322 message as a draft (ADR 0026, 0027).",
            {**account_field, **folder_field, "rfc822": {"type": "string"}},
            ["account", "folder", "rfc822"],
        ),
        _tool(
            "create_reply_draft",
            "Build and APPEND a top-posted reply draft (ADR 0026, 0027).",
            {
                **account_field,
                "source_folder": {"type": "string"},
                **uid_field,
                "drafts_folder": {"type": "string"},
                "reply_text": {"type": "string"},
            },
            ["account", "source_folder", "uid", "drafts_folder", "reply_text"],
        ),
        _tool(
            "add_attachment",
            "Add an attachment via FETCH/APPEND/DELETE (ADR 0026).",
            {
                **account_field,
                **folder_field,
                **uid_field,
                "filename": {"type": "string"},
                "mime_type": {"type": "string"},
                "content": {"type": "string"},
            },
            ["account", "folder", "uid", "filename", "mime_type", "content"],
        ),
        _tool(
            "replace_attachment",
            "Replace an attachment via FETCH/APPEND/DELETE (ADR 0026).",
            {
                **account_field,
                **folder_field,
                **uid_field,
                "filename": {"type": "string"},
                "new_content": {"type": "string"},
                "new_mime_type": {"type": "string"},
                "new_filename": {"type": "string"},
            },
            ["account", "folder", "uid", "filename", "new_content"],
        ),
        _tool(
            "delete_attachment",
            "Remove an attachment via FETCH/APPEND/DELETE (ADR 0026).",
            {
                **account_field,
                **folder_field,
                **uid_field,
                "filename": {"type": "string"},
            },
            ["account", "folder", "uid", "filename"],
        ),
        _tool(
            "describe_policy",
            "Return the caller's own policy profile (ADR 0017).",
            {},
        ),
        _tool(
            "get_transaction_status",
            "Return the WAL state of a saga transaction (ADR 0006).",
            {"tx_id": {"type": "string"}},
            ["tx_id"],
        ),
        _tool(
            "get_caller_identity",
            "Return the resolved caller_id (ADR 0015).",
            {},
        ),
        _tool(
            "tool_surface_info",
            "Return tool-set version and breaking-changes log (ADR 0027).",
            {},
        ),
    ]
