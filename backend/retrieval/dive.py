"""
DIVE — Deterministic Intelligence Vector Extraction
Five-stage pipeline that runs at request time to extract relevant
market intelligence from the prebuilt SQLite store.

Input:  role + company_type + market + experience_level
Output: FullMarketContext (distilled context + breaking signal)
"""

import json
import hashlib
import asyncio
import structlog
from pydantic import BaseModel

from ingestion.search import search_signals, count_signals_for_combo
from ingestion.embeddings import search_by_embedding
from backend.llm.router import call_groq_8b
from backend.storage.redis_client import redis

logger = structlog.get_logger()

SNAPSHOT_TTL = 15 * 24 * 3600   # 15 days
SNAPSHOT_PREV_TTL = 60 * 24 * 3600  # 60 days
RRF_K = 60


# ── Output schema ─────────────────────────────────────────────────────────────

class DistilledMarketContext(BaseModel):
    hiring_sentiment: str
    top_required_skills: list[str]
    competitive_pool_signal: str
    salary_band: str
    red_flag_triggers: list[str]
    format_expectations: str
    weight_map: dict
    confidence: str          # HIGH / LOW
    freshness_label: str     # Current / Recent / Needs Refresh


class FullMarketContext(BaseModel):
    distilled: DistilledMarketContext
    breaking_signal: str     # what happened in hiring this week
    breaking_available: bool
    raw_signal_count: int    # how many signals were retrieved from SQLite


# ── Stage 1: Query rewriting ──────────────────────────────────────────────────

def _build_retrieval_queries(
    role: str,
    company_type: str,
    market: str,
    experience_level: str,
) -> list[str]:
    """
    Expand structured input into 6 targeted retrieval queries.
    Each query targets a different downstream agent's needs.
    """
    return [
        f"{role} hiring sentiment {company_type} {market}",
        f"{role} required skills tools {company_type} {market}",
        f"{role} competitive pool applicants {market}",
        f"{role} definition expectations {experience_level} {market}",
        f"{role} red flags resume {company_type} {market}",
        f"{role} salary format norms {market}",
    ]


# ── Stage 2: Parallel BM25 + vector search ────────────────────────────────────

async def _parallel_search(
    role: str,
    company_type: str,
    market: str,
    queries: list[str],
    limit_per_query: int = 20,
) -> tuple[list[dict], list[dict]]:
    """
    Run BM25 and vector search simultaneously.
    Returns (bm25_results, vector_results).
    """
    # BM25 — run all 6 queries, merge results
    def _bm25_search():
        all_results = []
        seen_ids = set()
        for query in queries:
            try:
                results = search_signals(
                    role=role,
                    company_type=company_type,
                    market=market,
                    query=query,
                    limit=limit_per_query,
                )
                for r in results:
                    if r["id"] not in seen_ids:
                        seen_ids.add(r["id"])
                        all_results.append(r)
            except Exception:
                continue
        return all_results

    # Vector search — use combined query for semantic search
    def _vector_search():
        combined_query = " ".join(queries)
        try:
            return search_by_embedding(
                query=combined_query,
                role=role,
                company_type=company_type,
                market=market,
                limit=limit_per_query,
            )
        except Exception:
            return []

    # Run both in parallel using asyncio.to_thread (SQLite is blocking)
    bm25_results, vector_results = await asyncio.gather(
        asyncio.to_thread(_bm25_search),
        asyncio.to_thread(_vector_search),
    )

    return bm25_results, vector_results


# ── Stage 3: RRF fusion ───────────────────────────────────────────────────────

def _rrf_fusion(
    bm25_results: list[dict],
    vector_results: list[dict],
    k: int = RRF_K,
) -> list[dict]:
    """
    Merge BM25 and vector results using Reciprocal Rank Fusion.
    score(d) = 1/(k + rank_bm25) + 1/(k + rank_vector)
    Rows appearing in both lists float to the top.
    """
    scores: dict[int, float] = {}
    rows_by_id: dict[int, dict] = {}

    # Score BM25 results
    for rank, row in enumerate(bm25_results, start=1):
        row_id = row["id"]
        scores[row_id] = scores.get(row_id, 0) + 1 / (k + rank)
        rows_by_id[row_id] = row

    # Score vector results
    for rank, row in enumerate(vector_results, start=1):
        row_id = row["id"]
        scores[row_id] = scores.get(row_id, 0) + 1 / (k + rank)
        rows_by_id[row_id] = row

    # Sort by combined score descending
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [rows_by_id[i] for i in sorted_ids]


