"""One run: fixture → server → harness turns → feedback → evidence → grade → record."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import traceback
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from support.trace_collector import TraceCollector

from . import REPO_ROOT
from .evidence import Evidence, TranscriptTurn, file_entries
from .features import FeatureSet, ScenarioSpec
from .feedback import RETRY_PROMPT, check_feedback, feedback_prompt
from .fixtures import GmailState, build_mailbox, seed_state, snapshot
from .grading import NOT_ASSESSABLE, GradeResult, grade
from .harness import DRIVERS, McpServerSpec, TurnResult, Usage
from .harness.base import MCP_SERVER_NAME, IsolationBreach
from .judge import Judge, JudgeRequest
from .matrix import Matrix, PlannedRun
from .measurements import feedback_measurements, run_metrics, scenario_measurements
from .mock import MockGmail
from .server import ServerInfo
from .serverconf import write_server_config
from .simulator import answer as simulator_answer

SPAN_FLUSH_SECONDS = 2.0


@dataclass
class RunContext:
    matrix: Matrix
    features: FeatureSet
    feedback_schema: dict[str, Any]
    server: ServerInfo
    judge: Judge
    results_dir: Path
    collect_feedback: bool = True
    keep_temp: bool = False
    temp_root: Path | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def make_run_id(run: PlannedRun) -> str:
    return f"{run.scenario}-{run.harness}-{run.model_slot}-{run.variant}-r{run.repetition}-{uuid.uuid4().hex[:6]}"


def _read_audit(audit_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(audit_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def _fake_now(spec: ScenarioSpec, matrix: Matrix) -> str:
    configured = matrix.server_environment.get("IMAP_MCP_FAKE_NOW_UTC", "")
    if spec.server_date and not configured.startswith(spec.server_date):
        return f"{spec.server_date}T09:00:00Z"
    return configured


def _known_folders(state: GmailState, fixture: Any) -> set[str]:
    from .fixtures import decode_mutf7

    names = {decode_mutf7(f) for f in state.all_folders()}
    names |= {decode_mutf7(wire) for _canonical, wire, _flags in fixture.localized}
    return names


def _sum_usage(turns: list[TranscriptTurn]) -> Usage:
    total = Usage()
    for t in turns:
        total.add(t.result.usage)
    return total


def _judge_error_misreadings(ctx: RunContext, ev: Evidence, form: dict[str, Any]) -> dict[str, Any] | None:
    errors = [e for e in form.get("errors_encountered") or [] if isinstance(e, dict)]
    if not errors or not ctx.matrix.judge.judge_measurements:
        return None
    adr = REPO_ROOT / "docs" / "adr"
    context = "\n\n".join(
        p.read_text(encoding="utf-8") for p in sorted(adr.glob("0017-*.md")) + sorted(adr.glob("0027-*.md"))
    )
    listing = "\n".join(
        f"- Werkzeug {e.get('tool')}: wörtlich {e.get('verbatim')!r}; Interpretation des Agenten: "
        f"{e.get('your_interpretation')!r}" for e in errors
    )
    request = JudgeRequest(
        scenario=f"{ev.scenario.id} — Feedback-Formular",
        tasks="(Feedback-Formular nach der Aufgabe)",
        criterion="Mindestens eine Fehlerinterpretation des Agenten widerspricht der dokumentierten Bedeutung "
        "des Fehlers in ADR 0017 oder ADR 0027. Nenne im reason jede solche Fehldeutung (Werkzeug + warum).",
        facts=listing,
        ground_truth=context[:30000],
        tool_calls="(nicht relevant)",
        final_answer="(nicht relevant)",
    )
    judgement = ctx.judge.judge(request)
    return {"misreading": judgement.holds, "judgement": judgement.to_json()}


def execute_run(ctx: RunContext, run: PlannedRun, run_id: str, out_dir: Path) -> dict[str, Any]:
    """Play one run. Never raises: failures become `not-assessable` records."""
    matrix = ctx.matrix
    spec = ctx.features.scenarios[run.scenario]
    hcfg = matrix.harnesses[run.harness]
    evidence_dir = out_dir / "evidence" / run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f"imap-mcp-usability-{run_id}-", dir=ctx.temp_root))
    record: dict[str, Any] = {
        "run_id": run_id,
        "started_at": _now(),
        "scenario": run.scenario,
        "scenario_name": spec.name,
        "feature_file": spec.feature_file,
        "harness": run.harness,
        "harness_binary": hcfg.binary,
        "harness_version": None,
        "model_slot": run.model_slot,
        "model_requested": run.model,
        "model_resolved": None,
        "server_variant": run.variant,
        "server_variant_policy": matrix.variants[run.variant].policy if spec.custom_policy is None
        else f"scenario table ({spec.feature_file})",
        "server_version": ctx.server.version,
        "tool_set_version": ctx.server.tool_set_version,
        "repetition": run.repetition,
        "mcp_client": None,
        "grade": NOT_ASSESSABLE,
        "grade_cause": None,
        "evidence_dir": str(evidence_dir.relative_to(out_dir)),
    }
    mock: MockGmail | None = None
    collector: TraceCollector | None = None
    turns: list[TranscriptTurn] = []
    infra_error: str | None = None
    limit_hit: str | None = None
    try:
        fixture = build_mailbox(spec)
        state = GmailState()
        seed_state(state, fixture)
        mock = MockGmail(state)
        mock.start()
        mailbox_start = mock.call(lambda: snapshot(state))
        server_cfg = write_server_config(temp / "server", spec, matrix.variants[run.variant].policy, mock.port)
        if spec.sink_empty and any(server_cfg.sink_dir.iterdir()):
            raise RuntimeError("attachment sink is not empty")
        collector = TraceCollector(evidence_dir / "spans.jsonl")
        collector.start()

        resource = ",".join(
            f"{k}={v}" for k, v in {
                "run.id": run_id, "harness": run.harness, "model": run.model.replace(",", "_"),
                "scenario": run.scenario, "server.variant": run.variant,
            }.items()
        )
        env = dict(matrix.server_environment)
        env.update({
            "IMAP_MCP_CONFIG_DIR": str(server_cfg.config_dir),
            "IMAP_MCP_CALLER_ID": spec.caller,
            "IMAP_MCP_FAKE_NOW_UTC": _fake_now(spec, matrix),
            "OTEL_EXPORTER_OTLP_ENDPOINT": collector.endpoint,
            "OTEL_RESOURCE_ATTRIBUTES": resource,
        })
        mcp = McpServerSpec(MCP_SERVER_NAME, str(ctx.server.binary), ["--transport", "stdio"], env)
        workdir = temp / "work"
        workdir.mkdir(parents=True)
        driver = DRIVERS[run.harness](
            hcfg, run_dir=temp, workdir=workdir, log_dir=evidence_dir / "transcript", mcp=mcp,
            model=run.model, known_tools=set(ctx.server.tools),
        )
        record["isolation"] = driver.restrictions
        unavailable = driver.available()
        if unavailable:
            raise _Infra(unavailable)
        record["harness_version"] = driver.version()
        driver.prepare(spec.instructions_statement)
        driver.preflight()
        record["instructions_file"] = hcfg.instructions_file if spec.instructions_statement else None

        # ------------------------------------------------------------ turns
        pending: list[tuple[str, str]] = [(t.kind, t.prompt) for t in spec.turns]
        session_id = driver.new_session_id()
        start = time.monotonic()
        deadline = start + matrix.limits.wall_clock_seconds
        index = 0
        last_task_end = start
        while pending:
            source, prompt = pending.pop(0)
            if index >= matrix.limits.turns:
                limit_hit = f"turns: more than {matrix.limits.turns} agent turns"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                limit_hit = f"wall_clock: more than {matrix.limits.wall_clock_seconds} s"
                break
            audit_before = len(_read_audit(server_cfg.audit_dir))
            result = driver.run_turn(prompt, session_id=session_id, index=index, label=source,
                                     timeout=remaining)
            if (result.error is None and any(tc.is_mcp for tc in result.tool_calls)
                    and len(_read_audit(server_cfg.audit_dir)) == audit_before):
                result.error = ("isolation_breach: the transcript shows imap tool calls the run's "
                                "server never audited")
            last_task_end = time.monotonic()
            turn = TranscriptTurn(index, source, prompt, result)
            turns.append(turn)
            index += 1
            if result.session_id:
                session_id = result.session_id
            if result.error:
                infra_error = result.error
                if result.timed_out:
                    limit_hit = f"wall_clock: more than {matrix.limits.wall_clock_seconds} s"
                break
            if result.mcp_status and "connected" not in result.mcp_status and "pending" not in result.mcp_status:
                infra_error = f"mcp_unavailable: {result.mcp_status}"
                break
            if not session_id and pending:
                infra_error = "crash: harness returned no session id; cannot resume"
                break
            audit_count = sum(1 for r in _read_audit(server_cfg.audit_dir) if r.get("caller_id") == spec.caller)
            if audit_count > matrix.limits.tool_calls:
                limit_hit = f"tool_calls: {audit_count} > {matrix.limits.tool_calls}"
                break
            sim = simulator_answer(
                result.final_text,
                topics=matrix.simulator_topics,
                scenario_answers=spec.simulator_answers,
                default_answer=spec.simulator_default or matrix.simulator_default,
            )
            if sim is not None:
                turn.simulator = sim
                pending.insert(0, ("simulator", sim.text))
        wall_clock = round(last_task_end - start, 2)

        # --------------------------------------------------------- feedback
        feedback: dict[str, Any] = {"status": None, "attempts": 0, "form": None, "attempt_log": []}
        feedback_turns: list[TranscriptTurn] = []
        if ctx.collect_feedback and infra_error is None and turns and session_id:
            prompt = feedback_prompt(ctx.features.feedback_prompt, ctx.feedback_schema)
            fb_deadline = time.monotonic() + matrix.limits.feedback_seconds
            for attempt in (1, 2):
                remaining = fb_deadline - time.monotonic()
                if remaining <= 5:
                    feedback["error"] = "timeout: feedback budget exhausted"
                    break
                schema = ctx.feedback_schema if driver.supports_structured_output() else None
                res = driver.run_turn(prompt, session_id=session_id, index=index,
                                      label="feedback" if attempt == 1 else "feedback-retry",
                                      timeout=remaining, json_schema=schema)
                index += 1
                feedback_turns.append(TranscriptTurn(index - 1, "feedback", prompt, res))
                feedback["attempts"] = attempt
                if res.error:
                    feedback["status"] = feedback["status"] or "refused"
                    feedback["error"] = res.error
                    break
                check = check_feedback(res.final_text, ctx.feedback_schema)
                feedback.update(status=check.status, form=check.form, validation_error=check.error)
                feedback["attempt_log"].append({"status": check.status, "error": check.error})
                if check.status == "valid":
                    break
                prompt = RETRY_PROMPT.format(error=check.error)

        # --------------------------------------------------------- evidence
        time.sleep(SPAN_FLUSH_SECONDS)
        audit = _read_audit(server_cfg.audit_dir)
        spans = [
            asdict(s) for s in collector.sink.spans()
            if s.resource_attributes.get("run.id") == run_id
        ]
        mailbox_end = mock.call(lambda: snapshot(state))
        known_folders = mock.call(lambda: _known_folders(state, fixture))
        ev = Evidence(
            run_id=run_id, scenario=spec, fixture=fixture, audit=audit, spans=spans,
            mailbox_start=mailbox_start, mailbox_end=mailbox_end, turns=turns,
            sink_dir=str(server_cfg.sink_dir), sink_files=file_entries(server_cfg.sink_dir),
            wall_clock_seconds=wall_clock, known_folders=known_folders,
        )
        (evidence_dir / "audit.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in audit), encoding="utf-8"
        )
        (evidence_dir / "mailbox_start.json").write_text(json.dumps(mailbox_start, ensure_ascii=False, indent=1), encoding="utf-8")
        (evidence_dir / "mailbox_end.json").write_text(json.dumps(mailbox_end, ensure_ascii=False, indent=1), encoding="utf-8")
        (evidence_dir / "sink_files.json").write_text(json.dumps(ev.sink_files, indent=1), encoding="utf-8")

        # ----------------------------------------------------------- grade
        if infra_error or limit_hit:
            result_grade = GradeResult(NOT_ASSESSABLE, infra_error or limit_hit, None, [])
        else:
            result_grade = grade(ev, ctx.judge)
        record["grade"] = result_grade.grade
        record["grade_cause"] = result_grade.cause
        record["rubric"] = [asdict(c) for c in result_grade.criteria]
        record["limit_hit"] = limit_hit
        record["infra_error"] = infra_error

        task_usage = _sum_usage(turns)
        measurements = run_metrics(ev, asdict(task_usage), len(turns))
        measurements.update(scenario_measurements(ev))
        measurements["feedback_status"] = feedback["status"]
        measurements["feedback_attempts"] = feedback["attempts"] or None
        if feedback.get("status") == "valid" and feedback.get("form"):
            measurements.update(feedback_measurements(ev, feedback["form"], record["grade"], set(ctx.server.tools)))
            misreadings = _judge_error_misreadings(ctx, ev, feedback["form"])
            measurements["error_misreadings"] = misreadings
        record["measurements"] = measurements
        record["feedback"] = feedback

        resolved = sorted({t.result.model_resolved for t in turns + feedback_turns if t.result.model_resolved})
        record["model_resolved"] = ",".join(resolved) or None
        clients = {
            (s["attributes"].get("mcp.client.name"), s["attributes"].get("mcp.client.version"))
            for s in ev.tool_spans() if s["attributes"].get("mcp.client.name")
        }
        record["mcp_client"] = [{"name": n, "version": v} for n, v in sorted(clients)] or None
        total_usage = _sum_usage(turns + feedback_turns)
        record["usage_task"] = asdict(task_usage)
        record["usage_total"] = asdict(total_usage)
        record["simulator"] = [
            {"after_turn": t.index, "topics": t.simulator.topics, "answer": t.simulator.text,
             "question": t.simulator.question}
            for t in turns if t.simulator
        ]
        record["final_answer"] = ev.final_answer
        record["transcript"] = [
            {"index": t.index, "source": t.source, "prompt": t.prompt, **_turn_json(t.result)}
            for t in turns + feedback_turns
        ]
        record["spans"] = {"count": len(spans), "file": "spans.jsonl"}
    except IsolationBreach as exc:
        record["grade"], record["grade_cause"] = NOT_ASSESSABLE, f"isolation_breach: {exc}"
        record["infra_error"] = record["grade_cause"]
    except _Infra as exc:
        record["grade"], record["grade_cause"] = NOT_ASSESSABLE, str(exc)
        record["infra_error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - any runner fault ends only this run
        record["grade"] = NOT_ASSESSABLE
        record["grade_cause"] = f"runner_error: {exc!r}"
        record["infra_error"] = record["grade_cause"]
        (evidence_dir / "runner_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        if mock is not None:
            mock.stop()
        if collector is not None:
            collector.stop()
        if not ctx.keep_temp:
            shutil.rmtree(temp, ignore_errors=True)
        else:
            record["temp_dir"] = str(temp)
    record["finished_at"] = _now()
    return record


def _turn_json(result: TurnResult) -> dict[str, Any]:
    data = result.to_json()
    data["tool_calls"] = [
        {"name": tc["name"], "tool": tc["tool"], "is_mcp": tc["is_mcp"],
         "input": tc["input"], "error": tc["error"], "output": (tc["output"] or "")[:800] or None}
        for tc in data["tool_calls"]
    ]
    return data


class _Infra(Exception):
    """Harness cannot run (binary missing, …)."""


__all__ = ["RunContext", "execute_run", "make_run_id"]
