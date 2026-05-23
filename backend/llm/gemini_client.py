import asyncio
import structlog
from google import genai
from google.genai import types
from backend.config import GEMINI_API_KEYS
from backend.llm.circuit_breaker import gemini_circuit

logger = structlog.get_logger()

_keys = [k.strip() for k in GEMINI_API_KEYS.split(",") if k.strip()]
_current_index = 0
_index_lock = asyncio.Lock()

# Model IDs
GEMINI_FLASH_LITE = "gemini-2.5-flash-lite"   # 159 tok/s, thinking disabled
GEMINI_FLASH = "gemini-2.5-flash"             # 112 tok/s, thinking disabled
GEMMA_4_26B = "gemma-4-26b-a4b-it"            # ingestion only
GEMMA_27B = GEMINI_FLASH_LITE                  # alias used by router


def _get_client() -> genai.Client:
    return genai.Client(api_key=_keys[_current_index])


async def _rotate() -> None:
    async with _index_lock:
        global _current_index
        _current_index = (_current_index + 1) % len(_keys)


async def gemini_chat(
    prompt: str,
    model: str = GEMMA_27B,
    max_tokens: int = 1500,
    temperature: float = 0.1,
    session_id: str = "",
) -> tuple[str, dict]:
    """
    Make a Gemini/Gemma generation call with circuit breaker and key rotation.
    Returns (response_text, metadata).
    """
    if gemini_circuit.should_skip():
        raise RuntimeError("gemini_circuit_open")

    backoff = [2, 4, 8]

    for attempt in range(3):
        try:
            client = _get_client()
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )

            text = response.text.strip()
            gemini_circuit.record_success()

            metadata = {
                "provider": "gemini",
                "model": model,
                "key_index": _current_index,
            }

            return text, metadata

        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "rate limit" in error_str or "quota" in error_str:
                logger.warning("gemini_rate_limit", model=model, attempt=attempt, session_id=session_id)
                await _rotate()
                if attempt < 2:
                    await asyncio.sleep(backoff[attempt])
            elif "404" in error_str or "not found" in error_str:
                # Model not found — don't open circuit breaker, just raise immediately
                logger.error("gemini_model_not_found", model=model, error=str(e))
                raise
            else:
                gemini_circuit.record_failure()
                logger.error("gemini_error", error=str(e), model=model, session_id=session_id)
                if attempt < 2:
                    await asyncio.sleep(backoff[attempt])
                else:
                    raise

    raise RuntimeError("gemini_all_retries_exhausted")
