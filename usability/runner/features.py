"""Usability feature files → typed scenario specifications.

The feature files are the single source of the fixture data, the tasks,
the simulator answers and the rubric. This module parses them with
behave's Gherkin parser and maps every step onto a typed spec. A step
the runner does not understand is a deterministic infrastructure fault
(ADR 0039): parsing fails with `FeatureError` and the runner exits
non-zero before any agent is started.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from behave.parser import ParserError, parse_file


class FeatureError(Exception):
    """A usability feature file cannot be parsed or mapped."""


@dataclass(frozen=True)
class FolderLocalization:
    canonical: str
    localized: str


@dataclass(frozen=True)
class MessageRow:
    """One row of a `the mailbox ... holds messages:` table."""

    ref: int
    folder: str
    sender: str | None
    recipient: str | None
    subject: str
    date: str
    attachment: str | None
    invoice: bool | None
    amount_eur: str | None


@dataclass(frozen=True)
class OwnerDraft:
    folder: str
    to: str
    subject: str
    date: str


@dataclass(frozen=True)
class PolicyRow:
    folder: str
    mode: str
    default: str
    rules: list[dict[str, Any]]
    move_out: bool


@dataclass(frozen=True)
class Criterion:
    """`Then the run is graded "<grade>" if <text>` (text None = otherwise)."""

    grade: str
    text: str | None
    judged: bool

    @property
    def is_otherwise(self) -> bool:
        return self.text is None


@dataclass(frozen=True)
class Measurement:
    name: str
    description: str


@dataclass(frozen=True)
class Turn:
    kind: str  # "task" | "followup"
    prompt: str


@dataclass
class ScenarioSpec:
    id: str
    name: str
    feature_file: str
    feature_name: str
    account: str = ""
    provider: str = ""
    localized: list[FolderLocalization] = field(default_factory=list)
    messages: list[MessageRow] = field(default_factory=list)
    owner_drafts: list[OwnerDraft] = field(default_factory=list)
    empty_folders: list[str] = field(default_factory=list)
    caller: str = ""
    custom_policy: list[PolicyRow] | None = None
    server_date: str | None = None
    sink_empty: bool = False
    simulator_default: str | None = None
    simulator_answers: dict[str, str] = field(default_factory=dict)
    instructions_statement: str | None = None
    ideal_calls: int | None = None
    ideal_path: str = ""
    turns: list[Turn] = field(default_factory=list)
    rubric: list[Criterion] = field(default_factory=list)
    measurements: list[Measurement] = field(default_factory=list)

    @property
    def tasks(self) -> list[Turn]:
        return self.turns


@dataclass
class FeatureSet:
    scenarios: dict[str, ScenarioSpec]
    feedback_prompt: str
    feedback_measurements: list[Measurement]
    run_measurements: list[Measurement]


# ----------------------------------------------------------------- patterns

_P = re.compile
_SEEDED = _P(
    r'^the usability mailbox "(?P<acct>[^"]+)" is seeded fresh with provider "(?P<prov>[^"]+)"'
    r"(?P<loc> and localized folder names:)?$"
)
_EMPTY = _P(r'^the folder "(?P<folder>[^"]+)" of "(?P<acct>[^"]+)" is empty$')
_CALLER_VARIANT = _P(r'^the agent works as caller "(?P<caller>[^"]+)" with the policy of the server variant$')
_CALLER_TABLE = _P(r'^the agent works as caller "(?P<caller>[^"]+)" with a policy that grants:$')
_SIM_DEFAULT = _P(r'^the user simulator answers any question it does not recognise with "(?P<text>[^"]*)"$')
_SIM_TOPIC = _P(r'^the user simulator answers questions about (?:the )?(?P<topic>.+?) with "(?P<text>[^"]*)"$')
_IDEAL = _P(r"^the ideal path takes (?P<n>\d+) tool calls?: (?P<path>.+)$")
_DATE = _P(r'^the server date is pinned to "(?P<date>[^"]+)"$')
_HOLDS = _P(r'^the mailbox "(?P<acct>[^"]+)" holds messages:$')
_SINK_EMPTY = _P(r"^the attachment sink directory is empty$")
_NO_PREF = _P(r"^no format preference is stated in the harness instructions file$")
_PREF = _P(r'^the harness instructions file states "(?P<text>[^"]+)"$')
_OWNER_DRAFT = _P(r'^the folder "(?P<folder>[^"]+)" of "(?P<acct>[^"]+)" holds a draft written by the owner:$')
_TASK = _P(r"^the agent receives the task:$")
_FOLLOWUP = _P(r"^the agent receives the follow-up task:$")
_GRADE_IF = _P(r'^the run is graded "(?P<grade>[a-z-]+)" if (?P<text>.+)$')
_GRADE_OTHERWISE = _P(r'^the run is graded "(?P<grade>[a-z-]+)" otherwise$')
_MEASURE = _P(r'^the measurement "(?P<name>[^"]+)" records (?P<what>.+)$')
_FEEDBACK_PROMPT = _P(r"^the runner resumes the agent's session with the feedback prompt:$")
_SCENARIO_ID = _P(r"^(?P<id>[A-Z]\d+)\s+[—-]\s+")

GRADES = ("fulfilled", "partial", "missed", "misreported", "harmful", "not-assessable")
JUDGED_SUFFIX = "[judged]"


def _bool(value: str) -> bool:
    return value.strip().lower() in ("yes", "true", "1")


def _cell(row: Any, *names: str) -> str | None:
    for name in names:
        if name in row.headings:
            value = row[name].strip()
            return value or None
    return None


def _message_rows(table: Any) -> list[MessageRow]:
    rows: list[MessageRow] = []
    for row in table:
        party = _cell(row, "from / to", "from") or ""
        sender: str | None = party
        recipient: str | None = None
        if party.startswith("to:"):
            sender, recipient = None, party[3:].strip()
        invoice_cell = _cell(row, "invoice")
        rows.append(
            MessageRow(
                ref=int(row["uid"]),
                folder=row["folder"].strip(),
                sender=sender,
                recipient=recipient,
                subject=row["subject"].strip(),
                date=row["date"].strip(),
                attachment=_cell(row, "attachment"),
                invoice=None if invoice_cell is None else _bool(invoice_cell),
                amount_eur=_cell(row, "amount_eur"),
            )
        )
    return rows


def _policy_rows(table: Any) -> list[PolicyRow]:
    import json

    rows: list[PolicyRow] = []
    for row in table:
        rules_raw = (row["rules"] or "[]").strip()
        try:
            rules = json.loads(rules_raw)
        except json.JSONDecodeError as exc:
            raise FeatureError(f"policy rules are not JSON: {rules_raw!r}: {exc}") from exc
        rows.append(
            PolicyRow(
                folder=row["folder"].strip(),
                mode=row["mode"].strip(),
                default=row["default"].strip(),
                rules=rules,
                move_out=_bool(row["move_out"]) if "move_out" in row.headings else False,
            )
        )
    return rows


def _apply_step(spec: ScenarioSpec, step: Any, where: str) -> None:
    text = step.name.strip()
    m: re.Match[str] | None
    if m := _SEEDED.match(text):
        spec.account = m["acct"]
        spec.provider = m["prov"]
        if m["loc"]:
            if step.table is None:
                raise FeatureError(f"{where}: localized folder table missing")
            spec.localized = [
                FolderLocalization(r["canonical"].strip(), r["localized"].strip()) for r in step.table
            ]
        return
    if m := _EMPTY.match(text):
        spec.empty_folders.append(m["folder"])
        return
    if m := _CALLER_VARIANT.match(text):
        spec.caller = m["caller"]
        return
    if m := _CALLER_TABLE.match(text):
        spec.caller = m["caller"]
        if step.table is None:
            raise FeatureError(f"{where}: policy table missing")
        spec.custom_policy = _policy_rows(step.table)
        return
    if m := _SIM_DEFAULT.match(text):
        spec.simulator_default = m["text"]
        return
    if m := _SIM_TOPIC.match(text):
        spec.simulator_answers[m["topic"]] = m["text"]
        return
    if m := _IDEAL.match(text):
        spec.ideal_calls = int(m["n"])
        spec.ideal_path = m["path"]
        return
    if m := _DATE.match(text):
        spec.server_date = m["date"]
        return
    if m := _HOLDS.match(text):
        if step.table is None:
            raise FeatureError(f"{where}: message table missing")
        spec.messages.extend(_message_rows(step.table))
        return
    if _SINK_EMPTY.match(text):
        spec.sink_empty = True
        return
    if _NO_PREF.match(text):
        spec.instructions_statement = None
        return
    if m := _PREF.match(text):
        spec.instructions_statement = m["text"]
        return
    if m := _OWNER_DRAFT.match(text):
        if step.table is None:
            raise FeatureError(f"{where}: owner draft table missing")
        for row in step.table:
            spec.owner_drafts.append(
                OwnerDraft(m["folder"], row["to"].strip(), row["subject"].strip(), row["date"].strip())
            )
        return
    if _TASK.match(text) or _FOLLOWUP.match(text):
        if not step.text:
            raise FeatureError(f"{where}: task without doc string")
        kind = "task" if _TASK.match(text) else "followup"
        spec.turns.append(Turn(kind, step.text.strip()))
        return
    if m := _GRADE_IF.match(text):
        criterion = m["text"].strip()
        judged = criterion.endswith(JUDGED_SUFFIX)
        if judged:
            criterion = criterion[: -len(JUDGED_SUFFIX)].strip()
        spec.rubric.append(Criterion(m["grade"], criterion, judged))
        return
    if m := _GRADE_OTHERWISE.match(text):
        spec.rubric.append(Criterion(m["grade"], None, False))
        return
    if m := _MEASURE.match(text):
        spec.measurements.append(Measurement(m["name"], m["what"].strip()))
        return
    raise FeatureError(f"{where}: unrecognised step: {step.keyword} {text}")


def _validate(spec: ScenarioSpec) -> None:
    where = f"{spec.feature_file}: {spec.id}"
    if not spec.account:
        raise FeatureError(f"{where}: no seeded mailbox")
    if not spec.caller:
        raise FeatureError(f"{where}: no caller")
    if not spec.turns or spec.turns[0].kind != "task":
        raise FeatureError(f"{where}: first turn must be a task")
    if not spec.rubric:
        raise FeatureError(f"{where}: no rubric")
    for criterion in spec.rubric:
        if criterion.grade not in GRADES:
            raise FeatureError(f"{where}: unknown grade {criterion.grade!r}")
    if not spec.rubric[-1].is_otherwise:
        raise FeatureError(f"{where}: rubric must end with an 'otherwise' line")
    if any(c.is_otherwise for c in spec.rubric[:-1]):
        raise FeatureError(f"{where}: 'otherwise' must be the last rubric line")


def _parse(path: Path) -> Any:
    try:
        return parse_file(str(path))
    except ParserError as exc:
        raise FeatureError(f"{path.name}: {exc}") from exc
    except OSError as exc:
        raise FeatureError(f"{path.name}: {exc}") from exc


def parse_scenario_feature(path: Path) -> list[ScenarioSpec]:
    feature = _parse(path)
    if feature is None:
        raise FeatureError(f"{path.name}: empty feature file")
    specs: list[ScenarioSpec] = []
    for scenario in feature.scenarios:
        m = _SCENARIO_ID.match(scenario.name)
        if not m:
            raise FeatureError(f"{path.name}: scenario without id: {scenario.name!r}")
        spec = ScenarioSpec(
            id=m["id"], name=scenario.name, feature_file=path.name, feature_name=feature.name
        )
        background_steps = feature.background.steps if feature.background else []
        for step in [*background_steps, *scenario.steps]:
            _apply_step(spec, step, f"{path.name}:{step.line}")
        _validate(spec)
        specs.append(spec)
    return specs


def _parse_meta_feature(path: Path) -> tuple[str | None, list[Measurement]]:
    """06/07: the feedback prompt and the measurement names."""
    feature = _parse(path)
    prompt: str | None = None
    measurements: list[Measurement] = []
    for scenario in feature.scenarios:
        for step in scenario.steps:
            text = step.name.strip()
            if _FEEDBACK_PROMPT.match(text):
                prompt = (step.text or "").strip()
            elif m := _MEASURE.match(text):
                measurements.append(Measurement(m["name"], m["what"].strip()))
    return prompt, measurements


def load_features(features_dir: Path) -> FeatureSet:
    scenarios: dict[str, ScenarioSpec] = {}
    feedback_prompt: str | None = None
    feedback_measurements: list[Measurement] = []
    run_measurements: list[Measurement] = []
    files = sorted(features_dir.glob("*.feature"))
    if not files:
        raise FeatureError(f"no feature files in {features_dir}")
    for path in files:
        feature = _parse(path)
        tags = set(feature.tags)
        if "feedback" in tags:
            feedback_prompt, feedback_measurements = _parse_meta_feature(path)
            continue
        if "metrics" in tags:
            _, run_measurements = _parse_meta_feature(path)
            continue
        for spec in parse_scenario_feature(path):
            if spec.id in scenarios:
                raise FeatureError(f"duplicate scenario id {spec.id}")
            scenarios[spec.id] = spec
    if not feedback_prompt or "<schema>" not in feedback_prompt:
        raise FeatureError("feedback prompt with <schema> placeholder not found (06_feedback_form.feature)")
    return FeatureSet(scenarios, feedback_prompt, feedback_measurements, run_measurements)
