import re
import uuid
import httpx
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.storage.redis_client import redis
from backend.llm.langfuse_client import trace_feedback
from backend.market_data import normalize_company_type
import structlog

router = APIRouter()
logger = structlog.get_logger()


class TokenRequest(BaseModel):
    email: str


class TokenVerifyRequest(BaseModel):
    token: str
    session_id: str


@router.post("/token")
async def request_token(body: TokenRequest):
    """
    User enters email after 2nd analysis.
    Sends a one-time token via Resend.
    One token per email per day.
    """
    email = body.email.strip().lower()

    # Basic email validation — reject before consuming Resend quota
    if not EMAIL_REGEX.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address.")

    # One token per email per day
    email_key = f"token:email:{email}"
    if redis.exists(email_key):
        raise HTTPException(
            status_code=429,
            detail="A token was already sent to this email today. Check your inbox."
        )

    # Generate token
    token = str(uuid.uuid4())
    token_key = f"token:{token}"

    # Store token → email mapping
    redis.setex(token_key, TOKEN_TTL, email)
    # Store email → token exists flag (prevents duplicate sends)
    redis.setex(email_key, TOKEN_TTL, "1")

    # Send email via Resend
    if not RESEND_API_KEY:
        # Dev mode — no email provider configured, return token directly
        logger.info("dev_token", token=token, email=email)
        return {"message": "Dev mode: no email sent.", "dev_token": token}

    sent = await _send_token_email(email, token)
    if not sent:
        # Clean up Redis if email failed
        redis.delete(token_key)
        redis.delete(email_key)
        raise HTTPException(status_code=500, detail="Failed to send email. Please try again.")

    logger.info("token_sent", email_hash=hash(email))
    return {"message": "Token sent. Check your email."}


@router.post("/token/verify")
async def verify_token(body: TokenVerifyRequest):
    """
    User enters the token from their email.
    Unlocks a third analysis for this session.
    Token deleted immediately on first use.
    """
    token_key = f"token:{body.token}"
    email = redis.get(token_key)

    if not email:
        raise HTTPException(status_code=400, detail="Invalid or expired token.")

    # Delete immediately — one-time use
    redis.delete(token_key)

    # Grant extra analysis — increment rate limit allowance for this session
    # We do this by storing a token-unlock flag in Redis
    redis.setex(f"token_unlocked:{body.session_id}", TOKEN_TTL, email)

    logger.info("token_verified", session_id=body.session_id)
    return {"message": "Token verified. You have one more analysis available."}


async def _send_token_email(email: str, token: str) -> bool:
    """Send one-time token via Resend. Returns True on success."""
    if not RESEND_API_KEY:
        # Development — return token directly in response
        logger.info("dev_token", token=token, email=email)
        return True

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": "ROAST <onboarding@resend.dev>",
                    "to": [email],
                    "subject": "Your ROAST token",
                    "html": f"""
<div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; padding: 32px; background: #0b0f19; color: #f1f5f9; border-radius: 12px;">
  <h2 style="margin: 0 0 16px; color: #f97316;">🔥 Your ROAST token</h2>
  <p style="margin: 0 0 24px; color: #a1a1aa;">Use this token to unlock one more free analysis:</p>
  <div style="background: #161b27; border: 1px solid #1f2937; border-radius: 8px; padding: 20px; text-align: center; margin-bottom: 24px;">
    <code style="font-size: 18px; font-weight: bold; letter-spacing: 3px; color: #f97316;">{token}</code>
  </div>
  <p style="color: #71717a; font-size: 12px; margin: 0;">Valid for 24 hours · One use only · No spam, ever.</p>
</div>
""",
                },
            )
            return response.status_code == 200
    except Exception as e:
        logger.error("resend_failed", error=str(e))
        return False


# ── Feedback ──────────────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    session_id: str
    useful: bool
    role: str
    market: str
    company_type: str


@router.post("/feedback")
async def feedback(body: FeedbackRequest):
    """
    Single useful/not useful vote per session.
    No resume content, no PII.
    """
    # Increment appropriate counter
    if body.useful:
        redis.incr("counter:feedback_useful")
    else:
        redis.incr("counter:feedback_not_useful")

    # Track per-combination feedback for quality monitoring
    canonical_ct = normalize_company_type(body.company_type)
    combo_key = f"feedback:{body.role}:{canonical_ct}:{body.market}"
    if body.useful:
        redis.incr(f"{combo_key}:useful")
    else:
        redis.incr(f"{combo_key}:not_useful")

    # Send to Langfuse — links feedback to the session trace
    try:
        from backend.llm.langfuse_client import trace_feedback
        trace_feedback(session_id=body.session_id, useful=body.useful)
    except Exception:
        pass

    logger.info(
        "feedback_received",
        useful=body.useful,
        role=body.role,
        market=body.market,
    )

    return {"message": "Thanks for the feedback."}
