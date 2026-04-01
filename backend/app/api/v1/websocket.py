# WebSocket endpoint for real-time progress updates
import asyncio
import json
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["websocket"])

# Store active connections
active_connections: Set[WebSocket] = set()


class ConnectionManager:
    """Manager for WebSocket connections."""

    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict) -> None:
        """Broadcast a message to all connected clients."""
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

    async def send_personal(self, websocket: WebSocket, message: dict) -> None:
        """Send a message to a specific client."""
        try:
            await websocket.send_json(message)
        except Exception:
            pass


manager = ConnectionManager()


@router.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time progress updates.

    The client can subscribe to specific task updates by sending:
    {"action": "subscribe", "task_id": "xxx"}

    Progress updates are sent as:
    {
        "type": "progress",
        "task_id": "xxx",
        "stage": "planning",
        "message": "正在规划综述框架...",
        "progress": 0.1
    }
    """
    await manager.connect(websocket)
    subscribed_tasks: Set[str] = set()

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                action = message.get("action")

                if action == "subscribe":
                    task_id = message.get("task_id")
                    if task_id:
                        subscribed_tasks.add(task_id)
                        await manager.send_personal(websocket, {
                            "type": "subscribed",
                            "task_id": task_id,
                        })

                elif action == "unsubscribe":
                    task_id = message.get("task_id")
                    if task_id:
                        subscribed_tasks.discard(task_id)
                        await manager.send_personal(websocket, {
                            "type": "unsubscribed",
                            "task_id": task_id,
                        })

                elif action == "ping":
                    await manager.send_personal(websocket, {"type": "pong"})

            except json.JSONDecodeError:
                await manager.send_personal(websocket, {
                    "type": "error",
                    "message": "Invalid JSON format",
                })

    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def broadcast_progress(task_id: str, stage: str, message: str, progress: float) -> None:
    """Broadcast progress update to all subscribed clients.

    Args:
        task_id: The task ID.
        stage: Current stage name.
        message: Progress message.
        progress: Progress value (0.0 to 1.0).
    """
    await manager.broadcast({
        "type": "progress",
        "task_id": task_id,
        "stage": stage,
        "message": message,
        "progress": progress,
    })
