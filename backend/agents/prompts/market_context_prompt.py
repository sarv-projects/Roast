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

Weight map rules — apply ALL that match, most specific rule wins:

EXPERIENCE LEVEL RULES (apply first):
- Student / Fresher: cgpa >= 0.6, college_tier >= 0.6, experience = 0.0, projects >= 0.7
- Junior (0-2 YOE): cgpa = 0.4, college_tier = 0.3, experience = 0.5, projects >= 0.75
- Mid-level (2-5 YOE): cgpa = 0.2, college_tier = 0.1, experience = 0.8, projects = 0.6
- Senior (5-8 YOE): cgpa = 0.1, college_tier = 0.05, experience = 0.9, projects = 0.4
- Staff / Principal (8+ YOE): cgpa = 0.0, college_tier = 0.0, experience = 0.95, projects = 0.3

COMPANY TYPE RULES (apply on top of experience rules):
- FAANG / Big Tech: dsa >= 0.9 (non-negotiable), open_source = 0.5
- Indian Service Company: cgpa += 0.15 (add to experience-level value), dsa = 0.3 or lower, college_tier += 0.1
- Early Stage Startup: dsa = 0.2-0.4, projects >= 0.85, open_source = 0.6
- Indian Product Company: dsa = 0.6-0.8, projects >= 0.75
- MNC India (Non-FAANG): dsa = 0.5, cgpa += 0.1 for freshers
- Semiconductor / Hardware: dsa = 0.3, projects >= 0.8 (hardware projects), open_source = 0.2
- Consulting / IB: dsa = 0.2, projects = 0.5, cgpa += 0.1

MARKET RULES:
- USA market: college_tier = 0.2 (less important than India), open_source = 0.6
- Singapore / UK / UAE: similar to USA — college_tier less important, open_source more important

Set confidence to LOW if market signals are thin or contradictory.
""".strip()
