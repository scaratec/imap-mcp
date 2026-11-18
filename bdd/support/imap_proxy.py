"""IMAP MITM proxy for BDD scenarios that need wire-level fault injection.

The proxy listens on a free local port and forwards every byte to a
real Dovecot instance. Several hooks let scenarios induce behaviour
the live Dovecot cannot produce on its own.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CAPABILITY_LINE_RE = re.compile(rb"^(\*\s+CAPABILITY\s+)(.+)$", re.IGNORECASE)
CAPABILITY_BRACKET_RE = re.compile(rb"\[CAPABILITY\s+([^\]]+)\]", re.IGNORECASE)
LITERAL_TRAILER_RE = re.compile(rb"\{(\d+)\}\s*$")
UIDVALIDITY_RE = re.compile(rb"\[UIDVALIDITY\s+(\d+)\]", re.IGNORECASE)


def _decode_mutf7(wire: str) -> str:
    out: list[str] = []
    i = 0
    n = len(wire)
    while i < n:
        if wire[i] != "&":
            out.append(wire[i])
            i += 1
            continue
        end = wire.find("-", i + 1)
        if end < 0:
            return wire
        if end == i + 1:
            out.append("&")
            i = end + 1
            continue
        b64 = wire[i + 1 : end].replace(",", "/")
        pad = (4 - len(b64) % 4) % 4
        try:
            raw = base64.b64decode(b64 + "=" * pad)
            out.append(raw.decode("utf-16-be"))
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return wire
        i = end + 1
    return "".join(out)


def _encode_mutf7(utf8: str) -> str:
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        raw = "".join(buf).encode("utf-16-be")
        b64 = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        out.append("&" + b64 + "-")
        buf.clear()

    for ch in utf8:
        cp = ord(ch)
        if 0x20 <= cp <= 0x7E:
            flush()
            if ch == "&":
                out.append("&-")
            else:
                out.append(ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def _extract_mailbox(command_line: bytes) -> str | None:
    parts = command_line.split(maxsplit=2)
    if len(parts) < 3:
        return None
    rest = parts[2].strip()
    if not rest:
        return None
    if rest.startswith(b'"'):
        end = rest.find(b'"', 1)
        if end > 0:
            return rest[1:end].decode("ascii", errors="replace")
    first = rest.split(None, 1)[0]
    return first.decode("ascii", errors="replace")


def _mailbox_matches(spec_mailbox: str | None, cmd_mailbox: str | None) -> bool:
    if spec_mailbox is None:
        return True
    if cmd_mailbox is None:
        return False
    sm = spec_mailbox.strip('"')
    cm = cmd_mailbox.strip('"')
    if sm == cm:
        return True
    if _decode_mutf7(sm) == _decode_mutf7(cm):
        return True
    return bool(_encode_mutf7(sm) == cm or sm == _encode_mutf7(cm))


def _parse_tag_and_verb(command_line: bytes) -> tuple[bytes, str]:
    parts = command_line.split(maxsplit=2)
    if len(parts) < 2:
        return b"", ""
    tag = parts[0]
    verb = parts[1].decode("ascii", errors="replace").upper()
    if verb == "UID" and len(parts) >= 3:
        sub = parts[2].split(None, 1)[0].decode("ascii", errors="replace").upper()
        return tag, f"UID {sub}"
    return tag, verb


def _consume_command_match(specs: list[dict[str, Any]], verb_upper: str, mailbox: str | None) -> dict[str, Any] | None:
    for spec in specs:
        cmd = (spec.get("command") or "").upper()
        if cmd != verb_upper:
            continue
        if not _mailbox_matches(spec.get("mailbox"), mailbox):
            continue
        remaining = spec.get("remaining")
        if remaining is None:
            return spec
        if remaining <= 0:
            continue
        spec["remaining"] = remaining - 1
        return spec
    return None


def _consume_delay_match(spec: dict[str, Any], verb_upper: str, mailbox: str | None) -> bool:
    cmd = (spec.get("command") or "").upper()
    if cmd != verb_upper:
        return False
    if not _mailbox_matches(spec.get("mailbox"), mailbox):
        return False
    remaining = spec.get("remaining")
    if remaining is None:
        return True
    if remaining <= 0:
        return False
    spec["remaining"] = remaining - 1
    return True


def _build_tagged_response(
    tag: bytes,
    verb_upper: str,
    *,
    status: str = "NO",
    text: str | None = None,
) -> bytes:
    if not tag:
        tag = b"*"
    status_upper = status.upper()
    if text is None:
        text = f"[SERVERBUG] simulated error 500 ({verb_upper})"
    return (
        tag
        + b" "
        + status_upper.encode("ascii", "replace")
        + b" "
        + text.encode("utf-8", "replace")
        + b"\r\n"
    )


def _strip_tokens(payload: bytes, tokens: list[str]) -> bytes:
    if not tokens:
        return payload
    text = payload.decode("ascii", errors="replace")
    parts = text.split()
    keep = [p for p in parts if p.upper() not in {t.upper() for t in tokens}]
    return " ".join(keep).encode("ascii")


def _rewrite_capability(line: bytes, tokens: list[str]) -> bytes:
    if not tokens:
        return line
    match = CAPABILITY_LINE_RE.match(line)
    if match:
        head, payload = match.group(1), match.group(2)
        return head + _strip_tokens(payload, tokens) + b"\r\n" if line.endswith(
            b"\r\n"
        ) else head + _strip_tokens(payload, tokens)

    def _bracket_sub(m: re.Match[bytes]) -> bytes:
        return b"[CAPABILITY " + _strip_tokens(m.group(1), tokens) + b"]"

    return CAPABILITY_BRACKET_RE.sub(_bracket_sub, line)


def _translate_login(line: bytes, credentials: dict[str, str]) -> bytes:
    for scenario_password, fixture_password in credentials.items():
        quoted = b'"' + scenario_password.encode("utf-8") + b'"'
        if line.rstrip(b"\r\n").endswith(quoted):
            replacement = b'"' + fixture_password.encode("utf-8") + b'"'
            return line.replace(quoted, replacement)
    return line


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


class ProxySession:
    """One client <-> upstream session."""

    def __init__(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        config: dict[str, Any],
        config_path: Path | None = None,
    ) -> None:
        self.client_reader = client_reader
        self.client_writer = client_writer
        self.config = config
        self.config_path = config_path
        self._uidv_state = "idle"
        self._cmd_log_lock = asyncio.Lock()
        self.pending_delay_seconds: float = 0.0
        self.drop_before_tagged: bool = False
        self._awaiting_list_response = False
        self._current_selected_mailbox: str | None = None

    @staticmethod
    def _load_config(config_path: Path) -> dict[str, Any]:
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    async def _log_command(self, line: bytes) -> None:
        path = self.config.get("command_log_path")
        if not path:
            return
        async with self._cmd_log_lock:
            try:
                with open(path, "a", encoding="utf-8") as fh:  # noqa: ASYNC230
                    fh.write(f"{_now_iso()}\t{line.rstrip().decode('ascii', 'replace')}\n")
            except OSError as exc:
                logger.warning("Failed to write to proxy command log %s: %s", path, exc)

    async def run(self) -> None:
        upstream_host = self.config.get("upstream_host", "127.0.0.1")
        upstream_port = int(self.config.get("upstream_port", 11143))
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                upstream_host, upstream_port
            )
        except OSError as exc:
            logger.warning("Proxy could not connect to upstream %s:%d: %s", upstream_host, upstream_port, exc)
            self.client_writer.close()
            return

        c2s = asyncio.create_task(self._pump_c2s(upstream_writer))
        s2c = asyncio.create_task(self._pump_s2c(upstream_reader))
        try:
            _done, pending = await asyncio.wait(
                {c2s, s2c}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
        finally:
            for w in (self.client_writer, upstream_writer):
                try:
                    w.close()
                    await w.wait_closed()
                except OSError as exc:
                    logger.debug("Error closing proxy stream: %s", exc)

    async def _pump_c2s(self, upstream: asyncio.StreamWriter) -> None:
        while True:
            if self.config_path:
                self.config = ProxySession._load_config(self.config_path) or self.config
            trigger = (self.config.get("uidvalidity_change_after") or "").upper()
            inject_specs = list(self.config.get("inject_failure_on", []) or [])
            delay_spec = self.config.get("delay_command_seconds") or None

            line = await self.client_reader.readline()
            if not line:
                upstream.close()
                return
            command_line = line.rstrip(b"\r\n")
            await self._log_command(command_line)
            tag, verb = _parse_tag_and_verb(command_line)
            mailbox = _extract_mailbox(command_line)

            if verb in ("SELECT", "EXAMINE", "STATUS") and mailbox:
                self._current_selected_mailbox = mailbox

            if verb == "LIST":
                self._awaiting_list_response = True

            if verb == "LOGIN":
                line = _translate_login(line, self.config.get("login_credentials") or {})

            matched_spec = (
                _consume_command_match(inject_specs, verb, mailbox) if verb else None
            )
            if matched_spec is not None:
                if self.config_path:
                    try:
                        self.config["inject_failure_on"] = inject_specs
                        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
                    except OSError:
                        pass
                mode = (matched_spec.get("mode") or "no_response").lower()
                if mode == "close":
                    try:
                        self.client_writer.close()
                    except OSError as exc:
                        logger.debug("Error closing client writer on close fault: %s", exc)
                    return

                if mode == "close_after_forward":
                    self.drop_before_tagged = True
                    upstream.write(line)
                    try:
                        await upstream.drain()
                    except OSError as exc:
                        logger.debug("Upstream drain failed on close_after_forward: %s", exc)
                        return
                    tail = command_line
                    while True:
                        m = LITERAL_TRAILER_RE.search(tail)
                        if not m:
                            break
                        n = int(m.group(1))
                        payload = await self.client_reader.readexactly(n)
                        upstream.write(payload)
                        cont = await self.client_reader.readline()
                        if not cont:
                            try:
                                await upstream.drain()
                            except OSError:
                                pass
                            return
                        upstream.write(cont)
                        try:
                            await upstream.drain()
                        except OSError:
                            return
                        tail = cont.rstrip(b"\r\n")
                    continue

                status = "BAD" if mode == "bad_response" else "NO"
                resp = _build_tagged_response(
                    tag,
                    verb,
                    status=status,
                    text=matched_spec.get("response_text"),
                )
                self.client_writer.write(resp)
                try:
                    await self.client_writer.drain()
                except OSError as exc:
                    logger.debug("Client write error: %s", exc)
                    return
                continue

            if (
                delay_spec is not None
                and verb
                and _consume_delay_match(delay_spec, verb, mailbox)
            ):
                # Arm delayed response delivery on the response pump
                self.pending_delay_seconds = float(delay_spec.get("seconds", 0) or 0)
                if self.config_path:
                    try:
                        self.config["delay_command_seconds"] = delay_spec
                        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
                    except OSError:
                        pass

            if (
                trigger
                and self._uidv_state == "idle"
                and trigger.encode("ascii", "replace") in command_line.upper()
            ):
                self._uidv_state = "saw_cmd"

            upstream.write(line)
            try:
                await upstream.drain()
            except OSError as exc:
                logger.debug("Upstream drain failed: %s", exc)
                return

            tail = command_line
            while True:
                m = LITERAL_TRAILER_RE.search(tail)
                if not m:
                    break
                n = int(m.group(1))
                payload = await self.client_reader.readexactly(n)
                upstream.write(payload)
                cont = await self.client_reader.readline()
                if not cont:
                    try:
                        await upstream.drain()
                    except OSError:
                        pass
                    return
                upstream.write(cont)
                try:
                    await upstream.drain()
                except OSError:
                    return
                tail = cont.rstrip(b"\r\n")

    async def _pump_s2c(self, upstream: asyncio.StreamReader) -> None:
        while True:
            if self.config_path:
                self.config = ProxySession._load_config(self.config_path) or self.config
            strip = list(self.config.get("strip_capabilities", []) or [])
            new_validity = str(self.config.get("uidvalidity_new_value") or "")
            rewrite_rules = self.config.get("rewrite_uidvalidity")

            line = await upstream.readline()
            if not line:
                self.client_writer.close()
                return

            if self.drop_before_tagged and not line.startswith(b"+") and not line.startswith(b"*"):
                self.drop_before_tagged = False
                try:
                    self.client_writer.close()
                except OSError:
                    pass
                return

            # Apply pending delay before delivering response line to client,
            # but do not delay continuation (+) requests needed for literal transfer.
            if self.pending_delay_seconds > 0 and not line.startswith(b"+"):
                delay = self.pending_delay_seconds
                self.pending_delay_seconds = 0.0
                await asyncio.sleep(delay)

            # Handle inject_list_entry before the tagged completion of LIST
            if self._awaiting_list_response and not line.startswith(b"*"):
                self._awaiting_list_response = False
                inject_entry = self.config.get("inject_list_entry")
                if inject_entry:
                    raw_inject = inject_entry if inject_entry.endswith("\r\n") else inject_entry + "\r\n"
                    self.client_writer.write(raw_inject.encode("ascii", errors="replace"))

            # State machine for general UIDVALIDITY injection
            if self._uidv_state == "saw_cmd" and not line.startswith(b"*"):
                rewritten = _rewrite_capability(line, strip)
                self.client_writer.write(rewritten)
                self._uidv_state = "inject"
            elif self._uidv_state == "inject":
                inject = f"* OK [UIDVALIDITY {new_validity}] UIDs invalidated\r\n".encode("ascii")
                self.client_writer.write(inject)
                self._uidv_state = "idle"
                rewritten = _rewrite_capability(line, strip)
                self.client_writer.write(rewritten)
            else:
                rewritten = _rewrite_capability(line, strip)
                # Check folder-specific rewrite_uidvalidity
                if rewrite_rules and UIDVALIDITY_RE.search(rewritten):
                    target_val = None
                    if isinstance(rewrite_rules, dict):
                        target_mbox = rewrite_rules.get("mailbox")
                        if _mailbox_matches(target_mbox, self._current_selected_mailbox):
                            target_val = rewrite_rules.get("new_value")
                    elif isinstance(rewrite_rules, list):
                        for r in rewrite_rules:
                            if _mailbox_matches(r.get("mailbox"), self._current_selected_mailbox):
                                target_val = r.get("new_value")
                                break
                    if target_val is not None:
                        rewritten = UIDVALIDITY_RE.sub(f"[UIDVALIDITY {target_val}]".encode("ascii"), rewritten)

                self.client_writer.write(rewritten)

            stripped = line.rstrip(b"\r\n")
            m = LITERAL_TRAILER_RE.search(stripped)
            if m:
                payload = await upstream.readexactly(int(m.group(1)))
                self.client_writer.write(payload)
            try:
                await self.client_writer.drain()
            except OSError as exc:
                logger.debug("Client writer drain failed: %s", exc)
                return


def _log_connection(config: dict[str, Any], writer: asyncio.StreamWriter) -> None:
    path = config.get("connection_log_path")
    if not path:
        return
    peer = writer.get_extra_info("peername")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{_now_iso()}\tCONNECT {peer!r}\n")
    except OSError as exc:
        logger.warning("Failed to write connection log %s: %s", path, exc)


async def _main(host: str, port: int, config_path: Path) -> None:
    shared_config = ProxySession._load_config(config_path)

    async def _on_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        cfg = ProxySession._load_config(config_path) or shared_config
        _log_connection(cfg, writer)
        if cfg.get("refuse_connections"):
            writer.close()
            return
        session = ProxySession(reader, writer, cfg, config_path=config_path)
        try:
            await session.run()
        except OSError as exc:
            logger.debug("Proxy session error: %s", exc)

    server = await asyncio.start_server(_on_client, host, port)
    sys.stdout.write(f"LISTEN {host}:{port}\n")
    sys.stdout.flush()
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(_main(args.host, args.port, args.config))
