import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from backend.routes.ws_manager import connect, disconnect, heartbeat_loop
from backend.storage.redis_client import redis
from backend.storage.session_store import get_session, SESSION_TTL

router = APIRouter()

SHARE_TTL = 7 * 24 * 3600  # 7 days


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for real-time progress streaming.
    Client connects after POST /analyse returns session_id.
    Server streams events as each pipeline step completes.
    """
    await connect(session_id, websocket)

    # Start heartbeat in background
    heartbeat_task = asyncio.create_task(heartbeat_loop(session_id))

    try:
        # Send any already-completed sections immediately on connect
        # (handles reconnection case)
        completed = _get_completed_sections(session_id)
        for section, data in completed.items():
            await websocket.send_text(json.dumps({
                "event": "section_complete",
                "data": {"section": section, "result": data}
            }))

        # Keep connection alive — pipeline emits events via ws_manager.emit()
        while True:
            # Wait for client messages (pong responses, etc.)
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if msg == "pong":
                    continue
            except asyncio.TimeoutError:
                # No message in 30s — check if session is still active
                session = get_session(session_id)
                if session and session.get("status") in ("completed", "failed"):
                    break
                continue

    except WebSocketDisconnect:
        pass
    finally:
        heartbeat_task.cancel()
        disconnect(session_id)


@router.get("/session/{session_id}/state")
async def session_state(session_id: str):
    """
    Session recovery endpoint for WebSocket reconnection.
    Client polls this every 5 seconds when WebSocket is disconnected.
    Returns completed sections, pending sections, and cached results.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    status = session.get("status", "pending")
    completed = _get_completed_sections(session_id)

    all_sections = ["market_context", "red_flags", "six_second", "competitive", "technical_depth", "review"]
    pending = [s for s in all_sections if s not in completed]

    return {
        "status": status,
        "completed": list(completed.keys()),
        "pending": pending,
        "results": completed,
    }


@router.get("/share/{session_id}")
async def share_preview(session_id: str):
    """
    Public share preview — shows TL;DR block only.
    No resume text, no red flags. Safe to share publicly.
    """
    # Check Redis share cache first
    share_key = f"share:{session_id}:tldr"
    cached = redis.get(share_key)
    if cached:
        return json.loads(cached)

    # Build from session review data
    review_raw = redis.get(f"session:{session_id}:review")
    if not review_raw:
        raise HTTPException(status_code=404, detail="Share preview not found or expired.")

    review = json.loads(review_raw)
    session = get_session(session_id)

    tldr = {
        "shortlist_chance": review.get("tldr_shortlist_chance", ""),
        "biggest_blocker": review.get("tldr_biggest_blocker", ""),
        "fix_first": review.get("tldr_fix_first", ""),
        "role": session.get("role", "") if session else "",
        "market": session.get("market", "") if session else "",
    }

    # Cache for 7 days
    redis.setex(share_key, SHARE_TTL, json.dumps(tldr))

    # Track share view
    redis.incr("counter:share_previews_viewed")

    return tldr


def _get_completed_sections(session_id: str) -> dict:
    """Fetch all completed sections from Redis for this session."""
    sections = ["market_context", "red_flags", "six_second", "competitive", "technical_depth", "review"]
    completed = {}
    for section in sections:
        raw = redis.get(f"session:{session_id}:{section}")
        if raw:
            try:
                completed[section] = json.loads(raw)
            except Exception:
                pass
    return completed
