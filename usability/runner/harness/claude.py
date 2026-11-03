"""Claude Code: `claude --print ... --output-format stream-json`."""

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
    load_jsonl,
    normalize_mcp_tool,
    write_instructions,
)

# Built-in tools removed from the session. Read/Glob/Grep stay; in
# `dontAsk` mode anything not pre-approved (including Read outside the
# allow list) is denied instead of prompting.
DISALLOWED = [
    "Bash", "PowerShell", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch",
    "AskUserQuestion", "Task", "Workflow", "CronCreate", "CronDelete", "ScheduleWakeup",
    "EnterWorktree", "ExitWorktree", "RemoteTrigger",
]


class ClaudeCodeDriver(HarnessDriver):
    restrictions = [
        "MCP: only the run's server (--mcp-config <run file> --strict-mcp-config).",
        "Built-in tools removed: " + ", ".join(DISALLOWED) + " (--disallowedTools).",
        "Permission mode dontAsk: only mcp__imap is pre-approved; any other tool call that would "
        "prompt (e.g. Read outside the allow list) is denied.",
        "Settings: --setting-sources user (needed: the Vertex login lives in user settings); project "
        "and local settings are skipped. User-level plugins/skills may still load.",
        "Runtime guard: any other MCP server in the session, a call to it, or a shell/write call "
        "aborts the run as not-assessable (isolation_breach).",
        "HOME is not isolated (OAuth/Vertex login state lives in ~/.claude); ~/.claude/CLAUDE.md "
        "would be loaded if it existed (it does not on the reference machine).",
    ]

    _servers: list[str] = []

    def breach(self, result: TurnResult) -> str | None:
        foreign = [s for s in self._servers if s != MCP_SERVER_NAME]
        if foreign:
            return f"foreign MCP servers in session: {foreign}"
        for call in result.tool_calls:
            if call.name.startswith("mcp__") and not call.name.startswith(f"mcp__{MCP_SERVER_NAME}__"):
                return f"call to foreign MCP tool {call.name}"
            if call.category in ("shell", "write") and not call.error:
                return f"{call.name} ran although removed"
        return None

    def prepare(self, instructions: str | None) -> None:
        server = {
            "type": "stdio",
            "command": self.mcp.command,
            "args": self.mcp.args,
            "env": self.mcp.env,
        }
        self.mcp_file = self.run_dir / "harness" / "claude-mcp.json"
        self.mcp_file.parent.mkdir(parents=True, exist_ok=True)
        self.mcp_file.write_text(
            json.dumps({"mcpServers": {MCP_SERVER_NAME: server}}, indent=1), encoding="utf-8"
        )
        write_instructions(self.workdir, self.cfg.instructions_file, instructions)

    def command(
        self, prompt: str, session_id: str | None, json_schema: dict[str, Any] | None
    ) -> list[str]:
        argv = self.base_argv(prompt)
        argv += [self.cfg.model_flag, self.model]
        argv += self.cfg.mcp_isolation.replace("{file}", str(self.mcp_file)).split()
        argv += ["--setting-sources", "user", "--allowedTools", f"mcp__{MCP_SERVER_NAME}"]
        if session_id:
            argv += [self.cfg.resume_flag, session_id]
        argv += ["--disallowedTools", *DISALLOWED]
        return argv

    def parse(self, stdout: str, stderr: str, result: TurnResult) -> None:
        pending: dict[str, ToolCall] = {}
        texts: list[str] = []
        for event in load_jsonl(stdout):
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                result.session_id = event.get("session_id") or result.session_id
                servers = event.get("mcp_servers") or []
                result.mcp_status = ",".join(f"{s.get('name')}:{s.get('status')}" for s in servers)
                self._servers = [str(s.get("name")) for s in servers]
            elif kind == "assistant":
                for block in (event.get("message") or {}).get("content") or []:
                    if block.get("type") == "tool_use":
                        name = str(block.get("name"))
                        tool = normalize_mcp_tool(name, self.known_tools)
                        call = ToolCall(name=name, tool=tool or name, is_mcp=tool is not None,
                                        input=block.get("input"))
                        pending[str(block.get("id"))] = call
                        result.tool_calls.append(call)
                        texts = []
                    elif block.get("type") == "text":
                        texts.append(str(block.get("text") or ""))
            elif kind == "user":
                content = (event.get("message") or {}).get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            found = pending.get(str(block.get("tool_use_id")))
                            if found is None:
                                continue
                            call = found
                            output = block.get("content")
                            if isinstance(output, list):
                                output = "\n".join(
                                    str(b.get("text", "")) for b in output if isinstance(b, dict)
                                )
                            if block.get("is_error"):
                                call.error = str(output)[:2000]
                            else:
                                call.output = str(output)
            elif kind == "result":
                result.session_id = event.get("session_id") or result.session_id
                final = event.get("result")
                if isinstance(final, str):
                    result.final_text = final
                usage = event.get("usage") or {}
                result.usage = Usage(
                    input_tokens=_sum(usage, "input_tokens", "cache_creation_input_tokens",
                                      "cache_read_input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    cache_read_tokens=usage.get("cache_read_input_tokens"),
                    cache_write_tokens=usage.get("cache_creation_input_tokens"),
                    cost_usd=event.get("total_cost_usd"),
                )
                models = list((event.get("modelUsage") or {}).keys())
                if models:
                    result.model_resolved = ",".join(models)
                for denial in event.get("permission_denials") or []:
                    result.harness_denials.append(str(denial.get("tool_name")))
                if event.get("is_error"):
                    result.error = classify_failure(str(final), stderr) or f"crash: {str(final)[:300]}"
        if not result.final_text and texts:
            result.final_text = "\n".join(texts)


def _sum(mapping: dict[str, Any], *keys: str) -> int | None:
    values = [v for k in keys if isinstance(v := mapping.get(k), int)]
    return sum(values) if values else None
