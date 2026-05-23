"""
Anonymised corpus store.
Stores stripped resume signals after opted-in analyses.
No resume text, no name, no email — only structured metadata.
Used by CompetitivePositioningAgent to calibrate percentile estimates.
"""

import json
from datetime import datetime, timezone
from pydantic import BaseModel
from backend.storage.redis_client import redis

CORPUS_TTL = 90 * 24 * 3600   # 90 days
CORPUS_CALIBRATED_THRESHOLD = 30  # minimum signals for "calibrated" confidence


class AnonymisedSignal(BaseModel):
    role: str
    market: str
    company_type: str
    experience_level: str
    week: str                        # YYYY-WNN format e.g. "2026-W18"
    red_flag_count: int
    high_severity_flag_count: int
    has_github: bool
    github_verified: bool
    has_quantified_bullets: bool
    college_tier_signal: str         # tier1 / tier2 / tier3 / unknown
    yoe_band: str                    # 0-2 / 2-5 / 5-8 / 8+
    estimated_percentile_range: str  # e.g. "20th-30th"
    review_model_used: str           # which model wrote the review


def _corpus_key(role: str, company_type: str, market: str, week: str) -> str:
    return f"corpus:{role}:{company_type}:{market}:{week}"


def _current_week() -> str:
    """Returns current week in YYYY-WNN format (ISO 8601)."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-W%V")


def store_signal(signal: AnonymisedSignal) -> None:
    """
    Append one anonymised signal to the corpus list for this combination + week.
    Uses Redis list — each key holds a list of JSON-encoded signals.
    """
    key = _corpus_key(
        signal.role, signal.company_type, signal.market, signal.week
    )
    redis.rpush(key, signal.model_dump_json())
    redis.expire(key, CORPUS_TTL)


def get_signals(
    role: str,
    company_type: str,
    market: str,
    weeks: int = 12,
) -> list[dict]:
    """
    Retrieve last `weeks` weeks of signals for a combination.
    Returns list of dicts — used by CompetitivePositioningAgent.
    """
    now = datetime.now(timezone.utc)
    all_signals = []

    for week_offset in range(weeks):
        # Calculate week string for each past week
        from datetime import timedelta
        week_date = now - timedelta(weeks=week_offset)
        week_str = week_date.strftime("%Y-W%V")
        key = _corpus_key(role, company_type, market, week_str)

        raw_list = redis.lrange(key, 0, -1)
        if raw_list:
            for raw in raw_list:
                try:
                    all_signals.append(json.loads(raw))
                except Exception:
                    continue

    return all_signals


def get_corpus_size(role: str, company_type: str, market: str) -> int:
    """Count total signals for a combination across last 12 weeks."""
    return len(get_signals(role, company_type, market, weeks=12))


def _detect_college_tier(resume_text: str, market: str) -> str:
    """
    Estimate college tier from resume text.
    Market-aware — India has a well-defined tier system; other markets use different signals.
    Returns: "tier1" / "tier2" / "tier3" / "unknown"

    Matching strategy:
    - Short abbreviations (IIT, NIT) checked as whole words with word boundaries
    - Full names checked as substrings (case-insensitive)
    - "unknown" returned for non-India markets where tier is not meaningful
    """
    import re
    text_lower = resume_text.lower()

    if market == "India":
        return _detect_india_tier(text_lower)
    elif market == "USA":
        return _detect_usa_tier(text_lower)
    elif market == "UK":
        return _detect_uk_tier(text_lower)
    elif market == "Singapore":
        return _detect_singapore_tier(text_lower)
    elif market == "UAE":
        # UAE is mostly expat talent — college tier from home country, not meaningful to classify
        return "unknown"
    else:
        return "unknown"


def _word_match(text: str, terms: list[str]) -> bool:
    """Check if any term appears as a whole word (not substring of another word)."""
    import re
    for term in terms:
        # Escape special chars, then wrap in word boundaries
        pattern = r'\b' + re.escape(term) + r'\b'
        if re.search(pattern, text):
            return True
    return False


def _substr_match(text: str, terms: list[str]) -> bool:
    """Check if any term appears as a substring (for full institution names)."""
    return any(term in text for term in terms)


def _detect_india_tier(text: str) -> str:
    """
    India college tier detection.
    Tier 1: IITs, IISc, BITS Pilani, IIITs (top), NITs (top)
    Tier 2: NITs (remaining), IIITs (remaining), top private (VIT, Manipal, SRM, Thapar, etc.)
    Tier 3: everything else
    """
    # ── Tier 1 ────────────────────────────────────────────────────────────────
    # IITs — abbreviation + full name variants
    iit_abbrevs = ["iit"]  # "iit bombay", "iit delhi", "iit madras", etc.
    iit_full = [
        "indian institute of technology",
        "iit bombay", "iit delhi", "iit madras", "iit kanpur", "iit kharagpur",
        "iit roorkee", "iit guwahati", "iit hyderabad", "iit bangalore",
        "iit bhubaneswar", "iit gandhinagar", "iit jodhpur", "iit mandi",
        "iit patna", "iit ropar", "iit indore", "iit tirupati", "iit palakkad",
        "iit dharwad", "iit bhilai", "iit jammu", "iit varanasi",
        "banaras hindu university iit", "iit (bhu)",
    ]
    # IISc
    iisc_terms = ["iisc", "indian institute of science"]
    # BITS Pilani (only Pilani campus is Tier 1; Goa/Hyderabad are Tier 2)
    bits_tier1 = ["bits pilani", "birla institute of technology and science, pilani",
                  "birla institute of technology & science, pilani"]
    # Top IIITs
    iiit_tier1 = [
        "iiit hyderabad", "iiit-h", "international institute of information technology, hyderabad",
        "iiit bangalore", "iiitb", "international institute of information technology bangalore",
        "iiit allahabad", "iiita",
    ]
    # Top NITs
    nit_tier1 = [
        "nit trichy", "nit tiruchirappalli", "national institute of technology, tiruchirappalli",
        "nit warangal", "national institute of technology warangal",
        "nit surathkal", "nitk", "national institute of technology karnataka",
        "nit calicut", "national institute of technology calicut",
        "nit rourkela", "national institute of technology rourkela",
    ]

    if (
        _word_match(text, iit_abbrevs)
        or _substr_match(text, iit_full)
        or _substr_match(text, iisc_terms)
        or _substr_match(text, bits_tier1)
        or _substr_match(text, iiit_tier1)
        or _substr_match(text, nit_tier1)
    ):
        return "tier1"

    # ── Tier 2 ────────────────────────────────────────────────────────────────
    # Remaining NITs — use word boundary match for the short "nit" abbreviation
    nit_tier2_abbrev = ["nit"]  # word boundary matched
    nit_tier2_full = [
        "national institute of technology",
        "nit allahabad", "nit bhopal", "nit durgapur", "nit hamirpur",
        "nit jaipur", "nit jamshedpur", "nit kurukshetra", "nit nagpur",
        "nit patna", "nit raipur", "nit silchar", "nit srinagar",
        "nit uttarakhand", "nit agartala", "nit arunachal", "nit delhi",
        "nit goa", "nit manipur", "nit meghalaya", "nit mizoram",
        "nit nagaland", "nit puducherry", "nit sikkim",
    ]
    # BITS Goa and Hyderabad
    bits_tier2 = [
        "bits goa", "bits hyderabad", "bits pilani goa", "bits pilani hyderabad",
        "birla institute of technology and science, goa",
        "birla institute of technology and science, hyderabad",
        "birla institute of technology, mesra",  # BIT Mesra
    ]
    # Remaining IIITs — use word boundary for short abbreviation
    iiit_tier2_abbrev = ["iiit"]  # word boundary matched
    iiit_tier2_full = [
        "international institute of information technology",
        "iiit delhi", "iiit pune", "iiit kota", "iiit lucknow",
        "iiit vadodara", "iiit gwalior", "iiit jabalpur", "iiit kancheepuram",
        "iiit sri city", "iiit una", "iiit ranchi", "iiit nagpur",
        "iiit naya raipur", "iiit dharwad", "iiit kalyani", "iiit manipur",
        "iiit senapati", "iiit agartala", "iiit surat", "iiit bhagalpur",
        "iiit bhopal", "iiit kottayam", "iiit raichur",
    ]
    # Top private colleges
    top_private_abbrevs = ["vit", "srm", "pec", "lpu", "kiit", "dtu", "nsit"]  # word boundary matched
    top_private_full = [
        # VIT
        "vit vellore", "vit chennai", "vit bhopal", "vit ap",
        "vellore institute of technology",
        # Manipal
        "manipal", "manipal institute of technology", "mit manipal",
        # SRM
        "srm institute", "srm university", "srm kattankulathur", "srmist",
        # Thapar
        "thapar", "thapar institute",
        # Amrita
        "amrita", "amrita vishwa vidyapeetham",
        # PSG
        "psg college", "psg tech",
        # COEP
        "coep", "college of engineering pune",
        # VJTI
        "vjti", "veermata jijabai technological institute",
        # DTU / NSIT / IGDTUW (Delhi)
        "delhi technological university",
        "netaji subhas institute of technology",
        "igdtuw", "indira gandhi delhi technical university",
        # PEC
        "punjab engineering college",
        # Jadavpur
        "jadavpur", "jadavpur university",
        # RVCE / BMSCE / PES (Bangalore)
        "rvce", "r.v. college", "rv college of engineering",
        "bmsce", "b.m.s. college", "bms college of engineering",
        "pes university", "pes institute",
        # Symbiosis
        "symbiosis institute of technology", "sit pune",
        # Nirma
        "nirma university", "nirma institute",
        # LPU
        "lovely professional university",
        # Chandigarh University
        "chandigarh university",
        # Chitkara
        "chitkara university",
        # KIIT
        "kalinga institute",
        # MIT Pune (not MIT USA)
        "mit pune", "maharashtra institute of technology",
        # Shiv Nadar
        "shiv nadar university",
        # Ashoka
        "ashoka university",
        # Christ University
        "christ university",
        # Ramaiah
        "m.s. ramaiah", "ms ramaiah", "msrit",
        # KJ Somaiya
        "kj somaiya", "somaiya",
        # DAIICT
        "daiict", "dhirubhai ambani institute",
    ]

    if (
        _word_match(text, nit_tier2_abbrev)
        or _substr_match(text, nit_tier2_full)
        or _substr_match(text, bits_tier2)
        or _word_match(text, iiit_tier2_abbrev)
        or _substr_match(text, iiit_tier2_full)
        or _word_match(text, top_private_abbrevs)
        or _substr_match(text, top_private_full)
    ):
        return "tier2"

    return "tier3"


def _detect_usa_tier(text: str) -> str:
    """
    USA college tier detection.
    Tier 1: Ivy League + MIT + Stanford + top CS schools
    Tier 2: Strong state schools + top private non-Ivy
    Tier 3: everything else
    """
    tier1_abbrevs = ["mit", "cmu", "uiuc", "umich", "ucsd", "ucla", "uc berkeley"]  # word boundary
    tier1_full = [
        # Ivy League
        "harvard", "yale", "princeton", "columbia", "upenn", "university of pennsylvania",
        "dartmouth", "brown", "cornell",
        # Top tech schools
        "massachusetts institute of technology",
        "stanford", "stanford university",
        "caltech", "california institute of technology",
        # Top CS schools
        "carnegie mellon",
        "university of california, berkeley", "university of california berkeley",
        "georgia tech", "georgia institute of technology",
        "university of illinois", "illinois urbana",
        "university of michigan",
        "university of washington", "uw seattle",
        "university of texas at austin", "ut austin",
        "university of california san diego",
        "university of california los angeles",
    ]
    tier2_abbrevs = ["unc", "usc", "nyu", "rpi", "wpi", "bu"]  # word boundary
    tier2_full = [
        "purdue", "ohio state", "penn state", "university of wisconsin",
        "university of minnesota", "university of maryland", "university of virginia",
        "university of north carolina", "duke", "vanderbilt", "rice",
        "university of southern california", "northeastern",
        "boston university", "university of florida",
        "university of colorado", "university of arizona", "arizona state",
        "virginia tech", "nc state", "rutgers", "stony brook",
        "university of california davis", "uc davis",
        "university of california santa barbara", "ucsb",
        "university of california irvine", "uc irvine",
        "rensselaer", "worcester polytechnic",
        "drexel", "lehigh", "case western", "tulane",
    ]

    if _word_match(text, tier1_abbrevs) or _substr_match(text, tier1_full):
        return "tier1"
    if _word_match(text, tier2_abbrevs) or _substr_match(text, tier2_full):
        return "tier2"
    return "tier3"


def _detect_uk_tier(text: str) -> str:
    """
    UK college tier detection.
    Tier 1: Oxbridge + Russell Group top CS schools
    Tier 2: Remaining Russell Group + strong post-92
    Tier 3: everything else
    """
    tier1 = [
        "oxford", "university of oxford",
        "cambridge", "university of cambridge",
        "imperial college", "imperial london",
        "ucl", "university college london",
        "edinburgh", "university of edinburgh",
        "manchester", "university of manchester",
        "bristol", "university of bristol",
        "warwick", "university of warwick",
        "southampton", "university of southampton",
        "king's college london", "kcl",
        "lse", "london school of economics",
    ]
    tier2 = [
        "birmingham", "university of birmingham",
        "leeds", "university of leeds",
        "sheffield", "university of sheffield",
        "nottingham", "university of nottingham",
        "glasgow", "university of glasgow",
        "durham", "university of durham",
        "exeter", "university of exeter",
        "bath", "university of bath",
        "york", "university of york",
        "lancaster", "university of lancaster",
        "queen mary", "qmul",
        "city university", "city, university of london",
        "heriot-watt", "strathclyde",
        "st andrews", "university of st andrews",
    ]

    if _substr_match(text, tier1):
        return "tier1"
    if _substr_match(text, tier2):
        return "tier2"
    return "tier3"


def _detect_singapore_tier(text: str) -> str:
    """
    Singapore college tier detection.
    Tier 1: NUS, NTU, SMU (the three main universities)
    Tier 2: SUTD, SIT, SUSS, UniSIM
    Tier 3: everything else (polytechnics, overseas)
    """
    tier1_abbrevs = ["nus", "ntu", "smu"]  # word boundary
    tier1_full = [
        "national university of singapore",
        "nanyang technological university",
        "singapore management university",
    ]
    tier2_abbrevs = ["sutd", "sit", "suss", "nyp"]  # word boundary
    tier2_full = [
        "singapore university of technology and design",
        "singapore institute of technology",
        "singapore university of social sciences",
        "unisim",
        "singapore polytechnic",
        "ngee ann polytechnic",
        "temasek polytechnic",
        "republic polytechnic",
        "nanyang polytechnic",
    ]

    if _word_match(text, tier1_abbrevs) or _substr_match(text, tier1_full):
        return "tier1"
    if _word_match(text, tier2_abbrevs) or _substr_match(text, tier2_full):
        return "tier2"
    return "tier3"


def build_signal_from_pipeline(
    role: str,
    company_type: str,
    market: str,
    experience_level: str,
    red_flag_count: int,
    high_severity_count: int,
    profile_links: dict,
    resume_text: str,
    percentile_range: str,
    review_model: str,
) -> AnonymisedSignal:
    """
    Build an AnonymisedSignal from pipeline outputs.
    Called after pipeline completes if user opted in.
    """
    # Detect quantified bullets — look for numbers in bullet points
    import re
    has_quantified = bool(re.search(r'\d+[%xX]|\d+[KkMmBb]|\d+\s*(ms|s|hrs?|days?|users?|requests?)', resume_text))

    # Detect GitHub
    has_github = bool(profile_links.get("github"))
    github_verified = profile_links.get("github_verified", False)

    # Estimate college tier from common signals in resume text
    college_tier = _detect_college_tier(resume_text, market)

    # YOE band from experience level
    yoe_map = {
        "Student / Fresher": "0-2",
        "Junior": "0-2",
        "Mid-level": "2-5",
        "Senior": "5-8",
        "Staff / Principal": "8+",
    }
    yoe_band = yoe_map.get(experience_level, "0-2")

    return AnonymisedSignal(
        role=role,
        market=market,
        company_type=company_type,
        experience_level=experience_level,
        week=_current_week(),
        red_flag_count=red_flag_count,
        high_severity_flag_count=high_severity_count,
        has_github=has_github,
        github_verified=github_verified,
        has_quantified_bullets=has_quantified,
        college_tier_signal=college_tier,
        yoe_band=yoe_band,
        estimated_percentile_range=percentile_range,
        review_model_used=review_model,
    )
