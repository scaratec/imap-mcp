"""Unit tests for symlink-safe per-caller attachment sink delivery (ADR 0047)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from imap_mcp.handlers._common import (
    deliver_attachment_to_sink,
    sanitize_attachment_filename,
)


def test_sanitize_filename_removes_unsafe_characters_and_leading_dots() -> None:
    assert sanitize_attachment_filename("..hidden.pdf", b"abc") == "hidden.pdf"
    assert (
        sanitize_attachment_filename("invoice;rm -rf /.pdf", b"abc")
        == "invoice_rm_-rf__.pdf"
    )
    assert sanitize_attachment_filename("Rechnung März.pdf", b"abc") == "Rechnung_M_rz.pdf"


def test_deliver_attachment_creates_0400_file_in_0700_caller_dir(
    tmp_path: Path,
) -> None:
    base_sink = tmp_path / "sink"
    base_sink.mkdir()
    os.chmod(base_sink, 0o700)

    caller_id = "agent-x"
    payload = b"Sample PDF bytes repeated " * 10
    saved_to, saved_path, artifact_id, _content_hash, _size = deliver_attachment_to_sink(
        base_sink_dir=base_sink,
        caller_id=caller_id,
        original_filename="contract.pdf",
        payload=payload,
    )

    assert saved_to.startswith("agent-x/")
    assert artifact_id in saved_to
    assert saved_to.endswith("_contract.pdf")

    path = Path(saved_path)
    assert path.is_file()
    assert path.read_bytes() == payload
    assert len(artifact_id) == 32

    # Check file permissions (0400)
    file_mode = stat.S_IMODE(path.stat().st_mode)
    assert file_mode == 0o400

    # Check caller dir permissions (0700)
    caller_dir = path.parent
    dir_mode = stat.S_IMODE(caller_dir.stat().st_mode)
    assert dir_mode == 0o700
