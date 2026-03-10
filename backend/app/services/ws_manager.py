from collections import defaultdict

from fastapi import WebSocket


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
        dead = []
        for ws in self.active_connections[channel]:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(channel, ws)


ws_manager = ConnectionManager()
