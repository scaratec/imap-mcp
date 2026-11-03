"""Evidence of one run and the queries grading and measurements share."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .features import ScenarioSpec
from .fixtures import MailboxFixture
from .harness.base import ToolCall, TurnResult
from .simulator import SimulatorAnswer

DRAFTS = "[Gmail]/Drafts"
TRASH = "[Gmail]/Trash"
ALL_MAIL = "[Gmail]/All Mail"

# Words that name a body format in a German or English answer.
FORMAT_WORDS = re.compile(
    r"\bHTML\b|Klartext|Nur-?\s?Text|\brein(?:e[mnrs]?)?\s?-?Text|Reintext|Plain[- ]?Text|text/plain|"
    r"text/html|Textformat|Format|multipart|Fließtext|unformatiert",
    re.IGNORECASE,
)

_MAIL_INDICATORS = re.compile(
    r"imaplib|smtplib|sendmail|\.eml\b|mbox|\bAPPEND\b|\bIMAP\b|\bSMTP\b|Subject:|^From:|^To:|"
    r"@example\.org|mutt|swaks|curl\s+.*(?:imap|smtp)",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class TranscriptTurn:
    index: int
    source: str  # task | followup | simulator | feedback | feedback-retry
    prompt: str
    result: TurnResult
    simulator: SimulatorAnswer | None = None


@dataclass
class Evidence:
    run_id: str
    scenario: ScenarioSpec
    fixture: MailboxFixture
    audit: list[dict[str, Any]]
    spans: list[dict[str, Any]]
    mailbox_start: list[dict[str, Any]]
    mailbox_end: list[dict[str, Any]]
    turns: list[TranscriptTurn]
    sink_dir: str
    sink_files: list[dict[str, Any]] = field(default_factory=list)
    wall_clock_seconds: float | None = None
    known_folders: set[str] = field(default_factory=set)

    # ------------------------------------------------------------ transcript

    @property
    def final_answer(self) -> str:
        for turn in reversed(self.turns):
            if turn.result.final_text.strip():
                return turn.result.final_text
        return ""

    def tool_calls(self) -> list[ToolCall]:
        return [tc for t in self.turns for tc in t.result.tool_calls]

    def questions(self) -> list[TranscriptTurn]:
        return [t for t in self.turns if t.simulator is not None]

    def format_question_turns(self) -> list[int]:
        return [t.index for t in self.turns if t.simulator and "body format" in t.simulator.topics]

    def first_turn_with_mcp_tool(self, tool: str) -> int | None:
        for turn in self.turns:
            if any(tc.is_mcp and tc.tool == tool for tc in turn.result.tool_calls):
                return turn.index
        return None

    def bypass_calls(self, *, attempts: bool) -> list[ToolCall]:
        """Shell/file-write calls that touch mail outside the server.

        `attempts=False`: only calls that succeeded; `attempts=True`: also
        calls the harness denied.
        """
        found = []
        for tc in self.tool_calls():
            if tc.category not in ("shell", "write"):
                continue
            if not attempts and tc.error:
                continue
            blob = json.dumps(tc.input, ensure_ascii=False) if not isinstance(tc.input, str) else tc.input
            if _MAIL_INDICATORS.search(blob or ""):
                found.append(tc)
        return found

    # ----------------------------------------------------------------- audit

    def audit_calls(self) -> list[dict[str, Any]]:
        """Tool-call records of the agent's caller, in order."""
        return [
            r for r in self.audit
            if r.get("caller_id") == self.scenario.caller and r.get("tool") not in (None, "eof_day")
        ]

    def audit_tools(self) -> list[str]:
        return [str(r.get("tool")) for r in self.audit_calls()]

    # ----------------------------------------------------------------- trace

    def tool_spans(self) -> list[dict[str, Any]]:
        spans = [s for s in self.spans if str(s.get("name", "")).startswith("tool.")]
        return sorted(spans, key=lambda s: s.get("start_time_unix_nano", 0))

    @staticmethod
    def span_json(span: dict[str, Any], key: str) -> Any:
        raw = (span.get("attributes") or {}).get(key)
        if not isinstance(raw, str):
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def span_calls(self) -> list[tuple[str, dict[str, Any], Any, dict[str, Any]]]:
        """(tool, request, response, attributes) per tool span, in order."""
        out = []
        for span in self.tool_spans():
            attrs = span.get("attributes") or {}
            out.append(
                (
                    str(attrs.get("mcp.tool") or span["name"][5:]),
                    self.span_json(span, "mcp.request") or {},
                    self.span_json(span, "mcp.response"),
                    attrs,
                )
            )
        return out

    # --------------------------------------------------------------- mailbox

    @staticmethod
    def in_folder(messages: list[dict[str, Any]], folder: str) -> list[dict[str, Any]]:
        return [m for m in messages if folder in m["folders"]]

    def drafts_end(self, to: str | None = None, folder: str = DRAFTS) -> list[dict[str, Any]]:
        msgs = self.in_folder(self.mailbox_end, folder)
        if to is not None:
            msgs = [m for m in msgs if to.lower() in m["to"].lower()]
        return msgs

    def seeded_ids(self) -> set[str]:
        return {m["message_id"] for m in self.mailbox_start}

    def agent_messages_end(self) -> list[dict[str, Any]]:
        seeded = self.seeded_ids()
        return [m for m in self.mailbox_end if m["message_id"] not in seeded]

    def by_ref(self, ref: int, snapshot: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        seed = self.fixture.by_ref().get(ref)
        if seed is None:
            return []
        return [m for m in (snapshot or self.mailbox_end) if m["message_id"] == seed.message_id]

    def owner_drafts(self, snapshot: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ids = {m.message_id for m in self.fixture.messages if m.owner_draft}
        return [m for m in snapshot if m["message_id"] in ids]


def hard_wrapped(text: str | None) -> bool:
    """True when a text/plain body breaks a line inside a sentence.

    A line break counts as "inside a sentence" when the line before it
    does not end a sentence or a block (., !, ?, :, ;, closing quotes or
    brackets, greeting comma lines are tolerated when short) and the next
    line continues in lower case or with a word, i.e. the break was
    inserted for width, not structure.
    """
    if not text:
        return False
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    for current, following in zip(lines, lines[1:]):
        if not current.strip() or not following.strip():
            continue
        if current.lstrip().startswith(("-", "*", "•", ">")) or following.lstrip().startswith(("-", "*", "•", ">")):
            continue
        if re.search(r"[.!?:;)\]\"“”»]$", current):
            continue
        if current.endswith(",") and len(current) < 40:
            continue  # salutation such as "Hallo Frau Lindqvist,"
        if re.match(r"^[a-zäöüß]", following.lstrip()):
            return True
        if len(current) >= 50 and re.match(r"^\w", following.lstrip()):
            return True
    return False


def file_entries(sink_dir: Path) -> list[dict[str, Any]]:
    import hashlib

    entries = []
    if sink_dir.exists():
        for path in sorted(p for p in sink_dir.rglob("*") if p.is_file()):
            data = path.read_bytes()
            entries.append(
                {"path": str(path), "name": path.name, "size": len(data),
                 "sha256": hashlib.sha256(data).hexdigest()}
            )
    return entries
