"""Markdown report over the run records of one results directory."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

GRADE_CODES = {
    "fulfilled": "F", "partial": "P", "missed": "M", "misreported": "X", "harmful": "H",
    "not-assessable": "N",
}


def _fmt(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, (list, dict)):
        return str(len(value))
    return str(value)


def _tool_mentions(text: str, tools: set[str]) -> set[str]:
    return {t for t in tools if re.search(rf"\b{re.escape(t)}\b", text)}


def feedback_themes(records: list[dict[str, Any]], tools: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Mechanical grouping of unclear/missing/free_text by tool and by `unclear.subject`."""
    themes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        form = (rec.get("feedback") or {}).get("form") if (rec.get("feedback") or {}).get("status") == "valid" else None
        if not form:
            continue
        entries: list[tuple[str, str]] = []
        for item in form.get("unclear") or []:
            entries.append((f"unclear:{item.get('subject')}", str(item.get("what", ""))))
        for item in form.get("missing") or []:
            entries.append(("missing", str(item)))
        if form.get("free_text"):
            entries.append(("free_text", str(form["free_text"])))
        for kind, text in entries:
            keys = {f"tool:{t}" for t in _tool_mentions(text, tools)} | {kind}
            for key in keys:
                themes[key].append({"run_id": rec["run_id"], "harness": rec["harness"], "text": text})
    return dict(themes)


def render(records: list[dict[str, Any]], out_dir: Path, meta: dict[str, Any], tools: set[str]) -> str:
    lines: list[str] = []
    lines.append(f"# Usability matrix report — {meta.get('timestamp')}")
    lines.append("")
    lines.append(f"Selection: `{meta.get('selection_name')}` — {len(records)} runs. "
                 "Grades are observations, never failures (ADR 0039).")
    lines.append("")
    versions = sorted({(r["harness"], r.get("harness_version") or "?") for r in records})
    lines.append("Versions covered: server " + ", ".join(sorted({str(r.get('server_version')) for r in records}))
                 + "; harnesses " + ", ".join(f"{h} {v}" for h, v in versions) + ".")
    models = sorted({(r["harness"], r["model_requested"], r.get("model_resolved") or "–") for r in records})
    lines.append("Models (requested → resolved): " + "; ".join(f"{h}: {m} → {res}" for h, m, res in models) + ".")
    lines.append("")

    # Heatmap
    columns = sorted({(r["harness"], r["model_slot"]) for r in records})
    rows = sorted({(r["scenario"], r["server_variant"]) for r in records})
    cells: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        cells[(r["scenario"], r["server_variant"], r["harness"], r["model_slot"])].append(r)
    lines.append("## Grades")
    lines.append("")
    lines.append("Legend: F fulfilled · P partial · M missed · X misreported · H harmful · N not-assessable "
                 "(one letter per repetition).")
    lines.append("")
    lines.append("| scenario | variant | " + " | ".join(f"{h} / {m}" for h, m in columns) + " |")
    lines.append("|---|---|" + "---|" * len(columns))
    for scenario, variant in rows:
        rendered = []
        for h, m in columns:
            runs = sorted(cells.get((scenario, variant, h, m), []), key=lambda r: r["repetition"])
            rendered.append(" ".join(
                f"[{GRADE_CODES.get(r['grade'], '?')}](records/{r['run_id']}.json)" for r in runs) or "")
        lines.append(f"| {scenario} | {variant} | " + " | ".join(rendered) + " |")
    lines.append("")

    # Runs
    lines.append("## Runs")
    lines.append("")
    lines.append("| run | grade | tool calls | ideal ratio | failed | denied | response bytes | tokens in/out "
                 "| wall s | cost | feedback |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(records, key=lambda r: (r["scenario"], r["server_variant"], r["harness"], r["repetition"])):
        m = r.get("measurements") or {}
        tokens = m.get("tokens")
        tok = f"{tokens.get('input')}/{tokens.get('output')}" if isinstance(tokens, dict) else _fmt(tokens)
        usage = r.get("usage_total") or {}
        cost = []
        if usage.get("cost_usd") is not None:
            cost.append(f"${usage['cost_usd']:.3f}")
        if usage.get("premium_requests") is not None:
            cost.append(f"{usage['premium_requests']:g} premium req.")
        lines.append(
            f"| [{r['run_id']}](records/{r['run_id']}.json) | {r['grade']} | {_fmt(m.get('tool_calls'))} | "
            f"{_fmt(m.get('ideal_ratio'))} | {_fmt(m.get('failed_calls'))} | {_fmt(m.get('denied_calls'))} | "
            f"{_fmt(m.get('response_bytes'))} | {tok} | {_fmt(m.get('wall_clock_seconds'))} | "
            f"{' / '.join(cost) or '–'} | {_fmt((r.get('feedback') or {}).get('status'))} |"
        )
    lines.append("")

    causes = [r for r in records if r["grade"] == "not-assessable"]
    if causes:
        lines.append("## Not assessable")
        lines.append("")
        for r in causes:
            lines.append(f"- `{r['run_id']}`: {r.get('grade_cause')}")
        lines.append("")

    lines.append("## Scenario measurements")
    lines.append("")
    common = {"tool_calls", "ideal_ratio", "calls_by_tool", "failed_calls", "denied_calls", "repeated_calls",
              "refetch_after_listing", "repeated_discovery", "wrong_folder_name_calls", "unfinished_paging",
              "response_bytes", "largest_response_bytes", "tokens", "response_token_share",
              "wall_clock_seconds", "server_seconds", "imap_seconds", "imap_connections", "turns"}
    for r in sorted(records, key=lambda r: r["run_id"]):
        m = r.get("measurements") or {}
        specific = {k: v for k, v in m.items() if k not in common}
        if specific:
            lines.append(f"- `{r['run_id']}`: " + json.dumps(specific, ensure_ascii=False, default=str)[:900])
    lines.append("")

    themes = feedback_themes(records, tools)
    lines.append("## Feedback themes")
    lines.append("")
    lines.append("Grouped mechanically by `unclear.subject`, `missing`, `free_text` and by tool names "
                 "mentioned (the judged thematic grouping of F6 is not automated).")
    lines.append("")
    if not themes:
        lines.append("No valid feedback forms.")
    for key in sorted(themes, key=lambda k: -len(themes[k])):
        entries = themes[key]
        harnesses = sorted({e["harness"] for e in entries})
        backlog = len({e["run_id"] for e in entries}) >= 3 and len(harnesses) >= 2
        lines.append(f"- **{key}** — {len(entries)} mention(s), harnesses: {', '.join(harnesses)}"
                     + (" — **backlog candidate**" if backlog else ""))
        for e in entries[:5]:
            lines.append(f"  - [{e['run_id']}](records/{e['run_id']}.json): {e['text'][:300]}")
    lines.append("")
    return "\n".join(lines)


def write_report(out_dir: Path, meta: dict[str, Any], tools: set[str]) -> Path:
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out_dir / "records").glob("*.json"))]
    path = out_dir / "report.md"
    path.write_text(render(records, out_dir, meta, tools), encoding="utf-8")
    return path
