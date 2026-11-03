"""Steps that witness the server's traces through a test collector.

ADR 0037 (payload modes) and ADR 0038 (span content). Following
.scaratec/handbuch/observability.md §6, the server exports OTLP to an
in-process collector (support/trace_collector.py) exactly as it would to
a production collector; every Then reads only that collector's sink,
never the server. "within N seconds" polls the sink up to N seconds.

Attribute values are compared in their text form: booleans as `true` /
`false`, numbers as decimal digits, strings as they are.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from behave import given, then, use_step_matcher
from behave.runner import Context

use_step_matcher("re")

# Batch export interval for the server under test. The SDK default of
# five seconds would make every "within N seconds" a race; one second
# keeps the exporter batching, so the flush witnesses still depend on
# the shutdown flush (handbook §6: keep test export intervals short).
_TEST_BSP_SCHEDULE_DELAY_MS = "1000"

# The marker ADR 0037 appends to a cut payload, as the feature states it.
_TRUNCATION_MARKER = re.compile(r"…\[truncated (\d+) chars\]$")


# ------------------------------------------------------------- helpers


def _sink_spans(context: Context) -> list[Any]:
    collector = getattr(context, "trace_collector", None)
    if collector is None:
        raise AssertionError(
            "No test trace collector is running; the scenario needs "
            "`Given a test trace collector is listening and the server exports traces to it`."
        )
    return collector.sink.spans()


def _poll(
    context: Context, seconds: int, check: Callable[[list[Any]], str | None]
) -> list[Any]:
    """Poll the sink until `check` returns None (satisfied) or time runs out.

    `check` returns the reason it is not yet satisfied; the last reason
    becomes the failure message.
    """
    deadline = time.monotonic() + seconds
    while True:
        spans = _sink_spans(context)
        reason = check(spans)
        if reason is None:
            return spans
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"Not satisfied within {seconds} s: {reason}\n"
                f"Spans in the sink: {[s.name for s in spans]!r}"
            )
        time.sleep(0.1)


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _unescape(step_value: str) -> str:
    """Gherkin step text writes a quote inside a quoted value as `\\"`."""
    return step_value.replace('\\"', '"')


def _named(spans: list[Any], name: str) -> list[Any]:
    return [s for s in spans if s.name == name]


def _the_span(context: Context, name: str) -> Any:
    matches = _named(_sink_spans(context), name)
    if len(matches) != 1:
        raise AssertionError(
            f"Expected exactly one span {name!r} in the trace sink, found {len(matches)}"
        )
    return matches[0]


def _attribute(span: Any, attribute: str) -> Any:
    if attribute not in span.attributes:
        raise AssertionError(
            f"Span {span.name!r} has no attribute {attribute!r}; "
            f"attributes: {sorted(span.attributes)!r}"
        )
    return span.attributes[attribute]


def _wait_for_span(
    context: Context, seconds: int, name: str, predicate: Callable[[Any], str | None]
) -> Any:
    """Wait for a span `name` for which `predicate` returns None."""
    found: list[Any] = []

    def check(spans: list[Any]) -> str | None:
        candidates = _named(spans, name)
        if not candidates:
            return f"no span {name!r}"
        reasons = []
        for span in candidates:
            reason = predicate(span)
            if reason is None:
                found.append(span)
                return None
            reasons.append(reason)
        return "; ".join(reasons)

    _poll(context, seconds, check)
    return found[0]


def _contains(attribute: str, fragment: str) -> Callable[[Any], str | None]:
    def predicate(span: Any) -> str | None:
        if attribute not in span.attributes:
            return f"span {span.name!r} has no attribute {attribute!r}"
        value = _text(span.attributes[attribute])
        if fragment not in value:
            return f"{attribute}={value!r} does not contain {fragment!r}"
        return None

    return predicate


def _equals(attribute: str, expected: str) -> Callable[[Any], str | None]:
    def predicate(span: Any) -> str | None:
        if attribute not in span.attributes:
            return f"span {span.name!r} has no attribute {attribute!r}"
        actual = _text(span.attributes[attribute])
        if actual != expected:
            return f"{attribute}={actual!r}, expected {expected!r}"
        return None

    return predicate


def _resolve_uid_hint(context: Context, fragment: str) -> str:
    """Translate a scenario UID hint inside a JSON fragment.

    Seeded messages get server-assigned UIDs; the seeding step records
    hint -> UID in `context.message_uids`, as every UID-bearing step of
    the suite does. `"uid": 311` names the message seeded as uid 311.
    """
    lookup: dict[tuple[str, str, int], int] = getattr(context, "message_uids", {}) or {}

    def substitute(match: re.Match[str]) -> str:
        hint = int(match.group(2))
        actual = {uid for (_a, _f, h), uid in lookup.items() if h == hint}
        if len(actual) == 1:
            return f"{match.group(1)}{actual.pop()}"
        return match.group(0)

    return re.sub(r'("uid": )(\d+)', substitute, fragment)


def _all_values(span: Any) -> list[str]:
    """Every textual value a span carries, for negative witnesses."""
    values: list[str] = [span.name]

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                values.append(str(key))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif value is not None:
            values.append(_text(value))

    walk(span.attributes)
    walk(span.resource_attributes)
    walk(span.events)
    return values


def _installed_server_version(context: Context) -> str:
    """Version of the distribution that installs the server binary,
    read from the server environment's package metadata."""
    from features.steps.mcp_steps import _server_binary

    python = Path(_server_binary(context)).parent / "python"
    probe = (
        "from importlib.metadata import distributions\n"
        "hits = {d.version for d in distributions() "
        "if any(ep.group == 'console_scripts' and ep.name == 'imap-mcp' "
        "for ep in d.entry_points)}\n"
        "print(hits.pop() if len(hits) == 1 else '')\n"
    )
    result = subprocess.run(
        [str(python), "-c", probe], capture_output=True, text=True, timeout=30, check=False
    )
    version = result.stdout.strip()
    if result.returncode != 0 or not version:
        raise AssertionError(
            f"Could not read the installed server version via {python}: "
            f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
    return version


def _iso_to_unix_nano(timestamp: str) -> int:
    moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = moment - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


# --------------------------------------------------------------- Given


@given(r"a test trace collector is listening and the server exports traces to it")
def step_trace_collector_listening(context: Context) -> None:
    from support.trace_collector import TraceCollector

    if getattr(context, "mcp", None) is not None:
        raise AssertionError("The server already runs; it cannot be pointed at a collector.")
    collector = TraceCollector(context.scratch_dir / "trace-sink.jsonl")
    collector.start()
    context.trace_collector = collector
    env = getattr(context, "mcp_extra_env", None) or {}
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = collector.endpoint
    env["OTEL_BSP_SCHEDULE_DELAY"] = _TEST_BSP_SCHEDULE_DELAY_MS
    context.mcp_extra_env = env


# --------------------------------------------------- Then: single spans


@then(r'within (?P<seconds>\d+) seconds the trace sink contains a span "(?P<name>[^"]+)"')
def step_sink_contains_span(context: Context, seconds: str, name: str) -> None:
    _wait_for_span(context, int(seconds), name, lambda span: None)


@then(
    r'within (?P<seconds>\d+) seconds the trace sink contains a span "(?P<name>[^"]+)" '
    r'with attribute "(?P<attribute>[^"]+)" equal to "(?P<expected>[^"]*)"'
)
def step_sink_span_attribute_equal(
    context: Context, seconds: str, name: str, attribute: str, expected: str
) -> None:
    _wait_for_span(context, int(seconds), name, _equals(attribute, expected))


@then(
    r'within (?P<seconds>\d+) seconds the trace sink contains a span "(?P<name>[^"]+)" '
    r"with attributes:"
)
def step_sink_span_attributes_table(context: Context, seconds: str, name: str) -> None:
    expected = [(row["attribute"], row["value"]) for row in context.table]

    def predicate(span: Any) -> str | None:
        reasons = [r for a, v in expected if (r := _equals(a, v)(span)) is not None]
        return "; ".join(reasons) or None

    _wait_for_span(context, int(seconds), name, predicate)


@then(
    r'within (?P<seconds>\d+) seconds the trace sink contains a span "(?P<name>[^"]+)" '
    r'whose attribute "(?P<attribute>[^"]+)" contains "(?P<fragment>.+)"'
)
def step_sink_span_attribute_contains(
    context: Context, seconds: str, name: str, attribute: str, fragment: str
) -> None:
    expected = _resolve_uid_hint(context, _unescape(fragment))
    _wait_for_span(context, int(seconds), name, _contains(attribute, expected))


@then(
    r'within (?P<seconds>\d+) seconds the trace sink contains a span "(?P<name>[^"]+)" '
    r'whose attribute "(?P<attribute>[^"]+)" is at most (?P<limit>\d+) characters '
    r"before the marker"
)
def step_sink_span_attribute_cut_at(
    context: Context, seconds: str, name: str, attribute: str, limit: str
) -> None:
    def predicate(span: Any) -> str | None:
        if attribute not in span.attributes:
            return f"span {span.name!r} has no attribute {attribute!r}"
        value = _text(span.attributes[attribute])
        marker = _TRUNCATION_MARKER.search(value)
        if marker is None:
            return f"{attribute} does not end with a truncation marker: ...{value[-80:]!r}"
        kept = len(value) - len(marker.group(0))
        if kept > int(limit):
            return f"{attribute} keeps {kept} characters before the marker, limit {limit}"
        return None

    _wait_for_span(context, int(seconds), name, predicate)


@then(
    r'within (?P<seconds>\d+) seconds the trace sink contains a span "(?P<name>[^"]+)" '
    r'whose attribute "(?P<attribute>[^"]+)" equals the UTF-8 length of the text '
    r"content the MCP test client received"
)
def step_sink_span_size_equals_received(
    context: Context, seconds: str, name: str, attribute: str
) -> None:
    texts = getattr(context, "last_response_texts", None)
    if not texts:
        raise AssertionError("The MCP test client recorded no text content for the last call.")
    received = sum(len(text.encode("utf-8")) for text in texts)
    _wait_for_span(context, int(seconds), name, _equals(attribute, str(received)))


@then(
    r'the span "(?P<name>[^"]+)" in the trace sink has attribute "(?P<attribute>[^"]+)" '
    r'containing "(?P<fragment>.+)"'
)
def step_span_attribute_containing(
    context: Context, name: str, attribute: str, fragment: str
) -> None:
    reason = _contains(attribute, _unescape(fragment))(_the_span(context, name))
    if reason is not None:
        raise AssertionError(reason)


@then(
    r'the span "(?P<name>[^"]+)" in the trace sink has attribute "(?P<attribute>[^"]+)" '
    r'ending with a marker of the form "(?P<template>[^"]+)" where n is greater than '
    r"(?P<minimum>\d+)"
)
def step_span_attribute_marker(
    context: Context, name: str, attribute: str, template: str, minimum: str
) -> None:
    if template.count("<n>") != 1:
        raise AssertionError(f"Marker template {template!r} must contain <n> once")
    head, tail = template.split("<n>")
    pattern = re.compile(re.escape(head) + r"(\d+)" + re.escape(tail) + "$")
    value = _text(_attribute(_the_span(context, name), attribute))
    match = pattern.search(value)
    if match is None:
        raise AssertionError(
            f"{attribute} does not end with a marker {template!r}: ...{value[-80:]!r}"
        )
    if int(match.group(1)) <= int(minimum):
        raise AssertionError(f"Marker reports n={match.group(1)}, expected more than {minimum}")


@then(r'the span "(?P<name>[^"]+)" in the trace sink has an attribute "(?P<attribute>[^"]+)"')
def step_span_has_attribute(context: Context, name: str, attribute: str) -> None:
    _attribute(_the_span(context, name), attribute)


@then(r'the span "(?P<name>[^"]+)" in the trace sink has no attribute "(?P<attribute>[^"]+)"')
def step_span_has_no_attribute(context: Context, name: str, attribute: str) -> None:
    span = _the_span(context, name)
    if attribute in span.attributes:
        raise AssertionError(
            f"Span {name!r} carries {attribute}={span.attributes[attribute]!r}"
        )


@then(r'the span "(?P<name>[^"]+)" in the trace sink has status "(?P<status>[A-Z]+)"')
def step_span_has_status(context: Context, name: str, status: str) -> None:
    actual = _the_span(context, name).status
    if actual != status:
        raise AssertionError(f"Span {name!r} has status {actual!r}, expected {status!r}")


# ------------------------------------------------ Then: negative witness


@then(r'no span in the trace sink contains the value "(?P<value>[^"]+)"')
def step_no_span_contains_value(context: Context, value: str) -> None:
    spans = _sink_spans(context)
    if not spans:
        raise AssertionError("The trace sink is empty; an absence witness needs spans to search.")
    for span in spans:
        for text in _all_values(span):
            if value in text:
                raise AssertionError(
                    f"Span {span.name!r} carries {value!r} in {text[:200]!r}"
                )


# --------------------------------------------------- Then: resources


def _every_span(
    context: Context, seconds: int, predicate: Callable[[Any], str | None]
) -> None:
    def check(spans: list[Any]) -> str | None:
        if not spans:
            return "the trace sink is empty"
        for span in spans:
            reason = predicate(span)
            if reason is not None:
                return f"span {span.name!r}: {reason}"
        return None

    _poll(context, seconds, check)


def _resource_equals(attribute: str, expected: str) -> Callable[[Any], str | None]:
    def predicate(span: Any) -> str | None:
        actual = span.resource_attributes.get(attribute)
        if actual is None or _text(actual) != expected:
            return f"resource attribute {attribute}={actual!r}, expected {expected!r}"
        return None

    return predicate


@then(
    r"within (?P<seconds>\d+) seconds every span in the trace sink has the resource attributes:"
)
def step_every_span_resource_table(context: Context, seconds: str) -> None:
    expected = [(row["attribute"], row["value"]) for row in context.table]

    def predicate(span: Any) -> str | None:
        reasons = [r for a, v in expected if (r := _resource_equals(a, v)(span)) is not None]
        return "; ".join(reasons) or None

    _every_span(context, int(seconds), predicate)


@then(
    r'within (?P<seconds>\d+) seconds every span in the trace sink has the resource attribute '
    r'"(?P<attribute>[^"]+)" equal to "(?P<expected>[^"]*)"'
)
def step_every_span_resource_equal(
    context: Context, seconds: str, attribute: str, expected: str
) -> None:
    _every_span(context, int(seconds), _resource_equals(attribute, expected))


@then(
    r'every span in the trace sink has the resource attribute "(?P<attribute>[^"]+)" '
    r"equal to the installed package version"
)
def step_every_span_resource_package_version(context: Context, attribute: str) -> None:
    _every_span(context, 0, _resource_equals(attribute, _installed_server_version(context)))


@then(r'every span in the trace sink has a non-empty resource attribute "(?P<attribute>[^"]+)"')
def step_every_span_resource_non_empty(context: Context, attribute: str) -> None:
    def predicate(span: Any) -> str | None:
        if not _text(span.resource_attributes.get(attribute) or ""):
            return f"resource attribute {attribute!r} is missing or empty"
        return None

    _every_span(context, 0, predicate)


# ------------------------------------------------ Then: trace structure


def _children(spans: list[Any], parent: Any) -> list[Any]:
    kids = [
        s for s in spans if s.trace_id == parent.trace_id and s.parent_span_id == parent.span_id
    ]
    return sorted(kids, key=lambda s: s.start_time_unix_nano)


@then(
    r"within (?P<seconds>\d+) seconds the trace sink contains one connected trace rooted at "
    r'span "(?P<root>[^"]+)" with child spans in order:'
)
def step_connected_trace_with_children(context: Context, seconds: str, root: str) -> None:
    """The root has no parent, every span of its trace reaches it through
    parents present in the trace, and the listed spans occur among its
    direct children in the given order. As for "the IMAP command log
    ... contains in order", other children may lie between them; the
    count witness that follows pins the total."""
    expected = [row["span"] for row in context.table]

    def check(spans: list[Any]) -> str | None:
        roots = _named(spans, root)
        if len(roots) != 1:
            return f"expected one span {root!r}, found {len(roots)}"
        top = roots[0]
        if top.parent_span_id:
            return f"{root!r} has a parent span {top.parent_span_id}"
        trace = [s for s in spans if s.trace_id == top.trace_id]
        ids = {s.span_id for s in trace}
        orphans = [s.name for s in trace if s is not top and s.parent_span_id not in ids]
        if orphans:
            return f"spans not connected to {root!r}: {orphans!r}"
        children = [s.name for s in _children(spans, top)]
        cursor = 0
        for name in expected:
            try:
                cursor = children.index(name, cursor) + 1
            except ValueError:
                return f"child {name!r} missing or out of order in {children!r}"
        return None

    _poll(context, int(seconds), check)


@then(
    r'the number of "(?P<prefix>[a-z]+)\.\*" child spans of "(?P<root>[^"]+)" equals the number '
    r'of commands the IMAP command log for "(?P<account_id>[^"]+)" recorded for that call'
)
def step_child_span_count_equals_command_log(
    context: Context, prefix: str, root: str, account_id: str
) -> None:
    """Commands "for that call" are the proxy log lines stamped between
    the start and the end of the root span: the proxy records a command
    after the server sent it and before the server can see its reply."""
    spans = _sink_spans(context)
    top = _the_span(context, root)
    children = [s for s in _children(spans, top) if s.name.startswith(f"{prefix}.")]
    log_path = (getattr(context, "imap_proxy_log_paths", None) or {}).get(account_id)
    if log_path is None or not Path(log_path).exists():
        raise AssertionError(f"No IMAP command log captured for account {account_id!r}")
    commands = []
    for line in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
        stamp, _, command = line.partition("\t")
        if not command:
            continue
        at = _iso_to_unix_nano(stamp)
        if top.start_time_unix_nano <= at <= top.end_time_unix_nano:
            commands.append(command)
    if not commands:
        raise AssertionError(f"The IMAP command log for {account_id!r} recorded nothing in the call")
    if len(children) != len(commands):
        raise AssertionError(
            f"{len(children)} {prefix}.* child spans {[s.name for s in children]!r} "
            f"but {len(commands)} commands logged: {commands!r}"
        )


use_step_matcher("parse")
