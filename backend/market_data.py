"""
Dynamic market data tables — replacements for hardcoded strings in prompts.
All company lists, salary bands, role requirements, and weight rules live here.
Updated via seed scripts or automated pipelines, not code deploys.

Database file: backend/market_config.db (separate from ingestion/market_intel.db)
"""

import sqlite3
import json
import structlog
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).parent / "market_config.db"
logger = structlog.get_logger()

# ── Role → category mapping ────────────────────────────────────────────────────

ROLE_TO_CATEGORY: dict[str, str] = {
    "Software Engineer / Associate": "sde",
    "SDE1": "sde",
    "SDE2 / Senior SDE": "sde",
    "Full Stack Engineer": "sde",
    "Backend Engineer": "sde",
    "Embedded Systems Engineer": "embedded",
    "VLSI Design Engineer": "vlsi",
    "Data Analyst": "data_analyst",
    "Data Scientist": "data_scientist",
    "Data Engineer": "data_engineer",
    "AI Engineer": "ai_agentic",
    "AI/ML Engineer": "ai_agentic",
    "AI Agentic Engineer": "ai_agentic",
    "DevOps / SRE": "devops",
    "Platform Engineer": "platform",
    "Product Manager": "pm",
    "Business Analyst": "ba",
}

EXPERIENCE_LEVEL_TO_BAND: dict[str, str] = {
    "Student": "fresher",
    "Fresher": "fresher",
    "Student / Fresher": "fresher",
    "Junior": "junior",
    "Mid": "mid",
    "Mid-level": "mid",
    "Senior": "senior",
    "Staff": "staff",
    "Principal": "staff",
    "Staff / Principal": "staff",
}


def get_role_category(role: str) -> str:
    """Canonical role-to-category mapping. Single source of truth for the entire codebase."""
    # Exact match first
    if role in ROLE_TO_CATEGORY:
        return ROLE_TO_CATEGORY[role]
    # Substring fallback
    role_lower = role.lower()
    if any(x in role_lower for x in ["sde", "full stack", "backend", "software engineer", "associate"]):
        return "sde"
    if any(x in role_lower for x in ["ai", "ml", "machine learning", "genai"]):
        return "ai_agentic"
    if any(x in role_lower for x in ["data scientist"]):
        return "data_scientist"
    if any(x in role_lower for x in ["data engineer"]):
        return "data_engineer"
    if any(x in role_lower for x in ["data analyst", "data"]):
        return "data_analyst"
    if any(x in role_lower for x in ["devops", "sre", "platform"]):
        return "devops"
    if any(x in role_lower for x in ["embedded", "vlsi"]):
        return "hardware"
    return "general"


