"""
Real-time technology lookup for agents.
When an agent encounters a tool/technology it needs to evaluate,
it can search for it and get a quick summary.
No API key needed — uses DuckDuckGo directly.

Results are cached in Redis (30-day TTL) — same term across different
resumes/sessions returns instantly without hitting DDG again.
"""

import re
import asyncio
import structlog
from ddgs import DDGS
from backend.storage.redis_client import redis

logger = structlog.get_logger()

# Cache TTLs
_HIT_TTL  = 30 * 24 * 3600   # 30 days — real result
_MISS_TTL =  7 * 24 * 3600   # 7 days  — empty/failed result (don't retry too soon)


def _cache_key(tech_name: str) -> str:
    """
    Normalize tech name → Redis key.
    "SQLite-Vec", "sqlite-vec", "sqlite vec" → "tech_lookup:sqlite vec"
    Strips punctuation except spaces, lowercases, collapses whitespace.
    """
    normalized = tech_name.lower()
    normalized = re.sub(r"[^\w\s]", " ", normalized)   # replace punctuation with space
    normalized = re.sub(r"\s+", " ", normalized).strip()  # collapse whitespace
    return f"tech_lookup:{normalized}"


async def lookup_technology(tech_name: str, context: str = "") -> str:
    """
    Look up a technology/tool/framework and return a brief summary.
    Used by TechnicalDepthAgent when it encounters unfamiliar tools.

    Checks Redis cache first — same term is never searched twice.

    Args:
        tech_name: e.g. "Bayesian NBV", "d-vector speaker verification", "SIP call transfer"
        context: e.g. "robotics", "voice AI" — helps narrow the search

    Returns:
        Brief description of what the technology is and what it's used for.
        Empty string if lookup fails.
    """
    key = _cache_key(tech_name)

    # ── Cache check ───────────────────────────────────────────────────────────
    cached = redis.get(key)
    if cached is not None:
        # Empty string is also a valid cached value (means "searched, found nothing")
        logger.info("tech_lookup_cache_hit", tech=tech_name, key=key)
        return cached

    # ── Cache miss — search DDG ───────────────────────────────────────────────
    query = f"{tech_name} {context} technical explanation what is it used for".strip()

    try:
        results = await asyncio.to_thread(_ddg_search, query)

        if not results:
            # Cache the miss so we don't retry for 7 days
            redis.setex(key, _MISS_TTL, "")
            logger.info("tech_lookup_no_results", tech=tech_name)
            return ""

        # Take first 2 results, combine snippets
        snippets = [r.get("body", "") for r in results[:2] if r.get("body")]
        combined = " ".join(snippets)[:500]

        # Cache the result for 30 days
        redis.setex(key, _HIT_TTL, combined)
        logger.info("tech_lookup_cached", tech=tech_name, chars=len(combined))

        return combined

    except Exception as e:
        # Don't cache exceptions — transient DDG failures should retry next time
        logger.warning("tech_lookup_failed", tech=tech_name, error=str(e))
        return ""


def _ddg_search(query: str) -> list[dict]:
    """Synchronous DuckDuckGo search — run via asyncio.to_thread."""
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=3))
