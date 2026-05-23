"""
Resume fact extractor — uses llama-3.1-8b-instant to extract structured facts.
Replaces the old hardcoded keyword patterns with a general-purpose LLM extractor.
Results cached in Redis by resume content hash — same resume = instant on retry.
"""

import hashlib
import json
import structlog
from backend.agents.schemas import ResumeFacts, ExtractedSkill, ExtractedProject, ExtractedEducation, ExtractedExperience
from backend.agents.json_utils import extract_json
from backend.llm.router import call_groq_8b
from backend.storage.redis_client import redis

logger = structlog.get_logger()

EXTRACTOR_CACHE_TTL = 3600  # 1 hour

EXTRACTOR_SYSTEM = """You are a resume fact extractor. Extract structured, verifiable facts from the resume text.
Return ONLY valid JSON. Output MUST be under 1200 tokens.

WRITE THE JSON IN THIS EXACT ORDER to ensure critical fields are not truncated:
1. projects, education, experience, github_url, linkedin_url, total_projects, has_production_experience, has_shipped_product, yoe_estimate
2. skills LAST (the largest field, can be truncated gently)

Output schema:
{
  "projects": [
    {"name": "ExampleProject", "technologies": ["Python", "FastAPI"], "description": "Brief description of the project", "key_metrics": ["served 10K users", "reduced latency by 40%"]}
  ],
  "education": [
    {"institution": "Example University", "degree": "B.Tech Computer Science", "cgpa": "8.5/10"}
  ],
  "experience": [
    {"company": "Example Corp", "role": "Software Engineer Intern", "duration": "Jan 2025 – Jun 2025"}
  ],
  "github_url": "",
  "linkedin_url": "",
  "total_projects": 2,
  "has_production_experience": true,
  "has_shipped_product": true,
  "yoe_estimate": "0-1 years",
  "skills": [
    {"name": "Python", "evidence": "Used in ExampleProject and AnotherProject", "confidence": "verified"}
  ]
}

RULES:
- has_production_experience: true if resume mentions deployment, production, live users, real customers, serving, multi-tenant. ANY of these = true.
- has_shipped_product: true if resume mentions shipped, delivered, launched, deployed, built end-to-end, solo-built. ANY = true.
- EXPERIENCE: Look for EXPERIENCE / WORK / INTERNSHIP headers. Extract EVERY entry including internships.
- GITHUB: Extract the github.com URL if present. Do NOT make up URLs.
- yoe_estimate: Calculate from work experience date ranges. Internships count. If <1 year, use "0-1 years".
- CONFIDENCE for skills: "verified" = appears in a project/experience description. "claimed" = only in skills list.
- key_metrics: Extract EVERY number, percentage, latency figure, throughput stat, or quantified result from project descriptions. Examples: "reduced latency from ~10s to ~3s", "95+ live analyses", "97.5% mAP", "10h→1h ingestion". Do not hallucinate metrics — only extract numbers explicitly in the resume.
"""


def _resume_hash(resume_text: str) -> str:
    return hashlib.sha256(resume_text[:2000].encode()).hexdigest()[:16]


def _cache_key(resume_hash: str) -> str:
    return f"resume_facts:{resume_hash}"


