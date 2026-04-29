"""Entry point for the `imap-mcp` console script."""

from __future__ import annotations

import argparse
import asyncio
import os

from .server import (
    _config_dir_from_env_or_exit,
    run_http,
    run_stdio,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="imap-mcp")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport to serve on. stdio expects orchestrator-trust "
        "for caller identity; http expects shared_token bearer auth "
        "(ADR 0015, ADR 0023).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("IMAP_MCP_HTTP_HOST", "127.0.0.1"),
        help="Bind address for HTTP transport (default 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("IMAP_MCP_HTTP_PORT", "0")),
        help="TCP port for HTTP transport. 0 selects an ephemeral port "
        "(default; the actual port is printed to stdout once bound).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    config_dir = _config_dir_from_env_or_exit()
    if args.transport == "stdio":
        # ADR-0015: caller_id is the orchestrator-supplied identity.
        # Missing or unknown values are NOT a startup failure — the
        # server must accept the connection, run the Initialize
        # handshake, return a structured JSON-RPC error, and exit
        # cleanly. SystemExit-before-Initialize would surface as a
        # broken pipe to the orchestrator.
        caller_id = os.environ.get("IMAP_MCP_CALLER_ID") or None
        asyncio.run(run_stdio(config_dir, caller_id))
        return
    if args.transport == "http":
        asyncio.run(run_http(config_dir, host=args.host, port=args.port))
        return
    raise SystemExit(f"Unsupported transport: {args.transport!r}")


if __name__ == "__main__":
    main()