# ── Stage 4: Hash deduplication ───────────────────────────────────────────────

# ── Quality filter ───────────────────────────────────────────────────────────


def _is_signal_relevant(signal: dict) -> bool:
    """
    Drop signals with no usable content.
    We do NOT filter by city/country — the RRF ranker + distiller handle relevance.
    """
    content = (signal.get("content", "") or "").strip()
    return len(content) >= 30


def _filter_signals_quality(signals: list[dict]) -> list[dict]:
    """Remove signals with no usable content before distillation."""
    return [s for s in signals if _is_signal_relevant(s)]


def _hash_dedup(results: list[dict], limit: int = 25) -> list[dict]:
    """
    Remove near-duplicate signals using content hash.
    Keeps the highest-ranked representative of each unique signal.
    """
    seen_hashes = set()
    deduped = []

    for row in results:
        content = row.get("content", "")
        # Hash first 200 chars — enough to detect duplicates
        content_hash = hashlib.md5(content[:200].encode()).hexdigest()

        if content_hash not in seen_hashes:
            seen_hashes.add(content_hash)
            deduped.append(row)

        if len(deduped) >= limit:
            break

    return deduped


# ── Stage 5: Context distiller ────────────────────────────────────────────────

DISTILLER_SYSTEM = """You are a market intelligence distiller for a resume review system.

Given a list of hiring signals for a specific role + company type + market,
extract a structured summary. Return ONLY valid JSON:

{
  "hiring_sentiment": "positive/cautious/negative/neutral — one sentence",
  "top_required_skills": ["skill1", "skill2", "skill3"],
  "competitive_pool_signal": "what the typical applicant looks like",
  "salary_band": "e.g. 18-28L base or 'data unavailable'",
  "red_flag_triggers": ["thing1 that gets resumes binned", "thing2"],
  "format_expectations": "resume format norms for this market",
  "weight_map": {
    "dsa": 0.0-1.0,
    "projects": 0.0-1.0,
    "cgpa": 0.0-1.0,
    "experience": 0.0-1.0,
    "open_source": 0.0-1.0,
    "college_tier": 0.0-1.0
  },
  "confidence": "HIGH or LOW"
}

Be specific. Use numbers and company names from the signals where available.
If signals are thin or contradictory, set confidence to LOW."""


