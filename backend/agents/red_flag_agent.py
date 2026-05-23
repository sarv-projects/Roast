import structlog
from backend.agents.schemas import RedFlagOutput, RedFlag
from backend.agents.prompts.template import build_system_prompt
from backend.agents.prompts.red_flag_prompt import get_red_flag_task
from backend.agents.schemas import MarketContextOutput, JDRequirements, ResumeFacts
from backend.llm.router import call_red_flag_agent, call_groq_8b
from backend.agents.json_utils import extract_json

logger = structlog.get_logger()

GENERIC_CHAIN_BLOCKLIST = [
    "recruiters look for",
    "is important to",
    "hiring managers want",
    "this shows that",
    "lacks quantifiable",
    "should include metrics",
    "demonstrates that you",
    "will negatively impact",
]


def _passes_quality_gate(flag: RedFlag) -> bool:
    if len(flag.location) < 10:
        return False
    if len(flag.fix) < 20:
        return False
    if len(flag.inference_chain) < 50:
        return False
    chain_lower = flag.inference_chain.lower()
    generic_count = sum(1 for phrase in GENERIC_CHAIN_BLOCKLIST if phrase in chain_lower)
    return generic_count < 2


async def run_red_flag_agent(
    resume_text: str,
    market_context: MarketContextOutput,
    role: str,
    company_type: str,
    market: str,
    experience_level: str,
    user_context: str = "",
    jd_requirements: JDRequirements | None = None,
    profile_links: dict | None = None,
    resume_facts: ResumeFacts | None = None,
    session_id: str = "",
) -> RedFlagOutput:
    task = get_red_flag_task(role, company_type, market)

    from backend.agents.resume_extractor import facts_to_prompt
    facts_section = facts_to_prompt(resume_facts) if resume_facts else ""
    agent_constraints = ""
    if facts_section:
        agent_constraints = f"{facts_section}\n\nIMPORTANT: Use the facts above to verify your flags. Do NOT flag something the resume already addresses."

    system = build_system_prompt(
        role=role,
        company_type=company_type,
        market=market,
        experience_level=experience_level,
        agent_task=task,
        agent_output_rules="Return only valid JSON with red_flags array and visual_scan_notes string.",
        agent_specific_constraints=agent_constraints,
    )

    jd_section = ""
    if jd_requirements:
        jd_section = f"\n\nJD REQUIREMENTS (flag gaps as jd_gap: true):\n{jd_requirements.model_dump_json(indent=2)}"

    links_section = ""
    if profile_links:
        github = profile_links.get("github", "not found")
        linkedin = profile_links.get("linkedin", "not found")
        links_section = f"\n\nPROFILE LINKS:\nGitHub: {github}\nLinkedIn: {linkedin}"

    prompt = f"""{system}

<resume>
{resume_text[:8000]}
</resume>

MARKET RED FLAG TRIGGERS:
{chr(10).join(f'- {t}' for t in market_context.red_flag_triggers[:8])}

USER CONTEXT: {user_context or 'None provided'}
{jd_section}
{links_section}

Find all red flags and produce the JSON output."""

    # ── LLM call with fallback ────────────────────────────────────────────────
    text = None
    meta = {}
    try:
        text, meta = await call_red_flag_agent(
            prompt=prompt, max_tokens=2500, session_id=session_id,
        )
    except Exception as primary_err:
        logger.warning("red_flag_primary_failed_falling_back",
                       error=str(primary_err), session_id=session_id)
        try:
            text, meta = await call_groq_8b(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2000,
                session_id=session_id,
            )
        except Exception as groq_err:
            logger.error("red_flag_agent_all_failed", error=str(groq_err), session_id=session_id)
            return RedFlagOutput(red_flags=[], visual_scan_notes="", confidence="LOW")

    # ── Parse ─────────────────────────────────────────────────────────────────
    try:
        data = extract_json(text)
        raw_flags = []
        for f in data.get("red_flags", []):
            try:
                raw_flags.append(RedFlag(**f))
            except Exception:
                continue

        passed_flags = []
        for flag in raw_flags:
            if _passes_quality_gate(flag):
                passed_flags.append(flag)
            else:
                logger.warning("red_flag_quality_gate_failed",
                               flag=flag.flag[:50], session_id=session_id)

        # Determine confidence from flag count and quality
        llm_conf = data.get("confidence", "MEDIUM")
        if llm_conf not in ("HIGH", "MEDIUM", "LOW"):
            llm_conf = "MEDIUM"

        output = RedFlagOutput(
            red_flags=passed_flags,
            visual_scan_notes=data.get("visual_scan_notes", ""),
            confidence=llm_conf,
        )

        logger.info(
            "red_flag_agent_complete",
            session_id=session_id,
            flags_found=len(passed_flags),
            flags_filtered=len(raw_flags) - len(passed_flags),
            model=meta.get("model"),
            prompt_version="v1",
            confidence=llm_conf,
        )

        return output

    except Exception as e:
        logger.error("red_flag_agent_parse_failed", error=str(e), session_id=session_id)
        return RedFlagOutput(red_flags=[], visual_scan_notes="", confidence="LOW")
