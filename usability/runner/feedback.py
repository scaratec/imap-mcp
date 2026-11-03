"""Feedback form (features/06_feedback_form.feature): prompt, parsing, validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .judge import extract_json_object

RETRY_PROMPT = (
    "Das Formular war nicht gültig: {error}\n"
    "Antworte bitte noch einmal ausschließlich mit JSON nach dem Schema, ohne weiteren Text."
)


@dataclass
class FeedbackCheck:
    status: str  # valid | invalid_json | schema_violation | refused
    form: dict[str, Any] | None
    error: str | None


def load_schema(path: Path) -> dict[str, Any]:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def feedback_prompt(template: str, schema: dict[str, Any]) -> str:
    return template.replace("<schema>", json.dumps(schema, ensure_ascii=False, indent=1))


def check_feedback(text: str, schema: dict[str, Any]) -> FeedbackCheck:
    stripped = text.strip()
    if not stripped or "{" not in stripped:
        return FeedbackCheck("refused", None, "no JSON object in the answer")
    value = extract_json_object(stripped)
    if not isinstance(value, dict):
        return FeedbackCheck("invalid_json", None, "the answer contains no parsable JSON object")
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.absolute_path))
    if errors:
        described = "; ".join(
            f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in errors[:5]
        )
        return FeedbackCheck("schema_violation", value, described[:1200])
    return FeedbackCheck("valid", value, None)
