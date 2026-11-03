"""Harness output parsers on recorded-style samples (no CLI is started)."""

from __future__ import annotations

import json
from pathlib import Path

from usability.runner import USABILITY_DIR
from usability.runner.harness import DRIVERS, McpServerSpec, TurnResult
from usability.runner.harness.base import classify_failure, normalize_mcp_tool
from usability.runner.matrix import load_matrix

MATRIX = load_matrix(USABILITY_DIR / "matrix.yaml")
KNOWN = {"create_draft", "list_folders", "list_accounts"}


def _driver(name: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    work = tmp_path / "work"
    work.mkdir()
    driver = DRIVERS[name](MATRIX.harnesses[name], run_dir=tmp_path, workdir=work, log_dir=tmp_path / "log",
                           mcp=McpServerSpec("imap", "/bin/imap-mcp", ["--transport", "stdio"], {"A": "1"}),
                           model="m", known_tools=KNOWN)
    driver.prepare("Bitte HTML.")
    return driver


def _jsonl(*events: dict) -> str:  # type: ignore[type-arg]
    return "\n".join(json.dumps(e) for e in events)


def test_claude_parse_and_command(tmp_path: Path) -> None:
    d = _driver("claude-code", tmp_path)
    assert (tmp_path / "work" / "CLAUDE.md").read_text().startswith("Bitte HTML.")
    argv = d.command("Hallo", None, None)
    assert argv[:3] == ["claude", "--print", "Hallo"]
    assert "--strict-mcp-config" in argv and "--resume" not in argv
    assert argv.index("--disallowedTools") > argv.index("Hallo")
    assert "--resume" in d.command("Weiter", "sid", None)
    out = _jsonl(
        {"type": "system", "subtype": "init", "session_id": "sid", "mcp_servers": [{"name": "imap", "status": "connected"}]},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__imap__create_draft", "input": {"a": 1}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "ok"}]}]}},
        {"type": "result", "session_id": "sid", "result": "Fertig.", "total_cost_usd": 0.5,
         "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 1, "output_tokens": 7},
         "modelUsage": {"claude-x": {}}},
    )
    r = TurnResult()
    d.parse(out, "", r)
    assert r.final_text == "Fertig." and r.session_id == "sid" and r.model_resolved == "claude-x"
    assert [(c.tool, c.is_mcp, c.output) for c in r.tool_calls] == [("create_draft", True, "ok")]
    assert r.usage.input_tokens == 16 and r.usage.output_tokens == 7 and r.usage.cost_usd == 0.5
    assert d.breach(r) is None


def test_claude_breach_on_foreign_server(tmp_path: Path) -> None:
    d = _driver("claude-code", tmp_path)
    r = TurnResult()
    d.parse(_jsonl({"type": "system", "subtype": "init", "mcp_servers": [{"name": "imap"}, {"name": "imap-mcp"}]}), "", r)
    assert "imap-mcp" in (d.breach(r) or "")


def test_copilot_parse_and_resume(tmp_path: Path) -> None:
    d = _driver("copilot", tmp_path)
    sid = d.new_session_id()
    first = d.command("Hallo", sid, None)
    assert f"--session-id={sid}" in first and "--disallow-temp-dir" in first
    assert f"--resume={sid}" in d.command("Weiter", sid, None)
    assert d.env()["COPILOT_HOME"].endswith("copilot-home") and d.env()["PWD"].endswith("work")
    out = _jsonl(
        {"type": "session.mcp_servers_loaded", "data": {"servers": [{"name": "imap", "status": "connected"},
                                                                   {"name": "github-mcp-server", "status": "disabled"}]}},
        {"type": "tool.execution_start", "data": {"toolCallId": "1", "toolName": "imap-create_draft", "mcpServerName": "imap",
                                                  "mcpToolName": "create_draft", "arguments": {"x": 1}}},
        {"type": "tool.execution_complete", "data": {"toolCallId": "1", "success": True, "result": {"content": "ok"}}},
        {"type": "tool.execution_start", "data": {"toolCallId": "2", "toolName": "bash", "arguments": {"command": "ls"}}},
        {"type": "tool.execution_complete", "data": {"toolCallId": "2", "success": False, "error": {"message": "denied", "code": "denied"}}},
        {"type": "assistant.message", "data": {"content": "Fertig.", "toolRequests": []}},
    )
    r = TurnResult()
    d.parse(out, "", r)
    assert r.final_text == "Fertig."
    assert [(c.tool, c.is_mcp, c.error) for c in r.tool_calls] == [("create_draft", True, None), ("bash", False, "denied")]
    assert r.harness_denials == ["bash"]
    assert d.breach(r) is None


def test_opencode_parse_omits_model_on_resume(tmp_path: Path) -> None:
    d = _driver("opencode", tmp_path)
    assert "--model" in d.command("Hallo", None, None)
    resumed = d.command("Weiter", "ses_1", None)
    assert "--model" not in resumed and resumed[-3:] == ["--session", "ses_1", "Weiter"]
    out = _jsonl(
        {"type": "text", "sessionID": "ses_1", "part": {"text": "Ich lege an."}},
        {"type": "tool_use", "sessionID": "ses_1", "part": {"tool": "imap_create_draft", "state": {"status": "completed", "input": {}, "output": "ok"}}},
        {"type": "step_finish", "sessionID": "ses_1", "part": {"cost": 0.1, "tokens": {"input": 3, "output": 4, "reasoning": 1, "cache": {"read": 10, "write": 2}}}},
        {"type": "text", "sessionID": "ses_1", "part": {"text": "Fertig."}},
    )
    r = TurnResult()
    d.parse(out, "", r)
    assert r.final_text == "Fertig." and r.session_id == "ses_1"
    assert [(c.tool, c.is_mcp) for c in r.tool_calls] == [("create_draft", True)]
    assert r.usage.input_tokens == 15 and r.usage.output_tokens == 5
    assert d.breach(r) is None


def test_opencode_breach_on_code_mode_or_foreign_tool(tmp_path: Path) -> None:
    d = _driver("opencode", tmp_path)
    for tool in ("execute", "imap-mcp_create_draft", "notion_search"):
        r = TurnResult()
        d.parse(_jsonl({"type": "tool_use", "part": {"tool": tool, "state": {"status": "completed"}}}), "", r)
        assert d.breach(r), tool
    r = TurnResult()
    d.parse(_jsonl({"type": "tool_use", "part": {"tool": "shell", "state": {"status": "completed", "output": "x"}}}), "", r)
    assert "shell" in (d.breach(r) or "")


def test_antigravity_parse_and_home(tmp_path: Path) -> None:
    d = _driver("antigravity", tmp_path)
    cfg = json.loads((tmp_path / "harness" / "agy-home" / ".gemini" / "config" / "mcp_config.json").read_text())
    assert list(cfg["mcpServers"]) == ["imap"]
    argv = d.command("Hallo", None, {"type": "object"})
    assert argv[argv.index("--print") + 1] == "Hallo" and "--json-schema" in argv
    assert d.env()["HOME"].endswith("agy-home")
    out = _jsonl(
        {"event": "init", "conversation_id": "c1", "init": {"model": "m-resolved"}},
        {"event": "step_update", "step_update": {"step_index": 2, "state": "DONE", "step_type": "tool", "tool_name": "call_mcp_tool",
                                                 "tool_info": {"parameters": {"ServerName": "imap", "ToolName": "create_draft", "Arguments": {"a": 1}}, "output": "ok"}}},
        {"event": "step_update", "step_update": {"step_index": 3, "state": "ERROR", "step_type": "tool", "tool_name": "run_command",
                                                 "tool_info": {"parameters": {"CommandLine": "ls"}, "error": {"message": "Matches user-configured deny rule."}}}},
        {"event": "result", "result": {"conversation_id": "c1", "status": "SUCCESS", "response": "Fertig.",
                                       "usage": {"input_tokens": 9, "output_tokens": 2, "thinking_tokens": 1}}},
    )
    r = TurnResult()
    d.parse(out, "", r)
    assert r.final_text == "Fertig." and r.session_id == "c1" and r.model_resolved == "m-resolved"
    assert [(c.tool, c.is_mcp) for c in r.tool_calls] == [("create_draft", True), ("run_command", False)]
    assert r.usage.output_tokens == 3 and r.harness_denials == ["run_command"]
    assert d.breach(r) is None


def test_failure_classification_and_name_normalization() -> None:
    assert (classify_failure("The model claude-x is not available on your vertex deployment") or "").startswith("model_unavailable")
    assert (classify_failure("HTTP 429 Too Many Requests") or "").startswith("quota")
    assert (classify_failure("Not logged in · Please run /login") or "").startswith("auth")
    assert classify_failure("all fine") is None
    assert normalize_mcp_tool("mcp__imap__create_draft", KNOWN) == "create_draft"
    assert normalize_mcp_tool("imap-mcp_create_draft", KNOWN) is None
    assert normalize_mcp_tool("imap_list_folders", KNOWN) == "list_folders"
