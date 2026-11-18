"""Unit tests for single-caller boundary and validated configuration (ADR 0049, ADR 0050)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from imap_mcp.config import load_configuration


def _write_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_multi_caller_callers_yaml_fails(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "accounts.yaml",
        {
            "accounts": [{"id": "acc-1"}],
            "secret_store": {"backend": "file_dir", "path": str(tmp_path / "secrets"), "acknowledge_plaintext": True},
        },
    )
    _write_file(
        tmp_path / "callers.yaml",
        {
            "callers": [
                {"id": "caller-1", "policy": "p1", "auth": {"type": "stdio_trusted"}},
                {"id": "caller-2", "policy": "p1", "auth": {"type": "stdio_trusted"}},
            ]
        },
    )
    _write_file(
        tmp_path / "policies" / "p1.yaml",
        {
            "name": "p1",
            "accounts": {"acc-1": [{"path": "INBOX", "mode": "blacklist", "default": "FULL"}]},
        },
    )

    with pytest.raises(Exception, match="callers.yaml must define exactly one caller"):
        load_configuration(tmp_path)


def test_missing_auth_block_in_caller_fails(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "accounts.yaml",
        {
            "accounts": [{"id": "acc-1"}],
            "secret_store": {"backend": "file_dir", "path": str(tmp_path / "secrets"), "acknowledge_plaintext": True},
        },
    )
    _write_file(
        tmp_path / "callers.yaml",
        {
            "callers": [
                {"id": "caller-1", "policy": "p1"},
            ]
        },
    )
    _write_file(
        tmp_path / "policies" / "p1.yaml",
        {
            "name": "p1",
            "accounts": {"acc-1": [{"path": "INBOX", "mode": "blacklist", "default": "FULL"}]},
        },
    )

    with pytest.raises(Exception, match="missing auth block"):
        load_configuration(tmp_path)


def test_file_dir_without_acknowledge_plaintext_fails(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "accounts.yaml",
        {
            "accounts": [{"id": "acc-1"}],
            "secret_store": {"backend": "file_dir", "path": str(tmp_path / "secrets")},
        },
    )
    _write_file(
        tmp_path / "callers.yaml",
        {
            "callers": [
                {"id": "caller-1", "policy": "p1", "auth": {"type": "stdio_trusted"}},
            ]
        },
    )
    _write_file(
        tmp_path / "policies" / "p1.yaml",
        {
            "name": "p1",
            "accounts": {"acc-1": [{"path": "INBOX", "mode": "blacklist", "default": "FULL"}]},
        },
    )

    with pytest.raises(Exception, match="acknowledge_plaintext"):
        load_configuration(tmp_path)
