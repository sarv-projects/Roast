"""
Breaking signal layer — multi-source, multi-layer.
Layer 1 (Tavily): real-time news search for major events (layoffs, funding, expansions).
Layer 2 (DDG fallback): free text search when Tavily budget is exhausted.
Layer 3 (Groq synthesis): merged summary with company names and numbers.

Keyed per market + role_category + company_type.
Refreshes on first request then cached 24 hours.
"""

import asyncio
import structlog
from backend.storage.redis_client import redis
from ingestion.tavily_client import general

logger = structlog.get_logger()

BREAKING_TTL = 24 * 3600  # 24 hours
TAVILY_BUDGET_THRESHOLD = 100  # switch to DDG when Tavily budget below this


def _breaking_key(market: str, role_category: str, company_type: str) -> str:
    return f"breaking:{market.lower()}:{role_category}:{company_type.lower().replace(' ', '_').replace('/', '_')}"


async def get_breaking_signal(
    role: str,
    company_type: str,
    market: str,
    session_id: str = "",
) -> tuple[str, bool]:
    """
    Get breaking signal for this combination.
    Returns (signal_text, is_available).

    Checks Redis cache first (24h TTL).
    On cache miss: fetches from Tavily + synthesises with Gemini Flash Lite.
    If fetch fails: returns empty string, is_available=False.
    Analysis never fails because of a missing breaking signal.
    """
    from backend.market_data import get_role_category
    role_category = get_role_category(role)
    key = _breaking_key(market, role_category, company_type)

    # Check cache
    cached = redis.get(key)
    if cached:
        return cached, True

    # Cache miss — fetch live
    signal = await _fetch_breaking_signal(role, company_type, market, session_id)

    if signal:
        redis.setex(key, BREAKING_TTL, signal)
        return signal, True

    return "", False


async def _fetch_breaking_signal(
    role: str,
    company_type: str,
    market: str,
    session_id: str = "",
) -> str:
    """
    Fetch and synthesise breaking signal.
    Layer 1: Tavily search (6 queries) for major events.
    Layer 2: Falls back to DuckDuckGo text search if Tavily budget is low.
    Layer 3: Groq synthesis into structured summary.
    """
    queries = [
        f"{market} tech hiring news layoffs {role} last 7 days",
        f"{company_type} {market} hiring freeze OR expansion {role} this week",
        f"{market} tech job market changes salaries {role} 2026",
        f"{company_type} layoffs OR hiring spree {market} 2026",
        f"{role} demand supply skills gap {market} current year",
        f"{market} engineering hiring trends budget {role} this quarter",
    ]

    results = []

    # Layer 1: Tavily
    tavily_budget_ok = _check_tavily_budget()
    if tavily_budget_ok:
        for query in queries[:4]:
            try:
                items = await general.search(query, max_results=2)
                for item in items:
                    content = item.get("content", "").strip()
                    if content and len(content) > 50:
                        results.append(content[:500])
            except Exception:
                continue
    else:
        # Layer 2: DDG fallback (free, no rate limit)
        logger.info("breaking_signal_tavily_budget_low", switching_to="ddg", session_id=session_id)
        results = await _ddg_fallback_search(role, company_type, market)

    if not results:
        logger.warning("breaking_signal_no_results", role=role, market=market, session_id=session_id)
        return ""

    # Layer 3: Groq synthesis
    combined = "\n\n---\n\n".join(results[:6])

    try:
        from backend.llm.router import call_groq_8b
        text, _ = await call_groq_8b(
            messages=[
                {"role": "system", "content": (
                    "You are a hiring market analyst. Summarise the key hiring news "
                    "in 4-6 specific sentences. Name companies, salary bands, and numbers "
                    "where present. Mention specific role requirements if they appear. "
                    "If the data is thin, say 'Limited market signals this week.'"
                )},
                {"role": "user", "content": f"Summarise hiring news for {role} roles at {company_type} companies in {market}:\n\n{combined}"},
            ],
            max_tokens=250,
            temperature=0.1,
            session_id=session_id,
        )
        logger.info("breaking_signal_fetched", role=role, market=market, session_id=session_id)
        return text.strip()

    except Exception as e:
        logger.warning("breaking_signal_synthesis_failed", error=str(e), session_id=session_id)
        return ""


def _check_tavily_budget() -> bool:
    """Check if Tavily General budget has enough remaining searches."""
    try:
        remaining = redis.get("tavily:general:remaining")
        if remaining is not None:
            return int(remaining) > TAVILY_BUDGET_THRESHOLD
    except Exception:
        pass
    return True  # default: assume budget is available


async def _ddg_fallback_search(role: str, company_type: str, market: str) -> list[str]:
    """
    Free fallback using DuckDuckGo text search (no API key needed).
    Returns list of text snippets.
    """
    import httpx
    query = f"{role} hiring {company_type} {market}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
                headers={"User-Agent": "roast-market-intel/1.0"},
            )
            # Extract text snippets from HTML
            text = resp.text
            # Guard against CAPTCHA, rate limit, or error pages
            if len(text) < 200 or any(kw in text.lower() for kw in ["captcha", "rate limit", "429", "blocked", "access denied"]):
                logger.warning("breaking_signal_ddg_blocked", text_len=len(text))
                return []
            snippets = []
            for line in text.split("\n"):
                line = line.strip()
                if len(line) > 80 and "http" not in line:
                    snippets.append(line[:400])
            if not snippets:
                logger.warning("breaking_signal_ddg_no_snippets", text_len=len(text))
            return snippets[:5]
    except Exception as e:
        logger.warning("breaking_signal_ddg_failed", error=str(e))
        return []
