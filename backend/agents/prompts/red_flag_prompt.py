"""
Red flag hunting prompt — self_sabotage defined, 9 hunting categories.
"""


def get_red_flag_task(role: str, company_type: str, market: str) -> str:
    return f"""
Hunt for red flags in this resume. You also perform the visual scan.

PART A — RED FLAGS:
Find things that would get this resume binned by a recruiter at {role} level in {company_type} in {market}.

HUNT SPECIFICALLY FOR THESE — they are the most common and most damaging:

1. HEDGE WORDS that undermine real work:
   "near-production", "attempted to", "worked on", "helped with", "contributed to", "exposure to"
   If the candidate actually shipped something, these words make it sound like they didn't.
   Flag every instance. The fix is always: replace with what actually happened.

2. UNVERIFIED SKILLS — skills listed with zero project evidence:
   If a skill appears in the skills section but no project demonstrates it, flag it.
   These are interview traps. Interviewers will ask. If the candidate can't answer, it damages credibility.

3. MISSING CONTACT SIGNALS for a job-seeking candidate:
   No LinkedIn when actively job-seeking = invisible to inbound sourcing.
   No portfolio link when projects exist publicly = missed opportunity.

4. CGPA consequences — be specific about which companies auto-filter:
   Below 7.5: Cisco, Walmart Global Tech, some MNC AI labs use ATS cutoffs
   Below 8.0: Some FAANG-adjacent companies
   Above 7.5 but below 8.0: Flag only for specific company types, not universally
   NOTE: CGPA is only relevant for Student/Fresher and Junior levels. Do NOT flag CGPA for Senior (5+ YOE) or Staff/Principal.

5. PROFILE SUMMARY that buries the lead:
   If the most impressive thing (production deployment, real users, shipped system) is not in the first 2 lines, flag it.
   The fix is a rewrite — provide the rewritten summary.

6. RESPONSIBILITY WITHOUT OUTCOME:
   "Responsible for X", "Led a team of Y people", "Managed Z" with no result stated.
   Flag every instance. The fix: replace with what was actually delivered and measured.
   Example fix: "Responsible for backend API" → "Built and owned the backend API serving 50K daily requests, reducing p99 latency from 800ms to 120ms"

7. DATE ARITHMETIC:
   Check: do all employment dates add up? Overlapping roles? Unexplained gaps?
   Very short tenures (<3 months) hidden by month-only dates?
   Flag any date inconsistency with the specific dates that don't add up.
   NOTE: Do NOT flag dates as suspicious without checking the current date in the system prompt context.

8. HIDDEN CGPA (Student/Fresher only):
   If experience_level is Student/Fresher and no CGPA is shown anywhere, flag it.
   A missing CGPA reads as a low one to every recruiter. If it's 8+, show it. If it's below 6.5, hiding it signals the candidate knows it's a liability.
   Only apply this flag for Student/Fresher level — not for experienced candidates.

9. GENERIC SUMMARY / FILLER LANGUAGE:
    "Passionate about technology", "enthusiastic learner", "results-oriented professional",
    "seeking challenging opportunities", "team player with strong communication skills"
    These add zero information and waste the most-read section of the resume.
    Flag and provide a specific rewrite based on the candidate's actual strongest signal.
    ALSO: detect AI-generated filler — overly polished bullet points with no real specifics,
    generic sentence structures that read like ChatGPT output, all bullet points starting
    with the same past-tense verb pattern. Flag with "possible AI-generated text" note.

10. ATS KEYWORD GAPS:
    Cross-reference the resume text against expected keywords for {role} at {company_type}.
    If critical keywords from the expected stack are entirely absent, flag them.
    This is the #1 reason Indian resumes never reach a human — ATS filters by keyword.
    For example: a DevOps resume that never mentions "Docker" or "Kubernetes" will
    be auto-rejected by most ATS systems before a recruiter sees it.
    Only flag if at least 2 critical keywords are missing.

11. ROLE-SPECIFIC MISTAKES:
    Apply role-specific checks based on {role} at {company_type}:
    
    - SDE / Full Stack / Backend: missing GitHub is a MINOR flag (not major — many 
      Indian SDEs don't have active GitHub). Missing DSA signal is a MAJOR flag 
      for product companies. Listing every language ever used ("Python, Java, C++, 
      Go, Rust, JavaScript, TypeScript, Kotlin, PHP") without depth signals is a 
      MEDIUM flag — it reads as keyword stuffing.
    
    - AI Agentic Engineer / AI/ML Engineer: no GitHub or Hugging Face link is a 
      MAJOR flag — AI work is inherently public. Colab notebook vs shipped product 
      — flag if the resume implies product work but links only to notebooks.
      No mention of model evaluation metrics is a MEDIUM flag.
    
    - DevOps / SRE / Platform Engineer: missing metrics (uptime, latency, incident 
      counts) is a HIGH flag — DevOps is measured by numbers. Listing every CI/CD 
      tool without showing what they were used for is a MEDIUM flag. No mention 
      of on-call or incident response for mid-level+ is a MEDIUM flag.
    
    - Product Manager: missing product metrics (DAU, retention, conversion) is a 
      HIGH flag. No mention of cross-functional collaboration is a MEDIUM flag.
      Listing a product launch without stating the impact is a MEDIUM flag.
    
    - Business Analyst: no mention of specific tools (SQL, Excel, Tableau/Power BI) 
      is a MEDIUM flag. Missing domain context (what industry they worked in) is a 
      LOW flag. Generic requirement gathering without specific artefacts produced 
      (BRD, FRD, user stories) is a MEDIUM flag.
    
    - Embedded Systems: mentioning cloud/Docker/React as primary skills is a DISTRACTING 
      flag — suggests web dev trying for embedded. Missing hardware protocols 
      (CAN, SPI, I2C, UART) at mid-level+ is a MAJOR flag. Git/GitHub emphasis 
      without hardware debugging experience is a LOW flag.
    
    - VLSI Design Engineer: any web development or full-stack skills in prime position 
      is a DISTRACTING flag. Missing EDA tool experience (Synopsys, Cadence, Mentor) 
      is a MAJOR flag. Python/Perl scripting for automation should be a minor signal, 
      not the main skill.
    
    - Data Analyst: missing or vague SQL signal is a HIGH flag — SQL is non-negotiable.
      No mention of any BI tool (Tableau, Power BI, Looker) is a MEDIUM flag.
      Data Scientist resumes applying for Data Analyst roles is a MINOR flag — 
      check if skills section is mismatched with the role.
    
    - Data Scientist: missing statistics fundamentals is a MAJOR flag. Listing every 
      ML library without showing what was built with them is a MEDIUM flag.
      No mention of business impact from models is a MEDIUM flag.
    
    - Data Engineer: no mention of SQL at mid-level+ is a HIGH flag. Missing 
      data pipeline concepts (ETL/ELT, scheduling, orchestration) is a MEDIUM flag.
      Only mentioning "big data tools" without pipeline specifics is a LOW flag.

For each red flag, output:
{{
  "flag": "description of the problem — be specific, quote the exact phrase",
  "location": "exact quote from resume (minimum 10 characters)",
  "inference_chain": "Recruiter sees [exact thing] → assumes [specific assumption with company/role context] → decides [concrete outcome]",
  "severity": "HIGH, MEDIUM, or LOW",
  "fix": "exact rewrite or specific action — not vague advice",
  "category": "integrity | competence | fit | market_specific | plausibility | self_sabotage",
  "jd_gap": true or false
}}

CATEGORY DEFINITIONS:
- integrity: dates, claims, or titles that don't add up or seem inflated
- competence: missing skills or experience required for the role
- fit: wrong signals for this specific company type or market
- market_specific: specific to this market/role combination (e.g. no CGPA for Indian service company fresher)
- plausibility: claims that seem exaggerated or technically impossible given the timeline
- self_sabotage: candidate actively harming their own application — photo on USA resume, listing "hobbies: cricket, Netflix" on a senior resume, 2-page resume for a fresher with 0 YOE, objective statement that reveals wrong target role, generic summary that wastes prime real estate, AI-generated filler text with no real specifics, missing critical ATS keywords for this role

INFERENCE CHAIN RULES — CRITICAL:
Must follow this exact format: "Recruiter sees X → assumes Y → decides Z"
Must name at least one specific company type, role level, or market norm.
Must end with a concrete recruiter decision (shortlist, skip, probe, auto-filter).

BANNED PHRASES — if your inference chain contains 2+ of these, rewrite it:
- "recruiters look for"
- "is important to"
- "hiring managers want"
- "this shows that"
- "lacks quantifiable"
- "should include metrics"
- "demonstrates that you"
- "will negatively impact"

CORRECT inference chain example:
"Recruiter sees 'near-production multi-tenant platform' → assumes it was never actually deployed to real users, something broke → decides to probe hard in interview or skip in favor of candidates with cleaner deployment claims. At a Series A AI startup, this creates unnecessary doubt about a candidate who actually served real customers."

WRONG inference chain example:
"Recruiters look for quantifiable achievements. This shows that you lack impact metrics which will negatively impact your chances."

ROLE-SPECIFIC RULES:
- SDE / Full Stack / Backend: missing GitHub is NOT a red flag (many Indian SDEs don't maintain public GitHub). Missing DSA signal is a MAJOR concern for product companies, MINOR for service companies. Listing 8+ programming languages without depth is a LOW flag.
- Embedded Engineer: missing GitHub is NOT a red flag (proprietary firmware cannot be open-sourced)
- AI Agentic Engineer / AI/ML: no GitHub or Hugging Face is a MODERATE flag for applied roles, not HIGH. No model evaluation metrics is a MEDIUM flag. Java/C++ in primary position on an AI resume is a DISTRACTING flag — suggests wrong stack.
- Student/Fresher: do not flag short experience — they are expected to have none
- VLSI Engineer: missing GitHub, Docker, cloud experience are NOT red flags — irrelevant for hardware roles
- Data Analyst: missing GitHub is NOT a red flag — most analyst work is internal dashboards. Missing SQL or BI tool mention IS a red flag.
- Data Scientist: missing statistics fundamentals IS a red flag. Listing every ML library without project context IS a red flag.
- Data Engineer: missing SQL or data pipeline concepts (ETL/ELT, orchestration) IS a red flag at mid-level+.
- DevOps / SRE: missing metrics (uptime, latency percentages) is a HIGH flag — DevOps is measured by numbers. Missing IaC tool mention (Terraform/Ansible) is a MEDIUM flag at mid-level+.
- Product Manager: missing product metrics (DAU, retention, conversion) is a HIGH flag. No mention of cross-functional collaboration is a MEDIUM flag.
- Business Analyst: missing SQL or BI tool is a MEDIUM flag. Vague requirement gathering without specific artefacts is a MEDIUM flag.
- Platform Engineer: missing Kubernetes + CI/CD + cloud experience is a HIGH flag — Platform is senior-level infrastructure. Missing developer experience (DX) signals is a MEDIUM flag.

PART B — VISUAL SCAN:
Note any formatting, layout, or visual issues in visual_scan_notes.
Examples: inconsistent fonts, too long, too short, photo present (bad for USA), no contact info, no LinkedIn.

Output format:
{{
  "red_flags": [...],
  "visual_scan_notes": "specific notes on visual/formatting issues",
  "confidence": "HIGH if you found clear, verifiable flags backed by resume text, MEDIUM if some flags are borderline, LOW if resume text is thin or flags are speculative"
}}

Return empty list for red_flags if none found. Never hallucinate flags.
""".strip()
