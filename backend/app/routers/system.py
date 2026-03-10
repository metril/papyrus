from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.schemas import HealthResponse
from app.services.ws_manager import ws_manager

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse()


@router.websocket("/ws/jobs")
async def jobs_ws(websocket: WebSocket):
    """WebSocket for real-time print job status updates."""
    await ws_manager.connect("jobs", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect("jobs", websocket)