async def _distill_context(
    signals: list[dict],
    role: str,
    company_type: str,
    market: str,
    experience_level: str,
    session_id: str = "",
) -> DistilledMarketContext:
    """
    Compress top signals into a DistilledMarketContext using llama-3.1-8b-instant.
    """
    # Format signals for the prompt
    # Pass up to 25 quality-filtered, deduped signals to the distiller
    signals_text = "\n\n".join([
        f"[{i+1}] Source: {s.get('source', 'unknown')} | Type: {s.get('signal_type', 'unknown')}\n{s.get('content', '')}"
        for i, s in enumerate(signals[:25])
    ])

    messages = [
        {"role": "system", "content": DISTILLER_SYSTEM},
        {
            "role": "user",
            "content": f"""Role: {role}
Company type: {company_type}
Market: {market}
Experience level: {experience_level}

SIGNALS:
{signals_text}

Distil into the JSON summary.""",
        },
    ]

    try:
        text, _ = await call_groq_8b(messages, max_tokens=800, session_id=session_id)

        # Extract JSON
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]

        data = json.loads(text)

        # Determine freshness label based on signal age
        freshness = _get_freshness_label(signals)

        # Fetch role-specific weights from DB as fallback, merge with distiller output
        from backend.market_data import get_role_weights, get_role_category
        role_cat = get_role_category(role)
        db_weights = get_role_weights(role_cat) if role_cat else {}
        if not db_weights:
            db_weights = {
                "dsa": 0.7, "projects": 0.7, "cgpa": 0.5,
                "experience": 0.7, "open_source": 0.4, "college_tier": 0.4
            }
        merged_weights = {**db_weights, **(data.get("weight_map") or {})}

        return DistilledMarketContext(
            hiring_sentiment=data.get("hiring_sentiment", "neutral"),
            top_required_skills=data.get("top_required_skills", []),
            competitive_pool_signal=data.get("competitive_pool_signal", ""),
            salary_band=data.get("salary_band", "data unavailable"),
            red_flag_triggers=data.get("red_flag_triggers", []),
            format_expectations=data.get("format_expectations", ""),
            weight_map=merged_weights,
            confidence=data.get("confidence", "LOW"),
            freshness_label=freshness,
        )

    except Exception as e:
        logger.error("distiller_failed", error=str(e), session_id=session_id)
        from backend.market_data import get_role_weights, get_role_category
        role_cat = get_role_category(role)
        db_weights = get_role_weights(role_cat) if role_cat else {}
        if not db_weights:
            db_weights = {
                "dsa": 0.7, "projects": 0.7, "cgpa": 0.5,
                "experience": 0.7, "open_source": 0.4, "college_tier": 0.4
            }
        return DistilledMarketContext(
            hiring_sentiment="neutral",
            top_required_skills=[],
            competitive_pool_signal="",
            salary_band="data unavailable",
            red_flag_triggers=[],
            format_expectations="",
            weight_map=db_weights,
            confidence="LOW",
            freshness_label="Needs Refresh",
        )


def _get_freshness_label(signals: list[dict]) -> str:
    """Determine freshness label based on oldest signal in the set."""
    import time
    if not signals:
        return "Needs Refresh"

    now = int(time.time())
    oldest = min(s.get("fetched_at", now) for s in signals)
    age_days = (now - oldest) / 86400

    if age_days <= 15:
        return "Current"
    elif age_days <= 60:
        return "Recent"
    else:
        return "Needs Refresh"


# ── Breaking signal ───────────────────────────────────────────────────────────

def _breaking_signal_key(role: str, company_type: str, market: str) -> str:
    """Redis key for breaking signal — keyed per market + role_category + company_type."""
    from backend.market_data import get_role_category
    role_category = get_role_category(role)
    return f"breaking:{market.lower()}:{role_category}:{company_type.lower().replace(' ', '_')}"


def _get_breaking_signal(role: str, company_type: str, market: str) -> tuple[str, bool]:
    """
    Fetch breaking signal from Redis cache.
    Returns (signal_text, is_available).
    For live fetch on cache miss, use get_breaking_signal() from breaking_signal.py.
    """
    key = _breaking_signal_key(role, company_type, market)
    cached = redis.get(key)
    if cached:
        return cached, True
    return "", False


async def _get_breaking_signal_with_fetch(
    role: str, company_type: str, market: str, session_id: str = ""
) -> tuple[str, bool]:
    """Fetch breaking signal — checks cache first, fetches live on miss."""
    try:
        from ingestion.breaking_signal import get_breaking_signal
        return await get_breaking_signal(role, company_type, market, session_id)
    except Exception:
        return "", False


# ── Redis snapshot cache ──────────────────────────────────────────────────────

def _snapshot_key(role: str, company_type: str, market: str) -> str:
    return f"snapshot:{role}:{company_type}:{market}"


def _snapshot_count_key(role: str, company_type: str, market: str) -> str:
    return f"snapshot_count:{role}:{company_type}:{market}"


def _snapshot_prev_key(role: str, company_type: str, market: str) -> str:
    return f"snapshot_prev:{role}:{company_type}:{market}"


def _get_cached_snapshot(role: str, company_type: str, market: str) -> tuple[DistilledMarketContext | None, int]:
    """
    Check Redis for a cached distilled context.
    Returns (context, signal_count) — signal_count is 0 if cache miss.
    """
    key = _snapshot_key(role, company_type, market)
    count_key = _snapshot_count_key(role, company_type, market)
    cached = redis.get(key)
    if cached:
        try:
            ctx = DistilledMarketContext(**json.loads(cached))
            count = redis.get(count_key)
            return ctx, int(count) if count else 0
        except Exception:
            return None, 0
    return None, 0


