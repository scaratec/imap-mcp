"""MCP tool dispatcher.

`build_server` constructs the MCP `Server` instance: the static tool
list (descriptions + JSON-Schema), the call-tool request handler that
gates unknown tools at the JSON-RPC level (ADR 0018), and the dispatch
table that routes each known tool to its handler module.

`_emit`, `_audit_tool_call`, `_sanitise_args` shape the wire response
and the audit record around each call.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Mapping
from typing import Any

from mcp.server import Server
from mcp.shared.exceptions import McpError
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ContentBlock,
    ErrorData,
    ServerResult,
    TextContent,
    Tool,
)

from .context import ServerContext, _package_version
from .handlers._common import (
    READ_TOOL_MIN_VIS,
    TOOL_SET_VERSION,
    WRITE_TOOL_CAP,
    canonicalize_folder_arguments,
)
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
from .handlers.drafts import (
    handle_create_draft,
    handle_create_reply_draft,
    handle_discard_draft,
    handle_get_draft,
    handle_list_drafts,
    handle_update_draft,
)
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
from .trace_payloads import PayloadSettings, payload_attributes, payload_settings
from .tracing import mark_span_error


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

    # mcp 1.x leaves Server.list_tools() unannotated, so mypy cannot see
    # through the decorator. Revisit with the mcp 2.x port.
    @app.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def _list_tools() -> list[Tool]:
        # Tool descriptions are computed dynamically per call so that
        # the attachment-sink state (ADR 0028 §3) is fresh: an agent
        # that calls list_tools sees the current configured / writable
        # / missing state of the sink before issuing its first
        # fetch_attachment.
        return _build_tool_list(context)

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
        # server-side: required arguments and JSON types of the
        # inputSchema, criteria field grammar (ADR 0024) and the draft
        # arguments (ADR 0031, 0035).
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
        payloads = payload_settings()
        with tracer.start_as_current_span(
            f"tool.{name}",
            attributes={
                "mcp.tool": name,
                "mcp.caller_id": context.caller_id,
                "mcp.account": arguments.get("account", ""),
                "mcp.folder": arguments.get("folder", ""),
                "imap_mcp.trace.payloads": payloads.mode,
                **_client_attributes(app),
            },
        ) as span:
            start = _time.monotonic()
            # ADR 0033: Gmail special-use folders are canonical from
            # here on — for policy, IMAP resolution, response, audit.
            arguments = await canonicalize_folder_arguments(context, arguments)
            if "folder" in arguments:
                span.set_attribute("mcp.folder", arguments["folder"])
            result = await _dispatch(context, name, arguments)
            elapsed_ms = int((_time.monotonic() - start) * 1000)
            span.set_attribute("mcp.decision", result.get("decision", ""))
            span.set_attribute("mcp.reason", result.get("reason", ""))
            span.set_attribute("mcp.latency_ms", elapsed_ms)
            content = _emit(result)
            _record_span_outcome(span, name, arguments, result, content, payloads)
            _audit_tool_call(context, name, arguments, result, latency_ms=elapsed_ms)
            return ServerResult(CallToolResult(content=content, isError=False))

    app.request_handlers[CallToolRequest] = _raw_call_tool_handler

    async def _dispatch(
        context: ServerContext, name: str, arguments: dict[str, Any]
    ) -> Mapping[str, Any]:
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
        if name == "list_drafts":
            return await handle_list_drafts(context, arguments)
        if name == "get_draft":
            return await handle_get_draft(context, arguments)
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
        if name == "update_draft":
            return await handle_update_draft(context, arguments)
        if name == "discard_draft":
            return await handle_discard_draft(context, arguments)
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


def _emit(payload: Mapping[str, Any]) -> list[ContentBlock]:
    """Per ADR 0028: tool responses are text-only. EmbeddedResource +
    BlobResourceContents are gone — `fetch_attachment` writes the
    bytes to the sink and returns only the filename in `saved_to`.

    Private fields starting with `_` (e.g. `_saved_to_absolute`,
    `_matched_sender`) are stripped here so they never reach the
    caller; the audit emitter consumes them just before this call.
    """
    import json

    public = {k: v for k, v in payload.items() if not k.startswith("_")}
    return [TextContent(type="text", text=json.dumps(public))]


# ADR 0038: the list member whose length is `mcp.result.returned`.
_LISTING_FIELDS: dict[str, str] = {
    "list_messages": "messages",
    "search": "uids",
    "list_drafts": "drafts",
    "list_folders": "folders",
    "list_accounts": "accounts",
    "list_labels": "labels",
    "list_attachments": "attachments",
}
_RESULT_COUNT_ATTRIBUTES = (
    ("matched_total", "mcp.result.matched"),
    ("matched_visible", "mcp.result.visible"),
    ("has_more", "mcp.result.has_more"),
)


def _client_attributes(app: Server) -> dict[str, str]:
    """ADR 0038: the MCP client as its Initialize `clientInfo` names it."""
    try:
        params = app.request_context.session.client_params
    except LookupError:
        return {}
    if params is None:
        return {}
    return {
        "mcp.client.name": params.clientInfo.name,
        "mcp.client.version": params.clientInfo.version,
    }


def _record_span_outcome(
    span: Any,
    tool: str,
    arguments: dict[str, Any],
    result: Mapping[str, Any],
    content: list[ContentBlock],
    payloads: PayloadSettings,
) -> None:
    """Size, result counts, outcome and payloads of one tool call
    (ADR 0037, ADR 0038)."""
    public = {k: v for k, v in result.items() if not k.startswith("_")}
    span.set_attribute(
        "mcp.response.size_bytes",
        sum(len(block.text.encode("utf-8")) for block in content if isinstance(block, TextContent)),
    )
    listed = public.get(_LISTING_FIELDS.get(tool, ""))
    if isinstance(listed, list):
        span.set_attribute("mcp.result.returned", len(listed))
        for key, attribute in _RESULT_COUNT_ATTRIBUTES:
            if key in public:
                span.set_attribute(attribute, public[key])
    # Observability standard §2: a policy denial is a rejected outcome,
    # not a span error; only the error envelope is a technical failure.
    if public.get("decision") == "DENY":
        span.set_attribute("domain.outcome", "rejected")
        span.set_attribute("domain.rejection.reason", str(public.get("reason", "")))
    elif public.get("decision") == "ALLOW":
        span.set_attribute("domain.outcome", "accepted")
    if public.get("result") == "ERROR":
        error = public.get("error")
        error_type = str(error.get("type") or "") if isinstance(error, dict) else ""
        # move/copy carry the type at the top level (ADR 0006).
        error_type = error_type or str(public.get("error_type") or "")
        span.set_attribute("error.type", error_type or "unknown")
        mark_span_error(span, error_type or "error envelope")
    span.set_attributes(
        payload_attributes(
            payloads,
            request=arguments,
            audited_request=_sanitise_args(arguments),
            response=public,
        )
    )


def _audit_tool_call(
    context: ServerContext,
    tool: str,
    arguments: dict[str, Any],
    result: Mapping[str, Any],
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
    # `_matched_sender` key, which _emit's `_`-prefix filter keeps out
    # of the JSON response.
    matched_sender = result.get("_matched_sender")
    if result.get("reason") == "sender_blacklisted" and matched_sender:
        import hashlib

        domain = str(matched_sender).rsplit("@", 1)[-1]
        record["from_domain_sha256"] = hashlib.sha256(domain.encode("utf-8")).hexdigest()
    # ADR 0028 §4: attachment writes record the full absolute path
    # in the audit so a forensic reviewer does not need to also
    # reconstruct the sink config at the time of the call. The
    # field is read here and stripped from the public response
    # by _emit's `_`-prefix filter.
    saved_to_absolute = result.get("_saved_to_absolute")
    if saved_to_absolute is not None:
        record["saved_to_absolute"] = saved_to_absolute
    context.audit.write(record)


_DURATION_RE = None  # initialised lazily

_TOOLS_WITH_CRITERIA = frozenset({"search", "list_messages", "bulk_mark_seen", "bulk_mark_tagged"})
_DURATION_KEYS = ("newer_than", "older_than")


def _validate_arguments_against_surface(name: str, arguments: dict[str, Any]) -> str | None:
    """Reject the argument shapes the in-handler code cannot
    distinguish from runtime errors: a required argument missing or an
    argument of the wrong JSON type (the tool's inputSchema), malformed
    duration strings and calendar bounds (ADR 0024, 0036), and
    malformed draft arguments.

    Returns the JSON-RPC error message string when invalid, None when
    the call may proceed.
    """
    import re as _re

    global _DURATION_RE
    if _DURATION_RE is None:
        _DURATION_RE = _re.compile(_DURATION_PATTERN)

    schema = _input_schemas().get(name)
    if schema is not None:
        violation = _object_violation(schema, arguments, "")
        if violation is not None:
            return f"{name}: {violation}"

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
            from .handlers.search import validate_calendar_bounds

            calendar_error = validate_calendar_bounds(criteria)
            if calendar_error is not None:
                return calendar_error
    if name == "update_draft":
        from .handlers.drafts import validate_changes

        base_revision = arguments.get("base_revision")
        if not isinstance(base_revision, int) or isinstance(base_revision, bool):
            return "update_draft requires an integer base_revision (ADR 0031)"
        if base_revision < 1:
            return f"Invalid base_revision {base_revision!r}: draft revisions start at 1 (ADR 0031)"
        changes_error = validate_changes(arguments.get("changes"))
        if changes_error is not None:
            return changes_error
    if name == "create_draft":
        from .handlers.drafts import validate_create_draft_arguments

        return validate_create_draft_arguments(arguments)
    if name == "create_reply_draft":
        for key in ("reply_text", "reply_html"):
            if key in arguments and not isinstance(arguments[key], str):
                return f"create_reply_draft `{key}` must be a string (ADR 0035)"
    return None


@functools.cache
def _input_schemas() -> dict[str, dict[str, Any]]:
    """The inputSchema of every published tool, by name. The schemas
    are static; only descriptions depend on the context."""
    return {tool.name: tool.inputSchema for tool in _build_tool_list()}


def _json_type(value: object) -> str:
    """The JSON type name of a decoded JSON value (JSON Schema §4.2.1)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _schema_types(schema: Mapping[str, Any]) -> list[str]:
    declared = schema.get("type")
    if isinstance(declared, str):
        return [declared]
    if isinstance(declared, list):
        return [t for t in declared if isinstance(t, str)]
    return []


_TYPE_PHRASES = {
    "string": "a string",
    "integer": "an integer",
    "number": "a number",
    "boolean": "a boolean",
    "object": "an object",
    "array": "an array",
    "null": "null",
}


def _type_phrase(schema: Mapping[str, Any]) -> str:
    phrases: list[str] = []
    for declared in _schema_types(schema):
        items = schema.get("items")
        item_types = _schema_types(items) if isinstance(items, Mapping) else []
        if declared == "array" and len(item_types) == 1:
            phrases.append(f"an array of {item_types[0]}s")
        else:
            phrases.append(_TYPE_PHRASES.get(declared, declared))
    return " or ".join(phrases)


def _value_violation(schema: Mapping[str, Any], value: object, path: str) -> str | None:
    """Check `value` against the type of `schema`, recursing into object
    members and array items. The value itself is never echoed."""
    declared = _schema_types(schema)
    actual = _json_type(value)
    if declared and not any(
        actual == expected or (expected == "number" and actual == "integer")
        for expected in declared
    ):
        return f"argument '{path}' must be {_type_phrase(schema)}, got {actual}"
    if isinstance(value, dict) and "object" in declared:
        return _object_violation(schema, value, f"{path}.")
    items = schema.get("items")
    if isinstance(value, list) and isinstance(items, Mapping):
        for index, item in enumerate(value):
            violation = _value_violation(items, item, f"{path}[{index}]")
            if violation is not None:
                return violation
    return None


def _object_violation(
    schema: Mapping[str, Any], value: Mapping[str, Any], prefix: str
) -> str | None:
    """Required members first, then the type of every declared member
    present. Undeclared members are not judged here (ADR 0018: extra
    arguments are ignored)."""
    required = schema.get("required") or []
    missing = [f"'{prefix}{key}'" for key in required if key not in value]
    if missing:
        noun = "argument" if len(missing) == 1 else "arguments"
        return f"missing required {noun} {', '.join(missing)}"
    properties = schema.get("properties") or {}
    for key, member_schema in properties.items():
        if key in value and isinstance(member_schema, Mapping):
            violation = _value_violation(member_schema, value[key], f"{prefix}{key}")
            if violation is not None:
                return violation
    return None


# ADR 0021 / ADR 0035: draft content arguments are never audited; only
# the names of those present are, as `update_draft` records its changes.
_DRAFT_CONTENT_ARGS = frozenset(
    {
        "to",
        "cc",
        "bcc",
        "subject",
        "body_text",
        "body_html",
        "in_reply_to",
        "reply_text",
        "reply_html",
    }
)


def _sanitise_args(arguments: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    content_fields = sorted(key for key in arguments if key in _DRAFT_CONTENT_ARGS)
    if content_fields:
        safe["changed_fields"] = content_fields
    for key, value in arguments.items():
        if key in ("rfc822", "tags"):
            safe[key] = "<redacted>" if key == "rfc822" else value
            continue
        if key in (
            "account",
            "folder",
            "uid",
            "seen",
            "mode",
            "part_id",
            "scope",
            "draft_id",
            "base_revision",
        ):
            safe[key] = value
            continue
        if key == "changes" and isinstance(value, dict):
            # ADR 0021: draft content is never audited. The member names
            # record *what* was touched without recording the text.
            safe["changed_fields"] = sorted(value.keys())
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
# ADR 0036: `since` inclusive, `before` exclusive, IMAP SEARCH semantics.
_CALENDAR_DATE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "format": "date",
    "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
}
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
        "since": _CALENDAR_DATE_SCHEMA,
        "before": _CALENDAR_DATE_SCHEMA,
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
# ADR 0031: the three-valued change surface of update_draft. A member
# absent leaves the field alone, a value sets it, `null` (or `[]` for an
# address list) removes it. `minProperties: 1` is what makes an empty
# object a schema reject rather than a runtime error type.
_ADDRESS_LIST_SCHEMA: dict[str, Any] = {
    "type": ["array", "null"],
    "items": {"type": "string"},
}
_NULLABLE_STRING_SCHEMA: dict[str, Any] = {"type": ["string", "null"]}
# ADR 0035: create_draft takes the same field set without the removal
# form; a new draft has nothing to remove.
_STRING_LIST_SCHEMA: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
_DRAFT_CHANGES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": _NULLABLE_STRING_SCHEMA,
        "to": _ADDRESS_LIST_SCHEMA,
        "cc": _ADDRESS_LIST_SCHEMA,
        "bcc": _ADDRESS_LIST_SCHEMA,
        "body_text": _NULLABLE_STRING_SCHEMA,
        "body_html": _NULLABLE_STRING_SCHEMA,
        "in_reply_to": _NULLABLE_STRING_SCHEMA,
    },
    "additionalProperties": False,
    "minProperties": 1,
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


