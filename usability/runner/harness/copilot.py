"""GitHub Copilot CLI: `copilot --prompt ... --output-format json`."""

from __future__ import annotations

import json
import uuid
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
    normalize_mcp_tool,
    write_instructions,
)

DENY = ["shell", "write", "url"]
# Keep the agent out of the system temp dir, where the server config of
# this and other runs lives (the working directory stays accessible).
EXTRA = ["--disallow-temp-dir"]


class CopilotDriver(HarnessDriver):
    restrictions = [
        "MCP: --additional-mcp-config @<run file>; --disable-builtin-mcps removes the GitHub MCP "
        "servers; a per-run COPILOT_HOME holds no user MCP config, skills, plugins or instructions "
        "(login comes from the system keyring and still works).",
        "Built-in tools denied: --deny-tool shell, write, url (deny beats --allow-all-tools). The "
        "tools stay visible to the model; attempts are recorded as denials.",
        "--no-ask-user removes the ask_user tool: questions end the turn as text.",
        "Runtime guard: another connected MCP server, a call to it, or a shell/write call that was "
        "not denied aborts the run as not-assessable (isolation_breach).",
        "File access is limited to the working directory; --disallow-temp-dir removes the default "
        "access to the system temp dir (where the run's server config lives).",
    ]

    def prepare(self, instructions: str | None) -> None:
        harness_dir = self.run_dir / "harness"
        self.copilot_home = harness_dir / "copilot-home"
        self.copilot_home.mkdir(parents=True, exist_ok=True)
        server = {
            "type": "local",
            "command": self.mcp.command,
            "args": self.mcp.args,
            "env": self.mcp.env,
            "tools": ["*"],
        }
        self.mcp_file = harness_dir / "copilot-mcp.json"
        self.mcp_file.write_text(
            json.dumps({"mcpServers": {MCP_SERVER_NAME: server}}, indent=1), encoding="utf-8"
        )
        self.usage_files: list[str] = []
        write_instructions(self.workdir, self.cfg.instructions_file, instructions)

    def env(self) -> dict[str, str]:
        extra = {"COPILOT_AUTO_UPDATE": "false"}
        home = getattr(self, "copilot_home", None)
        if home is not None:
            extra["COPILOT_HOME"] = str(home)
        return harness_env(self.workdir, extra)

    def breach(self, result: TurnResult) -> str | None:
        foreign = [s for s in self._connected if s != MCP_SERVER_NAME]
        if foreign:
            return f"foreign MCP servers connected: {foreign}"
        for call in result.tool_calls:
            if call.category in ("shell", "write") and not call.error:
                return f"{call.name} ran although denied"
            if not call.is_mcp and call.name.startswith(tuple(f"{s}-" for s in self._connected if s != MCP_SERVER_NAME)):
                return f"call to foreign MCP tool {call.name}"
        return None

    _connected: list[str] = []

    def new_session_id(self) -> str | None:
        return str(uuid.uuid4())

    def command(
        self, prompt: str, session_id: str | None, json_schema: dict[str, Any] | None
    ) -> list[str]:
        argv = self.base_argv(prompt)
        argv += [self.cfg.model_flag, self.model]
        argv += [self.cfg.mcp_isolation, f"@{self.mcp_file}"]
        for rule in DENY:
            argv += ["--deny-tool", rule]
        argv += EXTRA
        if session_id and self._started:
            argv.append(f"{self.cfg.resume_flag}={session_id}")
        elif session_id:
            argv.append(f"--session-id={session_id}")
        self._usage_file = self.log_dir / f"copilot-usage-{len(self.usage_files):02d}.json"
        self.usage_files.append(str(self._usage_file))
        argv += ["--usage-output-file", str(self._usage_file)]
        self._started = True
        return argv

    _started = False

    def parse(self, stdout: str, stderr: str, result: TurnResult) -> None:
        calls: dict[str, ToolCall] = {}
        last_text = ""
        for event in load_jsonl(stdout):
            kind = str(event.get("type"))
            data = event.get("data") or {}
            if kind == "session.mcp_server_status_changed" and data.get("serverName") == MCP_SERVER_NAME:
                result.mcp_status = f"{MCP_SERVER_NAME}:{data.get('status')}"
            elif kind == "session.mcp_servers_loaded":
                self._connected = [
                    str(s.get("name")) for s in data.get("servers") or []
                    if isinstance(s, dict) and s.get("status") == "connected"
                ]
            elif kind == "assistant.message":
                content = data.get("content")
                if isinstance(content, str) and content.strip():
                    last_text = content
                if data.get("model"):
                    result.model_resolved = str(data.get("model"))
            elif kind == "tool.execution_start":
                name = str(data.get("toolName"))
                if data.get("mcpServerName") == MCP_SERVER_NAME and data.get("mcpToolName"):
                    tool: str | None = str(data.get("mcpToolName"))
                elif data.get("mcpServerName"):
                    tool = None
                    self._connected = [*self._connected, str(data.get("mcpServerName"))]
                else:
                    tool = normalize_mcp_tool(name, self.known_tools)
                call = ToolCall(name=name, tool=tool or name, is_mcp=tool is not None,
                                input=data.get("arguments"))
                calls[str(data.get("toolCallId"))] = call
                result.tool_calls.append(call)
            elif kind == "tool.execution_complete":
                done = calls.get(str(data.get("toolCallId")))
                if done is None:
                    continue
                call = done
                if data.get("success"):
                    res = data.get("result") or {}
                    call.output = str(res.get("content") if isinstance(res, dict) else res)
                else:
                    err = data.get("error") or {}
                    call.error = str(err.get("message") if isinstance(err, dict) else err)[:2000]
                    if isinstance(err, dict) and err.get("code") == "denied":
                        result.harness_denials.append(call.name)
            elif kind in ("session.error", "error") or kind.endswith(".error"):
                message = json.dumps(data)[:600]
                result.error = classify_failure(message) or f"crash: {message[:300]}"
        result.final_text = last_text
        usage_path = getattr(self, "_usage_file", None)
        if usage_path is not None and usage_path.exists():
            try:
                usage = json.loads(usage_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                usage = {}
            details = usage.get("tokenDetails") or {}

            def count(key: str) -> int | None:
                value = (details.get(key) or {}).get("tokenCount")
                return int(value) if isinstance(value, (int, float)) else None

            inp, cr, cw = count("input"), count("cache_read"), count("cache_write")
            result.usage = Usage(
                input_tokens=sum(v for v in (inp, cr, cw) if v is not None) if inp is not None else None,
                output_tokens=count("output"),
                cache_read_tokens=cr,
                cache_write_tokens=cw,
                premium_requests=usage.get("totalPremiumRequestCost"),
            )
            models = list((usage.get("modelMetrics") or {}).keys())
            if models:
                result.model_resolved = ",".join(models)
