"""Builders for recorded-style sample evidence (no agents, no servers)."""

from __future__ import annotations

import json
from typing import Any

from usability.runner import USABILITY_DIR
from usability.runner.evidence import Evidence, TranscriptTurn
from usability.runner.features import load_features
from usability.runner.fixtures import GmailState, build_mailbox, seed_state, snapshot
from usability.runner.harness.base import ToolCall, TurnResult
from usability.runner.simulator import SimulatorAnswer

FEATURES = load_features(USABILITY_DIR / "features")


def mailbox_after(scenario: str, mutate: Any = None) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
    """Seed the scenario's fixture, apply `mutate(state)`, return (fixture, start, end)."""
    spec = FEATURES.scenarios[scenario]
    fixture = build_mailbox(spec)
    state = GmailState()
    seed_state(state, fixture)
    start = snapshot(state)
    if mutate is not None:
        mutate(state)
    return fixture, start, snapshot(state)


def append_draft(state: GmailState, to: str, subject: str, *, html: bool, body: str = "Hallo,\n\nTest.\n") -> None:
    from mock_gmail.state import Message

    if html:
        raw = (
            f"From: inhaber@example.org\r\nTo: {to}\r\nSubject: {subject}\r\nMessage-ID: <d-{subject}@x>\r\n"
            "MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=\"b\"\r\n\r\n"
            f"--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n"
            "--b\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>Hallo</p>\r\n--b--\r\n"
        )
    else:
        raw = (
            f"From: inhaber@example.org\r\nTo: {to}\r\nSubject: {subject}\r\nMessage-ID: <d-{subject}@x>\r\n"
            f"MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}"
        )
    msgid = state.new_msgid()
    state.add_message(Message(gm_msgid=msgid, gm_thrid=msgid, rfc822=raw.encode(), labels={"\\Drafts"},
                              message_id=f"<d-{subject}@x>", from_addr="inhaber@example.org", to_addr=to,
                              subject=subject))


def turn(index: int, final_text: str, calls: list[tuple[str, bool]] | None = None, *, source: str = "task",
         simulator_topics: list[str] | None = None) -> TranscriptTurn:
    result = TurnResult(session_id="s", final_text=final_text,
                        tool_calls=[ToolCall(name=n, tool=n, is_mcp=m) for n, m in (calls or [])])
    sim = SimulatorAnswer(simulator_topics, "Bitte als HTML.", final_text) if simulator_topics is not None else None
    return TranscriptTurn(index, source, "prompt", result, sim)


def audit(*records: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i, r in enumerate(records):
        out.append({"seq": i, "caller_id": "usability-agent", "decision": "ALLOW", "result": "OK", **r})
    return out


def span(tool: str, request: dict[str, Any], response: Any, *, start: int, dur_ms: int = 10,
         size: int | None = None, name: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    body = json.dumps(response) if response is not None else None
    attrs: dict[str, Any] = {"mcp.tool": tool, "mcp.request": json.dumps(request),
                             "mcp.response.size_bytes": size if size is not None else len(body or "")}
    if body is not None:
        attrs["mcp.response"] = body
    attrs.update(extra or {})
    return {"name": name or f"tool.{tool}", "start_time_unix_nano": start * 1_000_000,
            "end_time_unix_nano": (start + dur_ms) * 1_000_000, "attributes": attrs,
            "resource_attributes": {"run.id": "r"}}


def evidence(scenario: str, *, mutate: Any = None, turns: list[TranscriptTurn] | None = None,
             audit_records: list[dict[str, Any]] | None = None, spans: list[dict[str, Any]] | None = None,
             sink_files: list[dict[str, Any]] | None = None) -> Evidence:
    fixture, start, end = mailbox_after(scenario, mutate)
    return Evidence(
        run_id="r", scenario=FEATURES.scenarios[scenario], fixture=fixture, audit=audit_records or [],
        spans=spans or [], mailbox_start=start, mailbox_end=end, turns=turns or [], sink_dir="/sink",
        sink_files=sink_files or [], wall_clock_seconds=12.5,
        known_folders={"INBOX", "[Gmail]/Entwürfe", "[Gmail]/All Mail", "INBOX/Rechnungseingänge"},
    )
