"""Harness drivers, one per CLI listed in matrix.yaml."""

from __future__ import annotations

from .antigravity import AntigravityDriver
from .base import HarnessDriver, McpServerSpec, ToolCall, TurnResult, Usage
from .claude import ClaudeCodeDriver
from .copilot import CopilotDriver
from .opencode import OpenCodeDriver

DRIVERS: dict[str, type[HarnessDriver]] = {
    "antigravity": AntigravityDriver,
    "copilot": CopilotDriver,
    "claude-code": ClaudeCodeDriver,
    "opencode": OpenCodeDriver,
}

__all__ = ["DRIVERS", "HarnessDriver", "McpServerSpec", "ToolCall", "TurnResult", "Usage"]
