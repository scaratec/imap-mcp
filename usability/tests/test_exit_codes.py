"""Exit code policy (ADR 0039): 0 once the matrix ran, 2 only when it cannot run."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from usability.runner import USABILITY_DIR, cli
from usability.runner.server import ServerInfo


def test_dry_run_of_the_pilot_lists_120_runs(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--dry-run"]) == cli.EXIT_OK
    assert capsys.readouterr().out.startswith("120 planned runs (pilot):")


def test_dry_run_of_the_smoke_lists_one_run_per_harness(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--dry-run", "--smoke"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("4 planned runs (smoke):")
    assert all(f"E1/{h}/reference/fixed/r1" in out for h in ("antigravity", "copilot", "claude-code", "opencode"))


def test_invalid_matrix_cannot_run(tmp_path: Path) -> None:
    bad = tmp_path / "matrix.yaml"
    bad.write_text("schema_version: 2\n", encoding="utf-8")
    assert cli.main(["--dry-run", "--matrix", str(bad)]) == cli.EXIT_CANNOT_RUN


def test_unparsable_feature_cannot_run(tmp_path: Path) -> None:
    features = tmp_path / "features"
    shutil.copytree(USABILITY_DIR / "features", features)
    (features / "01_draft_format_decision.feature").write_text("Feature: broken\n  Scenario E1 no colon\n",
                                                                encoding="utf-8")
    assert cli.main(["--dry-run", "--features", str(features)]) == cli.EXIT_CANNOT_RUN


def test_unknown_selection_cannot_run() -> None:
    assert cli.main(["--dry-run", "--harness", "nonexistent"]) == cli.EXIT_CANNOT_RUN
    assert cli.main(["--dry-run", "--model-override", "nonexistent=x"]) == cli.EXIT_CANNOT_RUN


def test_fixture_that_cannot_be_built_cannot_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from usability.runner import fixtures

    def boom(spec: Any) -> Any:
        raise ValueError("broken table")

    monkeypatch.setattr(fixtures, "build_mailbox", boom)
    assert cli.main(["--dry-run", "--smoke"]) == cli.EXIT_CANNOT_RUN


def test_matrix_that_ran_exits_zero_whatever_the_grades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import usability.runner.run as run_module
    import usability.runner.server as server_module

    grades = iter(["not-assessable", "harmful", "missed", "misreported"])

    def fake_execute(ctx: Any, run: Any, run_id: str, out_dir: Path) -> dict[str, Any]:
        return {"run_id": run_id, "scenario": run.scenario, "harness": run.harness, "model_slot": run.model_slot,
                "model_requested": run.model, "server_variant": run.variant, "repetition": run.repetition,
                "grade": next(grades), "grade_cause": "fake", "measurements": {}, "feedback": {}}

    monkeypatch.setattr(run_module, "execute_run", fake_execute)
    monkeypatch.setattr(server_module, "server_info",
                        lambda: ServerInfo(Path("/bin/true"), "0", "0", ["create_draft"]))
    code = cli.main(["--smoke", "--results-dir", str(tmp_path), "--no-judge"])
    assert code == cli.EXIT_OK
    (result_dir,) = list(tmp_path.iterdir())
    assert len(list((result_dir / "records").glob("*.json"))) == 4
    report = (result_dir / "report.md").read_text(encoding="utf-8")
    assert "| E1 | fixed |" in report
    assert json.loads((result_dir / "meta.json").read_text())["selection_name"] == "smoke"
