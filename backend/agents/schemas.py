from typing import Literal
from pydantic import BaseModel


# ── Resume Extraction ──────────────────────────────────────────────────────────

class ExtractedSkill(BaseModel):
    name: str
    evidence: str = ""            # which project/experience proves this skill
    confidence: str = "verified"  # verified | claimed | inferred

class ExtractedProject(BaseModel):
    name: str
    technologies: list[str] = []
    description: str = ""
    key_metrics: list[str] = []     # numbers, percentages, latency figures from description

class ExtractedEducation(BaseModel):
    institution: str = ""
    degree: str = ""
    cgpa: str = ""

class ExtractedExperience(BaseModel):
    company: str = ""
    role: str = ""
    duration: str = ""

class ResumeFacts(BaseModel):
    skills: list[ExtractedSkill] = []
    projects: list[ExtractedProject] = []
    education: list[ExtractedEducation] = []
    experience: list[ExtractedExperience] = []
    github_url: str = ""
    linkedin_url: str = ""
    total_projects: int = 0
    has_production_experience: bool = False
    has_shipped_product: bool = False
    yoe_estimate: str = ""


# ── JD Parser ─────────────────────────────────────────────────────────────────

class JDRequirements(BaseModel):
    required_skills: list[str]
    preferred_skills: list[str]
    experience_range: str          # e.g. "2-5 years"
    role_level: str                # e.g. "SDE2", "Senior"
    key_responsibilities: list[str]
    company_signals: list[str]     # signals about company culture/type


# ── Agent 1: MarketContextAgent ───────────────────────────────────────────────

class MarketContextOutput(BaseModel):
    market_norms: str
    format_expectations: str
    competitive_pool_description: str
    red_flag_triggers: list[str]
    weight_map: dict               # keys: dsa, projects, cgpa, experience, open_source, college_tier
    live_context_summary: str
    jd_requirements: JDRequirements | None = None
    confidence: Literal["HIGH", "LOW"]


# ── Agent 2: SixSecondAndTrajectoryAgent ──────────────────────────────────────

class GapSignal(BaseModel):
    gap: str
    inference_triggered: str
    severity: Literal["HIGH", "MEDIUM", "LOW"]


class SixSecondAndTrajectoryOutput(BaseModel):
    # Part A — Six-second scan
    remembered: list[str]          # what recruiter recalls after 6 seconds
    missed: list[str]              # what didn't register
    first_impression: str
    survived_cut_assessment: str   # YES / NO / MAYBE with reasoning
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"  # how confident in the scan assessment

    # Part B — Career trajectory
    career_story: str
    progression_signal: str
    gaps: list[GapSignal]
    promotion_velocity: str
    skill_evolution: str
    fresher_note: str = ""          # populated only if Student/Fresher
    github_signal: str = ""           # what the GitHub profile signals (if available)
    linkedin_signal: str = ""         # what the LinkedIn profile signals (if available)


# ── Agent 3: RedFlagAgent ─────────────────────────────────────────────────────

class RedFlag(BaseModel):
    flag: str
    location: str                  # exact quote from resume (≥10 chars)
    inference_chain: str           # recruiter thought process (≥50 chars, specific)
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    fix: str                       # actionable in 10 minutes (≥20 chars)
    category: Literal[
        "integrity",        # dates, claims that don't add up
        "competence",       # missing skills for the role
        "fit",              # wrong signals for this company type
        "market_specific",  # specific to this market/role combo
        "plausibility",     # claims that seem exaggerated
    ]
    jd_gap: bool                   # True if this flag is a gap vs the provided JD


class RedFlagOutput(BaseModel):
    red_flags: list[RedFlag]       # EMPTY LIST if no flags — never hallucinate
    visual_scan_notes: str         # formatting, layout, visual red flags
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"  # how confident in the flag assessment


# ── Agent 4: CompetitivePositioningAgent ──────────────────────────────────────

class PercentileEstimate(BaseModel):
    range: str                     # e.g. "35th-45th percentile"
    reasoning: str                 # must cite actual pool signals
    confidence: Literal["estimated", "calibrated"]
    # calibrated only when corpus_size >= 30


class CompetitiveOutput(BaseModel):
    strengths_vs_pool: list[str]
    weaknesses_vs_pool: list[str]
    percentile_estimate: PercentileEstimate
    expected_ctc_range: str = ""           # e.g. "₹18-24 LPA"
    highest_leverage_change: str   # one specific actionable change
    estimated_impact: str          # what that change would do to percentile
    jd_fit_score: str | None       # e.g. "7/10 — missing Kafka and system design depth"


# ── Agent 5: ReviewAgent ──────────────────────────────────────────────────────

class ReviewOutput(BaseModel):
    # TL;DR block
    tldr_shortlist_chance: str     # e.g. "Below average for this market right now"
    tldr_biggest_blocker: str      # one sentence
    tldr_fix_first: str            # one specific action
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"  # how confident in the overall review

    # Prose sections
    whats_working_section: str
    whats_hurting_section: str     # must contain inference chains
    career_story_section: str
    competitive_position_section: str
    action_plan_section: str
    jd_alignment_section: str      # populated only when JD provided

    # Follow-up questions per section (2-3 each)
    six_second_followups: list[str]
    whats_hurting_followups: list[str]
    career_story_followups: list[str]
    competitive_followups: list[str]


# ── Agent 6: FollowUpAgent ────────────────────────────────────────────────────

class FollowUpOutput(BaseModel):
    answer: str                    # 100-200 words, specific to resume and market


# ── TechnicalDepthAgent (imported from technical_depth_agent.py) ──────────────
# ProjectEvaluation and TechnicalDepthOutput are defined in technical_depth_agent.py
# Import them here for use in orchestrator
from backend.agents.technical_depth_agent import TechnicalDepthOutput, ProjectEvaluation