def _cache_snapshot(
    role: str,
    company_type: str,
    market: str,
    context: DistilledMarketContext,
    signal_count: int = 0,
) -> None:
    """Store distilled context and signal count in Redis. Also promote current to prev."""
    key = _snapshot_key(role, company_type, market)
    count_key = _snapshot_count_key(role, company_type, market)
    prev_key = _snapshot_prev_key(role, company_type, market)

    # Promote current to prev before overwriting
    current = redis.get(key)
    if current:
        redis.set(prev_key, current, ex=SNAPSHOT_PREV_TTL)

    redis.set(key, context.model_dump_json(), ex=SNAPSHOT_TTL)
    redis.set(count_key, str(signal_count), ex=SNAPSHOT_TTL)


# ── Main DIVE function ────────────────────────────────────────────────────────

async def run_dive(
    role: str,
    company_type: str,
    market: str,
    experience_level: str,
    session_id: str = "",
) -> FullMarketContext:
    """
    Full DIVE pipeline:
    1. Check Redis snapshot cache
    2. Cache miss: query rewrite → BM25 + vector → RRF → dedup → distil
    3. Cache result in Redis
    4. Add breaking signal overlay
    5. Return FullMarketContext
    """
    # Step 1 — check Redis snapshot cache
    cached, cached_signal_count = _get_cached_snapshot(role, company_type, market)
    if cached:
        logger.info("dive_cache_hit", role=role, market=market, signal_count=cached_signal_count, session_id=session_id)
        breaking, breaking_available = await _get_breaking_signal_with_fetch(
            role, company_type, market, session_id
        )
        return FullMarketContext(
            distilled=cached,
            breaking_signal=breaking,
            breaking_available=breaking_available,
            raw_signal_count=cached_signal_count,
        )

    # Step 2 — check if SQLite has data
    signal_count = count_signals_for_combo(role, company_type, market)

    if signal_count == 0:
        logger.warning(
            "dive_no_signals",
            role=role, company_type=company_type, market=market,
            session_id=session_id,
        )
        # Return baseline fallback — weights from DB, not hardcoded
        from backend.market_data import get_role_weights, get_role_category
        role_cat = get_role_category(role)
        db_weights = get_role_weights(role_cat) if role_cat else {}
        if not db_weights:
            db_weights = {
                "dsa": 0.7, "projects": 0.7, "cgpa": 0.5,
                "experience": 0.7, "open_source": 0.4, "college_tier": 0.4
            }
        breaking, breaking_available = await _get_breaking_signal_with_fetch(
            role, company_type, market, session_id
        )
        return FullMarketContext(
            distilled=DistilledMarketContext(
                hiring_sentiment="neutral",
                top_required_skills=[],
                competitive_pool_signal="No market data available for this combination yet.",
                salary_band="data unavailable",
                red_flag_triggers=[],
                format_expectations="Standard resume format",
                weight_map=db_weights,
                confidence="LOW",
                freshness_label="Needs Refresh",
            ),
            breaking_signal=breaking,
            breaking_available=breaking_available,
            raw_signal_count=0,
        )

    # Step 3 — run DIVE
    logger.info("dive_running", role=role, market=market, signal_count=signal_count, session_id=session_id)

    # Stage 1: Query rewriting
    queries = _build_retrieval_queries(role, company_type, market, experience_level)

    # Stage 2: Parallel BM25 + vector search
    bm25_results, vector_results = await _parallel_search(
        role=role,
        company_type=company_type,
        market=market,
        queries=queries,
    )

    # Stage 3: RRF fusion
    fused = _rrf_fusion(bm25_results, vector_results)

    # Stage 4: Hash deduplication (raised limit from 15 to 25)
    deduped = _hash_dedup(fused, limit=25)

    # Stage 4b: Quality filter — drop signals with no usable content
    filtered = _filter_signals_quality(deduped)
    filtered_count = len(filtered)
    if filtered_count < len(deduped):
        logger.info("dive_signals_filtered", role=role, market=market, before=len(deduped), after=filtered_count, session_id=session_id)

    # Stage 5: Context distiller (now sees up to 25 filtered signals)
    distilled = await _distill_context(
        signals=filtered,
        role=role,
        company_type=company_type,
        market=market,
        experience_level=experience_level,
        session_id=session_id,
    )

    # Cache in Redis (store actual total signal count from DB, not just deduped/filtered)
    _cache_snapshot(role, company_type, market, distilled, signal_count=signal_count)

    # Step 4 — add breaking signal (live fetch on cache miss)
    breaking, breaking_available = await _get_breaking_signal_with_fetch(
        role, company_type, market, session_id
    )

    logger.info(
        "dive_complete",
        role=role, market=market,
        signals_total=signal_count,
        signals_deduped=len(deduped),
        signals_filtered=filtered_count,
        confidence=distilled.confidence,
        session_id=session_id,
    )

    return FullMarketContext(
        distilled=distilled,
        breaking_signal=breaking,
        breaking_available=breaking_available,
        raw_signal_count=signal_count,
    )


