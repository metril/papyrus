import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, channel: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[channel].append(websocket)

    def disconnect(self, channel: str, websocket: WebSocket):
        if websocket in self.active_connections[channel]:
            self.active_connections[channel].remove(websocket)
        if not self.active_connections[channel]:
            del self.active_connections[channel]

    async def broadcast(self, channel: str, message: dict):
        # Snapshot the connection list so concurrent disconnects during the
        # gather don't mutate what we're iterating.
        conns = list(self.active_connections.get(channel, []))
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
