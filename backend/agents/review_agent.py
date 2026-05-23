import json
import re
import structlog
from backend.agents.schemas import (
    ReviewOutput, MarketContextOutput, RedFlagOutput,
    SixSecondAndTrajectoryOutput, CompetitiveOutput, JDRequirements,
    TechnicalDepthOutput, ResumeFacts
)
from backend.agents.prompts.template import build_system_prompt
from backend.agents.prompts.review_prompt import get_review_task
from backend.llm.router import call_review_agent
from backend.agents.json_utils import extract_json

logger = structlog.get_logger()

MIN_WORDS = 250
MAX_WORDS = 2000

PROSE_FIELDS = [
    "whats_working_section",
    "whats_hurting_section",
    "career_story_section",
    "competitive_position_section",
    "action_plan_section",
]


def _count_words(review: ReviewOutput) -> int:
    return sum(
        len(getattr(review, f, "").split())
        for f in PROSE_FIELDS
    )


def _passes_quality_gate(review: ReviewOutput) -> tuple[bool, str]:
    total = _count_words(review)
    if total < MIN_WORDS:
        return False, f"too_short:{total}"
    if total > MAX_WORDS:
        return False, f"too_long:{total}"

    # Follow-up questions must exist and be specific (not generic filler)
    for field in ["six_second_followups", "whats_hurting_followups",
                  "career_story_followups", "competitive_followups"]:
        followups = getattr(review, field, [])
        if not followups:
            return False, f"missing_followups:{field}"
        # Each follow-up must be at least 25 chars — filters out "Tell me more." etc.
        for q in followups:
            if len(q.strip()) < 25:
                return False, f"followup_too_generic:{field}:{q[:30]}"

    # whats_hurting_section must contain at least one inference chain (→ arrow)
    if review.whats_hurting_section:
        chains = re.findall(r'→|->|→', review.whats_hurting_section)
        if len(chains) < 1:
            return False, "no_inference_chains_in_hurting_section"

    # action_plan_section must be substantive
    action_words = len(review.action_plan_section.split())
    if action_words < 60:
        return False, f"action_plan_too_short:{action_words}"

    return True, "ok"


def _build_upstream_summary(
    market_context: MarketContextOutput,
    red_flags: RedFlagOutput,
    six_second: SixSecondAndTrajectoryOutput,
    competitive: CompetitiveOutput,
    jd_requirements: JDRequirements | None,
    technical_depth: TechnicalDepthOutput | None = None,
) -> str:
    """
    Deterministic Python function — no LLM.
    Concatenates upstream outputs into one structured input for ReviewAgent.
    Technical depth evaluation leads — recruiter inference is supporting context.
    """
    high_flags = [f for f in red_flags.red_flags if f.severity == "HIGH"]
    other_flags = [f for f in red_flags.red_flags if f.severity != "HIGH"]

    flags_text = ""
    if high_flags:
        flags_text += "HIGH SEVERITY FLAGS:\n"
        for f in high_flags:
            flags_text += f"- {f.flag}\n  Quote: \"{f.location}\"\n  Inference: {f.inference_chain}\n  Fix: {f.fix}\n\n"
    if other_flags:
        flags_text += "OTHER FLAGS:\n"
        for f in other_flags[:5]:
            flags_text += f"- [{f.severity}] {f.flag} | Fix: {f.fix}\n"

    jd_text = ""
    if jd_requirements:
        jd_text = f"""
JD REQUIREMENTS:
Required skills: {', '.join(jd_requirements.required_skills)}
Preferred skills: {', '.join(jd_requirements.preferred_skills)}
Experience range: {jd_requirements.experience_range}
"""

    # Technical depth section — leads the summary
    tech_text = ""
    if technical_depth and technical_depth.project_evaluations:
        tech_text = "TECHNICAL DEPTH EVALUATION:\n"
        tech_text += f"Overall: {technical_depth.overall_technical_level}\n"
        tech_text += f"Most differentiated signal: {technical_depth.most_differentiated_signal}\n"
        tech_text += f"Biggest technical gap: {technical_depth.biggest_technical_gap}\n"
        tech_text += f"Communication gap: {technical_depth.communication_gap}\n"
        tech_text += f"Honest summary: {technical_depth.honest_summary}\n"
        if technical_depth.unverified_skills:
            tech_text += f"UNVERIFIED SKILLS (listed but no project evidence): {', '.join(technical_depth.unverified_skills)}\n"
        tech_text += "\n"
        tech_text += "PROJECT EVALUATIONS:\n"
        for p in technical_depth.project_evaluations:
            tech_text += f"\n{p.name} [{p.difficulty_level.upper()}]:\n"
            tech_text += f"  Proves: {p.what_it_proves}\n"
            tech_text += f"  Strongest signal: {p.strongest_signal}\n"
            tech_text += f"  Missing: {p.what_is_missing}\n"
            tech_text += f"  Resume vs reality: {p.resume_vs_reality}\n"

    return f"""{tech_text}
MARKET CONTEXT:
Sentiment: {market_context.live_context_summary}
Weight map: {json.dumps(market_context.weight_map)}
Format expectations: {market_context.format_expectations}
Competitive pool: {market_context.competitive_pool_description}

SIX-SECOND SCAN (how a non-technical recruiter sees this):
Survived cut: {six_second.survived_cut_assessment}
First impression: {six_second.first_impression}
Remembered: {', '.join(six_second.remembered[:3])}
Career story: {six_second.career_story}
Progression: {six_second.progression_signal}

RED FLAGS (recruiter perspective):
{flags_text or 'No significant red flags found.'}
Visual scan: {red_flags.visual_scan_notes}

COMPETITIVE POSITION:
Percentile: {competitive.percentile_estimate.range} ({competitive.percentile_estimate.confidence})
Reasoning: {competitive.percentile_estimate.reasoning}
Expected CTC range: {competitive.expected_ctc_range or 'Not estimated'}
Highest leverage change: {competitive.highest_leverage_change}
{jd_text}"""


