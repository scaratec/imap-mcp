"""matrix.yaml → validated matrix and the list of planned runs."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class MatrixError(Exception):
    """matrix.yaml is missing, malformed or inconsistent."""


KNOWN_HARNESSES = ("antigravity", "copilot", "claude-code", "opencode")


@dataclass(frozen=True)
class HarnessConfig:
    name: str
    binary: str
    version_observed: str
    headless: list[str]
    resume_flag: str
    model_flag: str
    mcp_isolation: str
    instructions_file: str
    structured_output: str


@dataclass(frozen=True)
class VariantConfig:
    name: str
    description: str
    policy: str


@dataclass(frozen=True)
class Limits:
    wall_clock_seconds: int
    turns: int
    tool_calls: int
    feedback_seconds: int = 240


@dataclass(frozen=True)
class JudgeConfig:
    harness: str
    model: str
    fallback_models: tuple[str, ...] = ()
    timeout_seconds: int = 180
    judge_measurements: bool = True


@dataclass(frozen=True)
class Selection:
    harnesses: list[str]
    models: list[str]
    server_variants: list[str]
    scenarios: list[str]
    repetitions: int


@dataclass
class Matrix:
    path: Path
    harnesses: dict[str, HarnessConfig]
    models: dict[str, dict[str, str]]
    variants: dict[str, VariantConfig]
    repetitions: int
    server_environment: dict[str, str]
    limits: Limits
    simulator_default: str
    simulator_topics: dict[str, list[str]]
    pilot: Selection
    smoke: Selection
    judge: JudgeConfig
    raw: dict[str, Any] = field(default_factory=dict)

    def model_id(self, slot: str, harness: str) -> str:
        try:
            return self.models[slot][harness]
        except KeyError as exc:
            raise MatrixError(f"no model for slot {slot!r} and harness {harness!r}") from exc


@dataclass(frozen=True)
class PlannedRun:
    harness: str
    model_slot: str
    model: str
    variant: str
    scenario: str
    repetition: int

    @property
    def label(self) -> str:
        return f"{self.scenario}/{self.harness}/{self.model_slot}/{self.variant}/r{self.repetition}"


def _req(mapping: Any, key: str, where: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping:
        raise MatrixError(f"{where}: missing key {key!r}")
    return mapping[key]


def _str_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise MatrixError(f"{where}: expected a list of strings")
    return list(value)


def _selection(raw: Any, where: str) -> Selection:
    reps = _req(raw, "repetitions", where)
    if not isinstance(reps, int) or reps < 1:
        raise MatrixError(f"{where}.repetitions: positive integer expected")
    return Selection(
        harnesses=_str_list(_req(raw, "harnesses", where), f"{where}.harnesses"),
        models=_str_list(_req(raw, "models", where), f"{where}.models"),
        server_variants=_str_list(_req(raw, "server_variants", where), f"{where}.server_variants"),
        scenarios=_str_list(_req(raw, "scenarios", where), f"{where}.scenarios"),
        repetitions=reps,
    )


def load_matrix(path: Path) -> Matrix:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MatrixError(f"{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise MatrixError(f"{path}: top level must be a mapping")
    if raw.get("schema_version") != 1:
        raise MatrixError(f"{path}: schema_version 1 expected")

    harnesses: dict[str, HarnessConfig] = {}
    for name, h in _req(raw, "harnesses", "matrix").items():
        if name not in KNOWN_HARNESSES:
            raise MatrixError(f"harnesses.{name}: no driver for this harness")
        where = f"harnesses.{name}"
        harnesses[name] = HarnessConfig(
            name=name,
            binary=str(_req(h, "binary", where)),
            version_observed=str(h.get("version_observed", "")),
            headless=_str_list(_req(h, "headless", where), f"{where}.headless"),
            resume_flag=str(_req(h, "resume_flag", where)),
            model_flag=str(_req(h, "model_flag", where)),
            mcp_isolation=str(_req(h, "mcp_isolation", where)),
            instructions_file=str(_req(h, "instructions_file", where)),
            structured_output=str(h.get("structured_output", "none")),
        )

    models_raw = _req(raw, "models", "matrix")
    models: dict[str, dict[str, str]] = {}
    for slot, per in models_raw.items():
        if not isinstance(per, dict):
            raise MatrixError(f"models.{slot}: mapping expected")
        models[slot] = {str(k): str(v) for k, v in per.items()}
        for h in per:
            if h not in harnesses:
                raise MatrixError(f"models.{slot}.{h}: unknown harness")

    variants: dict[str, VariantConfig] = {}
    for name, v in _req(raw, "server_variants", "matrix").items():
        variants[name] = VariantConfig(
            name=name,
            description=str(v.get("description", "")),
            policy=str(_req(v, "policy", f"server_variants.{name}")),
        )

    limits_raw = _req(raw, "limits", "matrix")
    limits = Limits(
        wall_clock_seconds=int(_req(limits_raw, "wall_clock_seconds", "limits")),
        turns=int(_req(limits_raw, "turns", "limits")),
        tool_calls=int(_req(limits_raw, "tool_calls", "limits")),
        feedback_seconds=int(limits_raw.get("feedback_seconds", 240)),
    )

    sim = _req(raw, "user_simulator", "matrix")
    topics_raw = _req(sim, "topics", "user_simulator")
    topics = {str(k): _str_list(v, f"user_simulator.topics.{k}") for k, v in topics_raw.items()}

    judge_raw = raw.get("judge") or {"harness": "claude-code", "model_slot": "reference"}
    judge_harness = str(judge_raw.get("harness", "claude-code"))
    if judge_harness != "claude-code":
        raise MatrixError("judge.harness: only claude-code is implemented")
    judge_model = judge_raw.get("model") or models.get(
        str(judge_raw.get("model_slot", "reference")), {}
    ).get(judge_harness)
    if not judge_model:
        raise MatrixError("judge: no model resolved")
    judge = JudgeConfig(
        harness=judge_harness,
        model=str(judge_model),
        fallback_models=tuple(str(m) for m in judge_raw.get("fallback_models") or ()),
        timeout_seconds=int(judge_raw.get("timeout_seconds", 180)),
        judge_measurements=bool(judge_raw.get("judge_measurements", True)),
    )

    pilot = _selection(_req(raw, "pilot", "matrix"), "pilot")
    smoke_raw = raw.get("smoke") or {
        "harnesses": list(harnesses),
        "models": ["reference"],
        "server_variants": ["fixed"],
        "scenarios": ["E1"],
        "repetitions": 1,
    }
    smoke = _selection(smoke_raw, "smoke")

    env = {str(k): str(v) for k, v in (raw.get("server_environment") or {}).items()}

    matrix = Matrix(
        path=path,
        harnesses=harnesses,
        models=models,
        variants=variants,
        repetitions=int(raw.get("repetitions", 3)),
        server_environment=env,
        limits=limits,
        simulator_default=str(sim.get("default_answer", "")),
        simulator_topics=topics,
        pilot=pilot,
        smoke=smoke,
        judge=judge,
        raw=raw,
    )
    for sel_name, sel in (("pilot", pilot), ("smoke", smoke)):
        validate_selection(matrix, sel, sel_name)
    return matrix


def validate_selection(matrix: Matrix, sel: Selection, where: str = "selection") -> None:
    for h in sel.harnesses:
        if h not in matrix.harnesses:
            raise MatrixError(f"{where}: unknown harness {h!r}")
    for slot in sel.models:
        if slot not in matrix.models:
            raise MatrixError(f"{where}: unknown model slot {slot!r}")
        for h in sel.harnesses:
            matrix.model_id(slot, h)
    for v in sel.server_variants:
        if v not in matrix.variants:
            raise MatrixError(f"{where}: unknown server variant {v!r}")


def plan_runs(matrix: Matrix, sel: Selection, known_scenarios: set[str]) -> list[PlannedRun]:
    validate_selection(matrix, sel)
    for s in sel.scenarios:
        if s not in known_scenarios:
            raise MatrixError(f"unknown scenario {s!r} (known: {', '.join(sorted(known_scenarios))})")
    runs: list[PlannedRun] = []
    for scenario, variant, slot, harness, rep in itertools.product(
        sel.scenarios, sel.server_variants, sel.models, sel.harnesses, range(1, sel.repetitions + 1)
    ):
        runs.append(
            PlannedRun(
                harness=harness,
                model_slot=slot,
                model=matrix.model_id(slot, harness),
                variant=variant,
                scenario=scenario,
                repetition=rep,
            )
        )
    return runs
