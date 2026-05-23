import os
import tempfile
import time
import atexit
import asyncio

# Track temp files for crash cleanup
_temp_files: set[str] = set()


def _cleanup_temp_files():
    for path in list(_temp_files):
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass
        _temp_files.discard(path)


atexit.register(_cleanup_temp_files)

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile, Form
from backend.storage.rate_limit import check_and_increment_rate_limit
from backend.pdf_reader import extract_links, extract_text_from_pdf
from backend.storage.session_store import get_session, update_session
from backend.storage.redis_client import redis
from backend.pipeline.orchestrator import run_pipeline, PipelineRequest
from backend.routes.ws_manager import emit
from backend.market_data import normalize_company_type
import structlog

router = APIRouter()
logger = structlog.get_logger()

BOT_TIMING_GATE_SECONDS = 1.0


async def _run_pipeline_and_stream(
    session_id: str,
    resume_text: str,
    role: str,
    company_type: str,
    market: str,
    experience_level: str,
    user_context: str,
    jd_text: str,
    profile_links: dict,
    github_url: str,
    opted_in_corpus: bool = False,
) -> None:
    """
    Runs the full pipeline as a background task.
    WebSocket events are emitted from inside the orchestrator as each agent completes.
    """
    try:
        request = PipelineRequest(
            session_id=session_id,
            resume_text=resume_text,
            role=role,
            company_type=company_type,
            market=market,
            experience_level=experience_level,
            user_context=user_context,
            jd_text=jd_text,
            profile_links=profile_links,
            github_url=github_url,
            opted_in_corpus=opted_in_corpus,
        )

        await run_pipeline(request)

        # pipeline complete event
        await emit(session_id, "complete", {})

    except Exception as e:
        logger.error("pipeline_background_failed", error=str(e), session_id=session_id)
        update_session(session_id, {"status": "failed", "error": str(e)})
        await emit(session_id, "error", {"message": "Analysis failed. Please try again."})


@router.post("/analyse")
async def analyse(
    request: Request,
    background_tasks: BackgroundTasks,
    session_id: str = Form(...),
    role: str = Form(...),
    company_type: str = Form(...),
    market: str = Form(...),
    experience_level: str = Form(...),
    user_context: str = Form(default=""),
    jd_text: str = Form(default=""),
    github_url: str = Form(default=""),
    opted_in_corpus: bool = Form(default=False),
    file: UploadFile = File(...),
):
    # Normalize company type from user-facing display name to canonical DB key
    canonical_ct = normalize_company_type(company_type)

    # ── 1. Validate session ────────────────────────────────
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found. Call /session-init first.")

    # ── 2. Idempotency check ───────────────────────────────
    if session["status"] in ("processing", "completed"):
        return {
            "session_id": session_id,
            "status": session["status"],
            "message": "Analysis already in progress or complete.",
        }

    # ── 3. Timing gate ─────────────────────────────────────
    elapsed = time.time() - session["created_at"]
    if elapsed < BOT_TIMING_GATE_SECONDS:
        raise HTTPException(status_code=429, detail="Request too fast.")

    # ── 4. Rate limit ──────────────────────────────────────
    client = request.client
    xff = request.headers.get("x-forwarded-for")
    if xff:
        client_ip = xff.split(",")[0].strip()
    elif client and hasattr(client, "host"):
        client_ip = client.host
    elif client:
        client_ip = client[0]
    else:
        # No IP available — use a fallback key so rate limit still applies
        client_ip = "unknown"

    from backend.config import ENVIRONMENT
    rate = check_and_increment_rate_limit(client_ip)
    if not rate["allowed"] and ENVIRONMENT == "production":
        # Check if this session has a token unlock
        token_unlocked = redis.get(f"token_unlocked:{session_id}")
        if not token_unlocked:
            raise HTTPException(
                status_code=429,
                detail=f"Daily limit reached ({rate['limit']} analyses/day). Resets at midnight IST."
            )
        # Token unlock — delete it (one use only) and allow
        redis.delete(f"token_unlocked:{session_id}")

    # ── 5. Validate PDF ────────────────────────────────────
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail=f"Only PDF files accepted. Got: {file.content_type}")

    contents = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    _temp_files.add(tmp_path)

    try:
        pdf_result = extract_text_from_pdf(tmp_path)
        links = extract_links(tmp_path)
    finally:
        os.unlink(tmp_path)
        _temp_files.discard(tmp_path)

    if pdf_result["error"]:
        raise HTTPException(status_code=422, detail=f"PDF read error: {pdf_result['error']}")

    if not pdf_result["is_valid"]:
        raise HTTPException(status_code=422, detail=pdf_result["validation_error"])

    # ── 6. Update session + launch pipeline ───────────────
    update_session(session_id, {
        "status": "processing",
        "resume_text": pdf_result["full_text"],
        "resume_links": links,
        "page_count": pdf_result["page_count"],
        "role": role,
        "company_type": canonical_ct,
        "market": market,
        "experience_level": experience_level,
    })

    profile_links = {}
    if links.get("linkedin"):
        profile_links["linkedin"] = links["linkedin"]
    if links.get("github"):
        profile_links["github"] = links["github"]

    # Launch pipeline as background task — returns immediately
    background_tasks.add_task(
        _run_pipeline_and_stream,
        session_id=session_id,
        resume_text=pdf_result["full_text"],
        role=role,
        company_type=canonical_ct,
        market=market,
        experience_level=experience_level,
        user_context=user_context,
        jd_text=jd_text,
        profile_links=profile_links,
        github_url=github_url,
        opted_in_corpus=opted_in_corpus,
    )

    return {
        "session_id": session_id,
        "status": "processing",
        "message": "Analysis started. Connect to /ws/{session_id} for real-time updates.",
        "pages": pdf_result["page_count"],
        "chars": len(pdf_result["full_text"]),
    }
