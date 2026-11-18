"""Unit tests for Tool Surface 3.0.0 discovery and metadata (ADR 0052)."""

from __future__ import annotations

from types import SimpleNamespace

from imap_mcp.dispatch import _build_tool_list
from imap_mcp.handlers._common import (
    READ_TOOL_MIN_VIS,
    TOOL_SET_VERSION,
    WRITE_TOOL_METADATA,
)
from imap_mcp.handlers.introspection import handle_tool_surface_info


def test_surface_has_exactly_27_tools() -> None:
    tools = _build_tool_list()
    assert len(tools) == 27
    names = {t.name for t in tools}
    assert len(names) == 27

    expected_read = {
        "list_accounts",
        "list_folders",
        "list_labels",
        "folder_stats",
        "search",
        "list_messages",
        "fetch_envelope",
        "fetch_headers",
        "fetch_body",
        "list_attachments",
        "fetch_attachment",
        "list_drafts",
        "get_draft",
    }
    expected_write = {
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
    }
    expected_meta = {
        "describe_policy",
        "get_transaction_status",
        "get_caller_identity",
        "tool_surface_info",
    }

    assert names == (expected_read | expected_write | expected_meta)


def test_retired_tools_are_absent() -> None:
    tools = {t.name for t in _build_tool_list()}
    forbidden = [
        "add_attachment",
        "replace_attachment",
        "delete_attachment",
        "modify_message",
        "raw_imap_command",
        "fetch_raw_rfc822",
        "delete",
        "hard_delete",
        "expunge",
        "bulk_move",
        "_test_run_recovery",
        "_test_run_audit_rotation",
    ]
    for name in forbidden:
        assert name not in tools, f"Forbidden tool {name!r} found in tools/list"


def test_read_tools_advertise_exact_minimum_visibility() -> None:
    tools = {t.name: t for t in _build_tool_list()}
    for tool_name, expected_vis in READ_TOOL_MIN_VIS.items():
        t = tools[tool_name]
        meta = t.inputSchema.get("x-mcp-imap")
        assert meta is not None
        assert meta.get("category") == "read"
        if expected_vis is None:
            assert "minimum_visibility" not in meta
        else:
            assert meta.get("minimum_visibility") == expected_vis


def test_write_tools_advertise_exact_required_capabilities_and_visibility() -> None:
    tools = {t.name: t for t in _build_tool_list()}
    for tool_name, expected_meta in WRITE_TOOL_METADATA.items():
        t = tools[tool_name]
        meta = t.inputSchema.get("x-mcp-imap")
        assert meta is not None
        assert meta.get("category") == "write"
        assert meta.get("required_capabilities") == expected_meta["required_capabilities"]
        if "minimum_visibility" in expected_meta:
            assert meta.get("minimum_visibility") == expected_meta["minimum_visibility"]
        else:
            assert "minimum_visibility" not in meta


def test_meta_tools_advertise_category_meta_without_vis_or_caps() -> None:
    tools = {t.name: t for t in _build_tool_list()}
    for name in (
        "describe_policy",
        "get_transaction_status",
        "get_caller_identity",
        "tool_surface_info",
    ):
        t = tools[name]
        meta = t.inputSchema.get("x-mcp-imap")
        assert meta is not None
        assert meta.get("category") == "meta"
        assert "minimum_visibility" not in meta
        assert "required_capabilities" not in meta


def test_fetch_attachment_description_is_static_and_mentions_adr_0047() -> None:
    tools = {t.name: t for t in _build_tool_list()}
    desc = tools["fetch_attachment"].description
    assert "ADR 0047" in desc
    assert "/" not in desc or "sink directory" in desc
    assert "/tmp" not in desc


def test_create_draft_description_mentions_ask_and_plain_text_or_html() -> None:
    tools = {t.name: t for t in _build_tool_list()}
    desc = tools["create_draft"].description
    assert "plain text or HTML" in desc
    assert "If the user's preference is unknown, ask" in desc
    assert "prefer HTML" not in desc
    assert "prefer plain text" not in desc


def test_tool_surface_info_and_server_info_agree_on_3_0_0() -> None:
    info = handle_tool_surface_info(SimpleNamespace())
    assert info["decision"] == "ALLOW"
    assert info["result"] == "OK"
    assert info["reason"] == "authenticated"
    assert info["tool_set_version"] == "3.0.0"
    assert info["package_version"] == "3.0.0"
    assert TOOL_SET_VERSION == "3.0.0"

    breaks = info["breaking_changes_since"]
    v2_entry = next((b for b in breaks if b["version"] == "2.0.0"), None)
    assert v2_entry and "ADR 0028" in v2_entry["summary"]
    v3_entry = next((b for b in breaks if b["version"] == "3.0.0"), None)
    assert v3_entry and "ADR 0040" in v3_entry["summary"]
