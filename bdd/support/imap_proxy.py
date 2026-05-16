"""IMAP MITM proxy for BDD scenarios that need wire-level fault
injection (LIM-0005 paydown).

The proxy listens on a free local port and forwards every byte to a
real Dovecot instance. Several hooks let scenarios induce behaviour
the live Dovecot cannot produce on its own:

- **`strip_capabilities`** — token-list to remove from any
  `* CAPABILITY` line and from `* OK [CAPABILITY …]` status responses.
  Used by the "no MOVE extension" scenario to make the server fall
  back to COPY+STORE+EXPUNGE.

- **`uidvalidity_change_after`** — a string substring that triggers
  the proxy to inject one extra `* OK [UIDVALIDITY <new>]` untagged
  response immediately before the *next* upstream→client frame after
  the matching client→upstream command. Used by the UIDVALIDITY-
  staleness scenario.

- **`inject_failure_on`** — list of `{command, remaining}` specs.
  When the next client→upstream command's verb matches `command`
  (case-insensitive), the proxy synthesises a tagged `<tag> NO
  simulated error 500\r\n` reply to the client and does NOT forward
  the command to upstream. Used by the saga's APPEND-5xx and
  EXPUNGE-5xx scenarios. `remaining` decrements per match; `null`
  means unlimited.

- **`delay_command_seconds`** — single `{command, seconds, remaining}`
  spec. When the next client→upstream command's verb matches, the
  proxy `await asyncio.sleep(seconds)` BEFORE forwarding it upstream.
  Used by the APPEND-timeout scenario in conjunction with the server's
  `IMAP_MCP_APPEND_TIMEOUT` to make the server-side `wait_for` fire.

Every client→upstream command line is also written, with a UTC
timestamp, into a per-account command log file. The harness reads
the file as a second verification channel (BDD Guidelines §13.2).

The proxy parses only enough of the IMAP framing to honour
synchronous literals (`{N}`-prefixed continuation lines). It does NOT
implement IMAP semantics — it is a byte-level bridge with three
keyhole rewrite rules.

Configuration is loaded from the JSON file pointed at by
`IMAP_MCP_PROXY_CONFIG`:

    {
      "upstream_host": "127.0.0.1",
      "upstream_port": 11143,
      "command_log_path": "/tmp/.../proxy.log",
      "strip_capabilities": ["MOVE"],
      "uidvalidity_change_after": "UID SEARCH",
      "uidvalidity_new_value": "99999",
      "inject_failure_on": [{"command": "APPEND", "remaining": 1}],
      "delay_command_seconds": {"command": "APPEND", "seconds": 45, "remaining": 1}
    }

The file is read ONCE at proxy startup and shared across all
sessions. Fault counters live inside the shared dict and mutate in
place so they persist across the per-retry IMAP reconnects the
saga's recovery loop performs. To flip rules, the harness writes a
new config and restarts the proxy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CAPABILITY_LINE_RE = re.compile(rb"^(\*\s+CAPABILITY\s+)(.+)$", re.IGNORECASE)
CAPABILITY_BRACKET_RE = re.compile(
    rb"\[CAPABILITY\s+([^\]]+)\]", re.IGNORECASE
)
LITERAL_TRAILER_RE = re.compile(rb"\{(\d+)\}\s*$")


def _parse_tag_and_verb(command_line: bytes) -> tuple[bytes, str]:
    """Split an IMAP command line into `(tag, verb_upper)`. Returns
    `(b'', '')` if the line is malformed (not enough tokens)."""
    parts = command_line.split(maxsplit=2)
    if len(parts) < 2:
        return b"", ""
    return parts[0], parts[1].decode("ascii", errors="replace").upper()


def _consume_command_match(specs: list, verb_upper: str) -> dict | None:
    """If any spec in `specs` matches `verb_upper` and has a non-zero
    `remaining` slot (or `null` = unlimited), decrement and return the
    matched spec dict. Returns None when no spec matches.

    Specs are mutated in place — list contents are shared with the
    `ProxySession.config` dict, which is how the count survives
    across sequential commands within a single connection."""
    for spec in specs:
        cmd = (spec.get("command") or "").upper()
        if cmd != verb_upper:
            continue
        remaining = spec.get("remaining")
        if remaining is None:
            return spec
        if remaining <= 0:
            continue
        spec["remaining"] = remaining - 1
        return spec
    return None


def _consume_delay_match(spec: dict, verb_upper: str) -> bool:
    """Same idea as `_consume_command_match` but for the singular
    `delay_command_seconds` spec."""
    cmd = (spec.get("command") or "").upper()
    if cmd != verb_upper:
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
    """Tagged response line synthesised in lieu of forwarding `tag verb`
    to upstream. `status` is "NO" or "BAD". `text` is the reason text
    after the status token; when None, the default text mentions
    `simulated error 500` so grepping the IMAP client's exception text
    from logs is unambiguous."""
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
    """Remove `tokens` (case-insensitive whole words) from a
    capability-list payload, preserving spacing."""
    if not tokens:
        return payload
    text = payload.decode("ascii", errors="replace")
    parts = text.split()
    keep = [p for p in parts if p.upper() not in {t.upper() for t in tokens}]
    return " ".join(keep).encode("ascii")


def _rewrite_capability(line: bytes, tokens: list[str]) -> bytes:
    """Rewrite a single response line, stripping any capability
    tokens that match `tokens`."""
    if not tokens:
        return line
    match = CAPABILITY_LINE_RE.match(line)
    if match:
        head, payload = match.group(1), match.group(2)
        return head + _strip_tokens(payload, tokens) + b"\r\n" if line.endswith(
            b"\r\n"
        ) else head + _strip_tokens(payload, tokens)

    def _bracket_sub(m: "re.Match[bytes]") -> bytes:
        return b"[CAPABILITY " + _strip_tokens(m.group(1), tokens) + b"]"

    return CAPABILITY_BRACKET_RE.sub(_bracket_sub, line)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


class ProxySession:
    """One client↔upstream session. Owns its own pair of relays.

    The `config` dict is SHARED across all sessions (passed in by
    `_main`). Per-session UIDVALIDITY state lives on the instance so
    each connection re-arms the trigger; fault-counter state lives on
    the shared dict so `inject_failure_on[*].remaining` and
    `delay_command_seconds.remaining` decrement across reconnects (the
    server's recovery loop opens fresh IMAP connections per retry)."""

    def __init__(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        config: dict[str, Any],
    ) -> None:
        self.client_reader = client_reader
        self.client_writer = client_writer
        self.config = config
        # Three states on the UIDVALIDITY-injection state machine:
        #   "idle"      — trigger not yet seen
        #   "saw_cmd"   — client sent the trigger command; awaiting
        #                  upstream's tagged completion of THAT cmd
        #   "inject"    — tagged completion seen; the FIRST response
        #                  line of the NEXT upstream burst gets the
        #                  injection prepended
        self._uidv_state = "idle"
        self._cmd_log_lock = asyncio.Lock()

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
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(
                        f"{_now_iso()}\t{line.rstrip().decode('ascii', 'replace')}\n"
                    )
            except OSError:
                pass

    async def run(self) -> None:
        upstream_host = self.config.get("upstream_host", "127.0.0.1")
        upstream_port = int(self.config.get("upstream_port", 11143))
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                upstream_host, upstream_port
            )
        except OSError as exc:
            self.client_writer.close()
            return

        c2s = asyncio.create_task(self._pump_c2s(upstream_writer))
        s2c = asyncio.create_task(self._pump_s2c(upstream_reader))
        try:
            done, pending = await asyncio.wait(
                {c2s, s2c}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
        finally:
            for w in (self.client_writer, upstream_writer):
                try:
                    w.close()
                    await w.wait_closed()
                except Exception:
                    pass

    async def _read_imap_frame(
        self, reader: asyncio.StreamReader
    ) -> bytes:
        """Read one IMAP frame: a CRLF-terminated line, plus any
        synchronous literals it inlines (`{N}`-trailer expansion).

        Returns the entire frame including the terminating CRLF for
        each constituent line. Returns `b''` on EOF.
        """
        line = await reader.readline()
        if not line:
            return b""
        chunks = [line]
        while True:
            stripped = chunks[-1].rstrip(b"\r\n")
            m = LITERAL_TRAILER_RE.search(stripped)
            if not m:
                break
            n = int(m.group(1))
            payload = await reader.readexactly(n)
            chunks.append(payload)
            cont = await reader.readline()
            if not cont:
                break
            chunks.append(cont)
        return b"".join(chunks)

    async def _pump_c2s(self, upstream: asyncio.StreamWriter) -> None:
        """Client → Upstream. Logs commands; arms UIDVALIDITY-inject
        if the configured trigger matches; honours fault-injection
        hooks (`inject_failure_on`, `delay_command_seconds`)."""
        trigger = (self.config.get("uidvalidity_change_after") or "").upper()
        inject_specs = list(self.config.get("inject_failure_on", []) or [])
        delay_spec = self.config.get("delay_command_seconds") or None
        while True:
            line = await self.client_reader.readline()
            if not line:
                upstream.close()
                return
            command_line = line.rstrip(b"\r\n")
            await self._log_command(command_line)
            tag, verb = _parse_tag_and_verb(command_line)

            matched_spec = (
                _consume_command_match(inject_specs, verb) if verb else None
            )
            if matched_spec is not None:
                mode = (matched_spec.get("mode") or "no_response").lower()
                # Do NOT forward upstream. The client's APPEND-literal
                # body never gets a `+ continue` so it stays unsent —
                # upstream sees nothing.
                if mode == "close":
                    # Close the client-facing writer without sending any
                    # tagged response. The IMAP client sees a connection
                    # drop after its command line was acknowledged on
                    # the wire — exactly the "no tagged response, no
                    # timeout either" surface the append_failed
                    # catch-all maps to.
                    try:
                        self.client_writer.close()
                    except Exception:
                        pass
                    return
                if mode == "bad_response":
                    status = "BAD"
                else:
                    status = "NO"
                resp = _build_tagged_response(
                    tag,
                    verb,
                    status=status,
                    text=matched_spec.get("response_text"),
                )
                self.client_writer.write(resp)
                try:
                    await self.client_writer.drain()
                except Exception:
                    return
                continue

            if (
                delay_spec is not None
                and verb
                and _consume_delay_match(delay_spec, verb)
            ):
                # Sleep, then DO NOT forward. The server's
                # `wait_for(timeout=N)` will have fired by the time we
                # wake, and forwarding stale literal-body bytes after
                # the client's APPEND was cancelled leads to upstream
                # confusion. Subsequent commands (LOGOUT, retry on a
                # fresh connection) are handled normally.
                await asyncio.sleep(float(delay_spec.get("seconds", 0) or 0))
                continue

            if (
                trigger
                and self._uidv_state == "idle"
                and trigger.encode("ascii", "replace") in command_line.upper()
            ):
                self._uidv_state = "saw_cmd"

            # Forward the command line FIRST. Synchronous IMAP literals
            # require the upstream to send `+ continue` (handled by
            # `_pump_s2c` in parallel) before the client sends the body
            # bytes — so reading the body before forwarding the command
            # would deadlock.
            upstream.write(line)
            try:
                await upstream.drain()
            except Exception:
                return

            # Then iteratively forward any literal-body bytes the
            # client sends after seeing `+`, plus the trailing CRLF
            # line (which itself may carry another `{N}` trailer).
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
                    except Exception:
                        pass
                    return
                upstream.write(cont)
                try:
                    await upstream.drain()
                except Exception:
                    return
                tail = cont.rstrip(b"\r\n")

    async def _pump_s2c(self, upstream: asyncio.StreamReader) -> None:
        """Upstream → Client. Rewrites CAPABILITY lines and (when
        armed) prepends a `* OK [UIDVALIDITY <new>]` untagged
        response to the very first response line that follows the
        trigger command's tagged completion."""
        strip = list(self.config.get("strip_capabilities", []) or [])
        new_validity = str(self.config.get("uidvalidity_new_value") or "")
        while True:
            line = await upstream.readline()
            if not line:
                self.client_writer.close()
                return

            # State machine: when armed and the trigger command's
            # tagged completion has just gone through, the *next*
            # incoming line gets the injection prepended.
            if self._uidv_state == "saw_cmd" and not line.startswith(b"*"):
                # Tagged completion of the trigger command. Forward
                # it normally, then arm the next response.
                rewritten = _rewrite_capability(line, strip)
                self.client_writer.write(rewritten)
                self._uidv_state = "inject"
            elif self._uidv_state == "inject":
                inject = (
                    f"* OK [UIDVALIDITY {new_validity}] UIDs invalidated\r\n"
                ).encode("ascii")
                self.client_writer.write(inject)
                self._uidv_state = "idle"
                rewritten = _rewrite_capability(line, strip)
                self.client_writer.write(rewritten)
            else:
                rewritten = _rewrite_capability(line, strip)
                self.client_writer.write(rewritten)
            # Pass through any literals that follow (mostly applies to
            # FETCH responses; CAPABILITY rewrites never carry literals).
            stripped = line.rstrip(b"\r\n")
            m = LITERAL_TRAILER_RE.search(stripped)
            if m:
                payload = await upstream.readexactly(int(m.group(1)))
                self.client_writer.write(payload)
            try:
                await self.client_writer.drain()
            except Exception:
                return


async def _main(host: str, port: int, config_path: Path) -> None:
    # Load the config dict ONCE per proxy process and share it across
    # every session. Fault counters (`inject_failure_on[*].remaining`,
    # `delay_command_seconds.remaining`) live inside this dict and
    # mutate in place — they must persist across the per-retry IMAP
    # reconnects the saga's recovery loop performs.
    shared_config = ProxySession._load_config(config_path)

    async def _on_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        session = ProxySession(reader, writer, shared_config)
        try:
            await session.run()
        except Exception:
            pass

    server = await asyncio.start_server(_on_client, host, port)
    sys.stdout.write(f"LISTEN {host}:{port}\n")
    sys.stdout.flush()
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(prog="imap-proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    asyncio.run(_main(args.host, args.port, Path(args.config)))


if __name__ == "__main__":
    main()
