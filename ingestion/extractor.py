import asyncio
import json
from enum import Enum
from pydantic import BaseModel
from backend.config import GROQ_API_KEYS
from groq import AsyncGroq

# ── Groq setup for ingestion ──────────────────────────────────────────────────
# llama-3.1-8b-instant: 30 RPM, 14400 RPD, no thinking mode overhead
# Fast, proven for extraction tasks, sufficient quality for ingestion
_keys = [k.strip() for k in GROQ_API_KEYS.split(",") if k.strip()]
_current_index = 0
_index_lock = asyncio.Lock()
INGESTION_MODEL = "llama-3.1-8b-instant"


def _get_client() -> AsyncGroq:
    return AsyncGroq(api_key=_keys[_current_index])


async def _rotate():
    async with _index_lock:
        global _current_index
        _current_index = (_current_index + 1) % len(_keys)


# ── Source tier definitions ───────────────────────────────────────────────────

class SourceTier(str, Enum):
    JOB_POSTING = "job_posting"
    RECRUITER_POST = "recruiter_post"
    SALARY_SURVEY = "salary_survey"
    DEVELOPER_COMMUNITY = "developer_community"
    TECHNICAL_BLOG = "technical_blog"
    DISCARD = "discard"


TRUST_WEIGHTS = {
    SourceTier.JOB_POSTING: 1.0,
    SourceTier.RECRUITER_POST: 0.8,
    SourceTier.SALARY_SURVEY: 0.75,
    SourceTier.DEVELOPER_COMMUNITY: 0.5,
    SourceTier.TECHNICAL_BLOG: 0.3,
    SourceTier.DISCARD: 0.0,
}


# ── HiringSignal schema ───────────────────────────────────────────────────────

class HiringSignal(BaseModel):
    signal_type: str
    skills_mentioned: list[str]
    salary_range: str | None
    sentiment: str
    trust_weight: float
    source_tier: str
    key_insight: str
    red_flag_triggers: list[str]
    format_signals: list[str]


# ── Merged classify + extract prompt ─────────────────────────────────────────
# One call instead of two — saves RPM budget, faster ingestion

MERGED_SYSTEM = """You are a hiring intelligence extractor for a resume review system.

Given a piece of text scraped from the web:

STEP 1 — Classify the source:
- job_posting: actual job description with specific requirements
- recruiter_post: recruiter post naming specific roles/requirements
- salary_survey: published salary data with real numbers
- developer_community: Reddit/Blind/community discussions with real experiences
- technical_blog: substantive blog about role skills or career paths
- discard: SEO content, no real data, promotional, generic career advice, before 2024

STEP 2 — If source_tier is "discard", return exactly: {"discard": true}

STEP 3 — Otherwise extract structured information and return ONLY valid JSON:
{
  "discard": false,
  "source_tier": "job_posting|recruiter_post|salary_survey|developer_community|technical_blog",
  "signal_type": "job_posting|salary|interview_experience|sentiment|format_norm",
  "skills_mentioned": ["skill1", "skill2"],
  "salary_range": "28-35L base" or null,
  "sentiment": "positive|cautious|negative|neutral",
  "key_insight": "One specific sentence — name companies, numbers, skills. Never generic.",
  "red_flag_triggers": ["trigger1"],
  "format_signals": ["1 page required"]
}

Rules:
- key_insight must be specific. Bad: "Companies are hiring." Good: "Zepto is hiring SDE2s in Bangalore requiring Kafka and Go, offering 28-35L base."
- skills_mentioned: only technical skills (Kafka, Go, React) — not soft skills
- No markdown, no explanation — only the JSON object"""


async def process_raw_text(
    text: str,
    role: str,
    market: str,
) -> HiringSignal | None:
    """
    Classify + extract in ONE qwen3-32b call.
    Returns HiringSignal or None if discarded/failed.
    60 RPM on Groq — ~50x faster than Gemma for ingestion.
    """
    content = text[:2000]
    prompt = f"Role context: {role} in {market}\n\nText:\n{content}"

    for attempt in range(3):
        try:
            client = _get_client()
            response = await client.chat.completions.create(
                model=INGESTION_MODEL,
                messages=[
                    {"role": "system", "content": MERGED_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=500,
                temperature=0.1,
            )

            raw = response.choices[0].message.content.strip()

            # Strip markdown if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            # Extract JSON object
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start != -1 and end > start:
                raw = raw[start:end]

            data = json.loads(raw)

            # Discard check
            if data.get("discard", False):
                return None

            source_tier_str = data.get("source_tier", "discard")
            try:
                tier = SourceTier(source_tier_str)
            except ValueError:
                tier = SourceTier.DISCARD

            if tier == SourceTier.DISCARD:
                return None

            key_insight = data.get("key_insight", "")
            if not key_insight or len(key_insight) < 20:
                return None

            return HiringSignal(
                signal_type=data.get("signal_type", "sentiment"),
                skills_mentioned=data.get("skills_mentioned", []),
                salary_range=data.get("salary_range"),
                sentiment=data.get("sentiment", "neutral"),
                trust_weight=TRUST_WEIGHTS[tier],
                source_tier=tier.value,
                key_insight=key_insight,
                red_flag_triggers=data.get("red_flag_triggers", []),
                format_signals=data.get("format_signals", []),
            )

        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "rate limit" in error_str:
                await _rotate()
                await asyncio.sleep(1)
            elif attempt == 2:
                return None
            else:
                await asyncio.sleep(0.5)

    return None
