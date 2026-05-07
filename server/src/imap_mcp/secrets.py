"""Secret store — three V1 backends (ADR 0011).

A secret reference is a `secret://...` URI. The path portion of the
URI maps to a backend-specific lookup:

- **`file_dir`** — relative path under a root directory; the file's
  contents (with trailing newlines stripped) are the secret.
  Confidentiality relies on the surrounding system.
- **`env_var`** — `secret://callers/invoice-agent/token` →
  environment variable `IMAP_MCP_SECRET__CALLERS__INVOICE_AGENT__TOKEN`.
  Read-only; orchestrator-managed.
- **`gpg_file`** — same on-disk layout as `file_dir` but each file is
  GPG-encrypted (ASCII-armored or binary). Decryption shells out to
  `gpg --decrypt --batch …`. Stderr is captured and discarded so it
  never leaks into audit records.

The server performs no cryptography of its own; for `gpg_file` it
calls out to the system `gpg` binary.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol


class SecretStore(Protocol):
    def get(self, reference: str) -> str | None: ...


class SecretDecryptionFailed(RuntimeError):
    """A `gpg --decrypt` invocation returned non-zero. The message is
    a fixed sentinel; gpg's stderr is intentionally NOT included so it
    cannot leak into audit records via except-handlers that log
    `str(exc)`."""


_PREFIX = "secret://"


def _relpath(reference: str) -> str:
    if not reference.startswith(_PREFIX):
        raise ValueError(f"Secret reference must start with {_PREFIX!r}; got {reference!r}")
    return reference[len(_PREFIX) :].lstrip("/")


class FileDirSecretStore:
    """Plain files under a root directory (ADR 0011, backend `file_dir`)."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def get(self, reference: str) -> str | None:
        rel = _relpath(reference)
        path = self._root / rel
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8").rstrip("\n")


class EnvVarSecretStore:
    """Read-only backend that maps `secret://` URIs to environment
    variables. The mapping is deterministic so the orchestrator can
    populate the variables before launch.

    Example: `secret://callers/invoice-agent/token` →
    `IMAP_MCP_SECRET__CALLERS__INVOICE_AGENT__TOKEN`.
    """

    ENV_PREFIX = "IMAP_MCP_SECRET__"
    READONLY = True

    @classmethod
    def env_name(cls, reference: str) -> str:
        rel = _relpath(reference)
        return cls.ENV_PREFIX + rel.upper().replace("/", "__").replace("-", "_")

    def get(self, reference: str) -> str | None:
        return os.environ.get(self.env_name(reference))

    def put(self, reference: str, value: str) -> None:
        raise NotImplementedError(
            "env_var backend is read-only; bootstrap requires a writable secret store"
        )


class GpgFileSecretStore:
    """GPG-encrypted files under a root directory (ADR 0011, backend
    `gpg_file`). Each file is decrypted on demand via the system `gpg`
    binary. Stderr is captured and discarded — only the plaintext
    return value reaches the caller."""

    def __init__(self, root: Path, recipient: str, gnupghome: Path | None = None) -> None:
        self._root = root
        self._recipient = recipient
        self._gnupghome = gnupghome
        # Smoke check at construction so a missing gpg binary surfaces
        # at startup, not at first decrypt.
        if shutil.which("gpg") is None:
            raise RuntimeError("gpg_file backend requires the `gpg` binary on PATH")

    def get(self, reference: str) -> str | None:
        rel = _relpath(reference)
        # Files are stored with `.gpg` extension; allow the reference
        # to omit it.
        path = self._root / rel
        if not path.exists() and not str(path).endswith(".gpg"):
            path = self._root / (rel + ".gpg")
        if not path.is_file():
            return None
        env = dict(os.environ)
        if self._gnupghome is not None:
            env["GNUPGHOME"] = str(self._gnupghome)
        try:
            result = subprocess.run(
                [
                    "gpg",
                    "--decrypt",
                    "--batch",
                    "--yes",
                    "--quiet",
                    "--no-tty",
                    str(path),
                ],
                capture_output=True,
                env=env,
                timeout=10,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise SecretDecryptionFailed("gpg subprocess failed") from exc
        if result.returncode != 0:
            # Discard stderr — never propagate it to callers, so it
            # cannot leak into audit records.
            raise SecretDecryptionFailed("gpg decrypt returned non-zero")
        return result.stdout.decode("utf-8", errors="replace").rstrip("\n")


def build_secret_store(
    backend: str,
    path: Path | None,
    *,
    recipient: str | None = None,
    gnupghome: Path | None = None,
) -> SecretStore:
    if backend == "file_dir":
        if path is None:
            raise ValueError("file_dir backend requires a `path`")
        return FileDirSecretStore(path)
    if backend == "env_var":
        return EnvVarSecretStore()
    if backend == "gpg_file":
        if path is None:
            raise ValueError("gpg_file backend requires a `path`")
        if not recipient:
            raise ValueError("gpg_file backend requires a `recipient`")
        return GpgFileSecretStore(path, recipient, gnupghome)
    raise NotImplementedError(f"Secret store backend {backend!r} not implemented")