# ── Database ────────────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    # Integrity check on startup — detect corruption from container restarts
    result = conn.execute("PRAGMA integrity_check").fetchone()
    if result[0] != "ok":
        logger.error("market_config_db_corrupt", result=result[0])
        raise RuntimeError(f"market_config.db is corrupt: {result[0]}")
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS market_companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                company_type TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'India',
                tier INTEGER DEFAULT 2,
                is_active INTEGER DEFAULT 1,
                role_category TEXT DEFAULT 'general',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS market_salary_bands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_category TEXT NOT NULL,
                company_type TEXT NOT NULL,
                market TEXT NOT NULL,
                experience_level TEXT NOT NULL,
                min_lpa REAL,
                max_lpa REAL,
                currency TEXT DEFAULT 'INR',
                source TEXT DEFAULT 'manual',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS market_role_requirements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_category TEXT NOT NULL,
                company_type TEXT NOT NULL,
                market TEXT NOT NULL,
                required_skills TEXT,
                preferred_skills TEXT,
                cgpa_cutoff REAL,
                expected_stack TEXT,
                key_signals TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS market_company_naming (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_type TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'India',
                example_names TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS market_role_weights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_category TEXT NOT NULL UNIQUE,
                dsa REAL DEFAULT 0.5,
                projects REAL DEFAULT 0.7,
                cgpa REAL DEFAULT 0.5,
                experience REAL DEFAULT 0.5,
                open_source REAL DEFAULT 0.4,
                college_tier REAL DEFAULT 0.4,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS market_city_multipliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                market TEXT NOT NULL,
                multiplier REAL DEFAULT 1.0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
    conn.close()


# ── Query functions ─────────────────────────────────────────────────────────────

def get_companies(company_type: str, market: str = "India", role_category: str | None = None,
                  active_only: bool = True) -> list[str]:
    """Return example company names for a given type/market/role."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT name FROM market_companies WHERE company_type=? AND market=? "
        + ("AND is_active=1" if active_only else "") +
        (" AND (role_category=? OR role_category='general')" if role_category else "") +
        " ORDER BY tier, name",
        (company_type, market) + ((role_category,) if role_category else ())
    ).fetchall()
    conn.close()
    return [r["name"] for r in rows]


def get_company_naming_rule(company_type: str, market: str = "India") -> str:
    """Return the company naming instruction for ReviewAgent."""
    conn = get_connection()
    row = conn.execute(
        "SELECT example_names FROM market_company_naming WHERE company_type=? AND market=?",
        (company_type, market)
    ).fetchone()
    conn.close()
    if row:
        names = json.loads(row["example_names"])
        return f"Name {', '.join(names[:12])} — not companies outside this category."
    return f"Name real {company_type} companies appropriate for this role."


def get_salary_band(role_category: str, company_type: str, market: str,
                    experience_level: str) -> dict | None:
    """Return salary band for this combination, or None if not found."""
    exp_band = EXPERIENCE_LEVEL_TO_BAND.get(experience_level, "junior")
    conn = get_connection()
    row = conn.execute(
        "SELECT min_lpa, max_lpa, currency FROM market_salary_bands "
        "WHERE role_category=? AND company_type=? AND market=? AND experience_level=?",
        (role_category, company_type, market, exp_band)
    ).fetchone()
    conn.close()
    if row:
        return {"min": row["min_lpa"], "max": row["max_lpa"], "currency": row["currency"]}
    return None


def get_role_requirements(role_category: str, company_type: str, market: str) -> dict | None:
    """Return role requirements (skills, stack, CGPA cutoff) for calibration."""
    conn = get_connection()
    row = conn.execute(
        "SELECT required_skills, preferred_skills, cgpa_cutoff, expected_stack, key_signals "
        "FROM market_role_requirements "
        "WHERE role_category=? AND company_type=? AND market=?",
        (role_category, company_type, market)
    ).fetchone()
    conn.close()
    if row:
        return {
            "required_skills": json.loads(row["required_skills"] or "[]"),
            "preferred_skills": json.loads(row["preferred_skills"] or "[]"),
            "cgpa_cutoff": row["cgpa_cutoff"],
            "expected_stack": row["expected_stack"] or "",
            "key_signals": row["key_signals"] or "",
        }
    return None


def get_role_weights(role_category: str) -> dict[str, float]:
    """Return role-specific weight overrides. Returns empty dict if none."""
    conn = get_connection()
    row = conn.execute(
        "SELECT dsa, projects, cgpa, experience, open_source, college_tier "
        "FROM market_role_weights WHERE role_category=?",
        (role_category,)
    ).fetchone()
    conn.close()
    if row:
        return {
            "dsa": row["dsa"], "projects": row["projects"], "cgpa": row["cgpa"],
            "experience": row["experience"], "open_source": row["open_source"],
            "college_tier": row["college_tier"],
        }
    return {}


def get_city_multiplier(city: str, market: str) -> float:
    """Return salary multiplier for a city (1.0 = no adjustment)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT multiplier FROM market_city_multipliers WHERE city=? AND market=?",
        (city, market)
    ).fetchone()
    conn.close()
    return row["multiplier"] if row else 1.0


def is_market_data_stale(days: int = 90) -> bool:
    """Check if any market config data is older than `days` days."""
    conn = get_connection()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
    from datetime import timedelta
    cutoff = cutoff - timedelta(days=days)
    row = conn.execute(
        "SELECT MIN(updated_at) as oldest FROM ("
        "  SELECT updated_at FROM market_companies UNION ALL"
        "  SELECT updated_at FROM market_salary_bands UNION ALL"
        "  SELECT updated_at FROM market_role_requirements"
        ") WHERE updated_at IS NOT NULL"
    ).fetchone()
    conn.close()
    if row and row["oldest"]:
        return datetime.fromisoformat(row["oldest"]) < cutoff
    return False


# ── Seed data ───────────────────────────────────────────────────────────────────

def seed_all() -> None:
    """Populate all tables with current market knowledge. Idempotent."""
    init_db()
    _seed_companies()
    _seed_salary_bands()
    _seed_role_requirements()
    _seed_company_naming()
    _seed_role_weights()
    _seed_city_multipliers()
    logger.info("market_data_seeded", tables=6)


