"""Antigravity CLI: `agy --output-format stream-json --print <prompt>` with a per-run HOME."""

from __future__ import annotations

import json
from typing import Any

from .base import (
    MCP_SERVER_NAME,
    HarnessDriver,
    ToolCall,
    TurnResult,
    Usage,
    classify_failure,
    harness_env,
    load_jsonl,
    write_instructions,
)

# Applied with --dangerously-skip-permissions: deny rules still win and
# a denied tool returns an error to the agent instead of ending the turn
# (without the flag, headless agy aborts the whole turn on the first
# tool that needs a confirmation).
SETTINGS = {"permissions": {"deny": ["command(*)", "unsandboxed(*)", "write_file(*)"]}}


class AntigravityDriver(HarnessDriver):
    restrictions = [
        "Per-run HOME: ~/.gemini (mcp_config.json, settings, GEMINI.md, plugins, skills) is fresh "
        "and holds only the run's MCP server; the login comes from the system keyring.",
        "--dangerously-skip-permissions with session settings denying command(*), unsandboxed(*) "
        "and write_file(*): shell commands and file writes fail with a deny error.",
        "Not restricted (no verified rule names): browser tools, read_url_content/search_web, "
        "subagents, file reads, and the ask_question tool: headless agy skips it unanswered and "
        "stream-json reports only an undescribed step (recorded as agy_unknown_step), so the "
        "simulator cannot answer it.",
        "Runtime guard: a call to another MCP server or a shell/write call that was not denied "
        "aborts the run as not-assessable (isolation_breach).",
    ]

    def prepare(self, instructions: str | None) -> None:
        self.home = self.run_dir / "harness" / "agy-home"
        config_dir = self.home / ".gemini" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        server = {
            "command": self.mcp.command,
            "args": self.mcp.args,
            "disabled": False,
            "env": self.mcp.env,
        }
        (config_dir / "mcp_config.json").write_text(
            json.dumps({"mcpServers": {MCP_SERVER_NAME: server}}, indent=1), encoding="utf-8"
        )
        self.schema_file = self.run_dir / "harness" / "agy-feedback-schema.json"
        write_instructions(self.workdir, self.cfg.instructions_file, instructions)

    def env(self) -> dict[str, str]:
        home = getattr(self, "home", None)
        return harness_env(self.workdir, {"HOME": str(home)} if home else None)

    def breach(self, result: TurnResult) -> str | None:
        for call in result.tool_calls:
            if call.name == "call_mcp_tool" and not call.is_mcp:
                server = (call.input or {}).get("ServerName") if isinstance(call.input, dict) else None
                if server and server != MCP_SERVER_NAME:
                    return f"call to foreign MCP server {server!r}"
            if call.category in ("shell", "write") and not call.error:
                return f"{call.name} ran although denied"
        return None

    def command(
        self, prompt: str, session_id: str | None, json_schema: dict[str, Any] | None
    ) -> list[str]:
        argv = self.base_argv(prompt)
        argv += [self.cfg.model_flag, self.model, "--settings", json.dumps(SETTINGS)]
        if session_id:
            argv += [self.cfg.resume_flag, session_id]
        if json_schema is not None and self.supports_structured_output():
            self.schema_file.write_text(json.dumps(json_schema), encoding="utf-8")
            argv += [self.cfg.structured_output, str(self.schema_file)]
        return argv

    def parse(self, stdout: str, stderr: str, result: TurnResult) -> None:
        calls: dict[int, ToolCall] = {}
        for event in load_jsonl(stdout):
            kind = event.get("event")
            if kind == "init":
                init = event.get("init") or {}
                result.session_id = event.get("conversation_id") or result.session_id
                result.model_resolved = init.get("model")
            elif kind == "step_update":
                step = event.get("step_update") or {}
                if step.get("step_type") == "unknown" and step.get("state") == "DONE":
                    # Steps agy does not describe in stream-json, e.g. the
                    # ask_question tool, which headless mode skips unanswered.
                    calls[int(step.get("step_index", len(calls)))] = ToolCall(
                        name="agy_unknown_step", tool="agy_unknown_step", is_mcp=False)
                    continue
                if step.get("step_type") != "tool" or step.get("state") not in ("DONE", "ERROR"):
                    continue
                info = step.get("tool_info") or {}
                params = info.get("parameters") or {}
                name = str(step.get("tool_name") or info.get("name"))
                is_mcp = name == "call_mcp_tool" and params.get("ServerName") == MCP_SERVER_NAME
                tool = str(params.get("ToolName")) if is_mcp else name
                call = ToolCall(
                    name=name,
                    tool=tool,
                    is_mcp=is_mcp and tool in self.known_tools,
                    input=params.get("Arguments") if is_mcp else params,
                    output=None if info.get("output") is None else str(info.get("output")),
                )
                error = info.get("error")
                if step.get("state") == "ERROR" or error:
                    call.error = str((error or {}).get("message") if isinstance(error, dict) else error)[:2000]
                    if "deny rule" in (call.error or ""):
                        result.harness_denials.append(name)
                calls[int(step.get("step_index", len(calls)))] = call
            elif kind == "result":
                res = event.get("result") or {}
                result.session_id = res.get("conversation_id") or result.session_id
                result.final_text = str(res.get("response") or "")
                usage = res.get("usage") or {}
                result.usage = Usage(
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=(usage.get("output_tokens") or 0) + (usage.get("thinking_tokens") or 0)
                    if usage else None,
                    cache_read_tokens=usage.get("cache_read_tokens"),
                )
                for denied in res.get("denied_actions") or []:
                    result.harness_denials.append(str(denied.get("action")))
                if res.get("status") not in (None, "SUCCESS"):
                    result.error = classify_failure(json.dumps(res), stderr) or f"crash: status {res.get('status')}"
        result.tool_calls = [calls[k] for k in sorted(calls)]
        if "AGY_ERROR" in stderr and not result.final_text:
            result.error = classify_failure(stderr) or f"crash: {stderr.strip()[-300:]}"
