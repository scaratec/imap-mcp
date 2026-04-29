"""`imap-mcp-oauth-bootstrap` — operator CLI to mint and persist an
OAuth2 refresh token for an account that uses `provider: google` (or
any other OAuth-based provider). Walking-Skeleton implementation: the
bootstrap aborts before doing any browser flow when the configured
secret store is read-only — there is nowhere to write the resulting
refresh token (ADR 0011).

The interactive OAuth flow itself is gated by LIM-0003 (mock-OAuth
subproject). This stub exists to validate the read-only-backend
guard and to give operators a concrete error message instead of a
silent no-op.
"""

from __future__ import annotations

import argparse
import sys

from ..config import load_configuration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="imap-mcp-oauth-bootstrap")
    parser.add_argument("--account", required=True)
    parser.add_argument(
        "--config-dir",
        default=None,
        help=(
            "Override IMAP_MCP_CONFIG_DIR. Defaults to that env var if "
            "set, otherwise the current directory."
        ),
    )
    args = parser.parse_args(argv)

    import os
    from pathlib import Path

    config_dir = Path(
        args.config_dir or os.environ.get("IMAP_MCP_CONFIG_DIR") or "."
    )
    if not config_dir.is_dir():
        print(
            f"IMAP_MCP_CONFIG_DIR does not point at a directory: {config_dir}",
            file=sys.stderr,
        )
        return 2

    configuration = load_configuration(config_dir)
    store_cfg = configuration.accounts_file.secret_store
    if store_cfg is not None and store_cfg.backend == "env_var":
        print(
            "env_var backend is read-only; bootstrap requires a "
            "writable secret store",
            file=sys.stderr,
        )
        return 2

    # The actual interactive bootstrap is gated by LIM-0003. Stop
    # here for now with a clear "not implemented" message so the
    # operator knows this is a placeholder, not a silent success.
    print(
        f"oauth bootstrap for account {args.account!r} is not yet "
        "implemented (LIM-0003)",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
