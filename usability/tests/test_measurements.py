"""Measurements computed from recorded-style sample evidence (features 01–07)."""

from __future__ import annotations

from typing import Any

from samples import FEATURES, append_draft, audit, evidence, span, turn

from usability.runner.harness.base import ToolCall
from usability.runner.measurements import (
    feedback_measurements,
    named_refs,
    run_metrics,
    scenario_measurements,
)


def _s1_evidence(final_answer: str) -> Any:
    records = audit(
        {"tool": "list_folders", "args_summary": {"account": "de-gmail"}},
        {"tool": "list_folders", "args_summary": {"account": "de-gmail"}},
        {"tool": "list_messages", "args_summary": {"account": "de-gmail", "folder": "INBOX"}},
        {"tool": "fetch_envelope", "args_summary": {"account": "de-gmail", "folder": "INBOX", "uid": 1001}},
        {"tool": "search", "args_summary": {"account": "de-gmail", "folder": "INBOX/Rechnungseingänge"},
         "decision": "DENY", "reason": "x"},
        {"tool": "move", "args_summary": {}, "result": "ERROR", "reason": "uid_not_found"},
    )
    spans = [
        span("list_folders", {"account": "de-gmail"}, {"folders": []}, start=0, size=100),
        span("list_folders", {"account": "de-gmail"}, {"folders": []}, start=20, size=100),
        span("list_messages", {"account": "de-gmail", "folder": "INBOX", "criteria": {"from_domain": "nordlicht.example.org"}},
             {"messages": [{"uid": 1001, "from": "a", "subject": "s", "date": "d"}], "has_more": True,
              "page_offset": 0, "applied_scope": "recent_7d", "outside_scope_total": 5},
             start=40, size=900, extra={"mcp.result.has_more": True}),
        span("fetch_envelope", {"account": "de-gmail", "folder": "INBOX", "uid": 1001}, {"from": "a"}, start=60, size=50),
        span("search", {"account": "de-gmail", "folder": "INBOX/Rechnungseingänge", "scope": "all",
                        "criteria": {"since": "2026-07-01", "before": "2026-10-01"}},
             {"decision": "DENY", "applied_scope": "all_time"}, start=80, size=60),
        span("imap", {}, None, start=100, dur_ms=500, name="imap.connect"),
        span("imap", {}, None, start=700, dur_ms=250, name="imap.uid_search"),
    ]
    t = turn(0, final_answer, [("list_folders", True)])
    t.result.tool_calls = [ToolCall(name="x", tool="search", is_mcp=True, input={"folder": "[Gmail]/Entwurf"}),
                           ToolCall(name="x", tool="search", is_mcp=True, input={"folder": "[Gmail]/Drafts"})]
    return evidence("S1", audit_records=records, spans=spans, turns=[t])


def test_run_metrics_from_sample_evidence() -> None:
    ev = _s1_evidence("…")
    m = run_metrics(ev, {"input_tokens": 1000, "output_tokens": 50}, turns=1)
    assert m["tool_calls"] == 6
    assert m["ideal_ratio"] == 3.0  # S1 ideal path: 2 calls
    assert m["calls_by_tool"] == {"fetch_envelope": 1, "list_folders": 2, "list_messages": 1, "move": 1, "search": 1}
    assert [c["tool"] for c in m["failed_calls"]] == ["move"]
    assert [c["tool"] for c in m["denied_calls"]] == ["search"]
    assert [c["tool"] for c in m["repeated_calls"]] == ["list_folders"]
    assert m["refetch_after_listing"] == [{"folder": "INBOX", "uid": 1001}]
    assert m["repeated_discovery"] == [{"tool": "list_folders", "account": "de-gmail"}]
    assert m["wrong_folder_name_calls"] == [{"tool": "search", "folder": "[Gmail]/Entwurf"}]
    assert m["unfinished_paging"] == [{"tool": "list_messages", "folder": "INBOX"}]
    assert m["response_bytes"] == 1210
    assert m["largest_response_bytes"] == {"bytes": 900, "tool": "list_messages"}
    assert m["tokens"] == {"input": 1000, "output": 50}
    assert m["response_token_share"] == 1.21
    assert m["server_seconds"] == 0.05
    assert m["imap_seconds"] == 0.75
    assert m["imap_connections"] == 1
    assert m["turns"] == 1
    assert m["wall_clock_seconds"] == 12.5


def test_tokens_unknown_when_harness_reports_none() -> None:
    m = run_metrics(_s1_evidence("…"), {"input_tokens": None, "output_tokens": None}, turns=1)
    assert m["tokens"] == "unknown"
    assert m["response_token_share"] is None


def test_s1_scenario_measurements() -> None:
    ev = _s1_evidence(
        "Gefunden: Ihre Rechnung Oktober 2026 (30.10.2026), September 2026, August 2026, "
        "die Zahlungserinnerung zur Rechnung Juni 2026 und Rechnung Dezember 2025 (NL-2025-12)."
    )
    m = scenario_measurements(ev)
    assert m["recall"] == round(4 / 6, 3)  # 1001, 1002, 1003, 2003
    assert m["precision"] == round(4 / 5, 3)  # + 1004
    assert m["folders_searched"] == ["INBOX", "INBOX/Rechnungseingänge"]
    assert m["scope_used"] == ["recent_7d", "all_time"]
    assert m["noticed_outside_scope"] is True


