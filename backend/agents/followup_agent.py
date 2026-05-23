import structlog
from backend.agents.schemas import FollowUpOutput
from backend.agents.prompts.follow_up_prompt import get_followup_task
from backend.llm.router import call_groq_8b
from backend.storage.redis_client import redis

logger = structlog.get_logger()

FOLLOWUP_TTL = 1800  # 30 minutes


def _followup_key(session_id: str, section: str) -> str:
    return f"followup:{session_id}:{section}"


def has_used_followup(session_id: str, section: str) -> bool:
    """Check if this section's follow-up has already been used this session."""
    return redis.exists(_followup_key(session_id, section)) == 1


def mark_followup_used(session_id: str, section: str) -> None:
    """Mark this section's follow-up as used."""
    redis.setex(_followup_key(session_id, section), FOLLOWUP_TTL, "1")


async def run_followup_agent(
    question: str,
    section: str,
    resume_text: str,
    review_summary: str,
    role: str,
    company_type: str,
    market: str,
    experience_level: str,
    session_id: str = "",
) -> FollowUpOutput:
    """
    Agent 6 — on demand only.
    Answers a clicked follow-up question specific to the resume and market.
    One per section per session — enforced via Redis key.
    Does NOT consume the daily rate limit.
    """
    task = get_followup_task(role, company_type, market)

    system = f"""You are an expert resume analyst for {role} roles at {company_type} in {market}.
{task}"""

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"""<resume>
{resume_text[:1500]}
</resume>

REVIEW CONTEXT:
{review_summary[:800]}

SECTION: {section}
QUESTION: {question}

Answer in 100-200 words.""",
        },
    ]

    try:
        text, meta = await call_groq_8b(
            messages, max_tokens=300, temperature=0.3, session_id=session_id
        )

        logger.info(
            "followup_agent_complete",
            session_id=session_id,
            section=section,
            model=meta.get("model"),
        )

        return FollowUpOutput(answer=text.strip())

    except Exception as e:
        logger.error("followup_agent_failed", error=str(e), session_id=session_id)
        return FollowUpOutput(answer="Unable to load answer. Please try again.")