async def run_review_agent(
    resume_text: str,
    market_context: MarketContextOutput,
    red_flags: RedFlagOutput,
    six_second: SixSecondAndTrajectoryOutput,
    competitive: CompetitiveOutput,
    role: str,
    company_type: str,
    market: str,
    experience_level: str,
    user_context: str = "",
    jd_requirements: JDRequirements | None = None,
    technical_depth: TechnicalDepthOutput | None = None,
    resume_facts: ResumeFacts | None = None,
    session_id: str = "",
) -> ReviewOutput:
    """
    Agent 5 — runs alone last.
    Writes the complete flowing review from all upstream outputs.
    Uses full fallback chain with quality gate.
    """
    task = get_review_task(market=market, company_type=company_type, experience_level=experience_level)

    # Inject extracted resume facts into SYSTEM prompt so the LLM respects them
    from backend.agents.resume_extractor import facts_to_prompt
    facts_section = facts_to_prompt(resume_facts) if resume_facts else ""
    agent_constraints = ""
    if facts_section:
        agent_constraints = f"{facts_section}\n\nCRITICAL: The facts above were extracted from the actual resume. Do NOT contradict them."

    system = build_system_prompt(
        role=role,
        company_type=company_type,
        market=market,
        experience_level=experience_level,
        agent_task=task,
        agent_output_rules="Return only valid JSON matching the schema. No markdown. No explanation.",
        agent_specific_constraints=agent_constraints,
    )

    upstream = _build_upstream_summary(
        market_context, red_flags, six_second, competitive, jd_requirements, technical_depth
    )

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"""<resume>
{resume_text[:8000]}
</resume>

UPSTREAM ANALYSIS:
{upstream}

USER CONTEXT: {user_context or 'None provided'}

Write the complete review JSON.""",
        },
    ]

    last_error = None

    # Try up to 2 times per provider (quality gate retry)
    for attempt in range(2):
        try:
            # Give more tokens on retry — first attempt may have truncated
            attempt_max_tokens = 3000 if attempt == 0 else 4000
            text, meta = await call_review_agent(
                messages=messages,
                max_tokens=attempt_max_tokens,
                session_id=session_id,
            )

            # Extract + repair JSON
            import re
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
            data = extract_json(text)

            # Ensure all required fields exist with defaults
            for field in ["jd_alignment_section"]:
                if field not in data:
                    data[field] = ""
            if "confidence" not in data or data["confidence"] not in ("HIGH", "MEDIUM", "LOW"):
                data["confidence"] = "MEDIUM"
            for field in ["six_second_followups", "whats_hurting_followups",
                          "career_story_followups", "competitive_followups"]:
                if field not in data or not data[field]:
                    data[field] = ["Tell me more about this."]

            # Coerce list fields to strings if model returns arrays
            for field in ["whats_working_section", "whats_hurting_section",
                          "career_story_section", "competitive_position_section",
                          "action_plan_section", "jd_alignment_section",
                          "tldr_shortlist_chance", "tldr_biggest_blocker", "tldr_fix_first"]:
                if isinstance(data.get(field), list):
                    data[field] = " ".join(str(x) for x in data[field])
                elif data.get(field) is None:
                    data[field] = ""

            review = ReviewOutput(**data)

            # Quality gate
            passed, reason = _passes_quality_gate(review)
            if not passed:
                logger.warning(
                    "review_quality_gate_failed",
                    reason=reason,
                    attempt=attempt,
                    session_id=session_id,
                )
                if attempt == 0:
                    # Specific retry instruction based on failure reason
                    if "no_inference_chains" in reason:
                        retry_instruction = (
                            "The review failed because whats_hurting_section has no inference chains. "
                            "EVERY weakness MUST use this exact format: "
                            "\"Recruiter sees [exact quote] → assumes [specific assumption] → decides [concrete outcome]\". "
                            "Rewrite whats_hurting_section with at least 3 inference chains using → arrows. "
                            "Also ensure career_story_section is at least 120 words."
                        )
                    elif "too_short" in reason:
                        retry_instruction = (
                            f"The review failed quality check: {reason}. "
                            "Rewrite with 600-1200 words across all five prose sections. "
                            "career_story_section and competitive_position_section must each be at least 120 words."
                        )
                    elif "action_plan_too_short" in reason:
                        retry_instruction = (
                            "The action_plan_section is too short. "
                            "Rewrite it with 3-5 specific actions, each with exact rewrites, expected impact, and time required. "
                            "Minimum 80 words."
                        )
                    elif "followup_too_generic" in reason:
                        retry_instruction = (
                            "Follow-up questions are too generic. "
                            "Each follow-up MUST mention a specific project name, skill, or decision from this resume. "
                            "No generic questions like 'tell me more' or 'can you elaborate'."
                        )
                    else:
                        retry_instruction = (
                            f"The review failed quality check: {reason}. "
                            "Rewrite with 600-1200 words. Ensure all sections are complete."
                        )

                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "user", "content": retry_instruction})
                    continue
                # Second attempt also failed — use what we have
                logger.warning("review_quality_gate_failed_both_attempts", session_id=session_id)

            logger.info(
                "review_agent_complete",
                session_id=session_id,
                word_count=_count_words(review),
                provider=meta.get("provider"),
                model=meta.get("model"),
                prompt_version=f"v1:{market}:{company_type}",
            )

            return review

        except Exception as e:
            last_error = e
            logger.error("review_agent_attempt_failed", error=str(e), attempt=attempt, session_id=session_id)

    # All attempts failed — assemble partial review from upstream
    logger.error("review_agent_all_failed", error=str(last_error), session_id=session_id)
    return _assemble_partial_review(six_second, red_flags, competitive, market_context)