def _build_tool_list(context: ServerContext | None = None) -> list[Tool]:
    """Return the V2 tool surface (ADR 0026, 0030, 0031, 0034; 30 tools).

    `context` is consulted only to render the dynamic
    `fetch_attachment` description per ADR 0028 §3. When called
    without a context (e.g. in unit tests of the static shape),
    the sink-state suffix is left empty.
    """
    from .handlers._common import describe_attachment_sink

    sink_dir = getattr(context, "attachment_sink_directory", None) if context is not None else None
    sink_description = describe_attachment_sink(sink_dir)
    fetch_attachment_desc = (
        "Fetch one attachment part by index (ADR 0026, 0028). " + sink_description
    )

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
            (
                "Search a folder for matching UIDs (ADR 0024, 0026, 0036). Without "
                "a time bound only the last 7 days are searched (applied_scope "
                "recent_7d) and outside_scope_total counts the matches before that "
                'window; pass scope "all" to search all time. criteria.since '
                "(inclusive) and criteria.before (exclusive) take calendar dates "
                "YYYY-MM-DD and cannot be combined with newer_than/older_than."
            ),
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
            (
                "List messages with envelope data (ADR 0024, 0026, 0036). Without "
                "a time bound only the last 7 days are listed (applied_scope "
                "recent_7d) and outside_scope_total counts the matches before that "
                'window; pass scope "all" to list all time. criteria.since '
                "(inclusive) and criteria.before (exclusive) take calendar dates "
                "YYYY-MM-DD and cannot be combined with newer_than/older_than."
            ),
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
            fetch_attachment_desc,
            {
                **account_field,
                **folder_field,
                **uid_field,
                "part_id": {"type": "integer", "minimum": 0},
            },
            ["account", "folder", "uid", "part_id"],
        ),
        _tool(
            "list_drafts",
            "List the drafts in a folder with their handles (ADR 0030).",
            {**account_field, **folder_field},
            ["account", "folder"],
        ),
        _tool(
            "get_draft",
            "Return the composed fields of one draft by its handle (ADR 0030).",
            {**account_field, **folder_field, "draft_id": {"type": "string"}},
            ["account", "folder", "draft_id"],
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
            (
                "Append a new draft (ADR 0026, 0027, 0035). Choose the body format "
                "deliberately: plain text or HTML. If the user's preference is "
                "unknown, ask. Pass either structured fields (to, cc, bcc, subject, "
                "body_text and/or body_html, in_reply_to) or a complete RFC 5322 "
                "message in rfc822, never both. body_text alone is stored as "
                "text/plain; body_html as multipart/alternative, its plain-text "
                "part derived from the HTML unless body_text is given too. The "
                "response field format names the format that was stored."
            ),
            {
                **account_field,
                **folder_field,
                "rfc822": {"type": "string"},
                "to": _STRING_LIST_SCHEMA,
                "cc": _STRING_LIST_SCHEMA,
                "bcc": _STRING_LIST_SCHEMA,
                "subject": {"type": "string"},
                "body_text": {"type": "string"},
                "body_html": {"type": "string"},
                "in_reply_to": {"type": "string"},
            },
            ["account", "folder"],
        ),
        _tool(
            "create_reply_draft",
            (
                "Build and APPEND a top-posted reply draft (ADR 0026, 0027, 0035). "
                "Give reply_text, reply_html, or both. With reply_html the draft is "
                "multipart/alternative: the HTML part quotes the original in a "
                "<blockquote>, the text part with '>'; a missing reply_text is "
                "derived from reply_html."
            ),
            {
                **account_field,
                "source_folder": {"type": "string"},
                **uid_field,
                "drafts_folder": {"type": "string"},
                "reply_text": {"type": "string"},
                "reply_html": {"type": "string"},
            },
            ["account", "source_folder", "uid", "drafts_folder"],
        ),
        _tool(
            "update_draft",
            "Revise a draft field-wise under its handle (ADR 0031).",
            {
                **account_field,
                **folder_field,
                "draft_id": {"type": "string"},
                "base_revision": {"type": "integer", "minimum": 1},
                "changes": _DRAFT_CHANGES_SCHEMA,
            },
            ["account", "folder", "draft_id", "base_revision", "changes"],
        ),
        _tool(
            "discard_draft",
            (
                "Discard a draft by its handle: move every message carrying it "
                "(all revisions) to the account's trash folder, the one with the "
                "\\Trash special-use flag. Never deletes permanently. Needs "
                "move_out on the drafts folder and accept_incoming on the trash "
                "folder (ADR 0034)."
            ),
            {**account_field, **folder_field, "draft_id": {"type": "string"}},
            ["account", "folder", "draft_id"],
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
