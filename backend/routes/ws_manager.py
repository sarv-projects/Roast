"""
WebSocket connection manager.
Handles active connections and broadcasts progress events to clients.
"""

import json
import asyncio
import time
from fastapi import WebSocket
import structlog

logger = structlog.get_logger()

# Active WebSocket connections keyed by session_id
_connections: dict[str, WebSocket] = {}
# Track last activity per connection for stale cleanup
_last_activity: dict[str, float] = {}
STALE_THRESHOLD_SECONDS = 300  # 5 minutes


async def connect(session_id: str, websocket: WebSocket) -> None:
    await websocket.accept()
    _connections[session_id] = websocket
    _last_activity[session_id] = time.time()
    logger.info("ws_connected", session_id=session_id)


def disconnect(session_id: str) -> None:
    _connections.pop(session_id, None)
    _last_activity.pop(session_id, None)
    logger.info("ws_disconnected", session_id=session_id)


async def emit(session_id: str, event: str, data: dict) -> None:
    """
    Send a progress event to the client.
    Silently ignores if client is not connected (they'll recover via polling).
    """
    ws = _connections.get(session_id)
    if ws is None:
        return

    try:
        await ws.send_text(json.dumps({"event": event, "data": data}))
        _last_activity[session_id] = time.time()
    except Exception:
        disconnect(session_id)


async def heartbeat_loop(session_id: str, interval: int = 10) -> None:
    """
    Send a ping every `interval` seconds while the session is active.
    Prevents client-side WebSocket timeout on slow connections.
    Cleans up stale connections that haven't responded.
    """
    while session_id in _connections:
        await asyncio.sleep(interval)
        ws = _connections.get(session_id)
        if ws is None:
            break
        try:
            await ws.send_text(json.dumps({"event": "ping"}))
            _last_activity[session_id] = time.time()
        except Exception:
            disconnect(session_id)
            break


def cleanup_stale_connections() -> list[str]:
    """Remove connections that haven't had activity in STALE_THRESHOLD_SECONDS."""
    now = time.time()
    stale = []
    for sid, last_active in list(_last_activity.items()):
        if now - last_active > STALE_THRESHOLD_SECONDS:
            stale.append(sid)
    for sid in stale:
        disconnect(sid)
    if stale:
        logger.info("ws_stale_connections_cleaned", count=len(stale), sessions=stale)
    return stale
