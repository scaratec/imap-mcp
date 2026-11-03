"""RFC 3501 §6.4.4 SEARCH, evaluated against the mock's message store.

Every search key the imap-mcp server or the test harness sends is
implemented with its RFC semantics; any other key is a syntax error,
which Gmail answers with `BAD Could not parse command`. Gmail-specific
keys: X-GM-MSGID, X-GM-THRID, X-GM-LABELS.

Date keys compare calendar days, "disregarding time and timezone"
(RFC 3501 §6.4.4): BEFORE, ON and SINCE read the internal date, the
SENT* keys the Date header. A message without a parseable Date header
falls back to its internal date for the SENT* keys, as Dovecot does.
"""

from __future__ import annotations

import email
import email.policy
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from email.message import Message as EmailMessage
from email.utils import parsedate_to_datetime
from functools import cached_property

from .state import HIDDEN_LABELS, Message


class SearchSyntaxError(ValueError):
    """The search program does not parse (answered BAD)."""


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


@dataclass(frozen=True)
class Candidate:
    """One message of the selected mailbox as SEARCH sees it."""

    uid: int
    seq: int
    msg: Message

    @cached_property
    def parsed(self) -> EmailMessage:
        return email.message_from_bytes(self.msg.rfc822, policy=email.policy.default)


Predicate = Callable[[Candidate], bool]


def tokenize(text: str) -> list[object]:
    """Split a search program into atoms, quoted strings and nested
    parenthesised lists (RFC 3501 §9)."""
    stack: list[list[object]] = [[]]
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == " ":
            i += 1
        elif ch == "(":
            stack.append([])
            i += 1
        elif ch == ")":
            if len(stack) == 1:
                raise SearchSyntaxError("unbalanced ')'")
            inner = stack.pop()
            stack[-1].append(inner)
            i += 1
        elif ch == '"':
            out: list[str] = []
            i += 1
            while True:
                if i >= n:
                    raise SearchSyntaxError("unterminated quoted string")
                ch = text[i]
                if ch == "\\" and i + 1 < n and text[i + 1] in ('"', "\\"):
                    out.append(text[i + 1])
                    i += 2
                    continue
                if ch == '"':
                    i += 1
                    break
                out.append(ch)
                i += 1
            stack[-1].append("".join(out))
        else:
            j = i
            while j < n and text[j] not in ' ()"':
                j += 1
            stack[-1].append(text[i:j])
            i = j
    if len(stack) != 1:
        raise SearchSyntaxError("unbalanced '('")
    return stack[0]


def compile_search(text: str, *, max_uid: int, max_seq: int) -> Predicate:
    """Compile a search program into one predicate (all keys ANDed)."""
    tokens = tokenize(text)
    if len(tokens) >= 2 and isinstance(tokens[0], str) and tokens[0].upper() == "CHARSET":
        tokens = tokens[2:]
    if not tokens:
        raise SearchSyntaxError("empty search program")
    parser = _Parser(tokens, max_uid=max_uid, max_seq=max_seq)
    predicates: list[Predicate] = []
    while not parser.done():
        predicates.append(parser.key())
    return lambda c: all(p(c) for p in predicates)


def search(text: str, messages: Sequence[tuple[int, Message]]) -> list[int]:
    """UIDs of `messages` (uid, Message) that match, ascending."""
    ordered = sorted(messages, key=lambda pair: pair[0])
    candidates = [Candidate(uid, seq, msg) for seq, (uid, msg) in enumerate(ordered, start=1)]
    predicate = compile_search(
        text,
        max_uid=max((c.uid for c in candidates), default=0),
        max_seq=len(candidates),
    )
    return [c.uid for c in candidates if predicate(c)]


def parse_search_date(token: object) -> date:
    """RFC 3501 `date`: `d-Mon-yyyy` or `dd-Mon-yyyy`, optionally quoted."""
    if not isinstance(token, str):
        raise SearchSyntaxError("date expected")
    parts = token.strip().split("-")
    if len(parts) != 3 or parts[1].lower() not in _MONTHS:
        raise SearchSyntaxError(f"invalid date {token!r}")
    try:
        return date(int(parts[2]), _MONTHS.index(parts[1].lower()) + 1, int(parts[0]))
    except ValueError as exc:
        raise SearchSyntaxError(f"invalid date {token!r}") from exc


def _sequence_set(token: object, maximum: int) -> Callable[[int], bool]:
    if not isinstance(token, str) or not token:
        raise SearchSyntaxError("sequence set expected")
    ranges: list[tuple[int, int]] = []
    for part in token.split(","):
        bounds = part.split(":")
        if len(bounds) > 2:
            raise SearchSyntaxError(f"invalid sequence set {token!r}")
        try:
            numbers = [maximum if b == "*" else int(b) for b in bounds]
        except ValueError as exc:
            raise SearchSyntaxError(f"invalid sequence set {token!r}") from exc
        lo, hi = min(numbers), max(numbers)
        ranges.append((lo, hi))
    return lambda value: any(lo <= value <= hi for lo, hi in ranges)


def _is_sequence_set(token: str) -> bool:
    return bool(token) and all(ch.isdigit() or ch in ",:*" for ch in token)


def _header_contains(name: str, needle: str) -> Predicate:
    """RFC 3501: the header field contains the string, case-insensitively;
    an empty string matches every message that has the field."""
    lowered = needle.lower()

    def check(c: Candidate) -> bool:
        values = c.parsed.get_all(name) or []
        return any(lowered in str(v).lower() for v in values)

    return check