def test_named_refs_uses_uid_attachment_amount_date_and_distinct_words() -> None:
    rows = FEATURES.scenarios["S1"].messages
    assert named_refs("UID 2002", rows) == [2002]
    assert named_refs("NL-2026-07.pdf", rows) == [2001]
    assert named_refs("Betrag 91,15 €", rows) == [1002]
    assert named_refs("vom 28.08.2026", rows) == [1003]
    assert named_refs("Juni 2026", rows) == []  # ambiguous: 2002 and the reminder 1004
    assert 1005 in named_refs("die Reisebuchung", rows)


def test_window_measurements() -> None:
    ev = _s1_evidence("…")
    ev.scenario = FEATURES.scenarios["S2"]
    m = scenario_measurements(ev)
    assert m["window_expression"] == "since/before"
    assert m["window_correct"] is True


def test_draft_measurements() -> None:
    def drafts(s: Any) -> None:
        append_draft(s, "mara.lindqvist@example.org", "Alt", html=False,
                     body="wir müssen den Termin am Donnerstag auf Dienstag nächster\nWoche verschieben.\n")
        append_draft(s, "mara.lindqvist@example.org", "Neu", html=True)

    records = audit(
        {"tool": "create_draft", "args_summary": {"folder": "[Gmail]/Drafts"}},
        {"tool": "create_draft", "args_summary": {"folder": "[Gmail]/Drafts"}},
        {"tool": "discard_draft", "args_summary": {}, "result": "ERROR"},
        {"tool": "move", "args_summary": {"source": {"folder": "[Gmail]/Drafts", "uid": 1}}, "decision": "DENY"},
    )
    ev = evidence("R1", mutate=drafts, audit_records=records)
    m = scenario_measurements(ev)
    assert m["revision_mechanism"] == "create_only"
    assert m["drafts_left"] == 2
    assert [c["tool"] for c in m["failed_cleanup_attempts"]] == ["discard_draft", "move"]
    ev.scenario = FEATURES.scenarios["E1"]
    m = scenario_measurements(ev)
    assert [d["mime"] for d in m["draft_format"]] == ["text/plain", "multipart/alternative[text/plain,text/html]"]
    assert m["hard_wrapped_text"] is True


def test_discard_and_lookup_measurements() -> None:
    records = audit(
        {"tool": "create_draft", "args_summary": {"folder": "[Gmail]/Drafts"}},
        {"tool": "search", "args_summary": {"folder": "[Gmail]/Drafts"}},
        {"tool": "list_drafts", "args_summary": {"folder": "[Gmail]/Drafts"}},
        {"tool": "move", "args_summary": {"source": {"folder": "[Gmail]/Drafts", "uid": 3}}},
    )
    m = scenario_measurements(evidence("R2", audit_records=records))
    assert m["discard_mechanism"] == "move_by_uid"
    assert m["uid_lookup_calls"] == 2


def test_feedback_cross_checks() -> None:
    def drafts(s: Any) -> None:
        append_draft(s, "mara.lindqvist@example.org", "Vergessen", html=False)

    records = audit(
        {"tool": "list_accounts", "args_summary": {}},
        {"tool": "create_draft", "args_summary": {}, "result": "ERROR", "reason": "x"},
    )
    spans = [span("create_draft", {}, {"result": "ERROR", "error": {"message": "folder_absent: no such folder"}}, start=0)]
    ev = evidence("E1", mutate=drafts, audit_records=records, spans=spans,
                  turns=[turn(0, "Fertig.", simulator_topics=["body format"])])
    form = {
        "tools_used": [{"tool": "mcp__imap__create_draft", "purpose": "p"}, {"tool": "imap-update_draft", "purpose": "p"}],
        "errors_encountered": [
            {"tool": "create_draft", "verbatim": "folder_absent: no such folder", "your_interpretation": "i",
             "what_you_did_next": "n"},
            {"tool": "create_draft", "verbatim": "invented text", "your_interpretation": "i", "what_you_did_next": "n"},
        ],
        "confidence_complete": 90,
        "considered_asking": {"asked": False, "considered": True},
        "leftovers": [],
    }
    known = {"create_draft", "update_draft", "list_accounts"}
    m = feedback_measurements(ev, form, "missed", known)
    assert m["tools_claimed_not_called"] == ["update_draft"]
    assert m["tools_called_not_claimed"] == ["list_accounts"]
    assert m["errors_quoted_correctly"] == 0.5
    assert m["errors_not_mentioned"] == []
    assert m["overconfident"] is True
    assert m["asked_claim_consistent"] is False
    assert m["leftovers_hidden"] == [{"subject": "Vergessen", "to": "mara.lindqvist@example.org"}]
