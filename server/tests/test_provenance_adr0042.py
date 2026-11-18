"""Unit tests for cryptographic draft provenance (ADR 0042)."""

from __future__ import annotations

from imap_mcp.handlers.drafts import _strip_x_sc_headers
from imap_mcp.provenance import (
    compute_provenance_mac,
    verify_provenance_mac,
)


def test_strip_x_sc_headers_case_insensitively() -> None:
    raw = (
        b"From: a@example.org\r\n"
        b"X-SC-Draft-Id: 1111\r\n"
        b"x-sc-draft-rev: 1\r\n"
        b"X-Sc-Provenance: v1:xyz\r\n"
        b"Subject: Test\r\n"
        b"\r\n"
        b"Body\r\n"
    )
    head, _ = raw.split(b"\r\n\r\n")
    cleaned = _strip_x_sc_headers(head + b"\r\n")
    assert b"X-SC-Draft-Id" not in cleaned
    assert b"x-sc-draft-rev" not in cleaned
    assert b"X-Sc-Provenance" not in cleaned
    assert b"From: a@example.org" in cleaned
    assert b"Subject: Test" in cleaned


def test_provenance_mac_generation_and_verification() -> None:
    key = b"0123456789abcdef0123456789abcdef"
    caller = "invoice-agent"
    account = "gupta-scaratec"
    draft_id = "8f14e45f-ceea-4e7a-9d2b-0a1c3b5d7e90"
    rev = 1
    digest = "852164ec72e10f7535a54042d517b3c983bf8eb620409e750d1660b9a74bf0e0"

    mac = compute_provenance_mac(key, caller, account, draft_id, rev, digest)
    assert mac.startswith("v1:")
    assert len(mac) == 3 + 64

    # Verification passes
    assert verify_provenance_mac(key, mac, caller, account, draft_id, rev, digest) is True

    # Verification fails on wrong caller, rev, or digest
    assert verify_provenance_mac(key, mac, "other-caller", account, draft_id, rev, digest) is False
    assert verify_provenance_mac(key, mac, caller, account, draft_id, 2, digest) is False
    assert verify_provenance_mac(key, mac, caller, account, draft_id, rev, "wrong-digest") is False
