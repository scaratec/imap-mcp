"""Trace payload modes (ADR 0037).

`IMAP_MCP_TRACE_PAYLOADS` selects what a tool span records of the
request and the response:

- ``off``: neither ``mcp.request`` nor ``mcp.response``.
- ``redacted`` (default): the request as the audit log records it
  (ADR 0021) and the response without message content, addresses,
  subjects, headers, bodies or attachment data.
- ``full``: the complete JSON of both, each cut at
  `IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS` characters and marked.

This module holds only the pure parts (parsing, redaction,
truncation); it does not depend on OpenTelemetry.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

PayloadMode = Literal["off", "redacted", "full"]
PAYLOAD_MODES: tuple[PayloadMode, ...] = ("off", "redacted", "full")
DEFAULT_MODE: PayloadMode = "redacted"
DEFAULT_MAX_CHARS = 16384

MODE_ENV = "IMAP_MCP_TRACE_PAYLOADS"
MAX_CHARS_ENV = "IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS"

# Response members that carry message content, addresses, subjects,
# headers, bodies or attachment data. They are dropped wherever they
# occur, at any depth and inside list entries.
CONTENT_KEYS = frozenset(
    {
        "from",
        "to",
        "cc",
        "bcc",
        "reply_to",
        "sender",
        "subject",
        "body",
        "text_body",
        "html_body",
        "body_text",
        "body_html",
        "reply_text",
        "reply_html",
        "headers",
        "attachments",
        "attachment",
        "messages",
        "content",
        "new_content",
        "rfc822",
        "filename",
        "new_filename",
        "saved_to",
        "message_id",
        "in_reply_to",
        "references",
    }
)


class PayloadSettingsError(ValueError):
    """The environment names an unknown mode or an invalid limit."""


@dataclass(frozen=True)
class PayloadSettings:
    mode: PayloadMode
    max_chars: int


def parse_payload_settings(env: Mapping[str, str]) -> PayloadSettings:
    """Read the mode and the truncation limit from `env`.

    An unset or empty variable takes the default. Anything else that is
    not an allowed value raises `PayloadSettingsError`: a typo must not
    silently widen or narrow what reaches the trace store.
    """
    raw_mode = env.get(MODE_ENV, "").strip()
    if not raw_mode:
        mode = DEFAULT_MODE
    elif raw_mode.lower() in PAYLOAD_MODES:
        mode = cast(PayloadMode, raw_mode.lower())
    else:
        raise PayloadSettingsError(
            f"{MODE_ENV}={raw_mode!r} is not a trace payload mode; "
            f"allowed values: {', '.join(PAYLOAD_MODES)} (ADR 0037)"
        )
    raw_max = env.get(MAX_CHARS_ENV, "").strip()
    if not raw_max:
        max_chars = DEFAULT_MAX_CHARS
    else:
        try:
            max_chars = int(raw_max)
        except ValueError:
            max_chars = 0
        if max_chars < 1:
            raise PayloadSettingsError(
                f"{MAX_CHARS_ENV}={raw_max!r} is not a positive integer (ADR 0037)"
            )
    return PayloadSettings(mode=mode, max_chars=max_chars)


_settings: PayloadSettings | None = None


def payload_settings() -> PayloadSettings:
    """The process-wide settings, read from the environment once.

    Raises `SystemExit` with the reason on an invalid value; the entry
    point calls this at startup so the server refuses to start.
    """
    global _settings
    if _settings is None:
        try:
            _settings = parse_payload_settings(os.environ)
        except PayloadSettingsError as exc:
            raise SystemExit(str(exc)) from exc
    return _settings


def redact_response(value: Any) -> Any:
    """Return `value` without any member named in `CONTENT_KEYS`."""
    if isinstance(value, Mapping):
        return {
            key: redact_response(item) for key, item in value.items() if key not in CONTENT_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [redact_response(item) for item in value]
    return value


def truncate(text: str, max_chars: int) -> str:
    """Cut `text` at `max_chars` and mark how many characters were cut."""
    excess = len(text) - max_chars
    if excess <= 0:
        return text
    return f"{text[:max_chars]}…[truncated {excess} chars]"


def dumps(value: Any) -> str:
    """JSON for a span attribute: non-ASCII kept as is, unknown types as str."""
    return json.dumps(value, ensure_ascii=False, default=str)


def payload_attributes(
    settings: PayloadSettings,
    *,
    request: Mapping[str, Any],
    audited_request: Mapping[str, Any],
    response: Mapping[str, Any],
) -> dict[str, str]:
    """The `mcp.request` / `mcp.response` attributes for one tool call.

    `request` is the complete argument object, `audited_request` the
    same arguments as the audit log records them, `response` the
    public response the client receives.
    """
    if settings.mode == "off":
        return {}
    if settings.mode == "redacted":
        return {
            "mcp.request": dumps(audited_request),
            "mcp.response": dumps(redact_response(response)),
        }
    return {
        "mcp.request": truncate(dumps(request), settings.max_chars),
        "mcp.response": truncate(dumps(response), settings.max_chars),
    }
