import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

from app.services.scan_access import scan_access

logger = logging.getLogger(__name__)
_UNSET = object()


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = defaultdict(list)
        # Identity (WebSocketIdentity) per socket, for channels that filter by owner.
        self._identities: dict[WebSocket, object] = {}

    async def connect(self, channel: str, websocket: WebSocket, identity=None):
        await websocket.accept()
        if identity is not None:
            self._identities[websocket] = identity
        self.active_connections[channel].append(websocket)

    def disconnect(self, channel: str, websocket: WebSocket):
        self._identities.pop(websocket, None)
        if websocket in self.active_connections[channel]:
            self.active_connections[channel].remove(websocket)
        if not self.active_connections[channel]:
            del self.active_connections[channel]

    async def broadcast_scan(self, message: dict, owner_id) -> None:
        """Send a `scans` channel message only to sockets whose identity may see a scan owned by
        `owner_id` (None = network scan, sessions only)."""
        await self.broadcast("scans", message, owner_id=owner_id)

    async def broadcast(self, channel: str, message: dict, owner_id=_UNSET):
        # Snapshot the connection list so concurrent disconnects during the
        # gather don't mutate what we're iterating.
        conns = list(self.active_connections.get(channel, []))
        if channel == "scans":
            if owner_id is _UNSET:  # fail closed: an unowned broadcast reaches nobody
                logger.error("scans broadcast without an owner dropped")
                return
            conns = [
                ws for ws in conns
                if (ident := self._identities.get(ws)) is not None
                and scan_access(ident.user, ident.permissions, owner_id)
            ]
        if not conns:
            return

        results = await asyncio.gather(
            *(ws.send_json(message) for ws in conns),
            return_exceptions=True,
        )
        for ws, result in zip(conns, results):
            if isinstance(result, Exception):
                logger.debug(
                    "Dropping dead WebSocket on channel %s: %r", channel, result
                )
                self.disconnect(channel, ws)


ws_manager = ConnectionManager()