def _assemble_partial_review(
    six_second: SixSecondAndTrajectoryOutput,
    red_flags: RedFlagOutput,
    competitive: CompetitiveOutput,
    market_context: MarketContextOutput,
) -> ReviewOutput:
    """
    Last resort — assemble a basic review from upstream outputs
    when ReviewAgent completely fails.
    """
    high_flags = [f for f in red_flags.red_flags if f.severity == "HIGH"]
    flag_text = " ".join([f.flag for f in high_flags[:3]]) if high_flags else "No critical issues found."

    return ReviewOutput(
        tldr_shortlist_chance=competitive.percentile_estimate.range,
        tldr_biggest_blocker=flag_text,
        tldr_fix_first=competitive.highest_leverage_change,
        confidence="LOW",
        whats_working_section=" ".join(competitive.strengths_vs_pool[:2]),
        whats_hurting_section=" ".join([f.inference_chain for f in high_flags[:2]]),
        career_story_section=six_second.career_story,
        competitive_position_section=competitive.percentile_estimate.reasoning,
        action_plan_section=competitive.highest_leverage_change,
        jd_alignment_section="",
        six_second_followups=["What can I improve about my first impression?"],
        whats_hurting_followups=["How do I fix the biggest red flag?"],
        career_story_followups=["How do I improve my career narrative?"],
        competitive_followups=["What would move me to the next percentile?"],
    )
