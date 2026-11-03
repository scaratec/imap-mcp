"""Rule-based user simulator (matrix.yaml `user_simulator`, scenario steps)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TAIL_CHARS = 900


@dataclass(frozen=True)
class SimulatorAnswer:
    topics: list[str]
    text: str
    question: str


def question_region(text: str) -> str | None:
    """The part of a final message that asks something, or None.

    A turn "ends with a question" when its last paragraph(s) contain a
    question mark; the region is the tail of the message from the first
    of those paragraphs.
    """
    stripped = text.strip()
    if not stripped:
        return None
    paragraphs = [p for p in re.split(r"\n\s*\n", stripped) if p.strip()]
    tail: list[str] = []
    for paragraph in reversed(paragraphs):
        if len("\n\n".join([paragraph, *tail])) > _TAIL_CHARS and tail:
            break
        tail.insert(0, paragraph)
    region = "\n\n".join(tail)
    if "?" not in region:
        return None
    first_q = min(i for i, p in enumerate(tail) if "?" in p)
    return "\n\n".join(tail[max(0, first_q - 1):])


def topics_in(text: str, topics: dict[str, list[str]]) -> list[str]:
    lowered = text.lower()
    return [name for name, words in topics.items() if any(w.lower() in lowered for w in words)]


def answer(
    final_text: str,
    *,
    topics: dict[str, list[str]],
    scenario_answers: dict[str, str],
    default_answer: str,
) -> SimulatorAnswer | None:
    """The simulator's reply to a turn that ends with a question, else None."""
    region = question_region(final_text)
    if region is None:
        return None
    question_lines = "\n".join(line for line in region.splitlines() if "?" in line) or region
    matched = topics_in(question_lines, topics) or topics_in(region, topics)
    parts: list[str] = []
    for topic in matched:
        reply = scenario_answers.get(topic)
        if reply and reply not in parts:
            parts.append(reply)
    if not matched or any(t not in scenario_answers for t in matched):
        parts.append(default_answer)
    return SimulatorAnswer(topics=matched, text=" ".join(parts), question=region[:600])
