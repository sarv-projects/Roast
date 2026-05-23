"""
Competitive positioning prompt — market-aware salary bands.
"""


def get_competitive_task(role: str, company_type: str, market: str) -> str:
    return f"""
Assess where this resume sits in the actual applicant pool for {role} at {company_type} in {market}.

You have access to:
- Market context (what the pool looks like)
- Breaking signal (what changed this week)
- Anonymised corpus signals (if available — real opted-in data from previous analyses)

CRITICAL — PERCENTILE CALIBRATION:
Calibrate the percentile against applicants at the SAME experience level, not all applicants.
- If the candidate is a Student/Fresher or Junior (0-2 years): compare against OTHER freshers/students applying for this role
- A fresher with production experience, shipped projects, and GitHub presence should be 60th-80th percentile among freshers
- Do NOT compare a fresher against senior engineers with 5+ years experience
- The percentile must reflect realistic competition at this experience level
- You MUST always provide a percentile estimate. If corpus signals are thin, use your knowledge of the {market} hiring market for {role} at {company_type} to estimate. Never return "Unable to estimate" — always give a range with reasoning.

SALARY BANDS — MANDATORY:
You MUST always include expected_ctc_range. Use current {market} compensation norms for {role} at {company_type}.
The role calibration context already contains accurate salary bands for this combination — use those.
Do NOT use generic India LPA bands for non-India markets.
For India: express in LPA (e.g. ₹18-24 LPA). For USA: express in USD/year. For UAE: AED or USD. For UK: GBP/year. For Singapore: SGD/year.
Adjust based on experience level and percentile position — a Senior at 70th percentile earns more than a Fresher at 70th percentile.

LEVERAGE CHANGE CALIBRATION by experience level:
- Student/Fresher: usually a quick win — add GitHub link, quantify one project metric, fix hedge words
- Junior (0-2 YOE): usually about proving ownership — rewrite bullets to show "built X" not "contributed to X"
- Mid-level (2-5 YOE): usually about system design evidence — does the resume show architectural decisions, not just implementation?
- Senior (5-8 YOE): usually about scope signals — does the resume show cross-team impact, not just individual features?
- Staff/Principal (8+ YOE): usually about org impact and external reputation — conference talks, open-source leadership, technical strategy

Output:
{{
  "strengths_vs_pool": ["specific strengths compared to typical applicants AT THE SAME LEVEL"],
  "weaknesses_vs_pool": ["specific weaknesses compared to typical applicants AT THE SAME LEVEL"],
  "percentile_estimate": {{
    "range": "e.g. 65th-75th percentile among fresher/junior applicants",
    "reasoning": "must cite actual pool signals and specify the comparison group",
    "confidence": "estimated or calibrated"
  }},
  "expected_ctc_range": "e.g. ₹18-24 LPA — based on current {market} market for this role/company type",
  "highest_leverage_change": "ONE specific actionable change that would move the percentile most",
  "estimated_impact": "what that change would do",
  "jd_fit_score": "e.g. 7/10 — missing Kafka and system design depth (only if JD provided, else null)"
}}

highest_leverage_change must be ONE specific thing calibrated to the experience level. Not generic advice.
""".strip()
