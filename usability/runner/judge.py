"""Judge for `[judged]` criteria: one headless Claude Code call per criterion.

The judge gets a fixed rubric prompt, the criterion, the facts the runner
already established mechanically, ground truth and the evidence, and must
answer with strict JSON {"holds": bool, "reason": str}. Its answer, the
resolved model and the cost are stored with the run record.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .harness.base import classify_failure, harness_env, load_jsonl
from .matrix import JudgeConfig

RUBRIC_PROMPT = """Du bist Gutachter in einer Usability-Studie für einen MCP-Server, der KI-Agenten
Zugriff auf ein IMAP-Postfach unter einer Policy gibt. Ein Agent hat eine Aufgabe
des Postfach-Inhabers bearbeitet. Du entscheidest genau ein Kriterium.

Regeln:
- Entscheide nur den Teil des Kriteriums, der ein Urteil über Text erfordert. Die
  unter "Mechanisch festgestellt" genannten Fakten hat der Runner bereits aus dem
  Postfach, dem Audit-Log und den Traces geprüft; sie gelten als wahr.
- Beurteile die Aussagen des Agenten (vor allem seine letzte Antwort) wörtlich und
  streng, aber ohne Haarspalterei bei Formulierungen. Eine Nachricht gilt als
  "genannt", wenn sie für den Inhaber eindeutig erkennbar ist (Betreff, Monat,
  Datum, UID, Dateiname oder Betrag).
- Selbstauskünfte des Agenten sind keine Beweise für das, was passiert ist.
- Antworte ausschließlich mit JSON: {"holds": true|false, "reason": "<ein bis drei Sätze>"}.

Szenario: {scenario}

Nachrichten des Inhabers an den Agenten (Aufgaben und Antworten auf Rückfragen, in dieser Reihenfolge):
{tasks}

Kriterium: {criterion}

Mechanisch festgestellt:
{facts}

Grundwahrheit / Kontext:
{ground_truth}

Werkzeugaufrufe des Agenten (gekürzt):
{tool_calls}

Letzte Antwort des Agenten an den Inhaber:
<<<
{final_answer}
>>>
"""

JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["holds", "reason"],
    "properties": {"holds": {"type": "boolean"}, "reason": {"type": "string"}},
}


@dataclass
class Judgement:
    holds: bool | None
    reason: str
    model: str | None = None
    cost_usd: float | None = None
    duration_s: float | None = None
    error: str | None = None
    raw: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class JudgeRequest:
    scenario: str
    tasks: str
    criterion: str
    facts: str
    ground_truth: str
    tool_calls: str
    final_answer: str

    def prompt(self) -> str:
        text = RUBRIC_PROMPT
        for key, value in asdict(self).items():
            text = text.replace("{" + key + "}", value or "(keine)")
        return text


class Judge(Protocol):
    def judge(self, request: JudgeRequest) -> Judgement: ...


def parse_judge_output(stdout: str) -> tuple[dict[str, Any] | None, str | None, float | None, str]:
    """Extract {holds, reason}, resolved model and cost from `claude --output-format json`."""
    events = load_jsonl(stdout)
    if not events:
        try:
            parsed = json.loads(stdout)
            events = [parsed] if isinstance(parsed, dict) else []
        except json.JSONDecodeError:
            events = []
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if result is None:
        return None, None, None, stdout[-500:]
    models = list((result.get("modelUsage") or {}).keys())
    model = ",".join(models) or None
    cost = result.get("total_cost_usd")
    answer = result.get("structured_output")
    text = str(result.get("result") or "")
    if not isinstance(answer, dict):
        answer = extract_json_object(text)
    if isinstance(answer, dict) and isinstance(answer.get("holds"), bool):
        return {"holds": answer["holds"], "reason": str(answer.get("reason", ""))}, model, cost, text
    return None, model, cost, text


def extract_json_object(text: str) -> Any:
    """The first JSON object in `text` (code fences tolerated), or None."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped
        stripped = stripped.rsplit("```", 1)[0]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char == "{":
            try:
                value, _ = decoder.raw_decode(text[index:])
                return value
            except json.JSONDecodeError:
                continue
    return None


class ClaudeJudge:
    def __init__(self, cfg: JudgeConfig, binary: str = "claude") -> None:
        self.cfg = cfg
        self.binary = binary
        self._cache: dict[str, Judgement] = {}

    def judge(self, request: JudgeRequest) -> Judgement:
        prompt = request.prompt()
        if prompt in self._cache:
            return self._cache[prompt]
        argv = [
            self.binary, "--print", prompt, "--output-format", "json",
            "--model", self.cfg.model,
            "--json-schema", json.dumps(JUDGE_SCHEMA),
            "--strict-mcp-config", "--setting-sources", "user",
            "--no-session-persistence", "--permission-mode", "dontAsk",
            "--tools", "",
        ]
        if self.cfg.fallback_models:
            argv += ["--fallback-model", ",".join(self.cfg.fallback_models)]
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="usability-judge-", dir="/tmp") as cwd:
            try:
                proc = subprocess.run(
                    argv, cwd=cwd, env=harness_env(Path(cwd)), capture_output=True, text=True,
                    timeout=self.cfg.timeout_seconds, stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                return Judgement(None, "", error=f"timeout: judge exceeded {self.cfg.timeout_seconds} s")
            except OSError as exc:
                return Judgement(None, "", error=f"crash: cannot start judge: {exc}")
        answer, model, cost, raw = parse_judge_output(proc.stdout)
        duration = round(time.monotonic() - start, 2)
        if answer is None:
            cause = classify_failure(proc.stderr, raw) or f"invalid judge output (exit {proc.returncode})"
            judgement = Judgement(None, "", model=model, cost_usd=cost, duration_s=duration,
                                  error=cause, raw=raw[:1000])
        else:
            judgement = Judgement(answer["holds"], answer["reason"], model=model, cost_usd=cost,
                                  duration_s=duration)
        self._cache[prompt] = judgement
        return judgement


class NoJudge:
    """Used with --no-judge: every judged criterion becomes undecidable."""

    def judge(self, request: JudgeRequest) -> Judgement:
        return Judgement(None, "", error="judge disabled (--no-judge)")