async def extract_resume_facts(resume_text: str, session_id: str = "") -> ResumeFacts:
    """Extract structured facts from resume text. Cached by content hash."""
    rhash = _resume_hash(resume_text)
    key = _cache_key(rhash)

    cached = redis.get(key)
    if cached:
        logger.info("resume_facts_cache_hit", hash=rhash, session_id=session_id)
        try:
            return ResumeFacts(**json.loads(cached))
        except Exception:
            pass  # corrupt cache, re-extract

    logger.info("resume_facts_extracting", hash=rhash, session_id=session_id)

    messages = [
        {"role": "system", "content": EXTRACTOR_SYSTEM},
        {"role": "user", "content": f"<resume>\n{resume_text[:8000]}\n</resume>\n\nExtract the structured facts as JSON."},
    ]

    try:
        text, _ = await call_groq_8b(
            messages, max_tokens=1200, temperature=0.1,
            session_id=session_id, agent_name="resume_extractor",
        )
        data = extract_json(text)

        skills = [_parse_skill(s) for s in (data.get("skills") or [])]
        projects = [_parse_project(p) for p in (data.get("projects") or [])]
        edu_list = data.get("education") or []
        exp_list = data.get("experience") or []

        facts = ResumeFacts(
            skills=[s for s in skills if s is not None],
            projects=[p for p in projects if p is not None],
            education=[ExtractedEducation(**e) for e in edu_list if isinstance(e, dict)],
            experience=[ExtractedExperience(**e) for e in exp_list if isinstance(e, dict)],
            github_url=data.get("github_url", ""),
            linkedin_url=data.get("linkedin_url", ""),
            total_projects=data.get("total_projects", 0),
            has_production_experience=data.get("has_production_experience", False),
            has_shipped_product=data.get("has_shipped_product", False),
            yoe_estimate=data.get("yoe_estimate", ""),
        )

        redis.setex(key, EXTRACTOR_CACHE_TTL, facts.model_dump_json())
        logger.info("resume_facts_extracted", hash=rhash,
                    skills=len(facts.skills), projects=len(facts.projects),
                    session_id=session_id)

        return facts

    except Exception as e:
        logger.warning("resume_facts_extraction_failed", error=str(e), session_id=session_id)
        return ResumeFacts()


def _parse_skill(data: dict) -> ExtractedSkill | None:
    if not isinstance(data, dict) or not data.get("name"):
        return None
    return ExtractedSkill(
        name=data["name"],
        evidence=data.get("evidence", ""),
        confidence=data.get("confidence", "claimed"),
    )


def _parse_project(data: dict) -> ExtractedProject | None:
    if not isinstance(data, dict) or not data.get("name"):
        return None
    return ExtractedProject(
        name=data["name"],
        technologies=data.get("technologies", []),
        description=data.get("description", ""),
        key_metrics=data.get("key_metrics", []) or [],
    )


def facts_to_prompt(facts: ResumeFacts) -> str:
    """Convert extracted facts into a structured prompt section for SYSTEM injection."""
    if not facts.skills and not facts.projects:
        return ""

    lines = ["RESUME FACTS — VERIFIED BY EXTRACTOR (DO NOT CONTRADICT THESE):\n"]

    verified = [s for s in facts.skills if s.confidence == "verified"]
    claimed = [s for s in facts.skills if s.confidence == "claimed"]

    if verified:
        lines.append(f"Verified skills (demonstrated in projects): {', '.join(s.name for s in verified)}")
    if claimed:
        lines.append(f"Claimed skills (listed but no project evidence found): {', '.join(s.name for s in claimed)}")

    if facts.projects:
        lines.append(f"Projects found: {', '.join(p.name for p in facts.projects)}")
        for p in facts.projects:
            if p.technologies:
                lines.append(f"  {p.name}: {', '.join(p.technologies)}")
            if p.key_metrics:
                lines.append(f"    Metrics from resume: {'; '.join(p.key_metrics)}")

    if facts.education:
        for e in facts.education:
            parts = [e.institution, e.degree]
            if e.cgpa:
                parts.append(f"CGPA: {e.cgpa}")
            lines.append("Education: " + ", ".join(p for p in parts if p))

    if facts.experience:
        for e in facts.experience:
            lines.append(f"Experience: {e.role} at {e.company} ({e.duration})")

    if facts.has_production_experience:
        lines.append("This resume CLAIMS production experience (deployment, real users, live system).")
    if facts.has_shipped_product:
        lines.append("This resume CLAIMS shipped product(s) to real users.")

    if facts.github_url:
        lines.append(f"GitHub: {facts.github_url}")
    if facts.linkedin_url:
        lines.append(f"LinkedIn: {facts.linkedin_url}")

    return "\n".join(lines)
