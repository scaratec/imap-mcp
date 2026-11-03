"""Usability matrix runner (ADR 0039).

Plays the usability scenarios in `usability/features/` against real agent
harnesses, collects evidence, grades every run and writes run records plus
a Markdown report. Agent behaviour never fails the runner: once the matrix
ran, the exit code is 0.
"""

from __future__ import annotations

from pathlib import Path

USABILITY_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = USABILITY_DIR.parent

# Reused BDD infrastructure (OTLP test collector, MCP client) and the
# mock-gmail package are imported from their source trees.
import sys as _sys

for _path in (REPO_ROOT / "bdd", REPO_ROOT / "bdd" / "mock-gmail" / "src"):
    if str(_path) not in _sys.path:
        _sys.path.insert(0, str(_path))
