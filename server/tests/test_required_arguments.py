"""Unit tests for the required-argument and JSON-type check of every tool.

The BDD suite (tool_surface/required_arguments.feature) witnesses the
JSON-RPC -32602 on the wire for a handful of tools. These tests pin what
is cheaper to enumerate here: every required argument of every published
tool, nested objects, array items, nullable types, and that the value an
agent passed never appears in the message.
"""

from __future__ import annotations

from typing import Any

import pytest

from imap_mcp.dispatch import (
    _build_tool_list,
    _object_violation,
    _validate_arguments_against_surface,
)


def _sample(schema: dict[str, Any]) -> Any:
    """A value of the first non-null type `schema` declares."""
    declared = schema.get("type")
    types = [declared] if isinstance(declared, str) else list(declared or [])
    kind = next(t for t in types if t != "null")
    if kind == "string":
        return "x"
    if kind == "integer":
        return 1
    if kind == "boolean":
        return True
    if kind == "array":
        return [_sample(schema["items"])] if "items" in schema else []
    if kind == "object":
        properties = schema.get("properties", {})
        return {key: _sample(properties[key]) for key in schema.get("required", [])}
    raise AssertionError(f"no sample for {kind!r}")


def _complete_arguments(schema: dict[str, Any]) -> dict[str, Any]:
    arguments = _sample(schema)
    assert isinstance(arguments, dict)
    return arguments


_TOOLS = {tool.name: tool.inputSchema for tool in _build_tool_list()}
_REQUIRED = [(name, key) for name, schema in _TOOLS.items() for key in schema.get("required", [])]


@pytest.mark.parametrize("name", sorted(_TOOLS))
def test_a_call_with_every_required_argument_passes_the_schema_check(name: str) -> None:
    schema = _TOOLS[name]
    assert _object_violation(schema, _complete_arguments(schema), "") is None


@pytest.mark.parametrize(("name", "key"), _REQUIRED)
def test_each_missing_required_argument_is_named(name: str, key: str) -> None:
    arguments = _complete_arguments(_TOOLS[name])
    del arguments[key]
    assert (
        _validate_arguments_against_surface(name, arguments)
        == f"{name}: missing required argument '{key}'"
    )


def test_several_missing_arguments_are_named_in_schema_order() -> None:
    assert (
        _validate_arguments_against_surface("fetch_body", {"account": "a"})
        == "fetch_body: missing required arguments 'folder', 'uid'"
    )


def test_list_folders_without_any_argument_names_account() -> None:
    assert (
        _validate_arguments_against_surface("list_folders", {})
        == "list_folders: missing required argument 'account'"
    )


def test_a_missing_member_of_a_nested_object_is_named_with_its_path() -> None:
    arguments = {
        "source": {"account": "a", "folder": "INBOX"},
        "target": {"account": "a", "folder": "Archiv"},
    }
    assert (
        _validate_arguments_against_surface("move", arguments)
        == "move: missing required argument 'source.uid'"
    )


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        (
            "fetch_body",
            {"account": "a", "folder": "INBOX", "uid": "abc"},
            "fetch_body: argument 'uid' must be an integer, got string",
        ),
        (
            "fetch_body",
            {"account": "a", "folder": "INBOX", "uid": True},
            "fetch_body: argument 'uid' must be an integer, got boolean",
        ),
        (
            "fetch_body",
            {"account": "a", "folder": "INBOX", "uid": 1.0},
            "fetch_body: argument 'uid' must be an integer, got number",
        ),
        (
            "folder_stats",
            {"account": "a", "folder": 7},
            "folder_stats: argument 'folder' must be a string, got integer",
        ),
        (
            "list_drafts",
            {"account": "a", "folder": None},
            "list_drafts: argument 'folder' must be a string, got null",
        ),
        (
            "search",
            {"account": "a", "folder": "INBOX", "criteria": {"has_attachment": "yes"}},
            "search: argument 'criteria.has_attachment' must be a boolean, got string",
        ),
        (
            "search",
            {"account": "a", "folder": "INBOX", "criteria": "from:x"},
            "search: argument 'criteria' must be an object, got string",
        ),
        (
            "mark_tagged",
            {"account": "a", "folder": "INBOX", "uid": 1, "tags": ["ok", 2], "mode": "add"},
            "mark_tagged: argument 'tags[1]' must be a string, got integer",
        ),
        (
            "mark_seen",
            {"account": "a", "folder": "INBOX", "uid": 1, "seen": "yes"},
            "mark_seen: argument 'seen' must be a boolean, got string",
        ),
        (
            "move",
            {
                "source": {"account": "a", "folder": "INBOX", "uid": "7"},
                "target": {"account": "a", "folder": "Archiv"},
            },
            "move: argument 'source.uid' must be an integer, got string",
        ),
    ],
)
def test_an_argument_of_the_wrong_json_type_is_named(
    name: str, arguments: dict[str, Any], message: str
) -> None:
    assert _validate_arguments_against_surface(name, arguments) == message


def test_a_nullable_member_accepts_null_and_names_both_types_otherwise() -> None:
    base = {"account": "a", "folder": "Drafts", "draft_id": "d", "base_revision": 1}
    assert (
        _validate_arguments_against_surface("update_draft", {**base, "changes": {"cc": None}})
        is None
    )
    assert _validate_arguments_against_surface(
        "update_draft", {**base, "changes": {"cc": "a@example.org"}}
    ) == ("update_draft: argument 'changes.cc' must be an array of strings or null, got string")


def test_the_value_passed_is_never_echoed() -> None:
    message = _validate_arguments_against_surface(
        "fetch_body", {"account": "a", "folder": "INBOX", "uid": "geheim-4711"}
    )
    assert message is not None
    assert "geheim-4711" not in message


def test_extra_arguments_are_not_judged() -> None:
    assert _validate_arguments_against_surface("describe_policy", {"impersonate": "x"}) is None


def test_an_unknown_tool_is_left_to_the_unknown_tool_check() -> None:
    assert _validate_arguments_against_surface("delete", {}) is None


def test_fetch_attachment_without_part_id_names_it() -> None:
    assert (
        _validate_arguments_against_surface(
            "fetch_attachment", {"account": "a", "folder": "INBOX", "uid": 1}
        )
        == "fetch_attachment: missing required argument 'part_id'"
    )
