"""Deterministic IMAP failure injection (test-only).

Read at server startup from `IMAP_MCP_FAULT_INJECTION`. The value is a
JSON object keyed by IMAP account id; each entry lists primable faults
the `imap_core` primitives (and the saga's inline EXPUNGE) consult
before issuing IMAP traffic.

Supported fault keys (per account):

  {
    "append":  {"error": 500, "remaining": 1}    # next-APPEND fails
    "append":  {"error": 500, "remaining": null} # every APPEND fails
    "append":  {"delay_seconds": 45, "remaining": 1}  # delay next APPEND
    "expunge": {"error": 500, "remaining": 1}    # next EXPUNGE fails once
    "connect": {"refuse": true}                  # refuse all connects
  }

The BDD harness sets the env var via `extra_env` on MCPClient. Every
fault decrements `remaining`; when it reaches zero the entry is
dropped. `null`/missing means unlimited.

Not a production feature — deliberately env-only and never referenced
by non-test code paths beyond the `check_*` hooks that return immediately
when the registry is empty (the common case).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field


class FaultInjectionError(RuntimeError):
    """Raised when a primed fault fires. The saga catches this like any
    other IMAP error and advances the WAL accordingly."""


@dataclass
class _AccountFaults:
    append: dict | None = None
    expunge: dict | None = None
    connect: dict | None = None


@dataclass
class FaultRegistry:
    """In-process state. One instance per server process."""

    faults: dict[str, _AccountFaults] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "FaultRegistry":
        raw = os.environ.get("IMAP_MCP_FAULT_INJECTION")
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"IMAP_MCP_FAULT_INJECTION is not valid JSON: {exc}"
            ) from exc
        reg = cls()
        for account_id, spec in data.items():
            entry = _AccountFaults(
                append=spec.get("append"),
                expunge=spec.get("expunge"),
                connect=spec.get("connect"),
            )
            reg.faults[account_id] = entry
        return reg

    def _consume(self, entry: dict | None) -> bool:
        """Decrement `remaining`; return True if the fault should fire."""
        if entry is None:
            return False
        remaining = entry.get("remaining")
        if remaining is None:
            return True
        if remaining <= 0:
            return False
        entry["remaining"] = remaining - 1
        return True

    async def check_connect(self, account_id: str) -> None:
        entry = self.faults.get(account_id)
        if entry is None or entry.connect is None:
            return
        if entry.connect.get("refuse"):
            raise ConnectionRefusedError(
                f"fault_injection: refusing connect to {account_id}"
            )

    async def check_append(self, account_id: str) -> None:
        entry = self.faults.get(account_id)
        if entry is None or entry.append is None:
            return
        spec = entry.append
        if not self._consume(spec):
            return
        delay = spec.get("delay_seconds")
        if delay is not None:
            await asyncio.sleep(float(delay))
            return
        code = spec.get("error", 500)
        raise FaultInjectionError(
            f"fault_injection: APPEND on {account_id} simulated error {code}"
        )

    async def check_expunge(self, account_id: str) -> None:
        entry = self.faults.get(account_id)
        if entry is None or entry.expunge is None:
            return
        spec = entry.expunge
        if not self._consume(spec):
            return
        code = spec.get("error", 500)
        raise FaultInjectionError(
            f"fault_injection: EXPUNGE on {account_id} simulated error {code}"
        )


_REGISTRY: FaultRegistry | None = None


def get_registry() -> FaultRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = FaultRegistry.from_env()
    return _REGISTRY


def reset_for_tests() -> None:
    """Clear the process-wide registry. Used only from unit tests."""
    global _REGISTRY
    _REGISTRY = None
