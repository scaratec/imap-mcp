"""Common driver machinery: one headless harness turn = one subprocess."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..matrix import HarnessConfig

MCP_SERVER_NAME = "imap"

# Built-in tools that run commands or write files, per harness naming.
SHELL_TOOLS = frozenset(
    {"Bash", "bash", "run_command", "shell", "PowerShell", "send_command_input", "execute_command"}
)
WRITE_TOOLS = frozenset(
    {
        "Write", "Edit", "MultiEdit", "NotebookEdit", "create", "edit", "write", "patch",
        "write_file", "write_to_file", "replace_file_content", "multi_replace_file_content",
        "sed_file", "notebook_edit",
    }
)

_ENV_DROP_PREFIXES = ("OTEL_", "IMAP_MCP_")

_FAILURE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("model_unavailable", re.compile(
        r"model\s+\S+\s+is not available|not available on your \w+ deployment|unknown model|"
        r"invalid model|model[^\n]{0,40}not (?:found|supported)|unsupported model", re.IGNORECASE)),
    ("quota", re.compile(
        r"quota|rate.?limit|\b429\b|RESOURCE_EXHAUSTED|premium request|insufficient (?:credits|balance)|"
        r"usage limit|too many requests", re.IGNORECASE)),
    ("auth", re.compile(
        r"not logged in|please run /login|unauthori[sz]ed|\b401\b|authentication (?:failed|required)|"
        r"login required|token authentication failed|no credentials", re.IGNORECASE)),
]


@dataclass
class McpServerSpec:
    name: str
    command: str
    args: list[str]
    env: dict[str, str]


@dataclass
class ToolCall:
    name: str
    tool: str
    is_mcp: bool
    input: Any = None
    output: str | None = None
    error: str | None = None

    @property
    def category(self) -> str:
        if self.is_mcp:
            return "mcp"
        if self.name in SHELL_TOOLS:
            return "shell"
        if self.name in WRITE_TOOLS:
            return "write"
        return "builtin"


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None
    premium_requests: float | None = None

    def add(self, other: Usage) -> None:
        for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                     "cost_usd", "premium_requests"):
            mine, theirs = getattr(self, name), getattr(other, name)
            if theirs is None:
                continue
            setattr(self, name, theirs if mine is None else mine + theirs)

    @property
    def known(self) -> bool:
        return self.input_tokens is not None or self.output_tokens is not None


@dataclass
class TurnResult:
    session_id: str | None = None
    final_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model_resolved: str | None = None
    exit_code: int | None = None
    duration_s: float = 0.0
    error: str | None = None
    timed_out: bool = False
    stdout_path: str | None = None
    stderr_path: str | None = None
    harness_denials: list[str] = field(default_factory=list)
    mcp_status: str | None = None
    argv: list[str] = field(default_factory=list)
    retried_after: str | None = None

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["tool_calls"] = [
            {**asdict(tc), "output": (tc.output or "")[:2000] if tc.output else tc.output}
            for tc in self.tool_calls
        ]
        return data


def harness_env(workdir: Path | None, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Inherited environment without telemetry/server variables (they belong to the MCP server).

    PWD is set to the working directory: some harnesses (OpenCode) take
    their project directory from PWD rather than from the process cwd,
    and an inherited PWD would put them into the runner's directory with
    its config and the user's own MCP servers.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(_ENV_DROP_PREFIXES)}
    if workdir is not None:
        env["PWD"] = str(workdir)
    if extra:
        env.update(extra)
    return env


class IsolationBreach(Exception):
    """The harness reached something outside the run's sandbox (foreign MCP server, shell)."""


def classify_failure(*texts: str) -> str | None:
    blob = "\n".join(t for t in texts if t)
    for kind, pattern in _FAILURE_PATTERNS:
        m = pattern.search(blob)
        if m:
            start = max(0, m.start() - 80)
            snippet = " ".join(blob[start : m.end() + 120].split())
            return f"{kind}: {snippet[:300]}"
    return None


def normalize_mcp_tool(name: str, known: set[str]) -> str | None:
    """Harness tool name → server tool name, or None when it is not an imap-mcp tool."""
    for prefix in (f"mcp__{MCP_SERVER_NAME}__", f"{MCP_SERVER_NAME}-", f"{MCP_SERVER_NAME}_",
                   f"{MCP_SERVER_NAME}/", f"{MCP_SERVER_NAME}."):
        if name.startswith(prefix) and name[len(prefix):] in known:
            return name[len(prefix):]
    return None


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


