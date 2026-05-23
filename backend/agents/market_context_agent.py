import json
import structlog
from backend.agents.schemas import JDRequirements, MarketContextOutput
from backend.agents.prompts.template import build_system_prompt
from backend.agents.prompts.market_context_prompt import get_market_context_task
from backend.llm.router import call_groq_8b
from backend.agents.json_utils import extract_json
from backend.market_data import get_role_weights, ROLE_TO_CATEGORY

logger = structlog.get_logger()

# ── Weight map rules (enforced programmatically, not just in prompt) ──────────

_JUNIOR_DEFAULTS: dict[str, float] = {
    "dsa": 0.7, "projects": 0.75, "cgpa": 0.4,
    "experience": 0.5, "open_source": 0.4, "college_tier": 0.3,
}

_FRESHER_DEFAULTS: dict[str, float] = {
    "dsa": 0.5, "projects": 0.7, "cgpa": 0.6,
    "experience": 0.0, "open_source": 0.3, "college_tier": 0.6,
}

_MID_DEFAULTS: dict[str, float] = {
    "dsa": 0.5, "projects": 0.6, "cgpa": 0.2,
    "experience": 0.8, "open_source": 0.4, "college_tier": 0.1,
}

_SENIOR_DEFAULTS: dict[str, float] = {
    "dsa": 0.4, "projects": 0.4, "cgpa": 0.1,
    "experience": 0.9, "open_source": 0.5, "college_tier": 0.05,
}

_STAFF_DEFAULTS: dict[str, float] = {
    "dsa": 0.3, "projects": 0.3, "cgpa": 0.0,
    "experience": 0.95, "open_source": 0.5, "college_tier": 0.0,
}

_EXPERIENCE_MAP: dict[str, dict[str, float]] = {
    "Student": _FRESHER_DEFAULTS,
    "Fresher": _FRESHER_DEFAULTS,
    "Junior": _JUNIOR_DEFAULTS,
    "Mid": _MID_DEFAULTS,
    "Mid-Level": _MID_DEFAULTS,
    "Senior": _SENIOR_DEFAULTS,
    "Staff": _STAFF_DEFAULTS,
    "Principal": _STAFF_DEFAULTS,
}

_COMPANY_OVERRIDES: dict[str, dict[str, tuple[float, float]]] = {
    "FAANG":               {"dsa": (0.9, 1.0), "open_source": (0.5, 0.5)},
    "FAANG / Big Tech":    {"dsa": (0.9, 1.0), "open_source": (0.5, 0.5)},
    "Indian Service Company":  {"dsa": (0.0, 0.3), "cgpa_add": 0.15, "college_tier_add": 0.1},
    "Startup":             {"dsa": (0.2, 0.4), "projects": (0.85, 0.85), "open_source": (0.6, 0.6)},
    "Indian Product Company":  {"dsa": (0.6, 0.8), "projects": (0.75, 0.75)},
    "MNC India (Non-FAANG)":  {"dsa": (0.5, 0.5), "cgpa_add": 0.1},
    "Semiconductor":       {"dsa": (0.3, 0.3), "projects": (0.8, 0.8), "open_source": (0.2, 0.2)},
    "Semiconductor / Hardware": {"dsa": (0.3, 0.3), "projects": (0.8, 0.8), "open_source": (0.2, 0.2)},
    "Consulting / IB":     {"dsa": (0.2, 0.2), "projects": (0.5, 0.5), "cgpa_add": 0.1},
}


def _enforce_weight_map(
    llm_weights: dict[str, float],
    experience_level: str,
    company_type: str,
    role: str = "",
) -> dict[str, float]:
    """Apply the prompt's rules programmatically so the LLM can't ignore them."""
    # Start with experience-level defaults
    base = dict(_EXPERIENCE_MAP.get(experience_level, _JUNIOR_DEFAULTS))

    # Fetch role-specific weights
    role_weights: dict[str, float] = {}
    if role:
        role_cat = ROLE_TO_CATEGORY.get(role, "")
        role_weights = get_role_weights(role_cat) if role_cat else {}

    # Merge LLM values, clamping per company type rules.
    # BUT: if a role-specific weight exists for a key, the role is the authority
    # and company clamping does NOT apply to that key.
    company_rules = _COMPANY_OVERRIDES.get(company_type, {})
    for key in base:
        has_role_override = key in role_weights and role_weights[key] is not None
        llm_val = llm_weights.get(key)

        if has_role_override:
            # Role knows best — use role weight directly, ignore LLM and company bounds
            base[key] = role_weights[key]
        elif llm_val is not None:
            rule = company_rules.get(key)
            if rule:
                lo, hi = rule
                base[key] = max(lo, min(hi, llm_val))
            else:
                base[key] = max(0.0, min(1.0, llm_val))

    # Apply additive modifiers (always apply, even when role overrides key)
    cgpa_add = company_rules.get("cgpa_add", 0.0)
    if cgpa_add:
        base["cgpa"] = max(0.0, min(1.0, base["cgpa"] + cgpa_add))
    ct_add = company_rules.get("college_tier_add", 0.0)
    if ct_add:
        base["college_tier"] = max(0.0, min(1.0, base["college_tier"] + ct_add))

    return base


