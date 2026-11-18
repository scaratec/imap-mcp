"""MCP tool dispatcher for Tool Surface 3.0.0.

Adheres to:
- ADR 0040-0064: 27 tools (13 read, 10 write, 4 meta)
- Strict JSON Schema validation with additionalProperties: false (-32602)
- Normalized ALLOW + OK, ALLOW + ERROR, and DENY envelopes
- Pre-effect intent and post-effect outcome audit trail with keyed HMAC digests
- OpenTelemetry non-interfering spans (post-policy visible counts only)
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import time
import uuid
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

from .audit import compute_keyed_digest
from .auth.oauth_manager import NeedsRebootstrapError
from .config import parse_duration_seconds
from .context import _CURRENT_REQUEST_ID, ServerContext, _package_version, set_current_request_id
from .handlers._common import (
    META_TOOLS,
    READ_TOOL_MIN_VIS,
    TOOL_SET_VERSION,
    WRITE_TOOL_CAP,
    canonicalize_folder_arguments,
    error_envelope,
)
from .handlers.accounts import (
    handle_list_accounts,
    handle_list_folders,
    handle_list_labels,
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
from .imap_core import TransientIMAPFailure
from .pool import PoolTimeout
from .secrets import SecretDecryptionFailed
from .trace_payloads import PayloadSettings, payload_attributes, payload_settings
from .tracing import mark_span_error

_log = logging.getLogger(__name__)

MUTATING_TOOLS = frozenset(
    {
        "mark_seen",
        "bulk_mark_seen",
        "mark_tagged",
        "bulk_mark_tagged",
        "move",
        "copy",
        "create_draft",
        "create_reply_draft",
        "update_draft",
        "discard_draft",
        "fetch_attachment",
    }
)


def build_server(context: ServerContext) -> Server:
    surface_metadata = {
        "tool_set_version": TOOL_SET_VERSION,
        "package_version": _package_version(),
    }
    instructions = "imap-mcp surface metadata: " + json.dumps(surface_metadata)
    app: Server = Server("imap-mcp", version=_package_version(), instructions=instructions)

    @app.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def _list_tools() -> list[Tool]:
        return _build_tool_list(context)

    known_tools = set(READ_TOOL_MIN_VIS.keys()) | set(WRITE_TOOL_CAP.keys()) | set(META_TOOLS)
    if context.test_hooks.test_mode:
        known_tools = known_tools | {
            "_test_run_recovery",
            "_test_run_audit_rotation",
        }

    async def _raw_call_tool_handler(req: CallToolRequest) -> ServerResult:
        name = req.params.name
        arguments = req.params.arguments or {}

        # 1. Input schema & argument validation (-32602)
        schema_error = _validate_arguments_against_surface(name, arguments)
        if schema_error is not None:
            if context.audit is not None:
                context.audit.write(
                    {
                        "phase": "single",
                        "caller_id": context.caller_id,
                        "caller_addr": f"stdio:pid={os.getpid()}",
                        "tool": name,
                        "decision": "DENY",
                        "reason": "invalid_arguments",
                        "result": "ERROR",
                        "latency_ms": 0,
                    }
                )
            raise McpError(ErrorData(code=-32602, message=schema_error))

        # 2. Unknown tool (-32601)
        if name not in known_tools:
            if context.audit is not None:
                context.audit.write(
                    {
                        "phase": "single",
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
        from .tracing import tracer

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
            start = time.monotonic()
            req_id = str(uuid.uuid4())
            token_req = set_current_request_id(req_id)

            try:
                # Canonicalize special-use folders
                arguments = await canonicalize_folder_arguments(context, arguments)
                if "folder" in arguments:
                    span.set_attribute("mcp.folder", arguments["folder"])

                # Test hook: IMAP_MCP_TEST_INJECT_INTERNAL_ERROR (ADR 0058)
                if context.test_hooks.test_mode and context.test_hooks.test_inject_internal_error:
                    inj = context.test_hooks.test_inject_internal_error
                    inj_tool, _, inj_detail = inj.partition(":")
                    if name == inj_tool:
                        raise RuntimeError(inj_detail)

                result = await _dispatch(context, name, arguments)
            except SecretDecryptionFailed:
                result = error_envelope(
                    error_type="secret_unavailable",
                    detail="Account credential unavailable",
                    extra={"account": arguments.get("account", "")},
                )
            except NeedsRebootstrapError:
                result = {
                    "decision": "DENY",
                    "reason": "needs_rebootstrap",
                    "account": arguments.get("account", ""),
                }
            except TransientIMAPFailure:
                result = error_envelope(
                    error_type="transient_imap_failure",
                    detail="IMAP server unreachable or authentication failed",
                    extra={"account": arguments.get("account", "")},
                )
            except PoolTimeout as exc:
                result = error_envelope(
                    error_type="pool_exhausted",
                    detail=str(exc),
                    extra={"account": arguments.get("account", "")},
                )
            except Exception as exc:  # noqa: BLE001
                # ADR 0062 / ADR 0048: catch unhandled runtime exception inside authorized tool
                elapsed_ms = int((time.monotonic() - start) * 1000)
                if context.audit is not None:
                    context.audit.write(
                        {
                            "phase": "outcome" if name in MUTATING_TOOLS else "single",
                            "request_id": req_id,
                            "caller_id": context.caller_id,
                            "caller_addr": f"stdio:pid={os.getpid()}",
                            "tool": name,
                            "decision": "ALLOW",
                            "reason": "internal_error",
                            "result": "ERROR",
                            "error_class": exc.__class__.__name__,
                            "latency_ms": elapsed_ms,
                        }
                    )
                result = error_envelope(
                    error_type="internal_error",
                    detail="Internal server error",
                    extra={"account": arguments.get("account", "")},
                )
                elapsed_ms = int((time.monotonic() - start) * 1000)
                span.set_attribute("mcp.decision", "ALLOW")
                span.set_attribute("mcp.reason", "internal_error")
                span.set_attribute("mcp.latency_ms", elapsed_ms)
                content = _emit(result)
                _record_span_outcome(span, name, arguments, result, content, payloads)
                return ServerResult(CallToolResult(content=content, isError=False))

            elapsed_ms = int((time.monotonic() - start) * 1000)
            span.set_attribute("mcp.decision", result.get("decision", ""))
            span.set_attribute("mcp.reason", result.get("reason", ""))
            span.set_attribute("mcp.latency_ms", elapsed_ms)

            content = _emit(result)
            _record_span_outcome(span, name, arguments, result, content, payloads)

            # Audit logging (ADR 0048)
            _audit_tool_call(
                context,
                name,
                arguments,
                result,
                latency_ms=elapsed_ms,
                request_id=req_id,
            )
            _CURRENT_REQUEST_ID.reset(token_req)
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

        raise McpError(ErrorData(code=-32601, message=f"Unknown tool: {name!r}"))

    return app


def _emit(payload: Mapping[str, Any]) -> list[ContentBlock]:
    public = {k: v for k, v in payload.items() if not k.startswith("_")}
    return [TextContent(type="text", text=json.dumps(public))]


_LISTING_FIELDS: dict[str, str] = {
    "list_messages": "messages",
    "search": "uids",
    "list_drafts": "drafts",
    "list_folders": "folders",
    "list_accounts": "accounts",
    "list_labels": "labels",
    "list_attachments": "attachments",
}


def _client_attributes(app: Server) -> dict[str, str]:
    """ADR 0053: clientInfo bounded to 128 chars printable ASCII."""
    try:
        params = app.request_context.session.client_params
    except LookupError:
        return {}
    if params is None or not params.clientInfo:
        return {}

    def _clean(s: str) -> str:
        ascii_chars = [c for c in s if 32 <= ord(c) <= 126]
        return "".join(ascii_chars)[:128]

    return {
        "mcp.client.name": _clean(params.clientInfo.name or ""),
        "mcp.client.version": _clean(params.clientInfo.version or ""),
    }


def _record_span_outcome(
    span: Any,
    tool: str,
    arguments: dict[str, Any],
    result: Mapping[str, Any],
    content: list[ContentBlock],
    payloads: PayloadSettings,
) -> None:
    public = {k: v for k, v in result.items() if not k.startswith("_")}
    span.set_attribute(
        "mcp.response.size_bytes",
        sum(len(block.text.encode("utf-8")) for block in content if isinstance(block, TextContent)),
    )
    listed = public.get(_LISTING_FIELDS.get(tool, ""))
    if isinstance(listed, list):
        span.set_attribute("mcp.result.returned", len(listed))
    if "total_visible" in public:
        span.set_attribute("mcp.result.visible", public["total_visible"])
    if "has_more" in public:
        span.set_attribute("mcp.result.has_more", public["has_more"])

    if public.get("decision") == "DENY":
        span.set_attribute("domain.outcome", "rejected")
        span.set_attribute("domain.rejection.reason", str(public.get("reason", "")))
    elif public.get("decision") == "ALLOW":
        span.set_attribute("domain.outcome", "accepted")

    if public.get("result") == "ERROR":
        error = public.get("error")
        error_type = str(error.get("type") or "") if isinstance(error, dict) else ""
        span.set_attribute("error.type", error_type or "internal_error")
        mark_span_error(span, error_type or "error envelope")

    span.set_attributes(
        payload_attributes(
            payloads,
            request=arguments,
            audited_request=_sanitise_args(arguments, None),
            response=public,
        )
    )


def _audit_tool_call(
    context: ServerContext,
    tool: str,
    arguments: dict[str, Any],
    result: Mapping[str, Any],
    latency_ms: int = 0,
    request_id: str | None = None,
) -> None:
    if context.audit is None:
        return

    is_mutating = tool in MUTATING_TOOLS and result.get("decision") == "ALLOW"
    phase = "outcome" if is_mutating else "single"

    safe_args = _sanitise_args(arguments, context.provenance_hmac_key)
    record: dict[str, Any] = {
        "phase": phase,
        "caller_id": context.caller_id,
        "caller_addr": context.caller_addr,
        "tool": tool,
        "args_summary": safe_args,
        "decision": result.get("decision"),
        "reason": result.get("reason"),
        "result": result.get("result", "OK"),
        "latency_ms": latency_ms,
    }
    if request_id:
        record["request_id"] = request_id

    error = result.get("error")
    if isinstance(error, dict) and "type" in error:
        record["error_type"] = error["type"]

    if "visibility_applied" in result:
        record["visibility_granted"] = result["visibility_applied"]
    elif "visibility_granted" in result:
        record["visibility_granted"] = result["visibility_granted"]

    # Forensic audit detail keys (ADR 0048 §2)
    if "_audit_detail_reason" in result:
        record["audit_detail_reason"] = result["_audit_detail_reason"]

    if "_suppressed_by_policy" in result:
        record["suppressed_by_policy"] = result["_suppressed_by_policy"]

    if "_from_domain" in result:
        dom = str(result["_from_domain"]).lower().strip()
        record["from_domain_digest"] = compute_keyed_digest(
            context.provenance_hmac_key, dom.encode("utf-8")
        )

    # In fetch_envelope when capped, hash from_domain
    if result.get("_audit_detail_reason") == "message_capped" and "account" in result:
        pass

    if "_artifact_id" in result:
        record["artifact_id"] = result["_artifact_id"]
    if "_artifact_sha256" in result:
        record["artifact_sha256"] = result["_artifact_sha256"]

    # search_query_digest from safe_args
    if "search_query_digest" in safe_args:
        record["search_query_digest"] = safe_args["search_query_digest"]

    if "from_domain_digest" in safe_args:
        record["from_domain_digest"] = safe_args["from_domain_digest"]

    context.audit.write(record)


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
        "attachments",
    }
)


def _sanitise_args(arguments: dict[str, Any], hmac_key: bytes | None = None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    content_fields = sorted(k for k in arguments if k in _DRAFT_CONTENT_ARGS)
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
            safe["changed_fields"] = sorted(value.keys())
            continue
        if key in ("source", "target") and isinstance(value, dict):
            safe[key] = {k: v for k, v in value.items() if k in ("account", "folder", "uid")}
            continue
        if key == "criteria" and isinstance(value, dict):
            if hmac_key:
                canon = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
                safe["search_query_digest"] = compute_keyed_digest(hmac_key, canon)
                if "from_domain" in value:
                    dom = str(value["from_domain"]).strip().lower()
                    safe["from_domain_digest"] = compute_keyed_digest(hmac_key, dom.encode("utf-8"))
            continue
    return safe


# --------------------------------------------------------------------- schemas

_DURATION_PATTERN = r"^[1-9][0-9]{0,5}[smhdwy]$"
_CALENDAR_DATE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "format": "date",
    "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
}

# ADR 0041: 18 predicates (retired keys from, to, to_contains are omitted so additionalProperties rejects them)
_CRITERIA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "from_address": {"type": "string"},
        "from_domain": {"type": "string"},
        "to_address": {"type": "string"},
        "to_domain": {"type": "string"},
        "cc_address": {"type": "string"},
        "cc_domain": {"type": "string"},
        "recipient_address": {"type": "string"},
        "recipient_domain": {"type": "string"},
        "all_recipients_in_domains": {"type": "array", "items": {"type": "string"}},
        "subject_contains": {"type": "string"},
        "has_attachment": {"type": "boolean"},
        "flagged": {"type": "boolean"},
        "provenance": {"type": "string", "enum": ["own"]},
        "newer_than": {"type": "string", "pattern": _DURATION_PATTERN},
        "older_than": {"type": "string", "pattern": _DURATION_PATTERN},
        "since": _CALENDAR_DATE_SCHEMA,
        "before": _CALENDAR_DATE_SCHEMA,
        "size_gt": {"type": "integer", "minimum": 1, "maximum": 1073741824},
        "size_lt": {"type": "integer", "minimum": 1, "maximum": 1073741824},
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
        "uidvalidity": {"type": "integer", "minimum": 1},
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

_STRING_LIST_SCHEMA: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
_NULLABLE_STRING_SCHEMA: dict[str, Any] = {"type": ["string", "null"]}
_ADDRESS_LIST_SCHEMA: dict[str, Any] = {
    "type": ["array", "null"],
    "items": {"type": "string"},
}

_ATTACHMENT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "filename": {"type": "string"},
        "mime_type": {"type": "string"},
        "content_base64": {"type": "string"},
    },
    "required": ["filename", "mime_type", "content_base64"],
    "additionalProperties": False,
}

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
        "attachments": {"type": ["array", "null"], "items": _ATTACHMENT_INPUT_SCHEMA},
    },
    "additionalProperties": False,
    "minProperties": 1,
}


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
    *,
    category: str,
    minimum_visibility: str | None = None,
    required_capabilities: list[str] | None = None,
) -> Tool:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required

    meta_info: dict[str, Any] = {"category": category}
    if minimum_visibility is not None:
        meta_info["minimum_visibility"] = minimum_visibility
    if required_capabilities is not None:
        meta_info["required_capabilities"] = required_capabilities

    schema["x-mcp-imap"] = meta_info
    schema["_meta"] = meta_info
    return Tool(name=name, description=description, inputSchema=schema, _meta=meta_info)


def _build_tool_list(context: ServerContext | None = None) -> list[Tool]:
    account_field = {"account": {"type": "string"}}
    folder_field = {"folder": {"type": "string"}}
    uid_field = {"uid": {"type": "integer", "minimum": 1}}

    return [
        # --- 13 Read Tools ---
        _tool(
            "list_accounts",
            "List accounts visible to the caller (ADR 0026, ADR 0052).",
            {},
            category="read",
        ),
        _tool(
            "list_folders",
            "List folders the caller may see on an account (ADR 0025, ADR 0052, ADR 0056).",
            {**account_field},
            ["account"],
            category="read",
            minimum_visibility="COUNT",
        ),
        _tool(
            "list_labels",
            "List Gmail labels for a Google account (ADR 0019, ADR 0040, ADR 0052).",
            {**account_field},
            ["account"],
            category="read",
            minimum_visibility="COUNT",
        ),
        _tool(
            "folder_stats",
            "Return aggregate counts for a folder (ADR 0043, ADR 0052).",
            {**account_field, **folder_field},
            ["account", "folder"],
            category="read",
            minimum_visibility="COUNT",
        ),
        _tool(
            "search",
            (
                "Search a folder for matching UIDs (ADR 0041, ADR 0043, ADR 0052). "
                "Without a time bound only the last 7 days are searched (applied_scope recent_7d); "
                'pass scope "all" to search all time. criteria.since (inclusive) and criteria.before '
                "(exclusive) take calendar dates YYYY-MM-DD and cannot be combined with newer_than/older_than."
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
            category="read",
            minimum_visibility="METADATA",
        ),
        _tool(
            "list_messages",
            (
                "List messages with envelope data (ADR 0040, ADR 0041, ADR 0043, ADR 0052). "
                "Without a time bound only the last 7 days are listed (applied_scope recent_7d); "
                'pass scope "all" to list all time. criteria.since (inclusive) and criteria.before '
                "(exclusive) take calendar dates YYYY-MM-DD and cannot be combined with newer_than/older_than."
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
            category="read",
            minimum_visibility="METADATA",
        ),
        _tool(
            "fetch_envelope",
            "Fetch envelope fields for one UID (ADR 0026, ADR 0043, ADR 0052).",
            {**account_field, **folder_field, **uid_field},
            ["account", "folder", "uid"],
            category="read",
            minimum_visibility="ENVELOPE",
        ),
        _tool(
            "fetch_headers",
            "Fetch full RFC 5322 headers (ADR 0026, ADR 0043, ADR 0052).",
            {**account_field, **folder_field, **uid_field},
            ["account", "folder", "uid"],
            category="read",
            minimum_visibility="HEADERS",
        ),
        _tool(
            "fetch_body",
            "Fetch the plain/HTML body parts (ADR 0026, ADR 0043, ADR 0052).",
            {**account_field, **folder_field, **uid_field},
            ["account", "folder", "uid"],
            category="read",
            minimum_visibility="BODY",
        ),
        _tool(
            "list_attachments",
            "List attachment metadata for one UID (ADR 0026, ADR 0047, ADR 0052).",
            {**account_field, **folder_field, **uid_field},
            ["account", "folder", "uid"],
            category="read",
            minimum_visibility="BODY",
        ),
        _tool(
            "fetch_attachment",
            "Fetch one attachment part by index into the per-caller sink directory (ADR 0026, ADR 0047).",
            {
                **account_field,
                **folder_field,
                **uid_field,
                "part_id": {"type": "integer", "minimum": 0},
            },
            ["account", "folder", "uid", "part_id"],
            category="read",
            minimum_visibility="FULL",
        ),
        _tool(
            "list_drafts",
            "List the drafts in a folder with their handles (ADR 0030, ADR 0042, ADR 0052).",
            {**account_field, **folder_field},
            ["account", "folder"],
            category="read",
            minimum_visibility="METADATA",
        ),
        _tool(
            "get_draft",
            "Return the composed fields of one draft by its handle (ADR 0030, ADR 0042, ADR 0052).",
            {**account_field, **folder_field, "draft_id": {"type": "string"}},
            ["account", "folder", "draft_id"],
            category="read",
            minimum_visibility="BODY",
        ),
        # --- 10 Write Tools ---
        _tool(
            "mark_seen",
            "Toggle the \\Seen flag on a message (ADR 0005, ADR 0045, ADR 0052).",
            {**account_field, **folder_field, **uid_field, "seen": {"type": "boolean"}},
            ["account", "folder", "uid", "seen"],
            category="write",
            required_capabilities=["mark_seen"],
            minimum_visibility="METADATA",
        ),
        _tool(
            "bulk_mark_seen",
            "Mark every message matching criteria as seen/unseen (ADR 0026, ADR 0045, ADR 0052).",
            {
                **account_field,
                **folder_field,
                "criteria": _CRITERIA_SCHEMA,
                "scope": _SCOPE_SCHEMA,
                "seen": {"type": "boolean"},
            },
            ["account", "folder", "criteria", "seen"],
            category="write",
            required_capabilities=["mark_seen"],
            minimum_visibility="METADATA",
        ),
        _tool(
            "mark_tagged",
            "Add, remove, or replace keywords on a message (ADR 0005, ADR 0045, ADR 0052).",
            {
                **account_field,
                **folder_field,
                **uid_field,
                "tags": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string", "enum": ["add", "remove", "replace"]},
            },
            ["account", "folder", "uid", "tags", "mode"],
            category="write",
            required_capabilities=["mark_tagged"],
            minimum_visibility="ENVELOPE",
        ),
        _tool(
            "bulk_mark_tagged",
            "Tag/untag every message matching criteria (ADR 0026, ADR 0045, ADR 0052).",
            {
                **account_field,
                **folder_field,
                "criteria": _CRITERIA_SCHEMA,
                "scope": _SCOPE_SCHEMA,
                "tags": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string", "enum": ["add", "remove", "replace"]},
            },
            ["account", "folder", "criteria", "tags", "mode"],
            category="write",
            required_capabilities=["mark_tagged"],
            minimum_visibility="ENVELOPE",
        ),
        _tool(
            "move",
            "Move a message between folders (ADR 0006, ADR 0045, ADR 0046, ADR 0052).",
            {"source": _SOURCE_SCHEMA, "target": _TARGET_SCHEMA},
            ["source", "target"],
            category="write",
            required_capabilities=["move_out", "accept_incoming"],
            minimum_visibility="ENVELOPE",
        ),
        _tool(
            "copy",
            "Copy a message between folders (ADR 0006, ADR 0045, ADR 0046, ADR 0052).",
            {"source": _SOURCE_SCHEMA, "target": _TARGET_SCHEMA},
            ["source", "target"],
            category="write",
            required_capabilities=["copy_out", "accept_incoming"],
            minimum_visibility="ENVELOPE",
        ),
        _tool(
            "create_draft",
            (
                "Append a new draft (ADR 0026, ADR 0035, ADR 0063). Choose the body format "
                "deliberately: plain text or HTML. If the user's preference is unknown, ask. "
                "Pass either structured fields (to, cc, bcc, subject, body_text and/or body_html, "
                "in_reply_to, attachments) or a complete RFC 5322 message in rfc822, never both. "
                "The response field format names the format that was stored."
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
                "attachments": {"type": "array", "items": _ATTACHMENT_INPUT_SCHEMA},
            },
            ["account", "folder"],
            category="write",
            required_capabilities=["draft_append"],
        ),
        _tool(
            "create_reply_draft",
            (
                "Build and APPEND a top-posted reply draft (ADR 0026, ADR 0057, ADR 0063). "
                "Give reply_text, reply_html, or both."
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
            category="write",
            required_capabilities=["draft_append"],
            minimum_visibility="BODY",
        ),
        _tool(
            "update_draft",
            "Revise a draft field-wise under its handle (ADR 0031, ADR 0042, ADR 0063).",
            {
                **account_field,
                **folder_field,
                "draft_id": {"type": "string"},
                "base_revision": {"type": "integer", "minimum": 1},
                "changes": _DRAFT_CHANGES_SCHEMA,
            },
            ["account", "folder", "draft_id", "base_revision", "changes"],
            category="write",
            required_capabilities=["draft_edit"],
        ),
        _tool(
            "discard_draft",
            (
                "Discard a draft by its handle: move every message carrying it "
                "(all revisions) to the account's trash folder, the one with the "
                "\\Trash special-use flag. Never deletes permanently. Needs "
                "move_out on the drafts folder and accept_incoming on the trash "
                "folder (ADR 0034, ADR 0063)."
            ),
            {**account_field, **folder_field, "draft_id": {"type": "string"}},
            ["account", "folder", "draft_id"],
            category="write",
            required_capabilities=["move_out", "accept_incoming"],
        ),
        # --- 4 Meta Tools ---
        _tool(
            "describe_policy",
            "Return the caller's own policy profile (ADR 0017, ADR 0043, ADR 0052).",
            {},
            category="meta",
        ),
        _tool(
            "get_transaction_status",
            "Return the WAL state of a saga transaction (ADR 0006, ADR 0046, ADR 0052).",
            {"tx_id": {"type": "string"}},
            ["tx_id"],
            category="meta",
        ),
        _tool(
            "get_caller_identity",
            "Return the resolved caller_id (ADR 0015, ADR 0052).",
            {},
            category="meta",
        ),
        _tool(
            "tool_surface_info",
            "Return tool-set version and breaking-changes log (ADR 0027, ADR 0052).",
            {},
            category="meta",
        ),
    ]


@functools.cache
def _input_schemas() -> dict[str, dict[str, Any]]:
    return {tool.name: tool.inputSchema for tool in _build_tool_list()}


def _json_type(value: object) -> str:
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
    # 1. Required properties first
    required = schema.get("required") or []
    missing = [f"'{prefix}{key}'" for key in required if key not in value]
    if missing:
        noun = "argument" if len(missing) == 1 else "arguments"
        return f"missing required {noun} {', '.join(missing)}"

    properties = schema.get("properties") or {}

    # 2. Unknown properties check (additionalProperties: False)
    if not schema.get("additionalProperties", True):
        for k in value:
            if k not in properties:
                return f"unknown argument '{prefix}{k}'"

    # 3. Type violation check on declared properties
    for key, member_schema in properties.items():
        if key in value and isinstance(member_schema, Mapping):
            violation = _value_violation(member_schema, value[key], f"{prefix}{key}")
            if violation is not None:
                return violation

    return None


_CRLF_RE = re.compile(r"[\r\n]")


def _check_crlf(val: Any) -> bool:
    if isinstance(val, str):
        return bool(_CRLF_RE.search(val))
    if isinstance(val, list):
        return any(_check_crlf(x) for x in val)
    if isinstance(val, dict):
        return any(_check_crlf(x) for x in val.values())
    return False


def _validate_arguments_against_surface(name: str, arguments: dict[str, Any]) -> str | None:
    schema = _input_schemas().get(name)
    if schema is not None:
        violation = _object_violation(schema, arguments, "")
        if violation is not None:
            return f"{name}: {violation}"

    # Move: same source and target check
    if name == "move":
        src = arguments.get("source") or {}
        dst = arguments.get("target") or {}
        if src.get("account") == dst.get("account") and src.get("folder") == dst.get("folder"):
            return "move: source and target must not be identical (account and folder match)"

    # Criteria validation
    if name in ("search", "list_messages", "bulk_mark_seen", "bulk_mark_tagged"):
        criteria = arguments.get("criteria")
        if isinstance(criteria, dict):
            from .handlers.search import validate_calendar_bounds

            cal_err = validate_calendar_bounds(criteria)
            if cal_err is not None:
                return f"{name}: {cal_err}"

            for d_key in ("newer_than", "older_than"):
                val = criteria.get(d_key)
                if val is not None:
                    try:
                        parse_duration_seconds(val)
                    except ValueError as exc:
                        return f"{name}: Invalid criteria.{d_key} {val!r}: {exc}"

    # Drafts CRLF and composition checks
    if name == "create_draft":
        if "rfc822" in arguments and any(
            k in arguments
            for k in (
                "to",
                "cc",
                "bcc",
                "subject",
                "body_text",
                "body_html",
                "in_reply_to",
                "attachments",
            )
        ):
            return "create_draft takes either `rfc822` or structured fields, not both (ADR 0035)"
        if "rfc822" not in arguments and not any(
            k in arguments for k in ("body_text", "body_html")
        ):
            return "create_draft requires `rfc822` or a `body_text` or `body_html` field (ADR 0035)"
        for field in ("to", "cc", "bcc", "subject", "in_reply_to", "attachments"):
            if _check_crlf(arguments.get(field)):
                return f"create_draft `{field}` contains a line break; header values must be a single line"
        for addr_f in ("to", "cc", "bcc"):
            if addr_f in arguments:
                val = arguments[addr_f]
                if isinstance(val, list):
                    import email.utils

                    for item in val:
                        if isinstance(item, str):
                            if not item.isascii():
                                return f"create_draft `{addr_f}` holds an address outside ASCII"
                            _, spec = email.utils.parseaddr(item)
                            if not spec or not re.match(
                                r"^[^@\s<>,;:\"]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$", spec
                            ):
                                return f"create_draft `{addr_f}` holds a malformed address"

    if name == "update_draft":
        base_rev = arguments.get("base_revision")
        if isinstance(base_rev, bool) or not isinstance(base_rev, int) or base_rev < 1:
            return "update_draft: base_revision must be an integer >= 1"
        changes = arguments.get("changes")
        from .handlers.drafts import validate_changes

        v_err = validate_changes(changes)
        if v_err is not None:
            return v_err
        if isinstance(changes, dict):
            for field in ("to", "cc", "bcc", "subject", "in_reply_to", "attachments"):
                if _check_crlf(changes.get(field)):
                    return f"update_draft: argument 'changes.{field}' contains a line break"
            for addr_f in ("to", "cc", "bcc"):
                if addr_f in changes and changes[addr_f] is not None:
                    for a in changes[addr_f]:
                        if not isinstance(a, str) or "@" not in a or not a.strip():
                            return f"update_draft: malformed email address in changes.{addr_f}"

    if name == "create_reply_draft":
        for field in ("reply_text", "reply_html"):
            if field in arguments and not isinstance(arguments[field], str):
                return f"create_reply_draft: argument '{field}' must be a string"

    return None
