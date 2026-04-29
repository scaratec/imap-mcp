"""IMAP MITM proxy for BDD scenarios that need wire-level fault
injection (LIM-0005 paydown).

The proxy listens on a free local port and forwards every byte to a
real Dovecot instance. Two rewriting hooks let scenarios induce
behaviour the live Dovecot cannot produce on its own:

- **`strip_capabilities`** — token-list to remove from any
  `* CAPABILITY` line and from `* OK [CAPABILITY …]` status responses.
  Used by the "no MOVE extension" scenario to make the server fall
  back to COPY+STORE+EXPUNGE.

- **`uidvalidity_change_after`** — a string substring that triggers
  the proxy to inject one extra `* OK [UIDVALIDITY <new>]` untagged
  response immediately before the *next* upstream→client frame after
  the matching client→upstream command. Used by the UIDVALIDITY-
  staleness scenario.

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
      "uidvalidity_new_value": "99999"
    }

The file is reloaded on every new client connection so the harness
can flip rules between scenarios without restarting the proxy.
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
    """One client↔upstream session. Owns its own pair of relays."""

    def __init__(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        config_path: Path,
    ) -> None:
        self.client_reader = client_reader
        self.client_writer = client_writer
        self.config_path = config_path
        self.config: dict[str, Any] = self._load_config()
        # Three states on the UIDVALIDITY-injection state machine:
        #   "idle"      — trigger not yet seen
        #   "saw_cmd"   — client sent the trigger command; awaiting
        #                  upstream's tagged completion of THAT cmd
        #   "inject"    — tagged completion seen; the FIRST response
        #                  line of the NEXT upstream burst gets the
        #                  injection prepended
        self._uidv_state = "idle"
        self._cmd_log_lock = asyncio.Lock()

    def _load_config(self) -> dict[str, Any]:
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
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
        if the configured trigger matches."""
        trigger = (self.config.get("uidvalidity_change_after") or "").upper()
        while True:
            frame = await self._read_imap_frame(self.client_reader)
            if not frame:
                upstream.close()
                return
            command_line = frame.split(b"\r\n", 1)[0]
            await self._log_command(command_line)
            if (
                trigger
                and self._uidv_state == "idle"
                and trigger.encode("ascii", "replace") in command_line.upper()
            ):
                self._uidv_state = "saw_cmd"
            upstream.write(frame)
            try:
                await upstream.drain()
            except Exception:
                return

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
    async def _on_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        session = ProxySession(reader, writer, config_path)
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
