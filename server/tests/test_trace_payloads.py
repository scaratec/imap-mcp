"""Unit tests for trace payload modes (ADR 0037) and span outcomes (ADR 0038).

The BDD suite witnesses the spans through a test collector. These tests
pin what is cheaper to enumerate here: mode parsing and its refusals,
the recursive redaction of every content member, the truncation marker
arithmetic, the attribute set each mode produces, and the IMAP command
to span-name mapping of the traced client.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from imap_mcp import trace_payloads
from imap_mcp.dispatch import _record_span_outcome, _sanitise_args
from imap_mcp.imap_core import TracedIMAP
from imap_mcp.trace_payloads import (
    CONTENT_KEYS,
    DEFAULT_MAX_CHARS,
    PayloadSettings,
    PayloadSettingsError,
    parse_payload_settings,
    payload_attributes,
    redact_response,
    truncate,
)

# ------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    ("env", "mode"),
    [
        ({}, "redacted"),
        ({"IMAP_MCP_TRACE_PAYLOADS": ""}, "redacted"),
        ({"IMAP_MCP_TRACE_PAYLOADS": "off"}, "off"),
        ({"IMAP_MCP_TRACE_PAYLOADS": "redacted"}, "redacted"),
        ({"IMAP_MCP_TRACE_PAYLOADS": "full"}, "full"),
        ({"IMAP_MCP_TRACE_PAYLOADS": " Full "}, "full"),
    ],
)
def test_mode_parsing(env: dict[str, str], mode: str) -> None:
    settings = parse_payload_settings(env)
    assert settings.mode == mode
    assert settings.max_chars == DEFAULT_MAX_CHARS == 16384


@pytest.mark.parametrize("value", ["verbose", "none", "redact", "ful", "on", "1"])
def test_unknown_mode_is_refused_with_variable_and_allowed_values(value: str) -> None:
    with pytest.raises(PayloadSettingsError) as excinfo:
        parse_payload_settings({"IMAP_MCP_TRACE_PAYLOADS": value})
    message = str(excinfo.value)
    assert "IMAP_MCP_TRACE_PAYLOADS" in message
    assert "off, redacted, full" in message
    assert value in message


def test_max_chars_is_read() -> None:
    settings = parse_payload_settings({"IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS": "2000"})
    assert settings.max_chars == 2000


@pytest.mark.parametrize("value", ["0", "-5", "abc", "1.5"])
def test_invalid_max_chars_is_refused(value: str) -> None:
    with pytest.raises(PayloadSettingsError, match="IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS"):
        parse_payload_settings({"IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS": value})


def test_process_settings_exit_on_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trace_payloads, "_settings", None)
    monkeypatch.setenv("IMAP_MCP_TRACE_PAYLOADS", "verbose")
    with pytest.raises(SystemExit) as excinfo:
        trace_payloads.payload_settings()
    assert "off, redacted, full" in str(excinfo.value.code)


# ----------------------------------------------------------- redaction


def test_redaction_drops_content_at_top_level() -> None:
    response = {
        "decision": "ALLOW",
        "uid": 311,
        "from": "mara.lindqvist@example.org",
        "to": ["jonas.hartmann@example.org"],
        "cc": ["a@example.org"],
        "bcc": ["b@example.org"],
        "subject": "Kündigung Mietvertrag",
        "body": "Text",
        "text_body": "Text",
        "headers": {"Subject": "Kündigung"},
        "attachments": [{"filename": "vertrag.pdf"}],
        "messages": [{"uid": 1, "subject": "x"}],
        "message_id": "<id@example.org>",
        "date": "2026-10-30T07:00:00Z",
    }
    assert redact_response(response) == {
        "decision": "ALLOW",
        "uid": 311,
        "date": "2026-10-30T07:00:00Z",
    }


def test_redaction_recurses_into_nested_objects_and_list_entries() -> None:
    response = {
        "decision": "ALLOW",
        "drafts": [
            {"draft_id": "d1", "subject": "Mieterhöhung", "to": ["x@example.org"]},
            {"draft_id": "d2", "revision": 2, "body_text": "42 Euro"},
        ],
        "nested": {"deeper": {"from": "y@example.org", "keep": 1}},
    }
    assert redact_response(response) == {
        "decision": "ALLOW",
        "drafts": [{"draft_id": "d1"}, {"draft_id": "d2", "revision": 2}],
        "nested": {"deeper": {"keep": 1}},
    }


@pytest.mark.parametrize("key", sorted(CONTENT_KEYS))
def test_every_content_key_is_dropped_at_any_depth(key: str) -> None:
    secret = "content-value-7f3a"
    response = {"a": [{"b": {key: secret}}], key: secret}
    assert secret not in json.dumps(redact_response(response))


# ---------------------------------------------------------- truncation


def test_truncation_leaves_short_text_alone() -> None:
    assert truncate("abc", 3) == "abc"


def test_truncation_cuts_and_counts_the_cut_characters() -> None:
    text = "x" * 5000
    cut = truncate(text, 2000)
    assert cut == "x" * 2000 + "…[truncated 3000 chars]"


def test_truncation_counts_characters_not_bytes() -> None:
    assert truncate("ü" * 10, 4) == "üüüü…[truncated 6 chars]"


# ------------------------------------------------------------ attributes

_REQUEST: dict[str, Any] = {
    "account": "gupta-scaratec",
    "folder": "Drafts",
    "rfc822": "To: jonas.hartmann@example.org\r\n\r\n42 Euro",
}
_RESPONSE: dict[str, Any] = {
    "decision": "ALLOW",
    "uid": 7,
    "subject": "Kündigung Mietvertrag",
    "from": "mara.lindqvist@example.org",
}


def test_off_produces_no_payload_attributes() -> None:
    attributes = payload_attributes(
        PayloadSettings("off", 100),
        request=_REQUEST,
        audited_request=_sanitise_args(_REQUEST),
        response=_RESPONSE,
    )
    assert attributes == {}


def test_redacted_request_is_the_audited_request() -> None:
    attributes = payload_attributes(
        PayloadSettings("redacted", 100),
        request=_REQUEST,
        audited_request=_sanitise_args(_REQUEST),
        response=_RESPONSE,
    )
    assert json.loads(attributes["mcp.request"]) == _sanitise_args(_REQUEST)
    assert '"rfc822": "<redacted>"' in attributes["mcp.request"]
    assert json.loads(attributes["mcp.response"]) == {"decision": "ALLOW", "uid": 7}
    joined = attributes["mcp.request"] + attributes["mcp.response"]
    for leaked in ("jonas.hartmann", "42 Euro", "Kündigung", "mara.lindqvist"):
        assert leaked not in joined


def test_full_keeps_everything_unescaped() -> None:
    attributes = payload_attributes(
        PayloadSettings("full", DEFAULT_MAX_CHARS),
        request=_REQUEST,
        audited_request=_sanitise_args(_REQUEST),
        response=_RESPONSE,
    )
    assert json.loads(attributes["mcp.request"]) == _REQUEST
    assert "Kündigung Mietvertrag" in attributes["mcp.response"]


def test_full_truncates_each_attribute_separately() -> None:
    attributes = payload_attributes(
        PayloadSettings("full", 20),
        request=_REQUEST,
        audited_request=_sanitise_args(_REQUEST),
        response=_RESPONSE,
    )
    for value in attributes.values():
        head, _, marker = value.partition("…[truncated ")
        assert len(head) == 20
        assert marker.endswith(" chars]")


# ------------------------------------------------------- span outcome


class _RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}
        self.statuses: list[Any] = []

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        self.attributes.update(attributes)

    def set_status(self, status: Any) -> None:
        self.statuses.append(status)


def _record(tool: str, result: dict[str, Any], mode: str = "redacted") -> _RecordingSpan:
    from imap_mcp.dispatch import _emit

    span = _RecordingSpan()
    _record_span_outcome(
        span,
        tool,
        {"account": "a", "folder": "INBOX"},
        result,
        _emit(result),
        PayloadSettings(mode, 100),  # type: ignore[arg-type]
    )
    return span


def test_size_is_the_utf8_length_of_the_emitted_text() -> None:
    from imap_mcp.dispatch import _emit

    result = {"decision": "ALLOW", "subject": "Kündigung", "_private": "x"}
    span = _record("fetch_envelope", result)
    text = _emit(result)[0].text  # type: ignore[union-attr]
    assert span.attributes["mcp.response.size_bytes"] == len(text.encode("utf-8"))


def test_listing_counts() -> None:
    span = _record(
        "list_messages",
        {
            "decision": "ALLOW",
            "messages": [{"uid": 1}, {"uid": 2}],
            "matched_total": 4,
            "matched_visible": 3,
            "has_more": True,
        },
    )
    assert span.attributes["mcp.result.matched"] == 4
    assert span.attributes["mcp.result.visible"] == 3
    assert span.attributes["mcp.result.returned"] == 2
    assert span.attributes["mcp.result.has_more"] is True


def test_denial_is_rejected_without_error_status() -> None:
    span = _record("fetch_envelope", {"decision": "DENY", "reason": "sender_not_whitelisted"})
    assert span.attributes["domain.outcome"] == "rejected"
    assert span.attributes["domain.rejection.reason"] == "sender_not_whitelisted"
    assert "error.type" not in span.attributes
    assert span.statuses == []


def test_error_envelope_sets_error_type_and_status() -> None:
    span = _record(
        "list_messages",
        {"decision": "ALLOW", "result": "ERROR", "error": {"type": "folder_absent"}},
    )
    from imap_mcp import tracing

    assert span.attributes["error.type"] == "folder_absent"
    # Without the [tracing] extra there is no status type to set.
    assert len(span.statuses) == (1 if tracing._HAS_OTEL else 0)


def test_mode_off_leaves_payload_attributes_out() -> None:
    span = _record("fetch_envelope", {"decision": "ALLOW"}, mode="off")
    assert "mcp.request" not in span.attributes
    assert "mcp.response" not in span.attributes


# ------------------------------------------------------ traced client


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def has_capability(self, capability: str) -> bool:
        return capability == "MOVE"

    def __getattr__(self, name: str) -> Any:
        async def command(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args))
            return type("Response", (), {"result": "OK", "lines": []})()

        return command


class _SpanRecorder:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.attributes: list[dict[str, Any] | None] = []

    def start_as_current_span(self, name: str, *, attributes: Any = None) -> Any:
        from imap_mcp.tracing import _NoOpSpan

        self.names.append(name)
        self.attributes.append(attributes)
        return _NoOpSpan()


async def test_traced_client_maps_each_command_to_one_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from imap_mcp import tracing

    recorder = _SpanRecorder()
    monkeypatch.setattr(tracing, "_tracer", recorder)
    fake = _FakeClient()
    imap = TracedIMAP(fake)
    await imap.login("user", "pw-secret")
    await imap.select('"INBOX"')
    await imap.uid_search("FROM x@example.org")
    await imap.uid("fetch", "1:3", "(FLAGS)")
    await imap.uid("store", "1", "+FLAGS", "(\\Seen)")
    await imap.expunge()
    await imap.append(b"raw", mailbox='"Drafts"')
    await imap.logout()
    assert imap.has_capability("MOVE") is True
    assert recorder.names == [
        "imap.authenticate",
        "imap.select",
        "imap.uid_search",
        "imap.uid_fetch",
        "imap.uid_store",
        "imap.expunge",
        "imap.append",
        "imap.logout",
    ]
    assert recorder.attributes[0] == {"imap.auth_type": "password"}
    assert [name for name, _ in fake.calls] == [
        "login",
        "select",
        "uid_search",
        "uid",
        "uid",
        "expunge",
        "append",
        "logout",
    ]
    assert "pw-secret" not in repr(recorder.attributes)