class HarnessDriver(ABC):
    """Builds the headless command for one harness and parses its output."""

    #: What could and could not be restricted, for the run record and README.
    restrictions: list[str] = []
    #: Retry a turn once when it failed with an auth error before any tool ran.
    transient_auth_retry = False

    def __init__(
        self,
        cfg: HarnessConfig,
        *,
        run_dir: Path,
        workdir: Path,
        log_dir: Path,
        mcp: McpServerSpec,
        model: str,
        known_tools: set[str],
    ) -> None:
        self.cfg = cfg
        self.run_dir = run_dir  # temp dir: harness config, per-run HOMEs
        self.workdir = workdir  # the agent's working directory
        self.mcp = mcp
        self.model = model
        self.known_tools = known_tools
        self.log_dir = log_dir  # results dir: raw transcripts
        self.log_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ interface

    def available(self) -> str | None:
        """None when the binary is on PATH, else a cause."""
        if shutil.which(self.cfg.binary) is None:
            return f"binary_missing: {self.cfg.binary} not on PATH"
        return None

    def version(self) -> str | None:
        try:
            out = subprocess.run(
                [self.cfg.binary, "--version"], capture_output=True, text=True, timeout=30,
                env=self.env(), cwd=self.workdir,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.search(r"\d+\.\d+\.\d+", out.stdout + out.stderr)
        return m.group(0) if m else (out.stdout.strip() or None)

    @abstractmethod
    def prepare(self, instructions: str | None) -> None:
        """Write MCP config, instruction file and harness settings for this run."""

    @abstractmethod
    def command(
        self, prompt: str, session_id: str | None, json_schema: dict[str, Any] | None
    ) -> list[str]:
        """Full argv for one turn."""

    @abstractmethod
    def parse(self, stdout: str, stderr: str, result: TurnResult) -> None:
        """Fill `result` from the harness's output."""

    def env(self) -> dict[str, str]:
        return harness_env(self.workdir)

    def preflight(self) -> None:
        """Verify isolation before the first prompt; raise IsolationBreach if it does not hold."""

    def breach(self, result: TurnResult) -> str | None:
        """A description of an isolation breach visible in this turn, else None."""
        return None

    def new_session_id(self) -> str | None:
        """Session id chosen by the runner before the first turn (if the harness allows)."""
        return None

    def supports_structured_output(self) -> bool:
        return self.cfg.structured_output not in ("", "none")

    # ------------------------------------------------------------ execution

    def base_argv(self, prompt: str) -> list[str]:
        argv = [a.replace("{prompt}", prompt) if "{prompt}" in a else a for a in self.cfg.headless]
        argv[0] = self.cfg.binary
        return argv

    def run_turn(
        self,
        prompt: str,
        *,
        session_id: str | None,
        index: int,
        label: str,
        timeout: float,
        json_schema: dict[str, Any] | None = None,
    ) -> TurnResult:
        start = time.monotonic()
        result = self._run_once(prompt, session_id, index, label, timeout, json_schema)
        if (self.transient_auth_retry and result.error and result.error.startswith("auth:")
                and not result.tool_calls and timeout - (time.monotonic() - start) > 30):
            time.sleep(5)
            first_error = result.error
            remaining = timeout - (time.monotonic() - start)
            result = self._run_once(prompt, session_id, index, f"{label}-retry", remaining, json_schema)
            result.retried_after = first_error
        return result

    def _run_once(
        self,
        prompt: str,
        session_id: str | None,
        index: int,
        label: str,
        timeout: float,
        json_schema: dict[str, Any] | None,
    ) -> TurnResult:
        argv = self.command(prompt, session_id, json_schema)
        result = TurnResult(session_id=session_id)
        result.argv = [a if a != prompt else "<prompt>" for a in argv]
        stdout_path = self.log_dir / f"{index:02d}-{label}.stdout.jsonl"
        stderr_path = self.log_dir / f"{index:02d}-{label}.stderr.txt"
        result.stdout_path = str(stdout_path)
        result.stderr_path = str(stderr_path)
        start = time.monotonic()
        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            try:
                proc = subprocess.Popen(
                    argv, cwd=self.workdir, env=self.env(), stdout=out, stderr=err,
                    stdin=subprocess.DEVNULL, start_new_session=True,
                )
            except OSError as exc:
                result.error = f"crash: cannot start {argv[0]}: {exc}"
                return result
            try:
                result.exit_code = proc.wait(timeout=max(5.0, timeout))
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                result.timed_out = True
                result.exit_code = proc.returncode
        result.duration_s = round(time.monotonic() - start, 3)
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        try:
            self.parse(stdout, stderr, result)
        except Exception as exc:  # noqa: BLE001 - a parser bug must not abort the matrix
            result.error = result.error or f"crash: cannot parse harness output: {exc!r}"
        breach = self.breach(result)
        if breach:
            result.error = f"isolation_breach: {breach}"
            return result
        if result.timed_out:
            result.error = f"timeout: turn exceeded {int(timeout)} s"
        elif result.error is None and (result.exit_code not in (0, None) or not result.final_text.strip()):
            failure = classify_failure(stderr[-4000:], result.final_text if not result.tool_calls else "",
                                       _error_lines(stdout))
            if failure:
                result.error = failure
            elif result.exit_code not in (0, None):
                tail = " ".join(stderr.strip().split())[-300:]
                result.error = f"crash: exit code {result.exit_code}: {tail}"
        return result


def _error_lines(stdout: str) -> str:
    lines = []
    for line in stdout.splitlines():
        if '"error"' in line or '"is_error":true' in line or "AGY_ERROR" in line:
            lines.append(line[:1000])
    return "\n".join(lines[-5:])


def load_jsonl(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def write_instructions(workdir: Path, relative: str, statement: str | None) -> None:
    if statement is None:
        return
    path = workdir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(statement.strip() + "\n", encoding="utf-8")
