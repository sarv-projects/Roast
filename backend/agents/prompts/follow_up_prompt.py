def get_followup_task(role: str, company_type: str, market: str) -> str:
    return f"""
Answer the user's follow-up question about their resume review for {role} at {company_type} in {market}.

RULES:
- 100-200 words maximum
- Specific to this resume and market — reference actual resume content and review findings by name
- Direct and honest — same brutally honest tone as the review
- No bullet points — flowing prose only
- Name specific companies, skills, or projects from the review when relevant
- NEVER repeat what the original review already said — provide additional detail, not a summary
- NEVER give generic advice like "add metrics" or "quantify your work" — give the exact rewrite with the specific project name
- If the question asks about a weakness, give the exact fix with company-specific reasoning
- If you don't have enough context to answer specifically, say so honestly rather than inventing

EXAMPLES OF GOOD FOLLOW-UPS:
- Q: "How do I fix the hedge words in my ROAST project description?"
  → "Replace 'near-production multi-tenant AI automation platform' with 'designed and shipped a multi-tenant AI automation platform serving N live business clients'. Adding the exact client count (if >0) transforms this from a hedge to a strength signal. For Indian startups, 'shipped' is the strongest verb you can use — it signals ownership completion."
- Q: "Which companies should I target given my ACARE project?"
  → "ACARE signals competence in ROS2, computer vision (YOLOv11), and real-time safety systems. This maps to robotics roles at Bosch, ABB, Fanuc India, and healthcare robotics startups. The LangGraph + Groq integration also maps to AI-first robotics roles at Sarvam AI and Nvidia's Isaac platform teams. Emphasise the ROS2 architecture in your LinkedIn headline."

EXAMPLES OF BAD FOLLOW-UPS:
- ❌ "You should focus on improving your resume by adding more metrics and quantifying your achievements." (generic advice, no specifics)
- ❌ "As mentioned in the review, your resume lacks production experience and you should work on that." (repeating the review)
- ❌ "This is a good project that shows your skills. Keep building things like this." (no actionable advice)
""".strip()
