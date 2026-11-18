"""Per-run server configuration: accounts.yaml, callers.yaml, policy, secrets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import USABILITY_DIR
from .features import ScenarioSpec
from .fixtures import OWNER_ADDRESS

FIXTURE_PASSWORD = "fixture-only"  # Dedicated password configured for mock-gmail usability fixtures


@dataclass(frozen=True)
class ServerConfig:
    config_dir: Path
    audit_dir: Path
    sink_dir: Path
    wal_path: Path
    policy_name: str
    policy: dict[str, Any]


def _folder_entry(row: Any) -> dict[str, Any]:
    return {
        "path": row.folder,
        "mode": row.mode,
        "default": row.default,
        "move_out": row.move_out,
        "rules": row.rules,
    }


def _verify_variant_policy_requirements(
    spec: ScenarioSpec, template: dict[str, Any], variant: str
) -> None:
    accounts = template.get("accounts") or {}
    acc_entries = accounts.get(spec.account, [])
    by_folder: dict[str, dict[str, Any]] = {e.get("path"): e for e in acc_entries}

    for req in spec.variant_policy_requirements:
        if req.variant not in (None, "*", variant):
            continue

        entry = by_folder.get(req.folder)
        if entry is None:
            raise ValueError(
                f"Variant policy for {variant!r} is missing required folder {req.folder!r} for account {spec.account!r}"
            )

        if entry.get("mode") != req.mode:
            raise ValueError(
                f"Variant policy for {variant!r} folder {req.folder!r} has mode {entry.get('mode')!r}, "
                f"expected {req.mode!r}"
            )

        if entry.get("default") != req.default:
            raise ValueError(
                f"Variant policy for {variant!r} folder {req.folder!r} has default {entry.get('default')!r}, "
                f"expected {req.default!r}"
            )

        for cap_name, expected_val in req.capabilities.items():
            actual_val = bool(entry.get(cap_name, False))
            if actual_val != expected_val:
                raise ValueError(
                    f"Variant policy for {variant!r} folder {req.folder!r} has {cap_name}={actual_val}, "
                    f"expected {cap_name}={expected_val}"
                )


def policy_for(
    spec: ScenarioSpec, variant_policy_file: str, variant: str | None = None
) -> dict[str, Any]:
    """The caller's policy: the scenario's own table, or the variant template."""
    if spec.custom_policy is not None:
        return {
            "name": spec.caller,
            "accounts": {spec.account: [_folder_entry(r) for r in spec.custom_policy]},
        }
    path = USABILITY_DIR / variant_policy_file
    template = yaml.safe_load(path.read_text(encoding="utf-8"))
    accounts = template.get("accounts") or {}
    if spec.account not in accounts:
        raise ValueError(f"{path}: no entry for account {spec.account!r}")

    active_variant = variant
    if not active_variant:
        if "today" in variant_policy_file:
            active_variant = "today"
        elif "fixed" in variant_policy_file:
            active_variant = "fixed"
        else:
            active_variant = "fixed"

    if spec.variant_policy_requirements:
        _verify_variant_policy_requirements(spec, template, active_variant)

    return {"name": spec.caller, "accounts": {spec.account: accounts[spec.account]}}


def write_server_config(
    root: Path, spec: ScenarioSpec, variant_policy_file: str, mock_port: int, variant: str | None = None
) -> ServerConfig:
    config_dir = root / "config"
    secrets_dir = root / "secrets"
    audit_dir = root / "audit"
    sink_dir = root / "sink"
    wal_path = root / "wal.db"
    for d in (config_dir / "policies", secrets_dir / "accounts" / spec.account, audit_dir, sink_dir):
        d.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "accounts" / spec.account / "password").write_text(FIXTURE_PASSWORD, encoding="utf-8")

    accounts = {
        "accounts": [
            {
                "id": spec.account,
                "provider": spec.provider,
                "host": "127.0.0.1",
                "port": mock_port,
                "user": spec.account,
                "identity": OWNER_ADDRESS,
                "auth": {"type": "password", "secret_ref": f"secret://accounts/{spec.account}/password"},
                "token_cache": "memory_only",
            }
        ],
        "secret_store": {"backend": "file_dir", "path": str(secrets_dir), "acknowledge_plaintext": True},
        "audit": {"directory": str(audit_dir)},
        "wal": {"path": str(wal_path)},
        "attachment_sink": {"directory": str(sink_dir)},
    }
    (config_dir / "accounts.yaml").write_text(
        yaml.safe_dump(accounts, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    callers = {"callers": [{"id": spec.caller, "policy": spec.caller, "auth": {"type": "stdio_trusted"}}]}
    (config_dir / "callers.yaml").write_text(yaml.safe_dump(callers, sort_keys=False), encoding="utf-8")
    policy = policy_for(spec, variant_policy_file, variant=variant)
    (config_dir / "policies" / f"{spec.caller}.yaml").write_text(
        yaml.safe_dump(policy, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return ServerConfig(config_dir, audit_dir, sink_dir, wal_path, spec.caller, policy)
