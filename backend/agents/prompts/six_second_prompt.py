"""
Six-second scan + career trajectory prompt — company_type and market aware.
"""


def get_six_second_task(company_type: str, market: str) -> str:
    return f"""
You perform two separate analyses but return ONE combined JSON object.

SCAN CALIBRATION for {company_type} in {market}:
Different recruiters scan for completely different things. Apply the right lens:
- Indian Service Company: recruiter scanning for CGPA (≥6.5), college name, no backlogs, relevant certifications. Brand names on education section matter more than project names. Volume screening — 80% rejection in first 10 seconds.
- FAANG / Big Tech: recruiter scanning for recognised company names in work history, title progression, and any "impact at scale" numbers. College tier matters for new grads. DSA signal in projects.
- Indian Product Company / Startup: recruiter scanning for shipped product names, GitHub link, recognisable startup names in work history. Ownership signals. CGPA matters moderately for freshers (<1 YOE) but fades quickly — almost irrelevant by 2+ YOE.
- MNC India (Non-FAANG): recruiter scanning for domain certifications (AWS, Azure, SAP), enterprise stack signals, CGPA for freshers.
- Semiconductor / Hardware: recruiter scanning for specific chip families, protocols (CAN, SPI, I2C), RTOS names. GitHub absence is normal — proprietary firmware.
- Consulting / IB: recruiter scanning for college tier, analytical project signals, communication clarity in bullet writing.
- USA market: expect 1-page resume. Photo is an instant yellow flag. Quantified numbers in first 3 bullets expected.
- UAE / Singapore / UK: international format norms apply. Concise, achievement-focused.

PART A — SIX-SECOND RECRUITER SCAN:
Simulate the F-pattern scan a recruiter does in the first 6 seconds.
Write from the recruiter's perspective in second person.
Apply the SCAN CALIBRATION above — what THIS recruiter at THIS company type looks for.

Timeline:
0-1s: Name, current title, current company. Recognised brand or unknown?
1-2s: Most recent job title and company again. Relevant title?
2-3s: Second job OR education header if fresher. Pattern? College?
3-5s: Company names, dates, titles down left column. Total YOE estimate. Gaps?
5-6s: Any bold text, standout numbers, visually prominent terms. Red flag?
Decision: MAYBE pile (~20%) or NO pile (~80%)

PART B — CAREER TRAJECTORY:
Read the full resume and analyse the career story.

Return ONE JSON object combining both parts:
{{
  "remembered": ["what recruiter recalls after 6 seconds — specific to {company_type} scan criteria"],
  "missed": ["what didn't register — specific to {company_type} scan criteria"],
  "first_impression": "one sentence gut reaction from a {company_type} recruiter's perspective",
  "survived_cut_assessment": "YES/NO/MAYBE with one sentence reasoning",
  "confidence": "HIGH if resume is clear and scan is decisive, MEDIUM if scan is ambiguous, LOW if resume is too thin to scan properly",
  "career_story": "2-3 sentences on the narrative",
  "progression_signal": "growing/stagnating/declining with evidence",
  "gaps": [{{
    "gap": "description",
    "inference_triggered": "what recruiter assumes",
    "severity": "HIGH/MEDIUM/LOW"
  }}],
  "promotion_velocity": "fast/normal/slow with evidence",
  "skill_evolution": "deeper or wider?",
  "fresher_note": "for Student/Fresher only, else empty string",
  "github_signal": "what GitHub signals if URL provided, else empty string",
  "linkedin_signal": "what LinkedIn signals if URL provided, else empty string"
}}

Return ONLY the JSON object. No explanation. No markdown.
""".strip()
