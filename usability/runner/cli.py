"""Command line: `python -m usability.runner [--dry-run | --smoke | ...]`.

Exit code policy (ADR 0039): 0 once the matrix ran, whatever the grades;
2 when the runner cannot run at all (invalid matrix.yaml, a feature file
that does not parse, a fixture that cannot be built, a rubric criterion
or measurement without implementation, an invalid feedback schema).
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import USABILITY_DIR

EXIT_OK = 0
EXIT_CANNOT_RUN = 2


class CannotRun(Exception):
    """A deterministic fault of the test infrastructure."""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m usability.runner", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--matrix", type=Path, default=USABILITY_DIR / "matrix.yaml")
    p.add_argument("--features", type=Path, default=USABILITY_DIR / "features")
    p.add_argument("--schema", type=Path, default=USABILITY_DIR / "feedback-form.schema.json")
    p.add_argument("--results-dir", type=Path, default=USABILITY_DIR / "results")
    p.add_argument("--smoke", action="store_true", help="the `smoke` block (one E1 run per harness)")
    p.add_argument("--harness", action="append", help="restrict harnesses (repeatable)")
    p.add_argument("--model-slot", action="append", help="restrict model slots (repeatable)")
    p.add_argument("--variant", action="append", help="restrict server variants (repeatable)")
    p.add_argument("--scenario", action="append", help="restrict scenarios, e.g. E1 (repeatable)")
    p.add_argument("--repetitions", type=int, help="repetitions per cell")
    p.add_argument("--model-override", action="append", default=[], metavar="HARNESS=MODEL",
                   help="replace the selected slot's model for one harness (recorded in every record)")
    p.add_argument("--dry-run", action="store_true", help="print the planned runs, start nothing")
    p.add_argument("--jobs", type=int, default=1, help="runs in parallel (default 1)")
    p.add_argument("--no-judge", action="store_true", help="judged criteria become not-assessable")
    p.add_argument("--no-feedback", action="store_true", help="skip the feedback form")
    p.add_argument("--keep-temp", action="store_true", help="keep each run's temp dir (server config, HOMEs)")
    return p


def _load(args: argparse.Namespace) -> tuple[Any, Any, Any, list[Any], str]:
    from .criteria import missing_criteria
    from .features import FeatureError, load_features
    from .feedback import load_schema
    from .fixtures import build_mailbox
    from .matrix import MatrixError, load_matrix, plan_runs
    from .measurements import SCENARIO_MEASURES

    try:
        matrix = load_matrix(args.matrix)
        features = load_features(args.features)
    except (MatrixError, FeatureError) as exc:
        raise CannotRun(str(exc)) from exc
    try:
        schema = load_schema(args.schema)
    except Exception as exc:
        raise CannotRun(f"feedback schema: {exc}") from exc

    selection_name = "smoke" if args.smoke else "pilot"
    sel = matrix.smoke if args.smoke else matrix.pilot
    sel = replace(
        sel,
        harnesses=args.harness or sel.harnesses,
        models=args.model_slot or sel.models,
        server_variants=args.variant or sel.server_variants,
        scenarios=args.scenario or sel.scenarios,
        repetitions=args.repetitions or sel.repetitions,
    )
    try:
        runs = plan_runs(matrix, sel, set(features.scenarios))
    except MatrixError as exc:
        raise CannotRun(str(exc)) from exc
    overrides: dict[str, str] = {}
    for item in args.model_override:
        harness, _, model = item.partition("=")
        if harness not in matrix.harnesses or not model:
            raise CannotRun(f"--model-override {item!r}: expected HARNESS=MODEL")
        overrides[harness] = model
    runs = [replace(r, model=overrides.get(r.harness, r.model)) for r in runs]

    for scenario_id in sel.scenarios:
        spec = features.scenarios[scenario_id]
        missing = missing_criteria([c.text for c in spec.rubric if c.text is not None])
        if missing:
            raise CannotRun(f"{scenario_id}: rubric criteria without implementation: {missing}")
        unknown = [m.name for m in spec.measurements if m.name not in SCENARIO_MEASURES]
        if unknown:
            raise CannotRun(f"{scenario_id}: measurements without implementation: {unknown}")
        try:
            build_mailbox(spec)
        except Exception as exc:
            raise CannotRun(f"{scenario_id}: fixture cannot be built: {exc}") from exc
        if spec.custom_policy is None:
            for variant in sel.server_variants:
                policy = USABILITY_DIR / matrix.variants[variant].policy
                if not policy.exists():
                    raise CannotRun(f"server variant {variant}: policy {policy} missing")
    return matrix, features, schema, runs, selection_name


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        matrix, features, schema, runs, selection_name = _load(args)
    except CannotRun as exc:
        print(f"usability runner cannot run: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN

    if args.dry_run:
        print(f"{len(runs)} planned runs ({selection_name}):")
        for run in runs:
            print(f"  {run.label:45s} model={run.model}")
        return EXIT_OK

    from .judge import ClaudeJudge, NoJudge
    from .report import write_report
    from .run import RunContext, execute_run, make_run_id
    from .server import server_info

    try:
        server = server_info()
    except Exception as exc:  # noqa: BLE001
        print(f"usability runner cannot run: server under test unusable: {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.results_dir / timestamp
    (out_dir / "records").mkdir(parents=True, exist_ok=True)
    meta = {
        "timestamp": timestamp,
        "selection_name": selection_name,
        "planned_runs": [r.label for r in runs],
        "server_version": server.version,
        "tool_set_version": server.tool_set_version,
        "judge": {"harness": matrix.judge.harness, "model": matrix.judge.model,
                  "fallback_models": list(matrix.judge.fallback_models)} if not args.no_judge else None,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    ctx = RunContext(
        matrix=matrix, features=features, feedback_schema=schema, server=server,
        judge=NoJudge() if args.no_judge else ClaudeJudge(matrix.judge, matrix.harnesses["claude-code"].binary),
        results_dir=out_dir, collect_feedback=not args.no_feedback, keep_temp=args.keep_temp,
    )

    def one(run: Any) -> dict[str, Any]:
        run_id = make_run_id(run)
        print(f"[start] {run.label} ({run_id})", flush=True)
        record = execute_run(ctx, run, run_id, out_dir)
        (out_dir / "records" / f"{run_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        print(f"[done]  {run.label}: {record['grade']}"
              + (f" ({record.get('grade_cause')})" if record.get("grade_cause") else ""), flush=True)
        return record

    if args.jobs > 1:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for future in as_completed([pool.submit(one, r) for r in runs]):
                future.result()
    else:
        for run in runs:
            one(run)
    report = write_report(out_dir, meta, set(server.tools))
    print(f"report: {report}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
