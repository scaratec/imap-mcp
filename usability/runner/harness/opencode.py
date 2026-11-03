"""OpenCode: `opencode run --format json` via the background service, per-run project config."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from .base import (
    MCP_SERVER_NAME,
    HarnessDriver,
    IsolationBreach,
    ToolCall,
    TurnResult,
    Usage,
    classify_failure,
    load_jsonl,
    normalize_mcp_tool,
    write_instructions,
)

# Last matching rule wins: deny everything, then allow the run's MCP
# server and read-only file tools inside the working directory.
PERMISSIONS = [
    {"action": "*", "resource": "*", "effect": "deny"},
    {"action": f"{MCP_SERVER_NAME}_*", "resource": "*", "effect": "allow"},
    {"action": "read", "resource": "*", "effect": "allow"},
    {"action": "glob", "resource": "*", "effect": "allow"},
    {"action": "grep", "resource": "*", "effect": "allow"},
    {"action": "external_directory", "resource": "*", "effect": "deny"},
]


# OpenCode built-in tools (V2 names); anything else that is not imap_* is
# a foreign MCP tool.
BUILTIN_TOOLS = frozenset({
    "read", "glob", "grep", "shell", "bash", "edit", "write", "patch", "webfetch", "websearch",
    "question", "subagent", "task", "skill", "todowrite", "todoread", "list", "lsp", "invalid",
})
MCP_WARMUP_ATTEMPTS = 12
MCP_WARMUP_PAUSE = 5.0
# Always disabled by name: the server name the owner uses for the real
# imap-mcp in his global OpenCode config (observed 2026-11-03).
ALWAYS_DISABLE = ("imap-mcp",)


def _server_names(config: Any) -> set[str]:
    """MCP server names in one config document (V2 `mcp.servers` or V1 `mcp.<name>`)."""
    if not isinstance(config, dict):
        return set()
    mcp = config.get("mcp")
    if not isinstance(mcp, dict):
        return set()
    if isinstance(mcp.get("servers"), dict):
        return set(mcp["servers"])
    return {k for k, v in mcp.items() if isinstance(v, dict) and k not in ("timeout",)}


class OpenCodeDriver(HarnessDriver):
    disabled_servers: list[str]
    # The github-copilot provider intermittently answers HTTP 401
    # ("token authentication failed") and the next request succeeds.
    transient_auth_retry = True

    restrictions = [
        "Per-run project config (opencode.json in the working directory) adds the run's MCP server "
        "with codemode=false, so its tools are native tools named imap_<tool>.",
        "Permissions: deny '*' first, then allow imap_*, read, glob, grep; external_directory "
        "denied. Shell, edit/write, webfetch, websearch, question, subagents and Code Mode "
        "(execute) are denied, which also hides MCP servers delivered by the user's global config "
        "or a connected Console/integration (they cannot be removed by a project policy).",
        "Turns run through the user's OpenCode background service; the preflight warms up the "
        "service's MCP connection for the run directory and requires `opencode mcp list` to show "
        "exactly one connected server, the run's imap (foreign ones listed as disabled).",
        "PWD is set to the working directory: OpenCode takes its project directory from PWD.",
        "Preflight: `opencode debug config` must list the per-run opencode.json; every MCP server "
        "named in any other config source (plus 'imap-mcp') is overridden as disabled.",
        "Runtime guard: execute/Code Mode, a foreign tool, or a shell/write call that ran aborts the "
        "run as not-assessable (isolation_breach).",
        "Global config (~/.config/opencode) cannot be isolated: the github-copilot provider login "
        "lives under ~/.config, so XDG_CONFIG_HOME cannot be redirected; global instructions "
        "(AGENTS.md) would still load.",
    ]

    def _config(self, disabled: set[str]) -> dict[str, Any]:
        servers: dict[str, Any] = {
            name: {"type": "local", "command": ["false"], "disabled": True}
            for name in sorted(disabled) if name != MCP_SERVER_NAME
        }
        servers[MCP_SERVER_NAME] = {
            "type": "local",
            "command": [self.mcp.command, *self.mcp.args],
            "environment": self.mcp.env,
            "codemode": False,
        }
        return {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {"servers": servers},
            "permissions": PERMISSIONS,
            "snapshots": False,
        }

    def _config_sources(self, directory: Path) -> list[dict[str, Any]]:
        """`opencode debug config` as seen from `directory`.

        Parsed in memory only: the documents can hold the user's own
        settings; nothing but paths and MCP server names is kept.
        """
        try:
            out = subprocess.run(
                [self.cfg.binary, "debug", "config"], cwd=directory,
                env={**self.env(), "PWD": str(directory)},
                capture_output=True, text=True, timeout=90,
            )
            data = json.loads(out.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            raise IsolationBreach(f"cannot verify OpenCode config sources: {exc!r}") from exc
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []

    def preflight(self) -> None:
        # 1. Which MCP servers would a session here see without our config?
        #    Asked from an empty sibling directory (same ancestors), because
        #    OpenCode's service caches a directory's config after a query.
        probe = self.run_dir / "opencode-probe"
        probe.mkdir(parents=True, exist_ok=True)
        foreign = set(ALWAYS_DISABLE)
        for doc in self._config_sources(probe):
            foreign |= _server_names(doc.get("info"))
        foreign.discard(MCP_SERVER_NAME)
        self.disabled_servers = sorted(foreign)
        # 2. Write the final config, then verify it is loaded and disables them all.
        own = str(self.workdir / "opencode.json")
        (self.workdir / "opencode.json").write_text(
            json.dumps(self._config(foreign), indent=1), encoding="utf-8"
        )
        sources = self._config_sources(self.workdir)
        own_doc = next((d for d in sources if str(d.get("path")) == own), None)
        if own_doc is None:
            raise IsolationBreach(
                f"per-run OpenCode config not loaded (sources: {[d.get('path') for d in sources]})"
            )
        missing = foreign - _server_names(own_doc.get("info"))
        if missing:
            raise IsolationBreach(f"per-run OpenCode config does not disable {len(missing)} foreign MCP servers")
        # 3. Warm up the service's MCP connection for this directory and check
        #    that only the run's server is connected (no model call).
        status: dict[str, str] = {}
        for _attempt in range(MCP_WARMUP_ATTEMPTS):
            status = self._mcp_status()
            if status.get(MCP_SERVER_NAME) == "connected":
                break
            time.sleep(MCP_WARMUP_PAUSE)
        connected = sorted(n for n, s in status.items() if s == "connected")
        if connected != [MCP_SERVER_NAME]:
            raise IsolationBreach(f"OpenCode MCP servers connected for the run directory: {connected or 'none'}")

    def _mcp_status(self) -> dict[str, str]:
        try:
            out = subprocess.run(
                [self.cfg.binary, "mcp", "list"], cwd=self.workdir, env=self.env(),
                capture_output=True, text=True, timeout=90,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        status: dict[str, str] = {}
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and not parts[0].isalnum():
                status[parts[1]] = parts[2]
        return status

    def breach(self, result: TurnResult) -> str | None:
        for call in result.tool_calls:
            if call.is_mcp:
                continue
            if call.name == "execute" or call.name.startswith("opencode_"):
                return f"{call.name} ran (Code Mode / OpenCode tools are denied in the run config)"
            if call.name not in BUILTIN_TOOLS:
                return f"call to foreign tool {call.name}"
            if call.name in ("shell", "bash", "edit", "write", "patch") and not call.error:
                return f"{call.name} ran although denied"
        return None

    def prepare(self, instructions: str | None) -> None:
        # The config itself is written by preflight(), once the foreign
        # server names are known (a directory's config is cached by
        # OpenCode's service after the first query).
        self.disabled_servers = []
        write_instructions(self.workdir, self.cfg.instructions_file, instructions)

    def command(
        self, prompt: str, session_id: str | None, json_schema: dict[str, Any] | None
    ) -> list[str]:
        argv = self.base_argv(prompt)
        if session_id:
            # The session keeps the model of its first turn; `--session <id>
            # --model <m>` was seen failing with HTTP 401 from the
            # github-copilot provider (opencode 2.0.29, 2026-11-03).
            argv += [self.cfg.resume_flag, session_id]
        else:
            argv += [self.cfg.model_flag, self.model]
        argv.append(prompt)
        return argv

    def parse(self, stdout: str, stderr: str, result: TurnResult) -> None:
        usage = Usage()
        texts_since_tool: list[str] = []
        for event in load_jsonl(stdout):
            kind = event.get("type")
            result.session_id = event.get("sessionID") or result.session_id
            part = event.get("part") or {}
            if kind == "text":
                texts_since_tool.append(str(part.get("text") or ""))
            elif kind == "tool_use":
                name = str(part.get("tool"))
                state = part.get("state") or {}
                tool = normalize_mcp_tool(name, self.known_tools)
                call = ToolCall(name=name, tool=tool or name, is_mcp=tool is not None,
                                input=state.get("input"))
                if state.get("status") == "error":
                    call.error = str(state.get("error") or state.get("output"))[:2000]
                    if "denied" in (call.error or "").lower() or "policy" in (call.error or "").lower():
                        result.harness_denials.append(name)
                else:
                    call.output = str(state.get("output"))
                result.tool_calls.append(call)
                texts_since_tool = []
            elif kind == "step_finish":
                tokens = part.get("tokens") or {}
                cache = tokens.get("cache") or {}
                usage.add(
                    Usage(
                        input_tokens=int(tokens.get("input") or 0) + int(cache.get("read") or 0)
                        + int(cache.get("write") or 0),
                        output_tokens=int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0),
                        cache_read_tokens=int(cache.get("read") or 0),
                        cache_write_tokens=int(cache.get("write") or 0),
                        cost_usd=float(part.get("cost") or 0.0),
                    )
                )
            elif kind == "error":
                error = event.get("error") or {}
                message = json.dumps(error)[:600]
                result.error = classify_failure(message) or f"crash: {message[:300]}"
        result.final_text = "\n".join(t for t in texts_since_tool if t.strip())
        result.usage = usage
        # `opencode run --format json` reports no resolved model id.
        result.model_resolved = None