def _seed_companies() -> None:
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM market_companies").fetchone()[0]
    if count > 0:
        conn.close()
        return
    companies = [
        # Indian Product Company
        ("Flipkart", "Indian Product Company", "India", 1, "sde"),
        ("Swiggy", "Indian Product Company", "India", 1, "sde"),
        ("Razorpay", "Indian Product Company", "India", 1, "sde"),
        ("PhonePe", "Indian Product Company", "India", 1, "sde"),
        ("CRED", "Indian Product Company", "India", 1, "sde"),
        ("Meesho", "Indian Product Company", "India", 1, "sde"),
        ("Zepto", "Indian Product Company", "India", 1, "sde"),
        ("Navi", "Indian Product Company", "India", 1, "sde"),
        ("Groww", "Indian Product Company", "India", 1, "sde"),
        ("BrowserStack", "Indian Product Company", "India", 1, "sde"),
        ("Freshworks", "Indian Product Company", "India", 1, "sde"),
        ("Zoho", "Indian Product Company", "India", 1, "sde"),
        ("Postman", "Indian Product Company", "India", 1, "sde"),
        ("Hasura", "Indian Product Company", "India", 1, "sde"),
        ("Chargebee", "Indian Product Company", "India", 1, "sde"),
        ("Juspay", "Indian Product Company", "India", 2, "sde"),
        ("Cashfree", "Indian Product Company", "India", 2, "sde"),
        ("Slice", "Indian Product Company", "India", 2, "sde"),
        ("Ola", "Indian Product Company", "India", 2, "sde"),
        ("Rapido", "Indian Product Company", "India", 2, "sde"),
        ("Urban Company", "Indian Product Company", "India", 2, "sde"),
        ("Nykaa", "Indian Product Company", "India", 2, "general"),
        ("Lenskart", "Indian Product Company", "India", 2, "general"),
        ("Policybazaar", "Indian Product Company", "India", 2, "general"),
        ("Acko", "Indian Product Company", "India", 2, "general"),
        ("Delhivery", "Indian Product Company", "India", 2, "general"),
        ("Shiprocket", "Indian Product Company", "India", 2, "general"),
        ("Zomato", "Indian Product Company", "India", 1, "sde"),
        ("Sarvam AI", "Indian Product Company", "India", 1, "ai_agentic"),
        ("Krutrim", "Indian Product Company", "India", 1, "ai_agentic"),
        ("Gnani.ai", "Indian Product Company", "India", 2, "ai_agentic"),
        ("Haptik", "Indian Product Company", "India", 2, "ai_agentic"),
        ("Yellow.ai", "Indian Product Company", "India", 2, "ai_agentic"),
        ("Observe.AI", "Indian Product Company", "India", 2, "ai_agentic"),
        ("Uniphore", "Indian Product Company", "India", 2, "ai_agentic"),

        # Indian Service Company
        ("TCS", "Indian Service Company", "India", 1, "general"),
        ("Infosys", "Indian Service Company", "India", 1, "general"),
        ("Wipro", "Indian Service Company", "India", 1, "general"),
        ("Cognizant", "Indian Service Company", "India", 1, "general"),
        ("HCL Technologies", "Indian Service Company", "India", 1, "general"),
        ("Tech Mahindra", "Indian Service Company", "India", 1, "general"),
        ("LTIMindtree", "Indian Service Company", "India", 2, "general"),
        ("Mphasis", "Indian Service Company", "India", 2, "general"),
        ("Hexaware", "Indian Service Company", "India", 2, "general"),
        ("Persistent Systems", "Indian Service Company", "India", 2, "general"),
        ("Coforge", "Indian Service Company", "India", 2, "general"),
        ("KPIT Technologies", "Indian Service Company", "India", 2, "general"),
        ("Tata Elxsi", "Indian Service Company", "India", 2, "embedded"),

        # FAANG / Big Tech
        ("Google", "FAANG / Big Tech", "India", 1, "general"),
        ("Amazon", "FAANG / Big Tech", "India", 1, "general"),
        ("Microsoft", "FAANG / Big Tech", "India", 1, "general"),
        ("Meta", "FAANG / Big Tech", "India", 1, "general"),
        ("Apple", "FAANG / Big Tech", "India", 1, "general"),
        ("Adobe", "FAANG / Big Tech", "India", 1, "general"),
        ("Salesforce", "FAANG / Big Tech", "India", 1, "general"),
        ("Uber", "FAANG / Big Tech", "India", 1, "general"),
        ("LinkedIn", "FAANG / Big Tech", "India", 1, "general"),
        ("Atlassian", "FAANG / Big Tech", "India", 1, "general"),
        ("Stripe", "FAANG / Big Tech", "India", 1, "general"),
        ("Databricks", "FAANG / Big Tech", "India", 1, "general"),
        ("Snowflake", "FAANG / Big Tech", "India", 1, "general"),
        ("Intuit", "FAANG / Big Tech", "India", 1, "general"),
        ("Cisco", "FAANG / Big Tech", "India", 1, "general"),
        ("VMware", "FAANG / Big Tech", "India", 1, "general"),
        ("SAP Labs", "FAANG / Big Tech", "India", 2, "general"),
        ("Oracle", "FAANG / Big Tech", "India", 2, "general"),
        ("Nvidia", "FAANG / Big Tech", "India", 1, "hardware"),
        ("Qualcomm", "FAANG / Big Tech", "India", 1, "hardware"),

        # Startup
        ("Zepto", "Startup", "India", 1, "sde"),
        ("Sarvam AI", "Startup", "India", 1, "ai_agentic"),
        ("Krutrim", "Startup", "India", 1, "ai_agentic"),
        ("CRED", "Startup", "India", 1, "sde"),
        ("Meesho", "Startup", "India", 1, "sde"),
        ("Razorpay", "Startup", "India", 1, "sde"),
        ("PhonePe", "Startup", "India", 1, "sde"),
        ("BrowserStack", "Startup", "India", 1, "sde"),
        ("Juspay", "Startup", "India", 2, "sde"),
        ("Navi", "Startup", "India", 2, "sde"),
        ("Groww", "Startup", "India", 2, "sde"),
        ("Slice", "Startup", "India", 2, "sde"),
        ("upGrad", "Startup", "India", 2, "general"),
        ("Physics Wallah", "Startup", "India", 2, "general"),
        ("BharatPe", "Startup", "India", 2, "general"),
        ("Lenskart", "Startup", "India", 2, "general"),
        ("Mamaearth", "Startup", "India", 2, "general"),
        ("Gnani.ai", "Startup", "India", 2, "ai_agentic"),
        ("Haptik", "Startup", "India", 2, "ai_agentic"),
        ("Yellow.ai", "Startup", "India", 2, "ai_agentic"),

        # Consulting / IB
        ("McKinsey", "Consulting / IB", "India", 1, "general"),
        ("BCG", "Consulting / IB", "India", 1, "general"),
        ("Deloitte", "Consulting / IB", "India", 1, "general"),
        ("EY", "Consulting / IB", "India", 1, "general"),
        ("KPMG", "Consulting / IB", "India", 1, "general"),
        ("PwC", "Consulting / IB", "India", 1, "general"),
        ("Goldman Sachs", "Consulting / IB", "India", 1, "general"),
        ("JPMorgan", "Consulting / IB", "India", 1, "general"),
        ("Morgan Stanley", "Consulting / IB", "India", 1, "general"),
        ("Accenture Strategy", "Consulting / IB", "India", 2, "general"),

        # Semiconductor / Hardware
        ("Qualcomm", "Semiconductor / Hardware", "India", 1, "hardware"),
        ("NXP", "Semiconductor / Hardware", "India", 1, "hardware"),
        ("Texas Instruments", "Semiconductor / Hardware", "India", 1, "hardware"),
        ("Intel", "Semiconductor / Hardware", "India", 1, "hardware"),
        ("AMD", "Semiconductor / Hardware", "India", 1, "hardware"),
        ("Nvidia", "Semiconductor / Hardware", "India", 1, "hardware"),
        ("Broadcom", "Semiconductor / Hardware", "India", 1, "hardware"),
        ("Marvell", "Semiconductor / Hardware", "India", 1, "hardware"),
        ("Bosch", "Semiconductor / Hardware", "India", 1, "hardware"),
        ("Continental", "Semiconductor / Hardware", "India", 1, "hardware"),
        ("Renesas", "Semiconductor / Hardware", "India", 2, "hardware"),
        ("Infineon", "Semiconductor / Hardware", "India", 2, "hardware"),
        ("STMicroelectronics", "Semiconductor / Hardware", "India", 2, "hardware"),
        ("Analog Devices", "Semiconductor / Hardware", "India", 2, "hardware"),
        ("Microchip", "Semiconductor / Hardware", "India", 2, "hardware"),
        ("Tata Elxsi", "Semiconductor / Hardware", "India", 2, "hardware"),
        ("KPIT Technologies", "Semiconductor / Hardware", "India", 2, "hardware"),
        ("Synopsys", "Semiconductor / Hardware", "India", 1, "vlsi"),
        ("Cadence", "Semiconductor / Hardware", "India", 1, "vlsi"),
        ("Siemens EDA", "Semiconductor / Hardware", "India", 2, "vlsi"),

        # MNC India (Non-FAANG)
        ("Walmart Global Tech", "MNC India (Non-FAANG)", "India", 1, "sde"),
        ("JPMorgan", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("Goldman Sachs", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("IBM", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("Accenture", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("Capgemini", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("Deloitte", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("EY", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("KPMG", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("SAP Labs", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("Oracle", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("Bosch", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("Siemens", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("Honeywell", "MNC India (Non-FAANG)", "India", 2, "general"),
        ("Target", "MNC India (Non-FAANG)", "India", 2, "general"),
        ("Visa", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("Mastercard", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("American Express", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("Fidelity", "MNC India (Non-FAANG)", "India", 2, "general"),
        ("Deutsche Bank", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("Barclays", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("HSBC", "MNC India (Non-FAANG)", "India", 1, "general"),
        ("Ericsson", "MNC India (Non-FAANG)", "India", 2, "general"),
        ("Nokia", "MNC India (Non-FAANG)", "India", 2, "general"),
        ("Micron", "MNC India (Non-FAANG)", "India", 2, "hardware"),
        ("Western Digital", "MNC India (Non-FAANG)", "India", 2, "hardware"),
        ("NetApp", "MNC India (Non-FAANG)", "India", 2, "general"),
        ("General Electric", "MNC India (Non-FAANG)", "India", 2, "general"),
    ]

    # Mark known-defunct/stale companies
    stale = {"Byju's", "Doubtnut", "Lido", "Toppr", "Mastech Digital",
             "Syntel", "iGate", "Patni", "Rolta", "Polaris", "Mahindra Satyam",
             "Vedantu", "MobiKwik", "Housing.com", "99acres", "Byju's"}

    with conn:
        for name, ct, market, tier, role_cat in companies:
            active = 0 if name in stale else 1
            conn.execute(
                "INSERT INTO market_companies (name, company_type, market, tier, is_active, role_category) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, ct, market, tier, active, role_cat)
            )
    conn.close()


def _seed_salary_bands() -> None:
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM market_salary_bands").fetchone()[0]
    if count > 0:
        conn.close()
        return
    bands = [
        # SDE — India
        ("sde", "Indian Product Company", "India", "fresher", 8, 20, "INR"),
        ("sde", "Indian Product Company", "India", "junior", 12, 28, "INR"),
        ("sde", "Indian Product Company", "India", "mid", 18, 45, "INR"),
        ("sde", "Indian Product Company", "India", "senior", 30, 70, "INR"),
        ("sde", "Indian Product Company", "India", "staff", 55, 120, "INR"),
        ("sde", "Indian Service Company", "India", "fresher", 3.5, 6, "INR"),
        ("sde", "Indian Service Company", "India", "junior", 5, 10, "INR"),
        ("sde", "Indian Service Company", "India", "mid", 8, 18, "INR"),
        ("sde", "Indian Service Company", "India", "senior", 15, 30, "INR"),
        ("sde", "FAANG / Big Tech", "India", "fresher", 18, 40, "INR"),
        ("sde", "FAANG / Big Tech", "India", "junior", 25, 55, "INR"),
        ("sde", "FAANG / Big Tech", "India", "mid", 35, 80, "INR"),
        ("sde", "FAANG / Big Tech", "India", "senior", 55, 120, "INR"),
        ("sde", "Startup", "India", "fresher", 8, 18, "INR"),
        ("sde", "Startup", "India", "junior", 12, 25, "INR"),
        ("sde", "Startup", "India", "mid", 18, 40, "INR"),
        ("sde", "Startup", "India", "senior", 30, 65, "INR"),
        ("sde", "MNC India (Non-FAANG)", "India", "fresher", 6, 14, "INR"),
        ("sde", "MNC India (Non-FAANG)", "India", "junior", 10, 22, "INR"),
        ("sde", "MNC India (Non-FAANG)", "India", "mid", 15, 35, "INR"),
        ("sde", "MNC India (Non-FAANG)", "India", "senior", 25, 55, "INR"),
        ("sde", "Consulting / IB", "India", "fresher", 8, 18, "INR"),
        ("sde", "Consulting / IB", "India", "junior", 12, 25, "INR"),
        ("sde", "Consulting / IB", "India", "mid", 18, 40, "INR"),
        ("sde", "Semiconductor / Hardware", "India", "fresher", 6, 15, "INR"),
        ("sde", "Semiconductor / Hardware", "India", "junior", 10, 22, "INR"),
        ("sde", "Semiconductor / Hardware", "India", "mid", 15, 35, "INR"),

        # AI Agentic Engineer — India
        ("ai_agentic", "Indian Product Company", "India", "fresher", 10, 22, "INR"),
        ("ai_agentic", "Indian Product Company", "India", "junior", 14, 30, "INR"),
        ("ai_agentic", "Indian Product Company", "India", "mid", 22, 55, "INR"),
        ("ai_agentic", "Indian Product Company", "India", "senior", 35, 80, "INR"),
        ("ai_agentic", "Startup", "India", "fresher", 8, 18, "INR"),
        ("ai_agentic", "Startup", "India", "junior", 12, 25, "INR"),
        ("ai_agentic", "Startup", "India", "mid", 18, 40, "INR"),
        ("ai_agentic", "FAANG / Big Tech", "India", "fresher", 20, 40, "INR"),
        ("ai_agentic", "FAANG / Big Tech", "India", "junior", 25, 55, "INR"),
        ("ai_agentic", "FAANG / Big Tech", "India", "mid", 35, 80, "INR"),
        ("ai_agentic", "MNC India (Non-FAANG)", "India", "fresher", 8, 18, "INR"),
        ("ai_agentic", "MNC India (Non-FAANG)", "India", "junior", 12, 28, "INR"),
        ("ai_agentic", "MNC India (Non-FAANG)", "India", "mid", 20, 45, "INR"),

        # Data roles — India
        ("data_scientist", "Indian Product Company", "India", "fresher", 8, 18, "INR"),
        ("data_scientist", "Indian Product Company", "India", "junior", 12, 28, "INR"),
        ("data_scientist", "FAANG / Big Tech", "India", "fresher", 15, 30, "INR"),
        ("data_engineer", "Indian Product Company", "India", "fresher", 8, 18, "INR"),
        ("data_engineer", "Indian Product Company", "India", "junior", 12, 25, "INR"),
        ("data_analyst", "Indian Product Company", "India", "fresher", 5, 12, "INR"),
        ("data_analyst", "Indian Service Company", "India", "fresher", 3.5, 6, "INR"),
        ("data_analyst", "MNC India (Non-FAANG)", "India", "fresher", 4, 10, "INR"),

        # Hardware roles — India
        ("embedded", "Indian Product Company", "India", "fresher", 5, 12, "INR"),
        ("embedded", "Semiconductor / Hardware", "India", "fresher", 5, 12, "INR"),
        ("embedded", "Semiconductor / Hardware", "India", "junior", 8, 18, "INR"),
        ("embedded", "Semiconductor / Hardware", "India", "mid", 12, 28, "INR"),
        ("vlsi", "Semiconductor / Hardware", "India", "fresher", 6, 18, "INR"),
        ("vlsi", "Semiconductor / Hardware", "India", "junior", 10, 28, "INR"),
        ("vlsi", "Semiconductor / Hardware", "India", "mid", 15, 40, "INR"),

        # DevOps/Platform — India
        ("devops", "Indian Product Company", "India", "fresher", 6, 14, "INR"),
        ("devops", "Indian Product Company", "India", "junior", 10, 22, "INR"),
        ("devops", "Indian Product Company", "India", "mid", 15, 38, "INR"),
        ("platform", "Indian Product Company", "India", "mid", 18, 40, "INR"),
        ("platform", "Indian Product Company", "India", "senior", 30, 65, "INR"),
        ("platform", "FAANG / Big Tech", "India", "mid", 25, 55, "INR"),

        # PM/BA — India
        ("pm", "Indian Product Company", "India", "fresher", 10, 20, "INR"),
        ("pm", "Indian Product Company", "India", "junior", 14, 28, "INR"),
        ("pm", "FAANG / Big Tech", "India", "fresher", 15, 28, "INR"),
        ("ba", "Indian Product Company", "India", "fresher", 5, 12, "INR"),
        ("ba", "Indian Service Company", "India", "fresher", 3.5, 7, "INR"),
        ("ba", "MNC India (Non-FAANG)", "India", "fresher", 5, 10, "INR"),

        # USA bands (SDE)
        ("sde", "FAANG / Big Tech", "USA", "fresher", 100, 160, "USD"),
        ("sde", "FAANG / Big Tech", "USA", "junior", 120, 200, "USD"),
        ("sde", "FAANG / Big Tech", "USA", "mid", 160, 300, "USD"),
        ("sde", "Startup", "USA", "fresher", 80, 130, "USD"),
        ("sde", "Startup", "USA", "junior", 100, 160, "USD"),
        ("ai_agentic", "FAANG / Big Tech", "USA", "fresher", 110, 180, "USD"),
        ("ai_agentic", "Startup", "USA", "fresher", 90, 150, "USD"),

        # UAE bands
        ("sde", "Indian Product Company", "UAE", "fresher", 96, 180, "AED"),
        ("sde", "Indian Product Company", "UAE", "junior", 120, 240, "AED"),
        ("ai_agentic", "Startup", "UAE", "fresher", 120, 240, "AED"),

        # Singapore bands
        ("sde", "FAANG / Big Tech", "Singapore", "fresher", 54, 84, "SGD"),
        ("sde", "FAANG / Big Tech", "Singapore", "junior", 72, 120, "SGD"),
        ("sde", "Startup", "Singapore", "fresher", 45, 72, "SGD"),

        # UK bands
        ("sde", "FAANG / Big Tech", "UK", "fresher", 40, 65, "GBP"),
        ("sde", "FAANG / Big Tech", "UK", "junior", 50, 85, "GBP"),
        ("sde", "Startup", "UK", "fresher", 30, 50, "GBP"),
        ("ai_agentic", "Startup", "UK", "fresher", 35, 55, "GBP"),
    ]
    with conn:
        for row in bands:
            conn.execute(
                "INSERT INTO market_salary_bands (role_category, company_type, market, experience_level, min_lpa, max_lpa, currency) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", row
            )
    conn.close()


def _seed_role_requirements() -> None:
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM market_role_requirements").fetchone()[0]
    if count > 0:
        conn.close()
        return
    reqs = [
        ("sde", "Indian Product Company", "India",
         '["Python", "Java", "Go", "Node.js", "SQL", "REST APIs", "Docker", "Git"]',
         '["System Design", "Kafka", "Kubernetes", "AWS", "Redis"]',
         7.0, "Python/Go/Java, REST APIs, SQL/NoSQL, Docker, basic cloud",
         "GitHub + shipped projects are strong differentiators; DSA medium expected"),
        ("sde", "FAANG / Big Tech", "India",
         '["DSA proficiency", "System Design", "Any major language", "Distributed Systems"]',
         '["Cloud scale experience", "Open Source contributions"]',
         7.5, "Any language, strong DSA, system design depth",
         "LeetCode medium-hard, system design HLD+LLD, STAR behavioral rounds"),
        ("sde", "Indian Service Company", "India",
         '["Java", ".NET", "Python", "SQL", "Basic DSA", "SDLC", "Agile"]',
         '["AWS", "Azure"]', 6.5,
         "Java/Spring Boot, .NET, Python, SQL, basic DSA, SDLC, Agile",
         "Aptitude test + basic coding + HR; CGPA cutoff 6.5+; backlogs disqualifying"),
        ("sde", "Startup", "India",
         '["Python", "Go", "Java", "Node.js", "SQL", "REST APIs", "Docker"]',
         '["Kubernetes", "AWS", "System Design", "Open Source"]',
         7.0, "Python/Go/Java/Node.js, REST APIs, SQL/NoSQL, Docker, basic cloud",
         "Ownership signals + shipping speed over CGPA; GitHub and side projects matter"),
        ("sde", "MNC India (Non-FAANG)", "India",
         '["Java", ".NET", "Python", "SQL", "REST APIs", "AWS/Azure basics"]',
         '["Domain certifications", "SAP", "Cloud certifications"]',
         6.5, "Java/.NET/Python, SQL, REST APIs, basic cloud",
         "Aptitude + moderate DSA + HR; domain certifications valued"),

        ("ai_agentic", "Indian Product Company", "India",
         '["Python", "LangChain", "LlamaIndex", "FastAPI", "Vector DBs", "LLM APIs", "RAG pipelines", "asyncio"]',
         '["Multi-agent systems", "LLM observability", "Rate-limit handling", "Real-time streaming"]',
         7.0, "Python, LangChain/LlamaIndex, FastAPI, Vector DBs, LLM APIs, RAG, asyncio, Redis, WebSockets",
         "Shipped LLM product serving real users is rare—rate highly; Colab notebooks are baseline"),
        ("ai_agentic", "Startup", "India",
         '["Python", "LangChain", "FastAPI", "Vector DBs", "LLM APIs"]',
         '["Multi-agent systems", "Fine-tuning", "Open Source"]',
         7.0, "Python, LangChain/LlamaIndex, FastAPI, vector DBs, LLM APIs",
         "Shipped product > publications > notebooks; $0 infra patterns valued; cost-aware engineering is key"),

        ("data_scientist", "Indian Product Company", "India",
         '["Python", "pandas", "scikit-learn", "SQL", "Statistics", "Jupyter"]',
         '["PyTorch", "TensorFlow", "A/B Testing", "MLflow"]',
         7.0, "Python, pandas, SQL, scikit-learn, statistics, visualization",
         "End-to-end ML pipeline; model evaluation with business metrics; SQL proficiency"),

        ("data_engineer", "Indian Product Company", "India",
         '["Python", "SQL", "Spark/PySpark", "Airflow/Prefect", "Kafka"]',
         '["dbt", "BigQuery", "Snowflake", "Redshift"]',
         7.0, "Python, SQL, Spark, Airflow, Kafka, cloud data warehouses",
         "Pipeline reliability + data modeling + handling failures at scale"),

        ("data_analyst", "Indian Service Company", "India",
         '["SQL", "Excel", "Tableau/Power BI", "Basic Statistics"]',
         '["Python", "Looker"]',
         6.0, "SQL, Excel, Tableau/Power BI, basic statistics",
         "SQL complexity (JOINs, CTEs, window functions); business domain understanding"),
        ("data_analyst", "MNC India (Non-FAANG)", "India",
         '["SQL", "Excel", "Tableau/Power BI", "Basic Python"]',
         '["Looker", "Jupyter"]',
         6.5, "SQL, Excel, Tableau/Power BI, basic Python",
         "Data storytelling + stakeholder communication; dashboards used by business teams"),

        ("embedded", "Semiconductor / Hardware", "India",
         '["C", "C++", "RTOS", "ARM Cortex", "CAN/SPI/I2C/UART", "Makefile/CMake"]',
         '["FreeRTOS", "Zephyr", "AUTOSAR", "Bootloader development"]',
         7.0, "C/C++, RTOS, ARM Cortex-M/A, STM32/ESP32/NXP, CAN/SPI/I2C/UART, JTAG/SWD",
         "Firmware on physical hardware; interrupt handling; bare-metal memory management"),
        ("vlsi", "Semiconductor / Hardware", "India",
         '["Verilog", "SystemVerilog", "Synopsys/Cadence tools", "Digital Design"]',
         '["UVM", "Timing analysis", "DFT", "Low-power design"]',
         7.5, "Verilog/SystemVerilog, VHDL, Synopsys/Cadence, UVM, SPICE",
         "RTL passing timing closure + DRC/LVS; simulation coverage >95%"),

        ("devops", "Indian Product Company", "India",
         '["Linux", "Docker", "Kubernetes", "Terraform", "CI/CD", "Cloud (AWS/GCP)"]',
         '["Prometheus", "Grafana", "ArgoCD", "Service Mesh", "GitOps"]',
         7.0, "Linux, Docker, Kubernetes, Terraform/Ansible, CI/CD, monitoring, cloud",
         "IaC, observability setup, incident response, >99.9% uptime SLAs"),
        ("platform", "Indian Product Company", "India",
         '["Go", "Kubernetes", "Terraform", "CI/CD", "Cloud (AWS/GCP/Azure)", "Linux"]',
         '["Service Mesh", "GitOps", "Internal Developer Platforms", "Backstage"]',
         7.0, "Go/Python, Kubernetes, Terraform/Pulumi, CI/CD, cloud, Linux internals",
         "Internal developer platforms; API gateway design; developer experience tooling"),

        ("pm", "Indian Product Company", "India",
         '["PRD writing", "Roadmap prioritisation", "Stakeholder management", "Data-driven decisions"]',
         '["SQL", "A/B Testing", "User Research", "RICE/ICE frameworks"]',
         7.0, "PRDs, roadmap prioritisation, stakeholder management, user research, metrics definition",
         "Product sense + cross-functional collaboration; shipped features with measured impact"),

        ("ba", "Indian Service Company", "India",
         '["SQL", "Excel", "Requirements gathering", "BRD/FRD writing"]',
         '["Python", "Process mapping", "UAT coordination"]',
         6.0, "SQL, Excel, requirements gathering, BRD/FRD, stakeholder communication",
         "Domain knowledge + communication clarity; bridge business and technical teams"),
    ]
    with conn:
        for row in reqs:
            conn.execute(
                "INSERT INTO market_role_requirements (role_category, company_type, market, required_skills, preferred_skills, cgpa_cutoff, expected_stack, key_signals) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row
            )
    conn.close()


def _seed_company_naming() -> None:
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM market_company_naming").fetchone()[0]
    if count > 0:
        conn.close()
        return
    rules = [
        ("Indian Product Company", "India",
         '["Flipkart", "Swiggy", "Razorpay", "PhonePe", "CRED", "Meesho", "Zepto", "Navi", "Groww", "BrowserStack", "Freshworks", "Zoho", "Postman", "Hasura", "Chargebee", "Juspay"]'),
        ("Indian Service Company", "India",
         '["TCS", "Infosys", "Wipro", "Cognizant", "HCL Technologies", "Tech Mahindra", "LTIMindtree", "Mphasis", "Hexaware", "Persistent Systems"]'),
        ("FAANG / Big Tech", "India",
         '["Google", "Amazon", "Microsoft", "Meta", "Apple", "Adobe", "Salesforce", "Uber", "LinkedIn", "Atlassian", "Stripe", "Databricks", "Snowflake", "Intuit", "Cisco"]'),
        ("Startup", "India",
         '["Zepto", "Sarvam AI", "CRED", "Meesho", "Razorpay", "PhonePe", "BrowserStack", "Juspay", "Gnani.ai", "Haptik", "Yellow.ai", "Navi", "Groww", "Slice"]'),
        ("MNC India (Non-FAANG)", "India",
         '["Walmart Global Tech", "JPMorgan", "Goldman Sachs", "IBM", "Accenture", "Capgemini", "SAP Labs", "Oracle", "Bosch", "Siemens", "Visa", "Mastercard"]'),
        ("Semiconductor / Hardware", "India",
         '["Qualcomm", "NXP", "Texas Instruments", "Intel", "AMD", "Nvidia", "Broadcom", "Marvell", "Bosch", "Continental", "Renesas", "Infineon", "Tata Elxsi", "KPIT Technologies"]'),
        ("Consulting / IB", "India",
         '["McKinsey", "BCG", "Deloitte", "EY", "KPMG", "PwC", "Goldman Sachs", "JPMorgan", "Morgan Stanley", "Accenture Strategy"]'),
    ]
    with conn:
        for ct, market, names in rules:
            conn.execute(
                "INSERT INTO market_company_naming (company_type, market, example_names) VALUES (?, ?, ?)",
                (ct, market, names)
            )
    conn.close()


def _seed_role_weights() -> None:
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM market_role_weights").fetchone()[0]
    if count > 0:
        conn.close()
        return
    weights = [
        # role_category, dsa, projects, cgpa, experience, open_source, college_tier
        ("sde",            0.8, 0.6, 0.5, 0.5, 0.4, 0.4),
        ("ai_agentic",     0.3, 0.85, 0.4, 0.5, 0.5, 0.3),
        ("data_scientist", 0.3, 0.8, 0.4, 0.6, 0.5, 0.4),
        ("data_engineer",  0.4, 0.7, 0.4, 0.6, 0.5, 0.4),
        ("data_analyst",   0.2, 0.5, 0.4, 0.6, 0.3, 0.4),
        ("embedded",       0.2, 0.9, 0.5, 0.6, 0.2, 0.4),
        ("vlsi",           0.15, 0.9, 0.6, 0.6, 0.1, 0.5),
        ("devops",         0.3, 0.7, 0.3, 0.7, 0.5, 0.2),
        ("platform",       0.3, 0.7, 0.2, 0.8, 0.6, 0.15),
        ("pm",             0.0, 0.5, 0.4, 0.6, 0.4, 0.6),
        ("ba",             0.0, 0.4, 0.5, 0.5, 0.3, 0.6),
    ]
    with conn:
        for row in weights:
            conn.execute(
                "INSERT INTO market_role_weights (role_category, dsa, projects, cgpa, experience, open_source, college_tier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", row
            )
    conn.close()


def _seed_city_multipliers() -> None:
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM market_city_multipliers").fetchone()[0]
    if count > 0:
        conn.close()
        return
    multipliers = [
        # India cities — relative to Bangalore baseline (1.0)
        ("Bangalore", "India", 1.0),
        ("Hyderabad", "India", 0.9),
        ("Mumbai", "India", 0.95),
        ("Pune", "India", 0.75),
        ("Delhi NCR", "India", 0.85),
        ("Chennai", "India", 0.75),
        ("Kolkata", "India", 0.6),
        ("Kochi", "India", 0.55),
        ("Jaipur", "India", 0.5),
        ("Bhubaneswar", "India", 0.45),
        # USA
        ("San Francisco Bay Area", "USA", 1.0),
        ("New York", "USA", 0.95),
        ("Seattle", "USA", 0.9),
        ("Austin", "USA", 0.8),
        ("Boston", "USA", 0.85),
    ]
    with conn:
        for city, market, mult in multipliers:
            conn.execute(
                "INSERT INTO market_city_multipliers (city, market, multiplier) VALUES (?, ?, ?)",
                (city, market, mult)
            )
    conn.close()


# ── Run at import time ─────────────────────────────────────────────────────────
# seed_all() is called on first import to ensure tables exist with data.
# Subsequent imports are no-ops because seed functions check COUNT(*) first.

_seeded = False


def ensure_seeded() -> None:
    global _seeded
    if not _seeded:
        init_db()
        seed_all()
        _seeded = True
