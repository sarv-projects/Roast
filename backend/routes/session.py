from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.storage.session_store import create_session, get_session as redis_get_session
from backend.market_data import normalize_company_type

router = APIRouter()


class SessionInitRequest(BaseModel):
    role: str
    market: str
    company_type: str
    experience_level: str = "Junior"


class SessionInitResponse(BaseModel):
    session_id: str
    message: str


@router.post("/session-init", response_model=SessionInitResponse)
def session_init(body: SessionInitRequest):
    canonical_ct = normalize_company_type(body.company_type)
    session = create_session(body.role, body.market, canonical_ct, body.experience_level)
    return SessionInitResponse(
        session_id=session["session_id"],
        message="Session created. You may now upload your resume.",
    )


@router.get("/session/{session_id}")
def get_session_route(session_id: str):
    session = redis_get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session