# ── Cache warming ─────────────────────────────────────────────────────────────

_WARMUP_COMBOS: list[tuple[str, str, str]] = []


def _get_warmup_combos() -> list[tuple[str, str, str]]:
    """
    Generate warmup combos dynamically from DB signal counts.
    Falls back to a minimal hardcoded list only if DB is empty.
    """
    global _WARMUP_COMBOS
    if _WARMUP_COMBOS:
        return _WARMUP_COMBOS

    try:
        import sqlite3
        conn = sqlite3.connect("ingestion/market_intel.db")
        cur = conn.cursor()
        # Top 15 combos by signal count
        cur.execute(
            "SELECT role, company_type, market, COUNT(*) as cnt "
            "FROM market_signals GROUP BY role, company_type, market "
            "ORDER BY cnt DESC LIMIT 15"
        )
        combos = [(r, c, m) for r, c, m, _ in cur.fetchall()]
        conn.close()
        if combos:
            _WARMUP_COMBOS = combos
            return _WARMUP_COMBOS
    except Exception:
        pass

    # Fallback only if DB is unreachable or empty
    _WARMUP_COMBOS = [
        ("Software Engineer / Associate", "Indian Product Company", "India"),
        ("SDE1", "Indian Product Company", "India"),
        ("AI Agentic Engineer", "Indian Product Company", "India"),
    ]
    return _WARMUP_COMBOS


async def warmup_cache(experience_level: str = "Student / Fresher") -> dict:
    """
    Pre-warm DIVE cache for top combos on server startup.
    Clears stale cache entries (older than 1 day) before warming so that
    pipeline improvements are picked up on restart.
    Returns dict of {combo: status} — "hit" if cached, "warmed" if fetched, "skipped" if no data.
    """
    import time
    results = {}
    now = int(time.time())

    for role, company_type, market in _get_warmup_combos():
        combo_key = f"{role}:{company_type}:{market}"
        key = _snapshot_key(role, company_type, market)

        # Check if cached and fresh (< 24h old since creation)
        cached_ctx, cached_count = _get_cached_snapshot(role, company_type, market)
        ttl = redis.ttl(key)
        stale = (ttl is not None and ttl > 0 and ttl < (SNAPSHOT_TTL - 24 * 3600))

        if cached_ctx and not stale:
            results[combo_key] = "hit"
            continue

        # Clear stale entry so fresh distillation runs
        if stale:
            redis.delete(key)
            redis.delete(_snapshot_count_key(role, company_type, market))
            logger.info("cache_stale_cleared", combo=combo_key, ttl=ttl)

        # Skip if no signals in SQLite
        from ingestion.search import count_signals_for_combo
        if count_signals_for_combo(role, company_type, market) == 0:
            results[combo_key] = "skipped"
            continue

        # Run DIVE to populate cache
        try:
            await run_dive(role, company_type, market, experience_level)
            results[combo_key] = "warmed"
            logger.info("cache_warmed", combo=combo_key)
        except Exception as e:
            results[combo_key] = f"failed:{e}"
            logger.warning("cache_warmup_failed", combo=combo_key, error=str(e))
    return results
