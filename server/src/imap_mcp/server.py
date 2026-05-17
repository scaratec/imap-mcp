"""Back-compat facade for the historical `imap_mcp.server` module.

The actual code lives in `context.py`, `dispatch.py`, `reload.py`,
`runtime/{stdio,http}.py`, and `handlers/*` since the Phase B split.
This module exists only so existing import paths
(`from imap_mcp.server import run_stdio` etc.) keep working without
forcing every caller to chase the new layout.
"""

from __future__ import annotations

from .context import ServerContext
from .runtime.http import run_http
from .runtime.stdio import (
    _caller_id_from_env_or_exit,
    _config_dir_from_env_or_exit,
    run_stdio,
)

__all__ = [
    "ServerContext",
    "_caller_id_from_env_or_exit",
    "_config_dir_from_env_or_exit",
    "run_http",
    "run_stdio",
]
