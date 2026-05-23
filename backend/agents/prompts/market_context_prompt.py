"""
Market context prompt — full weight_map rules for all experience levels.
"""


def get_market_context_task() -> str:
    return """
Analyse the provided market intelligence and produce a structured calibration object.
Your job is to INTERPRET the distilled market context — not fetch anything new.
DIVE has already retrieved the relevant signals. You synthesise them.

Output a JSON object with these fields:
{
  "market_norms": "What hiring looks like right now for this combination",
  "format_expectations": "Resume format norms for this market (length, photo, sections)",
  "competitive_pool_description": "Who else is applying — what does the typical applicant look like",
  "red_flag_triggers": ["list of things that get resumes binned for this specific combo"],
  "weight_map": {
    "dsa": 0.0-1.0,
    "projects": 0.0-1.0,
    "cgpa": 0.0-1.0,
    "experience": 0.0-1.0,
    "open_source": 0.0-1.0,
    "college_tier": 0.0-1.0
  },
  "live_context_summary": "2-3 sentences on current market state from the signals",
  "confidence": "HIGH or LOW"
}

Weight map — synthesise from the signals:
- dsa: how much does algorithmic problem-solving matter for THIS role in THIS market?
- projects: how much does shipped work / portfolio matter?
- cgpa: how much do academics matter at THIS experience level?
- experience: how much does prior work history matter?
- open_source: how much do public contributions / GitHub matter?
- college_tier: how much does university reputation matter in THIS market?

Use the market signals as your primary source. If signals say "LLM orchestration and RAG are top skills", then projects and open_source should be weighted high and dsa lower. If signals say "DSA + system design are critical", then dsa should be high.

Do NOT apply generic SDE defaults. The weights must reflect what the retrieved signals say about THIS specific role + company + market combo.

Set confidence to LOW if market signals are thin or contradictory.
""".strip()
