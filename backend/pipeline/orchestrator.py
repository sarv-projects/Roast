"""
Full pipeline orchestrator.
Connects DIVE retrieval → MarketContextAgent → parallel agents → ReviewAgent.
Runs as a FastAPI BackgroundTask — never blocks the HTTP response.
"""

import asyncio
import time
import hashlib
import structlog
from datetime import datetime
from pydantic import BaseModel

from backend.retrieval.dive import run_dive, FullMarketContext
from backend.agents.market_context_agent import run_market_context_agent, parse_jd
from backend.agents.red_flag_agent import run_red_flag_agent
from backend.agents.six_second_agent import run_six_second_trajectory_agent
from backend.agents.competitive_agent import run_competitive_agent
from backend.agents.review_agent import run_review_agent
from backend.agents.technical_depth_agent import run_technical_depth_agent
from backend.agents.resume_extractor import extract_resume_facts, facts_to_prompt
from backend.agents.schemas import (
    MarketContextOutput, RedFlagOutput, SixSecondAndTrajectoryOutput,
    CompetitiveOutput, ReviewOutput, JDRequirements, TechnicalDepthOutput
)
from backend.storage.redis_client import redis
from backend.storage.session_store import update_session
from backend.corpus.corpus_store import build_signal_from_pipeline, store_signal
from backend.corpus.bullet_curator import extract_bullet_candidates, flag_bullet_candidate
from backend.market_data import ensure_seeded, is_market_data_stale

# Import emit lazily to avoid circular imports
async def _emit(session_id: str, event: str, data: dict) -> None:
    try:
        from backend.routes.ws_manager import emit
        await emit(session_id, event, data)
    except Exception:
        pass

logger = structlog.get_logger()

# Semaphores — shared across all concurrent pipeline runs
_groq_sem = asyncio.Semaphore(2)    # max 2 concurrent Groq calls
_gemini_sem = asyncio.Semaphore(1)  # max 1 concurrent Gemini call
_global_sem = asyncio.Semaphore(3)  # max 3 simultaneous full pipelines
_tech_depth_sem = asyncio.Semaphore(3)  # gpt-oss-120b: 8K TPM per key, 24K with 3 keys — safe for parallel


# ── Prompt version tracking ─────────────────────────────────────────────────────

def _compute_prompt_hash(role: str, company_type: str, market: str) -> str:
    """Generate a version hash from the prompt content for A/B testing."""
    from backend.agents.prompts.review_prompt import get_review_task
    from backend.agents.prompts.red_flag_prompt import get_red_flag_task
    from backend.agents.prompts.six_second_prompt import get_six_second_task
    from backend.agents.prompts.competitive_prompt import get_competitive_task
    from backend.agents.prompts.market_context_prompt import get_market_context_task
    combined = (
        get_review_task(market, company_type)
        + get_red_flag_task(role, company_type, market)
        + get_six_second_task(company_type, market)
        + get_competitive_task(role, company_type, market)
        + get_market_context_task()
    )
    return hashlib.sha256(combined.encode()).hexdigest()[:12]


def _get_prompt_variant(session_id: str) -> str:
    """
    Simple A/B routing — 50/50 split based on session_id hash.
    Returns "A" (current) or "B" (experimental).
    """
    import hashlib
    digest = hashlib.md5(session_id.encode()).hexdigest()
    # Use last hex digit — 0-7 = A (50%), 8-f = B (50%)
    return "A" if int(digest[-1], 16) < 8 else "B"


# ── Pipeline request/result models ──────────────────────────────────────────────

class PipelineRequest(BaseModel):
    session_id: str
    resume_text: str
    role: str
    company_type: str
    market: str
    experience_level: str
    user_context: str = ""
    jd_text: str = ""
    profile_links: dict = {}
    github_url: str = ""
    opted_in_corpus: bool = False  # user explicitly opted in to anonymised signals


class PipelineResult(BaseModel):
    session_id: str
    market_context: MarketContextOutput
    red_flags: RedFlagOutput
    six_second: SixSecondAndTrajectoryOutput
    competitive: CompetitiveOutput
    technical_depth: TechnicalDepthOutput
    review: ReviewOutput
    jd_requirements: JDRequirements | None
    full_market_context: FullMarketContext
    duration_seconds: float


