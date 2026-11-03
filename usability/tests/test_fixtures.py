"""Fixture generation is deterministic: same feature input → same bytes."""

from __future__ import annotations

import hashlib
import subprocess
import sys

from samples import FEATURES

from usability.runner import REPO_ROOT
from usability.runner.fixtures import GmailState, build_mailbox, encode_mutf7, seed_state, snapshot
from usability.runner.pdf import format_eur, invoice_lines, render_pdf


def _digest(scenario: str) -> str:
    fixture = build_mailbox(FEATURES.scenarios[scenario])
    h = hashlib.sha256()
    for m in fixture.messages:
        h.update(m.rfc822)
    return h.hexdigest()


def test_same_input_same_bytes_in_process() -> None:
    for scenario in FEATURES.scenarios:
        assert _digest(scenario) == _digest(scenario)


def test_same_bytes_across_processes() -> None:
    code = (
        "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
        "from samples import FEATURES\n"
        "from test_fixtures import _digest\n"
        "print(_digest('S1'), _digest('R3'))"
    ) % (str(REPO_ROOT), str(REPO_ROOT / "usability" / "tests"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env={"PYTHONHASHSEED": "12345", "PATH": ""})
    assert out.stdout.split() == [_digest("S1"), _digest("R3")]


def test_pdf_is_reproducible_and_carries_the_table_values() -> None:
    from datetime import date

    lines = invoice_lines(number="NL-2026-10", issuer="nordlicht.example.org", issued=date(2026, 10, 30),
                          amount_eur="89.40", recipient="Kim Brandt")
    a = render_pdf(lines, title="Rechnung NL-2026-10", creation=date(2026, 10, 30))
    b = render_pdf(lines, title="Rechnung NL-2026-10", creation=date(2026, 10, 30))
    assert a == b
    assert a.startswith(b"%PDF-1.4") and a.rstrip().endswith(b"%%EOF")
    assert b"89,40 EUR" in a and b"NL-2026-10" in a and b"30.10.2026" in a
    assert format_eur("1250.00") == "1.250,00 EUR"


def test_xref_offsets_point_at_objects() -> None:
    from datetime import date

    pdf = render_pdf(["x"], title="t", creation=date(2026, 1, 1))
    xref = pdf.rindex(b"\nxref\n") + 1
    entries = pdf[xref:].split(b"\n")[3:9]
    for number, entry in enumerate(entries, start=1):
        offset = int(entry.split()[0])
        assert pdf[offset:].startswith(f"{number} 0 obj".encode())


def test_attachment_bytes_are_embedded_unchanged() -> None:
    import email
    import email.policy

    fixture = build_mailbox(FEATURES.scenarios["A2"])
    seed = fixture.by_ref()[1001]
    parsed = email.message_from_bytes(seed.rfc822, policy=email.policy.default)
    parts = [p for p in parsed.walk() if p.get_filename() == "NL-2026-10.pdf"]
    assert parts[0].get_payload(decode=True) == seed.attachment_bytes


def test_seeded_uids_follow_the_table_and_localized_names_are_wire_encoded() -> None:
    spec = FEATURES.scenarios["S1"]
    state = GmailState()
    seed_state(state, build_mailbox(spec))
    snap = snapshot(state)
    by_subject = {m["subject"]: m for m in snap}
    assert by_subject["Ihre Rechnung Oktober 2026"]["uids"]["INBOX"] == 1001
    assert by_subject["Ihre Rechnung Juli 2026"]["uids"]["INBOX/Rechnungseingänge"] == 2001
    assert by_subject["Re: Angebot Frühjahr"]["uids"]["[Gmail]/Sent Mail"] == 3001
    assert encode_mutf7("[Gmail]/Entwürfe") == "[Gmail]/Entw&APw-rfe"
    assert encode_mutf7("INBOX/Rechnungseingänge") == "INBOX/Rechnungseing&AOQ-nge"
    assert encode_mutf7("a&b") == "a&-b"


def test_owner_draft_is_seeded_in_drafts() -> None:
    state = GmailState()
    seed_state(state, build_mailbox(FEATURES.scenarios["R3"]))
    drafts = [m for m in snapshot(state) if "[Gmail]/Drafts" in m["folders"]]
    assert [d["subject"] for d in drafts] == ["Lieferverzögerung"]
    assert "\\Draft" in drafts[0]["flags"]
