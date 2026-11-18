"""Cryptographic Draft Provenance (ADR 0042).

Binds every server-authored draft and sent successor to
(caller_id, account_id, draft_id, revision, canonical_digest)
via an HMAC-SHA256-signed X-SC-Provenance header and SQLite wal.db.
"""

from __future__ import annotations

import email
import hashlib
import hmac
from email.message import Message

from .policy import normalize_address


def compute_canonical_digest(raw_or_msg: bytes | Message) -> str:
    """Compute sha256 hex digest over normalized From, To, Cc, Bcc, Subject, and ordered MIME leaf payloads."""
    msg = email.message_from_bytes(raw_or_msg) if isinstance(raw_or_msg, bytes) else raw_or_msg

    from_addr, _ = normalize_address(msg.get("From") or "")
    from email.utils import getaddresses

    to_addrs = sorted(normalize_address(a)[0] for _, a in getaddresses(msg.get_all("To", [])))
    cc_addrs = sorted(normalize_address(a)[0] for _, a in getaddresses(msg.get_all("Cc", [])))
    bcc_addrs = sorted(normalize_address(a)[0] for _, a in getaddresses(msg.get_all("Bcc", [])))
    subject = (msg.get("Subject") or "").strip()

    hasher = hashlib.sha256()
    hasher.update(from_addr.encode("utf-8") + b"\n")
    hasher.update(",".join(to_addrs).encode("utf-8") + b"\n")
    hasher.update(",".join(cc_addrs).encode("utf-8") + b"\n")
    hasher.update(",".join(bcc_addrs).encode("utf-8") + b"\n")
    hasher.update(subject.encode("utf-8") + b"\n")

    # Ordered MIME leaf digests
    for part in msg.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        payload_bytes = (
            payload if isinstance(payload, bytes) else str(payload or "").encode("utf-8")
        )
        part_type = part.get_content_type()
        part_hash = hashlib.sha256(payload_bytes).hexdigest()
        hasher.update(f"{part_type}:{part_hash}\n".encode())

    return hasher.hexdigest()


def compute_provenance_mac(
    hmac_key: bytes,
    caller_id: str,
    account_id: str,
    draft_id: str,
    revision: int,
    canonical_digest: str,
) -> str:
    """Format: v1:<64hex> over v1|caller_id|account_id|draft_id|revision|canonical_digest."""
    token = f"v1|{caller_id}|{account_id}|{draft_id}|{revision}|{canonical_digest}".encode()
    mac = hmac.new(hmac_key, token, hashlib.sha256).hexdigest()
    return f"v1:{mac}"


def verify_provenance_mac(
    hmac_key: bytes,
    header_value: str | None,
    caller_id: str,
    account_id: str,
    draft_id: str,
    revision: int,
    canonical_digest: str,
) -> bool:
    if not header_value or not header_value.startswith("v1:"):
        return False
    expected = compute_provenance_mac(
        hmac_key, caller_id, account_id, draft_id, revision, canonical_digest
    )
    return hmac.compare_digest(header_value.strip(), expected)
