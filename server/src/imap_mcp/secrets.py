"""Secret store — three backends (ADR 0011, ADR 0050).

A secret reference is a `secret://...` URI. The path portion maps to a backend:
- `file_dir`: Plain files under a root directory (mode 0600 in 0700 dirs).
- `env_var`: Read-only environment variable mapping.
- `gpg_file`: GPG-encrypted files decrypted/encrypted via batch gpg subprocess without shell.

Operational errors vs. auth failures:
- Handshake token failure -> auth_failed
- Account credential failure during tool execution -> ALLOW + ERROR (secret_unavailable)
"""

from __future__ import annotations

import os
import secrets as py_secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol


class SecretStore(Protocol):
    def get(self, reference: str) -> str | None: ...
    def put(self, reference: str, value: str) -> None: ...


class SecretDecryptionFailed(RuntimeError):
    """A `gpg --decrypt` invocation failed. Sentinel error without leaking stderr."""


_PREFIX = "secret://"


def _relpath(reference: str) -> str:
    if not reference.startswith(_PREFIX):
        raise ValueError(f"Secret reference must start with {_PREFIX!r}; got {reference!r}")
    return reference[len(_PREFIX) :].lstrip("/")


class FileDirSecretStore:
    """Plain files under a root directory (mode 0600 in 0700 dirs)."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def get(self, reference: str) -> str | None:
        rel = _relpath(reference)
        path = self._root / rel
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8").rstrip("\n")

    def put(self, reference: str, value: str) -> None:
        rel = _relpath(reference)
        target = self._root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target.parent, 0o700)
        except OSError:
            pass
        fd, tmp_path = tempfile.mkstemp(dir=target.parent, prefix=".tmp-secret-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(value + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, target)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise


class EnvVarSecretStore:
    """Read-only environment variable backend."""

    ENV_PREFIX = "IMAP_MCP_SECRET__"
    READONLY = True

    @classmethod
    def env_name(cls, reference: str) -> str:
        rel = _relpath(reference)
        return cls.ENV_PREFIX + rel.upper().replace("/", "__").replace("-", "_")

    def get(self, reference: str) -> str | None:
        if reference == "secret://server/provenance_hmac_key":
            val = os.environ.get("IMAP_MCP_SERVER_HMAC_KEY")
            if val is not None:
                return val
        return os.environ.get(self.env_name(reference))

    def put(self, reference: str, value: str) -> None:
        raise NotImplementedError(
            "env_var backend is read-only; bootstrap requires a writable secret store"
        )


class GpgFileSecretStore:
    """GPG-encrypted files under a root directory (ADR 0050)."""

    def __init__(
        self,
        root: Path,
        recipient: str,
        gnupghome: Path | None = None,
        passphrase: str | None = None,
    ) -> None:
        self._root = root.resolve()
        self._recipient = recipient
        self._gnupghome = gnupghome
        self._passphrase = passphrase
        if shutil.which("gpg") is None:
            raise RuntimeError("gpg_file backend requires the `gpg` binary on PATH")

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self._gnupghome is not None:
            env["GNUPGHOME"] = str(self._gnupghome)
        return env

    def get(self, reference: str) -> str | None:
        rel = _relpath(reference)
        path = self._root / rel
        if not path.exists() and not str(path).endswith(".gpg"):
            path = self._root / (rel + ".gpg")
        if not path.is_file():
            return None
        cmd = [
            "gpg",
            "--decrypt",
            "--batch",
            "--yes",
            "--quiet",
            "--no-tty",
        ]
        if self._passphrase is not None:
            cmd.extend(["--pinentry-mode", "loopback", "--passphrase", self._passphrase])
        cmd.append(str(path))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                env=self._env(),
                timeout=10,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise SecretDecryptionFailed("gpg subprocess failed") from exc
        if result.returncode != 0:
            raise SecretDecryptionFailed("gpg decrypt returned non-zero")
        return result.stdout.decode("utf-8", errors="replace").rstrip("\n")

    def put(self, reference: str, value: str) -> None:
        rel = _relpath(reference)
        if not rel.endswith(".gpg"):
            rel = rel + ".gpg"
        target = self._root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target.parent, 0o700)
        except OSError:
            pass
        fd, tmp_path = tempfile.mkstemp(dir=target.parent, prefix=".tmp-gpg-")
        os.close(fd)
        try:
            result = subprocess.run(
                [
                    "gpg",
                    "--batch",
                    "--yes",
                    "--quiet",
                    "--no-tty",
                    "--encrypt",
                    "--recipient",
                    self._recipient,
                    "--output",
                    tmp_path,
                ],
                input=value.encode("utf-8") + b"\n",
                capture_output=True,
                env=self._env(),
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError("gpg encrypt returned non-zero")
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, target)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise


def build_secret_store(
    backend: str,
    path: Path | None,
    *,
    recipient: str | None = None,
    gnupghome: Path | None = None,
    acknowledge_plaintext: bool = False,
    passphrase: str | None = None,
) -> SecretStore:
    if backend == "file_dir":
        if path is None:
            raise ValueError("file_dir backend requires a `path`")
        if not acknowledge_plaintext:
            raise ValueError("file_dir backend requires explicit acknowledge_plaintext: true")
        return FileDirSecretStore(path)
    if backend == "env_var":
        return EnvVarSecretStore()
    if backend == "gpg_file":
        if path is None:
            raise ValueError("gpg_file backend requires a `path`")
        if not recipient:
            raise ValueError("gpg_file backend requires a `recipient`")
        return GpgFileSecretStore(path, recipient, gnupghome, passphrase=passphrase)
    raise NotImplementedError(f"Secret store backend {backend!r} not implemented")


def resolve_provenance_hmac_key(store: SecretStore | None) -> bytes:
    """Retrieve or generate the 256-bit server HMAC key (ADR 0042, ADR 0048).

    If stored as 64-hex string, decoded via bytes.fromhex to 32 raw bytes.
    """
    key_ref = "secret://server/provenance_hmac_key"
    val: str | None = None
    if store is not None:
        try:
            val = store.get(key_ref)
        except Exception:  # noqa: BLE001
            val = None

    if val is None:
        env_val = os.environ.get("IMAP_MCP_SERVER_HMAC_KEY")
        if env_val:
            val = env_val.strip()

    if val is not None and val.strip():
        clean = val.strip()
        if len(clean) == 64:
            try:
                return bytes.fromhex(clean)
            except ValueError:
                pass
        return clean.encode("utf-8")[:32].ljust(32, b"\0")

    # Generate fresh 32 bytes (64 hex characters)
    new_key_hex = py_secrets.token_hex(32)
    if store is not None:
        try:
            store.put(key_ref, new_key_hex)
        except Exception:  # noqa: BLE001, S110
            pass
    return bytes.fromhex(new_key_hex)
