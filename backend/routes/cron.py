"""
QStash cron endpoint.
Triggered by Upstash QStash on the 1st of every month at 03:00 IST.
Refreshes market intelligence for all active combinations.

To wire up on Digital Ocean:
  1. Set QSTASH_TOKEN + QSTASH_SIGNING_KEY in DO env vars
  2. In Upstash QStash dashboard, create a schedule:
       URL: https://your-app.ondigitalocean.app/refresh-market-intel
       Cron: 0 21 1 * *   (03:00 IST = 21:30 UTC on 1st of month)
       Method: POST
"""

import hmac
import hashlib
import asyncio
import structlog
from fastapi import APIRouter, Request, HTTPException
from backend.config import QSTASH_SIGNING_KEY
from backend.storage.redis_client import redis
from ingestion.pipeline import run_ingestion_for_combo

router = APIRouter()
logger = structlog.get_logger()

# ── Tier 1 combos — always refreshed regardless of usage ─────────────────────
# These match the exact role/company_type strings used in the frontend dropdowns
# and the prepopulate script. Keep in sync with scripts/prepopulate.py.

TIER_1_COMBINATIONS = [
    # SDE / Software Engineer
    ("Software Engineer / Associate", "Indian Product Company", "India"),
    ("Software Engineer / Associate", "Indian Service Company", "India"),
    ("Software Engineer / Associate", "MNC India (Non-FAANG)", "India"),
    ("Software Engineer / Associate", "Startup", "India"),
    ("SDE1", "Indian Product Company", "India"),
    ("SDE1", "Indian Service Company", "India"),
    ("SDE1", "Startup", "India"),
    ("SDE1", "FAANG / Big Tech", "India"),
    ("SDE1", "MNC India (Non-FAANG)", "India"),
    ("SDE2 / Senior SDE", "Indian Product Company", "India"),
    ("SDE2 / Senior SDE", "Indian Service Company", "India"),
    ("SDE2 / Senior SDE", "FAANG / Big Tech", "India"),
    ("SDE2 / Senior SDE", "Startup", "India"),
    ("Full Stack Engineer", "Indian Product Company", "India"),
    ("Full Stack Engineer", "Startup", "India"),
    ("Backend Engineer", "Indian Product Company", "India"),
    ("Backend Engineer", "Startup", "India"),

    # AI Agentic Engineer
    ("AI Agentic Engineer", "Indian Product Company", "India"),
    ("AI Agentic Engineer", "Startup", "India"),
    ("AI Agentic Engineer", "MNC India (Non-FAANG)", "India"),
    ("AI Agentic Engineer", "FAANG / Big Tech", "India"),
    ("AI Agentic Engineer", "FAANG / Big Tech", "USA"),
    ("AI Agentic Engineer", "Startup", "USA"),
    ("AI Agentic Engineer", "Startup", "UAE"),
    ("AI Agentic Engineer", "Startup", "UK"),

    # Data
    ("Data Analyst", "Indian Product Company", "India"),
    ("Data Analyst", "Indian Service Company", "India"),
    ("Data Analyst", "MNC India (Non-FAANG)", "India"),
    ("Data Analyst", "Startup", "India"),
    ("Data Analyst", "Consulting / IB", "India"),
    ("Data Analyst", "FAANG / Big Tech", "India"),
    ("Data Scientist", "Indian Product Company", "India"),
    ("Data Scientist", "Startup", "India"),
    ("Data Scientist", "FAANG / Big Tech", "India"),
    ("Data Engineer", "Indian Product Company", "India"),
    ("Data Engineer", "Startup", "India"),
    ("Data Engineer", "FAANG / Big Tech", "India"),

    # Hardware / VLSI / Embedded
    ("VLSI Design Engineer", "Semiconductor / Hardware", "India"),
    ("VLSI Design Engineer", "Indian Product Company", "India"),
    ("VLSI Design Engineer", "MNC India (Non-FAANG)", "India"),
    ("Embedded Systems Engineer", "Semiconductor / Hardware", "India"),
    ("Embedded Systems Engineer", "Indian Product Company", "India"),
    ("Embedded Systems Engineer", "MNC India (Non-FAANG)", "India"),

    # Other roles
    ("Product Manager", "Indian Product Company", "India"),
    ("Product Manager", "Startup", "India"),
    ("Product Manager", "FAANG / Big Tech", "India"),
    ("DevOps / SRE", "Indian Product Company", "India"),
    ("DevOps / SRE", "Startup", "India"),
    ("DevOps / SRE", "FAANG / Big Tech", "India"),
    ("Platform Engineer", "Indian Product Company", "India"),
    ("Platform Engineer", "FAANG / Big Tech", "India"),
    ("Platform Engineer", "MNC India (Non-FAANG)", "India"),
    ("Business Analyst", "Indian Product Company", "India"),
    ("Business Analyst", "Indian Service Company", "India"),
    ("Business Analyst", "MNC India (Non-FAANG)", "India"),
    ("Business Analyst", "Consulting / IB", "India"),

    # International market combos (key ones from prepopulate)
    ("Software Engineer / Associate", "FAANG / Big Tech", "USA"),
    ("SDE2 / Senior SDE", "FAANG / Big Tech", "USA"),
    ("AI Agentic Engineer", "FAANG / Big Tech", "USA"),
    ("Data Scientist", "FAANG / Big Tech", "USA"),
    ("Software Engineer / Associate", "FAANG / Big Tech", "Singapore"),
    ("Software Engineer / Associate", "FAANG / Big Tech", "UK"),
]


