"""Shared helpers for the Brother protocol/client tests: fixture loading and a
scripted fake scanner on a localhost socket (port 0, xdist-safe)."""
import asyncio
import base64
import hashlib
import json
import struct
from pathlib import Path

from app.services import brother_protocol as bp

FIXTURES = Path(__file__).parent / "fixtures" / "brother"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())
NAMES = sorted(MANIFEST)

OK = ("S>C", bp.GREETING_OK)
NG = ("S>C", bp.GREETING_BUSY)
HOLD = ("HOLD", b"")  # stay silent until the client disconnects
CLOSE = ("CLOSE", b"")  # hang up on the client


def load(name):
    """Fixture segments as [(dir, bytes)]."""
    out = []
    for line in (FIXTURES / f"{name}.jsonl").read_text().splitlines():
        seg = json.loads(line)
        out.append((seg["dir"], base64.b64decode(seg["b64"])))
    return out


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lease_reply(xdpi=300, ydpi=300, adf=2, w_mm=209, w_px=2480, h_mm=291, h_px=3437):
    body = f"{xdpi},{ydpi},{adf},{w_mm},{w_px},{h_mm},{h_px},".encode() + b"\0"
    return b"\x00" + bytes([len(body)]) + b"\x00" + body


def frame(kind, page, payload):
    return bytes([kind]) + b"\x07\x00" + struct.pack("<H", page) + b"\x84\x00\x00\x00\x00" + (
        struct.pack("<H", len(payload)) + payload
    )


def page_end(page):
    return b"\x82\x07\x00" + struct.pack("<H", page) + b"\x84\x00\x00\x00\x00"


def split_stream(stream: bytes):
    """Split a captured stream into whole frames (bytes each) plus the tail."""
    pos, frames = 0, []
    while stream[pos] in bp.DATA_KINDS:
        (n,) = struct.unpack_from("<H", stream, pos + 10)
        frames.append(stream[pos : pos + 12 + n])
        pos += 12 + n
    return frames, stream[pos:]


def with_page(frame_bytes: bytes, page: int) -> bytes:
    return frame_bytes[:3] + struct.pack("<H", page) + frame_bytes[5:]


class FakeScanner:
    """Replays scripted connections; verifies the exact bytes the client sends.

    ``connections`` is a list of scripts; a script is a list of
    ``("S>C", bytes)`` (send), ``("C>S", bytes)`` (expect exactly) or ``HOLD``.
    After the script the server waits for the client to hang up and records
    anything extra it sent in ``trailing``.
    """

    def __init__(self, connections, chunk=None):
        self.connections = connections
        self.chunk = chunk
        self.errors = []
        self.trailing = []
        self.accepted = 0
        self._server = None

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader, writer):
        idx = self.accepted
        self.accepted += 1
        script = self.connections[idx] if idx < len(self.connections) else [NG]
        trailing = b""
        try:
            for direction, data in script:
                if direction == "S>C":
                    if self.chunk:
                        for i in range(0, len(data), self.chunk):
                            writer.write(data[i : i + self.chunk])
                            await writer.drain()
                    else:
                        writer.write(data)
                        await writer.drain()
                elif direction == "C>S":
                    try:
                        got = await asyncio.wait_for(reader.readexactly(len(data)), 5)
                    except (asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
                        self.errors.append(f"conn {idx}: client sent too little ({exc!r})")
                        return
                    if got != data:
                        self.errors.append(f"conn {idx}: expected {data!r}, got {got!r}")
                        return
                elif direction == "CLOSE":
                    return
                else:  # HOLD
                    await reader.read()
                    return
            try:
                trailing = await asyncio.wait_for(reader.read(), 3)
            except asyncio.TimeoutError:
                self.errors.append(f"conn {idx}: client never hung up")
        finally:
            self.trailing.append(trailing)
            writer.close()
