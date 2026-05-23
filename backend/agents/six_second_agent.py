import json
import structlog
from backend.agents.schemas import SixSecondAndTrajectoryOutput, GapSignal
from backend.agents.prompts.template import build_system_prompt
from backend.agents.prompts.six_second_prompt import get_six_second_task
from backend.agents.schemas import MarketContextOutput
from backend.llm.router import call_six_second_agent as _call_agent
from backend.agents.json_utils import extract_json

logger = structlog.get_logger()


async def run_six_second_trajectory_agent(
    resume_text: str,
    market_context: MarketContextOutput,
    role: str,
    company_type: str,
    market: str,
    experience_level: str,
    user_context: str = "",
    profile_links: dict | None = None,
    session_id: str = "",
) -> SixSecondAndTrajectoryOutput:
    """
    Agent 3 — runs in parallel.
    Part A: simulates 6-second recruiter scan.
    Part B: analyses career trajectory, gaps, progression.
    """
    task = get_six_second_task(company_type, market)

    system = build_system_prompt(
        role=role,
        company_type=company_type,
        market=market,
        experience_level=experience_level,
        agent_task=task,
        agent_output_rules="Return only valid JSON with all fields from both Part A and Part B.",
    )

    # First 200 words for the scan simulation
    words = resume_text.split()
    first_200 = " ".join(words[:200])

    links_section = ""
    if profile_links:
        github = profile_links.get("github", "not found")
        linkedin = profile_links.get("linkedin", "not found")
        links_section = f"\nGitHub URL: {github}\nLinkedIn URL: {linkedin}"

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"""FIRST 200 WORDS (for 6-second scan simulation):
{first_200}

<resume>
{resume_text[:8000]}
</resume>

USER CONTEXT: {user_context or 'None provided'}
{links_section}

Produce the SixSecondAndTrajectory JSON output.""",
        },
    ]

    try:
        text, meta = await _call_agent(
            messages, max_tokens=1500, temperature=0.2, session_id=session_id
        )

        if not text or not text.strip():
            raise ValueError("empty_response")

        data = extract_json(text)

        # Parse gaps as GapSignal objects
        gaps = [GapSignal(**g) for g in data.get("gaps", [])]
        data["gaps"] = [g.model_dump() for g in gaps]

        # Coerce None to empty string for optional string fields
        for field in ["fresher_note", "github_signal", "linkedin_signal",
                      "progression_signal", "promotion_velocity", "skill_evolution",
                      "career_story", "first_impression", "survived_cut_assessment",
                      "confidence"]:
            if data.get(field) is None or data.get(field) == "":
                if field == "confidence":
                    data[field] = "MEDIUM"
                else:
                    data[field] = data.get(field) or ""

        output = SixSecondAndTrajectoryOutput(**data)

        logger.info(
            "six_second_agent_complete",
            session_id=session_id,
            survived=output.survived_cut_assessment[:20],
            gaps_found=len(output.gaps),
            model=meta.get("model"),
            prompt_version="v1",
        )

        return output

    except Exception as e:
        logger.error("six_second_agent_failed", error=str(e), session_id=session_id)
        return SixSecondAndTrajectoryOutput(
            remembered=[], missed=[],
            first_impression="Analysis unavailable",
            survived_cut_assessment="MAYBE — analysis failed",
            confidence="LOW",
            career_story="", progression_signal="", gaps=[],
            promotion_velocity="", skill_evolution="",
            fresher_note="", github_signal="", linkedin_signal="",
        )