# ── Signature verification ────────────────────────────────────────────────────

def _verify_qstash_signature(body: bytes, signature: str) -> bool:
    """
    Verify the QStash HMAC-SHA256 signature.
    Unsigned or tampered requests are rejected with 401.
    In development (no QSTASH_SIGNING_KEY set), verification is skipped.
    """
    if not QSTASH_SIGNING_KEY:
        return True  # dev mode — skip

    expected = hmac.new(
        QSTASH_SIGNING_KEY.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


# ── Active combo discovery ────────────────────────────────────────────────────

def _get_active_combinations() -> list[tuple[str, str, str]]:
    """
    Returns Tier 1 combos + any combo that has had at least 1 real analysis
    in Redis (combo_count:{role}:{company_type}:{market}).
    This means popular user-driven combos get refreshed automatically
    even if they weren't in the Tier 1 list.
    """
    active = set(TIER_1_COMBINATIONS)

    try:
        cursor = 0
        while True:
            cursor, keys = redis.scan(cursor, match="combo_count:*", count=100)
            for key in keys:
                count = redis.get(key)
                if count and int(count) >= 3:  # at least 3 analyses before auto-refresh
                    # key format: combo_count:{role}:{company_type}:{market}
                    raw = key.replace("combo_count:", "", 1)
                    # role, company_type, and market are separated by : but may contain spaces.
                    # Split into exactly 3 parts using rsplit to preserve names with colons.
                    parts = raw.rsplit(":", 2)
                    if len(parts) == 3:
                        active.add((parts[0], parts[1], parts[2]))
            if cursor == 0:
                break
    except Exception as e:
        logger.warning("combo_scan_failed", error=str(e))

    return list(active)


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/refresh-market-intel")
async def refresh_market_intel(request: Request):
    """
    Monthly cron trigger — called by QStash or DO cron.
    Refreshes market intelligence for all Tier 1 + active combos.
    """
    body = await request.body()

    # Verify QStash signature
    signature = request.headers.get("upstash-signature", "")
    if not _verify_qstash_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature.")

    # Check Tavily budget before starting — abort if too low
    try:
        from ingestion.tavily_client import deep as tavily_deep, general as tavily_general
        deep_remaining = tavily_deep.budget_remaining()
        general_remaining = tavily_general.budget_remaining()

        if deep_remaining < 100 or general_remaining < 100:
            logger.warning(
                "cron_skipped_budget_low",
                deep_remaining=deep_remaining,
                general_remaining=general_remaining,
            )
            _notify_discord(
                f"⚠️ Monthly cron skipped — Tavily budget too low.\n"
                f"Deep: {deep_remaining} remaining, General: {general_remaining} remaining.\n"
                f"Top up Tavily credits before next run."
            )
            return {"status": "skipped", "reason": "tavily_budget_low",
                    "deep_remaining": deep_remaining, "general_remaining": general_remaining}
    except Exception as e:
        logger.warning("budget_check_failed", error=str(e))
        # Don't abort if budget check itself fails — proceed with refresh

    # Set running flag so frontend can show a banner if needed
    redis.setex("cron:running", 7200, "1")  # 2h TTL

    combinations = _get_active_combinations()
    logger.info("cron_started", total_combos=len(combinations))

    results = []
    errors = []

    for role, company_type, market in combinations:
        try:
            summary = await run_ingestion_for_combo(
                role=role,
                company_type=company_type,
                market=market,
                force_refresh=True,
            )
            results.append({
                "combo": f"{role} / {company_type} / {market}",
                "stored": summary.signals_stored,
                "discarded": summary.signals_discarded,
                "duration_s": summary.duration_seconds,
            })
            logger.info("combo_refreshed", role=role, company_type=company_type,
                        market=market, stored=summary.signals_stored)
            await asyncio.sleep(3)  # avoid Tavily rate spike

        except Exception as e:
            errors.append({"combo": f"{role} / {company_type} / {market}", "error": str(e)})
            logger.error("combo_refresh_failed", role=role, company_type=company_type,
                         market=market, error=str(e))

    redis.delete("cron:running")

    # Also invalidate all DIVE Redis snapshots so next request gets fresh data
    try:
        cursor = 0
        invalidated = 0
        while True:
            cursor, keys = redis.scan(cursor, match="snapshot:*", count=100)
            for key in keys:
                redis.delete(key)
                invalidated += 1
            if cursor == 0:
                break
        logger.info("dive_snapshots_invalidated", count=invalidated)
    except Exception as e:
        logger.warning("snapshot_invalidation_failed", error=str(e))

    total_stored = sum(r["stored"] for r in results)
    msg = (
        f"✅ Monthly cron complete\n"
        f"{len(results)} combos refreshed · {total_stored} signals stored · {len(errors)} errors"
    )
    if errors:
        msg += "\n\nFailed combos:\n" + "\n".join(f"• {e['combo']}: {e['error']}" for e in errors[:5])
    _notify_discord(msg)

    logger.info("cron_complete", refreshed=len(results), errors=len(errors),
                total_stored=total_stored)

    return {
        "status": "complete",
        "refreshed": len(results),
        "errors": len(errors),
        "total_signals_stored": total_stored,
        "results": results,
        "error_details": errors,
    }


def _notify_discord(message: str) -> None:
    """Fire Discord webhook. Silent fail — never blocks the cron response."""
    try:
        from backend.config import DISCORD_WEBHOOK_URL
        if not DISCORD_WEBHOOK_URL:
            return
        import httpx
        httpx.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=5)
    except Exception:
        pass
