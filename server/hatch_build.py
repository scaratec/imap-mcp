"""Supply the repository README as the package's long description."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.metadata.plugin.interface import MetadataHookInterface


class CustomMetadataHook(MetadataHookInterface):
    def update(self, metadata: dict[str, Any]) -> None:
        root = Path(self.root)
        # Inside an sdist the README sits next to pyproject.toml;
        # in the repository it is one level up.
        for candidate in (root / "PYPI_README.md", root.parent / "README.md"):
            if candidate.is_file():
                metadata["readme"] = {
                    "content-type": "text/markdown",
                    "text": candidate.read_text(encoding="utf-8"),
                }
                return
        raise FileNotFoundError("README for the package description not found")
