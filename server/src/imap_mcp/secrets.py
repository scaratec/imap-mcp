"""Secret store — Walking-Skeleton slice (ADR 0011).

Only the `file_dir` backend is implemented. A secret reference is
a `secret://...` URI whose path portion resolves relative to the
store's root directory. A plain-text file at that location is the
secret value. Confidentiality relies on the surrounding system
(filesystem permissions, encrypted disk, git-crypt) — the server
never encrypts or decrypts anything itself, per ADR 0011.

Other backends (env_var, gpg_file) are interface-compatible and
will land when the scenarios in secret_store_backends.feature
activate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class SecretStore(Protocol):
    def get(self, reference: str) -> str | None: ...


class FileDirSecretStore:
    """Plain files under a root directory (ADR 0011, backend `file_dir`)."""

    PREFIX = "secret://"

    def __init__(self, root: Path) -> None:
        self._root = root

    def get(self, reference: str) -> str | None:
        if not reference.startswith(self.PREFIX):
            raise ValueError(
                f"Secret reference must start with {self.PREFIX!r}; got {reference!r}"
            )
        rel = reference[len(self.PREFIX) :].lstrip("/")
        path = self._root / rel
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8").rstrip("\n")


def build_secret_store(backend: str, path: Path | None) -> SecretStore:
    if backend == "file_dir":
        if path is None:
            raise ValueError("file_dir backend requires a `path`")
        return FileDirSecretStore(path)
    raise NotImplementedError(f"Secret store backend {backend!r} not implemented yet")