async def run_pipeline(request: PipelineRequest) -> PipelineResult:
    """
    Full analysis pipeline. Called as a BackgroundTask from /analyse.

    Execution order:
    1. Pre-pipeline: parse JD, check corpus
    2. DIVE retrieval → FullMarketContext
    3. MarketContextAgent alone
    4. Agents 2-4 in parallel (with semaphores)
    5. Python synthesis (no LLM)
    6. ReviewAgent with fallback chain
    7. Update session state
    """
    async with _global_sem:
        return await _run_pipeline_inner(request)


async def _run_pipeline_inner(request: PipelineRequest) -> PipelineResult:
    start = time.time()
    sid = request.session_id

    # Ensure market data tables exist and are seeded
    ensure_seeded()

    # Check for stale market data (warn if >90 days since last update)
    if is_market_data_stale(90):
        logger.warning("market_data_stale", session_id=sid,
                       message="Market config data has not been updated in >90 days. Run scripts/seed_market_data.py.")
        try:
            from backend.config import DISCORD_WEBHOOK_URL
            if DISCORD_WEBHOOK_URL:
                import httpx
                httpx.post(DISCORD_WEBHOOK_URL, json={
                    "content": "⚠️ Market config data is >90 days stale. Company lists, salary bands, and role requirements may be outdated. Run `python scripts/update_market_data.py` or update `backend/market_data.py` seed functions."
                }, timeout=5)
        except Exception:
            pass

    # Compute prompt version hash for tracking
    prompt_version = _compute_prompt_hash(request.role, request.company_type, request.market)
    prompt_variant = _get_prompt_variant(sid)

    logger.info(
        "pipeline_started",
        session_id=sid,
        role=request.role,
        market=request.market,
        company_type=request.company_type,
        prompt_variant=prompt_variant,
    )

    # Update session status
    update_session(sid, {"status": "in_progress", "step": "starting"})

    # ── Pre-pipeline ──────────────────────────────────────────────────────────

    # Parse JD if provided
    jd_requirements: JDRequirements | None = None
    if request.jd_text and len(request.jd_text.strip()) > 50:
        update_session(sid, {"step": "parsing_jd"})
        jd_requirements = await parse_jd(request.jd_text, session_id=sid)

    # ── Stage 1: DIVE retrieval ───────────────────────────────────────────────

    update_session(sid, {"step": "fetching_market_intel"})

    full_market_ctx = await run_dive(
        role=request.role,
        company_type=request.company_type,
        market=request.market,
        experience_level=request.experience_level,
        session_id=sid,
    )

    # Extract structured resume facts via 8B extractor — shared across all agents
    resume_facts = await extract_resume_facts(request.resume_text, session_id=sid)

    # Format distilled context as text for MarketContextAgent
    distilled_text = _format_distilled_context(full_market_ctx)

    # ── Stage 2: MarketContextAgent (alone first) ─────────────────────────────

    update_session(sid, {"step": "market_context_agent"})

    async with _groq_sem:
        market_context = await run_market_context_agent(
            distilled_context=distilled_text,
            role=request.role,
            company_type=request.company_type,
            market=request.market,
            experience_level=request.experience_level,
            user_context=request.user_context,
            jd_requirements=jd_requirements,
            session_id=sid,
        )

    # Override confidence based on actual signal count — the LLM is too conservative.
    # 10+ signals retrieved = HIGH confidence, regardless of LLM's LOW judgment.
    if full_market_ctx.raw_signal_count >= 10 and market_context.confidence == "LOW":
        market_context.confidence = "HIGH"
        logger.info("confidence_override", session_id=sid,
                    signal_count=full_market_ctx.raw_signal_count,
                    reason="signal_threshold_met")

    # Store in session for WebSocket streaming
    _store_section(sid, "market_context", market_context.model_dump())
    await _emit(sid, "section_complete", {"section": "market_context", "result": market_context.model_dump()})

    # Also emit the full market intel (salary band, top skills, freshness, breaking signal)
    market_intel_payload = {
        "distilled": {
            "salary_band": full_market_ctx.distilled.salary_band,
            "top_required_skills": full_market_ctx.distilled.top_required_skills,
            "freshness_label": full_market_ctx.distilled.freshness_label,
            "hiring_sentiment": full_market_ctx.distilled.hiring_sentiment,
        },
        "breaking_signal": full_market_ctx.breaking_signal,
        "breaking_available": full_market_ctx.breaking_available,
    }
    _store_section(sid, "market_intel", market_intel_payload)
    await _emit(sid, "section_complete", {"section": "market_intel", "result": market_intel_payload})

    # ── Stage 3: Parallel agents ──────────────────────────────────────────────

    update_session(sid, {"step": "parallel_agents"})

    profile_links = request.profile_links
    if request.github_url:
        profile_links["github"] = request.github_url

    red_flags_task = _run_with_groq_sem(
        run_red_flag_agent(
            resume_text=request.resume_text,
            market_context=market_context,
            role=request.role,
            company_type=request.company_type,
            market=request.market,
            experience_level=request.experience_level,
            user_context=request.user_context,
            jd_requirements=jd_requirements,
            profile_links=profile_links,
            resume_facts=resume_facts,
            session_id=sid,
        )
    )

    # Competitive uses gpt-oss-20b, SixSecond uses qwen3-32b — no Groq semaphore needed
    six_second_task = run_six_second_trajectory_agent(
            resume_text=request.resume_text,
            market_context=market_context,
            role=request.role,
            company_type=request.company_type,
            market=request.market,
            experience_level=request.experience_level,
            user_context=request.user_context,
            profile_links=profile_links,
            session_id=sid,
        )

    competitive_task = run_competitive_agent(
            resume_text=request.resume_text,
            market_context=market_context,
            breaking_signal=full_market_ctx.breaking_signal,
            role=request.role,
            company_type=request.company_type,
            market=request.market,
            experience_level=request.experience_level,
            user_context=request.user_context,
            jd_requirements=jd_requirements,
            resume_facts=resume_facts,
            session_id=sid,
        )

    # TechnicalDepthAgent — semaphore to prevent gpt-oss-120b TPM overflow
    # 24K TPM with 3 keys, each call uses ~3500 tokens — 3 concurrent = 44% safe
    technical_depth_task = _run_with_tech_depth_sem(run_technical_depth_agent(
        resume_text=request.resume_text,
        role=request.role,
        company_type=request.company_type,
        market=request.market,
        experience_level=request.experience_level,
        session_id=sid,
    ))

    red_flags, six_second, competitive, technical_depth = await asyncio.gather(
        red_flags_task,
        six_second_task,
        competitive_task,
        technical_depth_task,
        return_exceptions=True,
    )

    # Handle failed agents gracefully — use fallback outputs instead of crashing
    from backend.agents.schemas import (
        RedFlagOutput, SixSecondAndTrajectoryOutput, CompetitiveOutput,
        PercentileEstimate
    )
    from backend.agents.technical_depth_agent import TechnicalDepthOutput

    if isinstance(red_flags, Exception):
        logger.error("red_flags_agent_exception", error=str(red_flags), session_id=sid)
        red_flags = RedFlagOutput(red_flags=[], visual_scan_notes="", confidence="LOW")

    if isinstance(six_second, Exception):
        logger.error("six_second_agent_exception", error=str(six_second), session_id=sid)
        six_second = SixSecondAndTrajectoryOutput(
            remembered=[], missed=[], first_impression="Analysis unavailable",
            survived_cut_assessment="MAYBE", career_story="", progression_signal="",
            gaps=[], promotion_velocity="", skill_evolution="",
            confidence="LOW",
        )

    if isinstance(competitive, Exception):
        logger.error("competitive_agent_exception", error=str(competitive), session_id=sid)
        competitive = CompetitiveOutput(
            strengths_vs_pool=[], weaknesses_vs_pool=[],
            percentile_estimate=PercentileEstimate(
                range="Unable to estimate", reasoning="Rate limit hit", confidence="estimated"
            ),
            highest_leverage_change="Analysis unavailable", estimated_impact="", jd_fit_score=None,
        )

    if isinstance(technical_depth, Exception):
        logger.error("technical_depth_exception", error=str(technical_depth), session_id=sid)
        technical_depth = TechnicalDepthOutput(
            project_evaluations=[], overall_technical_level="",
            most_differentiated_signal="", biggest_technical_gap="",
            communication_gap="", honest_summary="",
            unverified_skills=[],
        )

    # Store sections
    _store_section(sid, "red_flags", red_flags.model_dump())
    await _emit(sid, "section_complete", {"section": "red_flags", "result": red_flags.model_dump()})
    _store_section(sid, "six_second", six_second.model_dump())
    await _emit(sid, "section_complete", {"section": "six_second", "result": six_second.model_dump()})
    _store_section(sid, "competitive", competitive.model_dump())
    await _emit(sid, "section_complete", {"section": "competitive", "result": competitive.model_dump()})
    _store_section(sid, "technical_depth", technical_depth.model_dump())
    await _emit(sid, "section_complete", {"section": "technical_depth", "result": technical_depth.model_dump()})

    # ── Stage 5: ReviewAgent ──────────────────────────────────────────────────

    update_session(sid, {"step": "review_agent"})

    review = await run_review_agent(
        resume_text=request.resume_text,
        market_context=market_context,
        red_flags=red_flags,
        six_second=six_second,
        competitive=competitive,
        role=request.role,
        company_type=request.company_type,
        market=request.market,
        experience_level=request.experience_level,
        user_context=request.user_context,
        jd_requirements=jd_requirements,
        technical_depth=technical_depth,
        resume_facts=resume_facts,
        session_id=sid,
    )

    _store_section(sid, "review", review.model_dump())
    await _emit(sid, "section_complete", {"section": "review", "result": review.model_dump()})

    # ── Complete ──────────────────────────────────────────────────────────────

    duration = round(time.time() - start, 2)

    update_session(sid, {
        "status": "completed",
        "step": "done",
        "duration_seconds": duration,
        "signal_count": full_market_ctx.raw_signal_count,
    })

    # Increment total analyses counter
    redis.incr("counter:total_analyses")
    redis.incr(f"combo_count:{request.role}:{request.company_type}:{request.market}")

    # ── Post-pipeline: corpus + bullet curation (background, never blocks) ────

    # Store anonymised signal if user opted in
    if request.opted_in_corpus:
        try:
            high_count = sum(1 for f in red_flags.red_flags if f.severity == "HIGH")
            signal = build_signal_from_pipeline(
                role=request.role,
                company_type=request.company_type,
                market=request.market,
                experience_level=request.experience_level,
                red_flag_count=len(red_flags.red_flags),
                high_severity_count=high_count,
                profile_links=request.profile_links,
                resume_text=request.resume_text,
                percentile_range=competitive.percentile_estimate.range,
                review_model="groq",
            )
            store_signal(signal)
            logger.info("corpus_signal_stored", session_id=sid)
        except Exception as e:
            logger.warning("corpus_store_failed", error=str(e), session_id=sid)

    # Flag bullet candidates for curation queue
    try:
        candidates = extract_bullet_candidates(
            review_text=review.whats_hurting_section,
            role=request.role,
            company_type=request.company_type,
            market=request.market,
            session_id=sid,
        )
        for candidate in candidates:
            flag_bullet_candidate(candidate)
        if candidates:
            logger.info("bullet_candidates_flagged", count=len(candidates), session_id=sid)
    except Exception as e:
        logger.warning("bullet_curation_failed", error=str(e), session_id=sid)

    logger.info(
        "pipeline_complete",
        session_id=sid,
        duration_seconds=duration,
        role=request.role,
        market=request.market,
        prompt_version=prompt_version,
        prompt_variant=prompt_variant,
    )

    return PipelineResult(
        session_id=sid,
        market_context=market_context,
        red_flags=red_flags,
        six_second=six_second,
        competitive=competitive,
        technical_depth=technical_depth,
        review=review,
        jd_requirements=jd_requirements,
        full_market_context=full_market_ctx,
        duration_seconds=duration,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _run_with_groq_sem(coro):
    async with _groq_sem:
        return await coro


async def _run_with_tech_depth_sem(coro):
    async with _tech_depth_sem:
        return await coro


def _format_distilled_context(ctx: FullMarketContext) -> str:
    d = ctx.distilled
    breaking = f"\nBREAKING SIGNAL (last 7 days): {ctx.breaking_signal}" if ctx.breaking_available else ""
    return f"""hiring_sentiment: {d.hiring_sentiment}
top_required_skills: {d.top_required_skills}
competitive_pool_signal: {d.competitive_pool_signal}
salary_band: {d.salary_band}
red_flag_triggers: {d.red_flag_triggers}
format_expectations: {d.format_expectations}
confidence: {d.confidence}
freshness: {d.freshness_label}{breaking}"""


def _store_section(session_id: str, section: str, data: dict) -> None:
    """Store a completed section in Redis for WebSocket streaming and reconnection."""
    import json
    key = f"session:{session_id}:{section}"
    redis.setex(key, 3600, json.dumps(data))
