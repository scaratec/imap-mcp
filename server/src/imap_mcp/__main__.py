"""Entry point for the `imap-mcp` console script."""

from __future__ import annotations

import argparse
import asyncio

from .server import _caller_id_from_env_or_exit, _config_dir_from_env_or_exit, run_stdio


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="imap-mcp")
    parser.add_argument(
        "--transport",
        choices=["stdio"],
        default="stdio",
        help="Transport to serve on. Only stdio is supported in the "
        "Walking-Skeleton slice; sse/http follow when their scenarios "
        "activate (ADR 0015).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.transport != "stdio":
        raise SystemExit(f"Unsupported transport: {args.transport!r}")
    caller_id = _caller_id_from_env_or_exit()
    config_dir = _config_dir_from_env_or_exit()
    asyncio.run(run_stdio(config_dir, caller_id))


if __name__ == "__main__":
    main()
