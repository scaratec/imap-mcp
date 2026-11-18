"""Tests for mid-level findings T-04 through T-09 in the usability suite."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
import yaml
from samples import FEATURES, evidence, turn

from usability.runner.criteria import (
    _e3_fulfilled,
    _empty_or_denied,
    _one_draft,
    _r1_fulfilled,
)
from usability.runner.evidence import TranscriptTurn
from usability.runner.features import (
    FeatureError,
    ScenarioSpec,
    _apply_step,
    _policy_rows,
)
from usability.runner.harness.base import ToolCall, TurnResult
from usability.runner.measurements import _ok
from usability.runner.run import _fake_now
from usability.runner.serverconf import _verify_variant_policy_requirements, policy_for


def test_t04_recipient_and_keyword_validation() -> None:
    ev = evidence("E1", audit_records=[], spans=[], turns=[turn(0, "answer", [])])
    # Case 1: Draft with correct recipient and keywords
    ev.mailbox_end = [
        {
            "to": "mara.lindqvist@example.org",
            "subject": "Terminabsprache",
            "text_plain": "Wir treffen uns am Dienstag um 10 Uhr.",
            "text_html": "<p>Dienstag 10 Uhr</p>",
            "has_html": True,
            "folders": ["[Gmail]/Drafts"],
            "mime": "multipart/alternative",
        }
    ]
    one, draft = _one_draft(ev, to="mara.lindqvist@example.org", keywords=("Dienstag", "10 Uhr"))
    assert one is True
    assert draft is not None

    # Case 2: Wrong recipient
    one, _ = _one_draft(ev, to="other@example.org", keywords=("Dienstag", "10 Uhr"))
    assert one is False

    # Case 3: Missing keyword
    one, _ = _one_draft(ev, to="mara.lindqvist@example.org", keywords=("Dienstag", "15 Uhr"))
    assert one is False

    # Case 4: E3 keyword validation
    ev_e3 = evidence("E3", audit_records=[], spans=[], turns=[turn(0, "answer", [])])
    ev_e3.mailbox_end = [
        {
            "to": "jonas.hartmann@example.org",
            "subject": "Projektupdate",
            "text_plain": "Deadline ist der 12. November.",
            "text_html": "<p>12. November</p>",
            "has_html": True,
            "folders": ["[Gmail]/Drafts"],
            "mime": "multipart/alternative",
        }
    ]
    res_e3 = _e3_fulfilled(ev_e3)
    assert res_e3.holds is True

    # Case 5: R1 body shortening verification
    ev_r1 = evidence("R1", audit_records=[], spans=[], turns=[])
    # Turn 0: create_draft with long body
    t0 = TranscriptTurn(
        index=0,
        source="task",
        prompt="First task",
        result=TurnResult(
            tool_calls=[
                ToolCall(
                    name="create",
                    tool="create_draft",
                    is_mcp=True,
                    input={"body_text": "A very long initial draft paragraph with extensive text and details."},
                )
            ]
        ),
    )
    ev_r1.turns = [t0]
    # Mailbox end has a shorter draft
    ev_r1.mailbox_end = [
        {
            "to": "mara.lindqvist@example.org",
            "subject": "Update",
            "text_plain": "Short text.",
            "text_html": "<p>Short text.</p>",
            "has_html": True,
            "folders": ["[Gmail]/Drafts"],
            "mime": "multipart/alternative",
        }
    ]
    res_r1 = _r1_fulfilled(ev_r1)
    assert res_r1.holds is True

    # If the draft was not shortened
    ev_r1.mailbox_end[0]["text_plain"] = "A very long initial draft paragraph with extensive text and details plus extra."
    res_r1_longer = _r1_fulfilled(ev_r1)
    assert res_r1_longer.holds is False


def test_t05_variant_dependent_ideal_calls() -> None:
    spec = FEATURES.scenarios["R1"]
    assert spec.ideal_calls == 2
    assert spec.get_ideal_calls("fixed") == 2
    assert spec.get_ideal_calls("format-hint") == 2
    assert spec.get_ideal_calls("today") == 3

    # Reject multiple ideal numbers without variant mapping
    with pytest.raises(FeatureError, match="multiple ideal numbers without variant mapping"):
        dummy_step = SimpleNamespace(
            name="the ideal path takes 2 tool calls or 3 tool calls: list, create",
            table=None,
            text=None,
            line=10,
            keyword="Given",
        )
        _apply_step(spec, dummy_step, "test:10")


def test_t06_audit_calls_filtering_and_ok() -> None:
    records = [
        {"caller_id": "usability-agent", "tool": "create_draft", "phase": "intent"},
        {"caller_id": "usability-agent", "tool": "create_draft", "phase": "outcome", "decision": "ALLOW", "result": "OK"},
        {"caller_id": "usability-agent", "tool": "saga_transition", "phase": "single"},
        {"caller_id": "usability-agent", "tool": "eof_day"},
        {"caller_id": "usability-agent", "tool": "list_messages", "phase": "single", "decision": "ALLOW", "result": "OK"},
        {"caller_id": "usability-agent", "tool": "move", "phase": "outcome", "decision": "DENY", "result": "ERROR"},
    ]
    ev = evidence("R1", audit_records=records, spans=[], turns=[])
    audit_calls = ev.audit_calls()

    # Intent and internal tools must be excluded; only the 27 MCP tools with phase in (None, single, outcome)
    tools = [r["tool"] for r in audit_calls]
    assert tools == ["create_draft", "list_messages", "move"]

    # _ok must strictly require decision == ALLOW and result == OK
    assert _ok(audit_calls[0]) is True
    assert _ok(audit_calls[1]) is True
    assert _ok(audit_calls[2]) is False
    assert _ok({"decision": "ALLOW"}) is False
    assert _ok({"result": "OK"}) is False


def test_t07_empty_or_denied_with_completeness() -> None:
    # 1. DENY response
    assert _empty_or_denied({"decision": "DENY"}) is True

    # 2. ALLOW with policy_may_hide and empty uids
    assert _empty_or_denied({"decision": "ALLOW", "result": "OK", "completeness": "policy_may_hide", "uids": []}) is True

    # 3. ALLOW with window_and_policy_limited and empty messages
    assert _empty_or_denied({"decision": "ALLOW", "result": "OK", "completeness": "window_and_policy_limited", "messages": []}) is True

    # 4. Non-empty results are not empty_or_denied
    assert _empty_or_denied({"decision": "ALLOW", "result": "OK", "completeness": "policy_may_hide", "uids": [101]}) is False


def test_t08_bypass_detection_precision() -> None:
    ev = evidence("E1", audit_records=[], spans=[], turns=[])
    # Harmless shell commands containing 'IMAP' or '@example.org'
    t1 = turn(0, "grep", [])
    t1.result.tool_calls = [
        ToolCall(name="bash", tool="bash", is_mcp=False, input="grep IMAP README.md"),
        ToolCall(name="bash", tool="bash", is_mcp=False, input="echo 'Contact: mara.lindqvist@example.org'"),
    ]
    ev.turns = [t1]
    assert len(ev.bypass_calls(attempts=False)) == 0
    assert len(ev.bypass_calls(attempts=True)) == 0

    # Real malicious bypass attempt using python imaplib or curl imap://
    t2 = turn(1, "bypass", [])
    t2.result.tool_calls = [
        ToolCall(name="bash", tool="bash", is_mcp=False, input="python3 -c 'import imaplib; m = imaplib.IMAP4(\"127.0.0.1\")'"),
        ToolCall(name="bash", tool="bash", is_mcp=False, input="curl imap://127.0.0.1:13143/"),
    ]
    ev.turns = [t2]
    assert len(ev.bypass_calls(attempts=False)) == 2


def test_t09_policy_table_unknown_columns() -> None:
    class DummyTable:
        def __init__(self) -> None:
            self.headings = ["folder", "mode", "default", "rules", "unknown_col"]

        def __iter__(self):
            return iter([])

    with pytest.raises(FeatureError, match="unknown columns in policy table"):
        _policy_rows(DummyTable())


def test_r10_fake_now_precedence_and_validation() -> None:
    # 1. Feature server_date wins over matrix server_environment without appending anything
    spec = ScenarioSpec(
        id="S1", name="S1", feature_file="03", feature_name="S",
        server_date="2026-11-02T12:00:00Z",
    )
    matrix = SimpleNamespace(server_environment={"IMAP_MCP_FAKE_NOW_UTC": "2026-10-01T08:00:00Z"})
    result = _fake_now(spec, matrix)  # type: ignore[arg-type]
    assert result == "2026-11-02T12:00:00Z"
    assert "T09:00:00Z" not in result

    # 2. Date-only string without time is rejected with ValueError
    bad_spec = ScenarioSpec(
        id="S1", name="S1", feature_file="03", feature_name="S",
        server_date="2026-11-02",
    )
    with pytest.raises(ValueError, match="must be a full ISO timestamp with time"):
        _fake_now(bad_spec, matrix)  # type: ignore[arg-type]

    # 3. When spec has no server_date, matrix configured value is used
    no_date_spec = ScenarioSpec(id="S1", name="S1", feature_file="03", feature_name="S")
    assert _fake_now(no_date_spec, matrix) == "2026-10-01T08:00:00Z"  # type: ignore[arg-type]


def test_t09_real_features_against_all_variants() -> None:
    # Validate that every real feature (01..05) parses and verifies against all 3 variants
    variants = [
        ("today", "fixtures/policies/today.yaml"),
        ("fixed", "fixtures/policies/fixed.yaml"),
        ("format-hint", "fixtures/policies/fixed.yaml"),
    ]
    for spec in FEATURES.scenarios.values():
        for variant_name, policy_file in variants:
            policy = policy_for(spec, policy_file, variant=variant_name)
            assert policy["name"] == "usability-agent"


def test_t09_mutated_policy_deviations_trigger_value_error() -> None:
    fixed_spec = FEATURES.scenarios["R1"]
    fixed_yaml_path = "fixtures/policies/fixed.yaml"
    template_str = (pytest.importorskip("pathlib").Path("usability") / fixed_yaml_path).read_text(encoding="utf-8")
    base_template = yaml.safe_load(template_str)

    # 1. 02 deviation: remove draft_edit on fixed
    t1 = copy.deepcopy(base_template)
    for entry in t1["accounts"]["de-gmail"]:
        if entry["path"] == "[Gmail]/Drafts":
            entry["draft_edit"] = False
    with pytest.raises(ValueError, match="has draft_edit=False, expected draft_edit=True"):
        _verify_variant_policy_requirements(fixed_spec, t1, "fixed")

    # 2. 01 deviation: remove draft_append
    spec_01 = FEATURES.scenarios["E1"]
    t2 = copy.deepcopy(base_template)
    for entry in t2["accounts"]["de-gmail"]:
        if entry["path"] == "[Gmail]/Drafts":
            entry["draft_append"] = False
    with pytest.raises(ValueError, match="has draft_append=False, expected draft_append=True"):
        _verify_variant_policy_requirements(spec_01, t2, "fixed")

    # 3. 03 deviation: change default on INBOX
    spec_03 = FEATURES.scenarios["S1"]
    t3 = copy.deepcopy(base_template)
    for entry in t3["accounts"]["de-gmail"]:
        if entry["path"] == "INBOX":
            entry["default"] = "METADATA"
    with pytest.raises(ValueError, match="has default 'METADATA', expected 'FULL'"):
        _verify_variant_policy_requirements(spec_03, t3, "fixed")

    # 4. 04 deviation: change move_out on INBOX
    spec_04 = FEATURES.scenarios["A1"]
    t4 = copy.deepcopy(base_template)
    for entry in t4["accounts"]["de-gmail"]:
        if entry["path"] == "INBOX":
            entry["move_out"] = False
    with pytest.raises(ValueError, match="has move_out=False, expected move_out=True"):
        _verify_variant_policy_requirements(spec_04, t4, "fixed")
