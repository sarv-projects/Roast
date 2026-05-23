import asyncio
import httpx
import structlog
from backend.config import OPENROUTER_API_KEY
from backend.llm.circuit_breaker import openrouter_circuit

logger = structlog.get_logger()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "meta-llama/llama-3.3-70b:free"  # 50 RPD — last resort only


async def openrouter_chat(
    messages: list[dict],
    max_tokens: int = 1500,
    temperature: float = 0.3,
    session_id: str = "",
) -> tuple[str, dict]:
    """
    Last resort fallback. 50 RPD, ~25s latency.
    Only called when all other providers are exhausted.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError("openrouter_not_configured")

    if openrouter_circuit.should_skip():
        raise RuntimeError("openrouter_circuit_open")

    backoff = [2, 4, 8]

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=40) as client:
                response = await client.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://roast.dev",
                    },
                    json={
                        "model": OPENROUTER_MODEL,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    },
                )
                response.raise_for_status()
                data = response.json()
                text = data["choices"][0]["message"]["content"].strip()

                openrouter_circuit.record_success()

                return text, {
                    "provider": "openrouter",
                    "model": OPENROUTER_MODEL,
                }

        except Exception as e:
            err = str(e).lower()
            if "429" in err or "rate limit" in err or "402" in err:
                logger.warning("openrouter_rate_limit", attempt=attempt, session_id=session_id)
                if attempt < 2:
                    await asyncio.sleep(backoff[attempt])
                    continue
            else:
                openrouter_circuit.record_failure()
                logger.error("openrouter_error", error=str(e), session_id=session_id)
                if attempt < 2:
                    await asyncio.sleep(backoff[attempt])
                else:
                    raise

    raise RuntimeError("openrouter_all_retries_exhausted")
