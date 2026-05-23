import asyncio
import structlog
from backend.llm.groq_client import groq_chat
from backend.llm.gemini_client import gemini_chat, GEMINI_FLASH_LITE
from backend.llm.nvidia_nim_client import nim_chat
from backend.llm.openrouter_client import openrouter_chat

logger = structlog.get_logger()

# ── ReviewAgent fallback chain ────────────────────────────────────────────────
# llama-3.3-70b primary — 32K max output, 36K TPM with 3 keys (shared with RedFlag).
# Falls back to gpt-oss-20b (different TPM bucket), then qwen3, Gemini, NIM, OpenRouter.
REVIEW_MODEL_CHAIN = [
    ("groq",       "llama-3.3-70b-versatile"),                       # 280 tok/s, 32K output, safe at 2 concurrent
    ("groq",       "openai/gpt-oss-20b"),                            # 1000 tok/s, fits smaller prompts
    ("groq",       "qwen/qwen3-32b"),                                # 400 tok/s, separate TPM bucket
    ("gemini",     GEMINI_FLASH_LITE),                               # thinking disabled
    ("nvidia_nim", None),                                             # no daily cap
    ("openrouter", None),                                             # 50 RPD, emergency only
]


async def call_review_agent(
    messages: list[dict],
    max_tokens: int = 3000,
    session_id: str = "",
) -> tuple[str, dict]:
    """
    Try each provider in the fallback chain until one succeeds.
    Returns (response_text, metadata).
    """
    last_error = None

    for provider, model in REVIEW_MODEL_CHAIN:
        try:
            if provider == "groq":
                return await groq_chat(
                    messages=messages, model=model,
                    max_tokens=max_tokens, temperature=0.3,
                    session_id=session_id,
                )
            elif provider == "nvidia_nim":
                return await nim_chat(
                    messages=messages, max_tokens=max_tokens,
                    session_id=session_id,
                )
            elif provider == "gemini":
                prompt = _messages_to_prompt(messages)
                return await gemini_chat(
                    prompt=prompt, model=model,
                    max_tokens=max_tokens, temperature=0.3,
                    session_id=session_id,
                )
            elif provider == "openrouter":
                return await openrouter_chat(
                    messages=messages, max_tokens=max_tokens,
                    session_id=session_id,
                )

        except Exception as e:
            last_error = e
            logger.warning(
                "provider_failed_trying_next",
                provider=provider, model=model,
                error=str(e), session_id=session_id,
            )
            continue

    raise RuntimeError(f"all_providers_failed: {last_error}")


async def call_groq_8b(
    messages: list[dict],
    max_tokens: int = 1000,
    temperature: float = 0.1,
    session_id: str = "",
    agent_name: str = "groq_8b",
) -> tuple[str, dict]:
    """MarketContextAgent, DIVE distiller, JD parser, FollowUpAgent."""
    return await groq_chat(
        messages=messages, model="llama-3.1-8b-instant",
        max_tokens=max_tokens, temperature=temperature,
        session_id=session_id, agent_name=agent_name,
    )


async def call_red_flag_agent(
    prompt: str,
    max_tokens: int = 2500,
    session_id: str = "",
) -> tuple[str, dict]:
    messages = [{"role": "user", "content": prompt}]
    try:
        return await groq_chat(
            messages=messages, model="llama-3.3-70b-versatile",
            max_tokens=max_tokens, temperature=0.1,
            session_id=session_id, agent_name="red_flag_agent",
        )
    except Exception as e:
        logger.warning("red_flag_70b_failed_falling_back", error=str(e), session_id=session_id)
        return await groq_chat(
            messages=messages, model="llama-3.1-8b-instant",
            max_tokens=max_tokens, temperature=0.1,
            session_id=session_id, agent_name="red_flag_agent_fallback",
        )


async def call_technical_depth_agent(
    messages: list[dict],
    max_tokens: int = 2000,
    temperature: float = 0.2,
    session_id: str = "",
) -> tuple[str, dict]:
    """
    Non-agentic fallback path for TechnicalDepthAgent.
    The actual agentic loop (in technical_depth_agent.py) uses gpt-oss-120b.
    This is called when the agentic loop times out.
    Falls back to gpt-oss-120b for quality, then llama-3.1-8b.
    """
    try:
        return await groq_chat(
            messages=messages, model="openai/gpt-oss-120b",
            max_tokens=max_tokens, temperature=temperature, session_id=session_id,
        )
    except Exception as gpt_err:
        logger.warning("tech_depth_120b_failed_falling_back", error=str(gpt_err), session_id=session_id)
        return await groq_chat(
            messages=messages, model="llama-3.1-8b-instant",
            max_tokens=max_tokens, temperature=temperature, session_id=session_id,
        )


async def call_six_second_agent(
    messages: list[dict],
    max_tokens: int = 1000,
    temperature: float = 0.2,
    session_id: str = "",
) -> tuple[str, dict]:
    try:
        text, meta = await groq_chat(
            messages=messages, model="qwen/qwen3-32b",
            max_tokens=max_tokens, temperature=temperature,
            session_id=session_id, agent_name="six_second_agent",
        )
        if not text or not text.strip():
            raise ValueError("qwen3_32b_empty_response")
        return text, meta
    except Exception as e:
        logger.warning("six_second_primary_failed_falling_back", error=str(e), session_id=session_id)
        return await groq_chat(
            messages=messages, model="llama-3.1-8b-instant",
            max_tokens=max_tokens, temperature=temperature,
            session_id=session_id, agent_name="six_second_agent_fallback",
        )


async def call_competitive_agent(
    messages: list[dict],
    max_tokens: int = 1500,
    temperature: float = 0.2,
    session_id: str = "",
) -> tuple[str, dict]:
    try:
        return await groq_chat(
            messages=messages, model="openai/gpt-oss-20b",
            max_tokens=max_tokens, temperature=temperature,
            session_id=session_id, agent_name="competitive_agent",
        )
    except Exception as e:
        logger.warning("competitive_groq_failed_falling_back", error=str(e), session_id=session_id)
        return await nim_chat(
            messages=messages, max_tokens=max_tokens,
            temperature=temperature, session_id=session_id,
        )


def _messages_to_prompt(messages: list[dict]) -> str:
    parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts.append(f"[SYSTEM]\n{content}")
        elif role == "user":
            parts.append(f"[USER]\n{content}")
        elif role == "assistant":
            parts.append(f"[ASSISTANT]\n{content}")
    return "\n\n".join(parts)