# ── JD Parser ─────────────────────────────────────────────────────────────────

JD_PARSER_SYSTEM = """
Parse the provided job description and extract structured requirements.
Return ONLY valid JSON — no explanation, no markdown.

{
  "required_skills": ["skill1", "skill2"],
  "preferred_skills": ["skill1"],
  "experience_range": "2-5 years",
  "role_level": "SDE2",
  "key_responsibilities": ["responsibility1"],
  "company_signals": ["signal about company culture or type"]
}

Rules:
- required_skills: only hard technical requirements explicitly stated
- preferred_skills: nice-to-haves, bonus skills
- experience_range: exact range from JD or "not specified"
- role_level: infer from JD if not explicit
- company_signals: things that reveal company type (e.g. "fast-paced startup", "enterprise scale")
"""


async def parse_jd(jd_text: str, session_id: str = "") -> JDRequirements | None:
    """
    Parse a job description into structured requirements.
    Returns None if JD text is empty or parsing fails.
    """
    if not jd_text or len(jd_text.strip()) < 50:
        return None

    messages = [
        {"role": "system", "content": JD_PARSER_SYSTEM},
        {"role": "user", "content": f"Parse this job description:\n\n{jd_text[:2000]}"},
    ]

    try:
        text, _ = await call_groq_8b(messages, max_tokens=600, session_id=session_id,
                                      agent_name="jd_parser")

        # Strip markdown if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        data = json.loads(text)
        return JDRequirements(**data)

    except Exception as e:
        logger.error("jd_parse_failed", error=str(e), session_id=session_id)
        return None


# ── MarketContextAgent ────────────────────────────────────────────────────────

async def run_market_context_agent(
    distilled_context: str,
    role: str,
    company_type: str,
    market: str,
    experience_level: str,
    user_context: str = "",
    jd_requirements: JDRequirements | None = None,
    session_id: str = "",
) -> MarketContextOutput:
    """
    Agent 1 — runs alone first. All parallel agents wait for its output.
    Interprets FullMarketContext into weight_map and calibration structures.
    """
    task = get_market_context_task()

    system = build_system_prompt(
        role=role,
        company_type=company_type,
        market=market,
        experience_level=experience_level,
        agent_task=task,
        agent_output_rules="Return only valid JSON matching the schema above.",
    )

    jd_section = ""
    if jd_requirements:
        jd_section = f"\n\nJD REQUIREMENTS:\n{jd_requirements.model_dump_json(indent=2)}"

    user_content = f"""MARKET INTELLIGENCE:
{distilled_context}

USER CONTEXT: {user_context or 'None provided'}
{jd_section}

Produce the MarketContextOutput JSON."""

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

    try:
        text, meta = await call_groq_8b(
            messages, max_tokens=1000, temperature=0.1, session_id=session_id,
            agent_name="market_context_agent",
        )

        data = extract_json(text)

        # Coerce format_expectations to string if model returned a dict
        if isinstance(data.get("format_expectations"), dict):
            data["format_expectations"] = json.dumps(data["format_expectations"])

        # Coerce None/missing string fields to safe defaults
        for field, default in [
            ("competitive_pool_description", "Competitive pool data unavailable"),
            ("market_norms", ""),
            ("format_expectations", ""),
            ("live_context_summary", ""),
        ]:
            if not data.get(field):
                data[field] = default
        if not isinstance(data.get("red_flag_triggers"), list):
            data["red_flag_triggers"] = []
        if not isinstance(data.get("weight_map"), dict):
            data["weight_map"] = {
                "dsa": 0.7, "projects": 0.7, "cgpa": 0.5,
                "experience": 0.7, "open_source": 0.4, "college_tier": 0.4
            }

        # Enforce weight rules programmatically — the LLM can't be trusted to
        # follow the prompt rules consistently (e.g. it sets dsa=0 for Indian
        # Product Company despite the prompt saying 0.6-0.8).
        data["weight_map"] = _enforce_weight_map(
            data["weight_map"], experience_level, company_type, role,
        )

        # Inject JD requirements into output if provided
        if jd_requirements:
            data["jd_requirements"] = jd_requirements.model_dump()

        output = MarketContextOutput(**data)

        logger.info(
            "market_context_agent_complete",
            session_id=session_id,
            confidence=output.confidence,
            model=meta.get("model"),
            prompt_version="v1",
        )

        return output

    except Exception as e:
        logger.error("market_context_agent_failed", error=str(e), session_id=session_id)
        fallback_weights = _enforce_weight_map(
            {"dsa": 0.7, "projects": 0.7, "cgpa": 0.5,
             "experience": 0.7, "open_source": 0.4, "college_tier": 0.4},
            experience_level, company_type, role,
        )
        return MarketContextOutput(
            market_norms=f"Standard {role} hiring norms for {market}",
            format_expectations="Standard resume format",
            competitive_pool_description="Competitive pool data unavailable",
            red_flag_triggers=[],
            weight_map=fallback_weights,
            live_context_summary="Market intelligence unavailable for this analysis.",
            confidence="LOW",
        )
