"""Rubric evaluation: top to bottom, the first criterion that holds sets the grade."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from .criteria import REGISTRY, Outcome
from .evidence import Evidence
from .features import Criterion
from .judge import Judge, Judgement, JudgeRequest

NOT_ASSESSABLE = "not-assessable"


@dataclass
class Decision:
    holds: bool | None
    facts: list[str] = field(default_factory=list)
    judgement: Judgement | None = None
    judged_extra: bool = False
    error: str | None = None


@dataclass
class CriterionResult:
    grade: str
    criterion: str | None
    judged: bool
    holds: bool | None
    facts: list[str]
    judgement: dict[str, Any] | None
    judged_extra: bool = False


@dataclass
class GradeResult:
    grade: str
    cause: str | None
    decided_by: int | None
    criteria: list[CriterionResult]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def select_grade(rubric: list[Criterion], decide: Callable[[Criterion], Decision]) -> GradeResult:
    """Evaluate criteria in order; stop at the first that holds.

    `otherwise` closes the list. A criterion that cannot be decided
    (judge failure) ends the run `not-assessable` with the cause, since
    a later criterion must not win over an undecided earlier one.
    """
    results: list[CriterionResult] = []
    for index, criterion in enumerate(rubric):
        if criterion.is_otherwise:
            results.append(CriterionResult(criterion.grade, None, False, True, [], None))
            return GradeResult(criterion.grade, None, index, results)
        decision = decide(criterion)
        results.append(
            CriterionResult(
                criterion.grade, criterion.text, criterion.judged, decision.holds, decision.facts,
                decision.judgement.to_json() if decision.judgement else None, decision.judged_extra,
            )
        )
        if decision.holds is None:
            cause = decision.error or (decision.judgement.error if decision.judgement else None) or "undecided"
            return GradeResult(NOT_ASSESSABLE, f"criterion {index + 1} undecided: {cause}", index, results)
        if decision.holds:
            return GradeResult(criterion.grade, None, index, results)
    return GradeResult(NOT_ASSESSABLE, "rubric has no 'otherwise' line", None, results)


def _tool_call_digest(ev: Evidence, limit: int = 60) -> str:
    lines = []
    for tool, request, response, attrs in ev.span_calls()[:limit]:
        req = {k: v for k, v in request.items() if k not in ("body_text", "body_html", "rfc822")}
        resp = json.dumps(response, ensure_ascii=False)[:600] if response is not None else "(keine Antwort im Trace)"
        lines.append(f"- {tool} {json.dumps(req, ensure_ascii=False)[:300]} → {resp}")
    others = [tc for tc in ev.tool_calls() if not tc.is_mcp]
    for tc in others[:20]:
        status = f"FEHLER: {tc.error[:150]}" if tc.error else "ok"
        lines.append(f"- [Harness-Werkzeug] {tc.name} {json.dumps(tc.input, ensure_ascii=False)[:200]} → {status}")
    return "\n".join(lines) or "(keine)"


def make_decider(ev: Evidence, judge: Judge) -> Callable[[Criterion], Decision]:
    labels = {"task": "Aufgabe", "followup": "Folgeaufgabe", "simulator": "Antwort des Inhabers auf eine Rückfrage"}
    conversation = [f"[{labels.get(t.source, t.source)}] {t.prompt}" for t in ev.turns if t.source in labels]
    tasks = "\n---\n".join(conversation or [t.prompt for t in ev.scenario.turns])

    def decide(criterion: Criterion) -> Decision:
        assert criterion.text is not None
        evaluator = REGISTRY.get(criterion.text)
        if evaluator is None:
            return Decision(None, error=f"no implementation for criterion: {criterion.text}")
        try:
            outcome: Outcome = evaluator(ev)
        except Exception as exc:  # noqa: BLE001 - evidence gaps must not abort the matrix
            return Decision(None, error=f"criterion evaluation failed: {exc!r}")
        needs_judge = outcome.holds and outcome.judge_question is not None
        if not needs_judge:
            return Decision(outcome.holds, outcome.facts, judged_extra=outcome.judged_extra)
        request = JudgeRequest(
            scenario=f"{ev.scenario.id} — {ev.scenario.name}",
            tasks=tasks,
            criterion=f"{criterion.text}\nZu entscheiden: {outcome.judge_question}",
            facts="\n".join(f"- {f}" for f in outcome.facts) or "(keine)",
            ground_truth=outcome.ground_truth,
            tool_calls=_tool_call_digest(ev),
            final_answer=ev.final_answer or "(leer)",
        )
        judgement = judge.judge(request)
        return Decision(judgement.holds, outcome.facts, judgement, outcome.judged_extra,
                        error=judgement.error)

    return decide


def grade(ev: Evidence, judge: Judge) -> GradeResult:
    return select_grade(ev.scenario.rubric, make_decider(ev, judge))