def _sent_day(c: Candidate) -> date:
    header = str(c.parsed.get("Date") or "")
    if header:
        try:
            return parsedate_to_datetime(header).date()
        except (TypeError, ValueError):
            pass
    return internal_day(c.msg)


def internal_day(msg: Message) -> date:
    stamp = msg.internal_date
    if stamp is None:
        raise RuntimeError("message stored without an internal date")
    return stamp.date()


def _text_of(c: Candidate) -> str:
    return c.msg.rfc822.decode("utf-8", errors="replace").lower()


def _body_of(c: Candidate) -> str:
    raw = c.msg.rfc822
    end = raw.find(b"\r\n\r\n")
    body = raw[end + 4 :] if end >= 0 else b""
    return body.decode("utf-8", errors="replace").lower()


_FLAG_KEYS: dict[str, tuple[str, bool]] = {
    "ANSWERED": ("\\Answered", True),
    "DELETED": ("\\Deleted", True),
    "DRAFT": ("\\Draft", True),
    "FLAGGED": ("\\Flagged", True),
    "SEEN": ("\\Seen", True),
    "UNANSWERED": ("\\Answered", False),
    "UNDELETED": ("\\Deleted", False),
    "UNDRAFT": ("\\Draft", False),
    "UNFLAGGED": ("\\Flagged", False),
    "UNSEEN": ("\\Seen", False),
}

_ADDRESS_KEYS = {"BCC": "Bcc", "CC": "Cc", "FROM": "From", "TO": "To", "SUBJECT": "Subject"}


class _Parser:
    def __init__(self, tokens: list[object], *, max_uid: int, max_seq: int) -> None:
        self._tokens = tokens
        self._pos = 0
        self._max_uid = max_uid
        self._max_seq = max_seq

    def done(self) -> bool:
        return self._pos >= len(self._tokens)

    def _next(self) -> object:
        if self.done():
            raise SearchSyntaxError("search key argument missing")
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _string(self) -> str:
        token = self._next()
        if not isinstance(token, str):
            raise SearchSyntaxError("string expected")
        return token

    def _number(self) -> int:
        token = self._string()
        if not token.isdigit():
            raise SearchSyntaxError(f"number expected, got {token!r}")
        return int(token)

    def key(self) -> Predicate:
        token = self._next()
        if isinstance(token, list):
            inner = _Parser(token, max_uid=self._max_uid, max_seq=self._max_seq)
            parts: list[Predicate] = []
            while not inner.done():
                parts.append(inner.key())
            if not parts:
                raise SearchSyntaxError("empty parenthesised list")
            return lambda c: all(p(c) for p in parts)
        assert isinstance(token, str)
        word = token.upper()
        if word == "ALL":
            return lambda c: True
        if word in _FLAG_KEYS:
            flag, wanted = _FLAG_KEYS[word]
            return lambda c: (flag in c.msg.flags) is wanted
        if word in ("RECENT", "NEW"):
            # Gmail never sets \Recent.
            return lambda c: False
        if word == "OLD":
            return lambda c: True
        if word in ("KEYWORD", "UNKEYWORD"):
            keyword = self._string()
            wanted = word == "KEYWORD"
            return lambda c: (keyword in c.msg.flags) is wanted
        if word in _ADDRESS_KEYS:
            return _header_contains(_ADDRESS_KEYS[word], self._string())
        if word == "HEADER":
            name = self._string()
            return _header_contains(name, self._string())
        if word == "BODY":
            needle = self._string().lower()
            return lambda c: needle in _body_of(c)
        if word == "TEXT":
            needle = self._string().lower()
            return lambda c: needle in _text_of(c)
        if word == "LARGER":
            size = self._number()
            return lambda c: len(c.msg.rfc822) > size
        if word == "SMALLER":
            size = self._number()
            return lambda c: len(c.msg.rfc822) < size
        if word in ("BEFORE", "ON", "SINCE"):
            day = parse_search_date(self._next())
            return _day_predicate(word, day, lambda c: internal_day(c.msg))
        if word in ("SENTBEFORE", "SENTON", "SENTSINCE"):
            day = parse_search_date(self._next())
            return _day_predicate(word[4:], day, _sent_day)
        if word == "NOT":
            inner_key = self.key()
            return lambda c: not inner_key(c)
        if word == "OR":
            left = self.key()
            right = self.key()
            return lambda c: left(c) or right(c)
        if word == "UID":
            in_set = _sequence_set(self._next(), self._max_uid)
            return lambda c: in_set(c.uid)
        if word == "X-GM-MSGID":
            msgid = self._number()
            return lambda c: c.msg.gm_msgid == msgid
        if word == "X-GM-THRID":
            thrid = self._number()
            return lambda c: c.msg.gm_thrid == thrid
        if word == "X-GM-LABELS":
            label = self._string()
            return lambda c: label in c.msg.labels and label not in HIDDEN_LABELS
        if _is_sequence_set(token):
            in_seq = _sequence_set(token, self._max_seq)
            return lambda c: in_seq(c.seq)
        raise SearchSyntaxError(f"unknown search key {token!r}")


def _day_predicate(relation: str, day: date, day_of: Callable[[Candidate], date]) -> Predicate:
    if relation == "BEFORE":
        return lambda c: day_of(c) < day
    if relation == "ON":
        return lambda c: day_of(c) == day
    return lambda c: day_of(c) >= day
