"""
Pre-populate script — run once before launch.
Ingests market intelligence for all Tier 1/2 combinations.
Takes ~45-60 minutes total. Run in a separate terminal.

Usage:
    cd /home/sarvesh/projects/roast
    uv run python3 scripts/prepopulate.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import time
from ingestion.pipeline import run_ingestion_for_combo

COMBINATIONS = [
    # ── SDE roles — all company types × India ──────────────────────────────────
    ('Software Engineer / Associate', 'Indian Product Company', 'India'),
    ('Software Engineer / Associate', 'Indian Service Company', 'India'),
    ('Software Engineer / Associate', 'MNC India (Non-FAANG)', 'India'),
    ('Software Engineer / Associate', 'FAANG / Big Tech', 'India'),
    ('Software Engineer / Associate', 'Startup', 'India'),
    ('Software Engineer / Associate', 'Consulting / IB', 'India'),
    ('SDE1', 'Indian Product Company', 'India'),
    ('SDE1', 'Indian Service Company', 'India'),
    ('SDE1', 'MNC India (Non-FAANG)', 'India'),
    ('SDE1', 'FAANG / Big Tech', 'India'),
    ('SDE1', 'Startup', 'India'),
    ('SDE2 / Senior SDE', 'Indian Product Company', 'India'),
    ('SDE2 / Senior SDE', 'Indian Service Company', 'India'),
    ('SDE2 / Senior SDE', 'MNC India (Non-FAANG)', 'India'),
    ('SDE2 / Senior SDE', 'FAANG / Big Tech', 'India'),
    ('SDE2 / Senior SDE', 'Startup', 'India'),
    ('Full Stack Engineer', 'Indian Product Company', 'India'),
    ('Full Stack Engineer', 'MNC India (Non-FAANG)', 'India'),
    ('Full Stack Engineer', 'Startup', 'India'),
    ('Backend Engineer', 'Indian Product Company', 'India'),
    ('Backend Engineer', 'MNC India (Non-FAANG)', 'India'),
    ('Backend Engineer', 'Startup', 'India'),
    ('Backend Engineer', 'FAANG / Big Tech', 'India'),

    # ── AI Agentic Engineer — all relevant company types × India ───────────────
    ('AI Agentic Engineer', 'Indian Product Company', 'India'),
    ('AI Agentic Engineer', 'Startup', 'India'),
    ('AI Agentic Engineer', 'FAANG / Big Tech', 'India'),
    ('AI Agentic Engineer', 'MNC India (Non-FAANG)', 'India'),

    # ── Data roles — India ─────────────────────────────────────────────────────
    ('Data Scientist', 'Indian Product Company', 'India'),
    ('Data Scientist', 'MNC India (Non-FAANG)', 'India'),
    ('Data Scientist', 'FAANG / Big Tech', 'India'),
    ('Data Scientist', 'Startup', 'India'),
    ('Data Scientist', 'Consulting / IB', 'India'),
    ('Data Engineer', 'Indian Product Company', 'India'),
    ('Data Engineer', 'MNC India (Non-FAANG)', 'India'),
    ('Data Engineer', 'FAANG / Big Tech', 'India'),
    ('Data Engineer', 'Startup', 'India'),
    ('Data Analyst', 'Indian Product Company', 'India'),
    ('Data Analyst', 'Indian Service Company', 'India'),
    ('Data Analyst', 'MNC India (Non-FAANG)', 'India'),
    ('Data Analyst', 'FAANG / Big Tech', 'India'),
    ('Data Analyst', 'Startup', 'India'),
    ('Data Analyst', 'Consulting / IB', 'India'),

    # ── Hardware roles — India ─────────────────────────────────────────────────
    ('VLSI Design Engineer', 'Semiconductor / Hardware', 'India'),
    ('VLSI Design Engineer', 'Indian Product Company', 'India'),
    ('VLSI Design Engineer', 'MNC India (Non-FAANG)', 'India'),
    ('VLSI Design Engineer', 'FAANG / Big Tech', 'India'),
    ('Embedded Systems Engineer', 'Semiconductor / Hardware', 'India'),
    ('Embedded Systems Engineer', 'Indian Product Company', 'India'),
    ('Embedded Systems Engineer', 'MNC India (Non-FAANG)', 'India'),
    ('Embedded Systems Engineer', 'FAANG / Big Tech', 'India'),

    # ── DevOps / Platform — India ──────────────────────────────────────────────
    ('DevOps / SRE', 'Indian Product Company', 'India'),
    ('DevOps / SRE', 'Startup', 'India'),
    ('DevOps / SRE', 'FAANG / Big Tech', 'India'),
    ('DevOps / SRE', 'MNC India (Non-FAANG)', 'India'),
    ('Platform Engineer', 'Indian Product Company', 'India'),
    ('Platform Engineer', 'FAANG / Big Tech', 'India'),
    ('Platform Engineer', 'MNC India (Non-FAANG)', 'India'),

    # ── PM / BA — India ────────────────────────────────────────────────────────
    ('Product Manager', 'Indian Product Company', 'India'),
    ('Product Manager', 'Startup', 'India'),
    ('Product Manager', 'FAANG / Big Tech', 'India'),
    ('Business Analyst', 'Indian Product Company', 'India'),
    ('Business Analyst', 'Indian Service Company', 'India'),
    ('Business Analyst', 'MNC India (Non-FAANG)', 'India'),
    ('Business Analyst', 'Consulting / IB', 'India'),

    # ── USA key combos ─────────────────────────────────────────────────────────
    ('Software Engineer / Associate', 'FAANG / Big Tech', 'USA'),
    ('SDE2 / Senior SDE', 'FAANG / Big Tech', 'USA'),
    ('AI Agentic Engineer', 'FAANG / Big Tech', 'USA'),
    ('AI Agentic Engineer', 'Startup', 'USA'),
    ('Data Scientist', 'FAANG / Big Tech', 'USA'),
    ('Data Engineer', 'FAANG / Big Tech', 'USA'),

    # ── UAE key combos ─────────────────────────────────────────────────────────
    ('Software Engineer / Associate', 'Indian Product Company', 'UAE'),
    ('AI Agentic Engineer', 'Startup', 'UAE'),

    # ── Singapore key combos ───────────────────────────────────────────────────
    ('Software Engineer / Associate', 'FAANG / Big Tech', 'Singapore'),
    ('SDE2 / Senior SDE', 'FAANG / Big Tech', 'Singapore'),

    # ── UK key combos ──────────────────────────────────────────────────────────
    ('Software Engineer / Associate', 'FAANG / Big Tech', 'UK'),
    ('AI Agentic Engineer', 'Startup', 'UK'),
]


async def main():
    total = len(COMBINATIONS)
    success = 0
    failed = 0

    print(f"Pre-populating {total} combinations...")
    print("=" * 60)

    sem = asyncio.Semaphore(3)

    async def run_one(role, company_type, market, idx):
        nonlocal success, failed
        async with sem:
            print(f"\n[{idx}/{total}] {role} / {company_type} / {market}")
            start = time.time()
            try:
                summary = await run_ingestion_for_combo(
                    role=role, company_type=company_type, market=market,
                    force_refresh=False,
                )
                elapsed = round(time.time() - start, 1)
                print(f"  Stored: {summary.signals_stored} | Discarded: {summary.signals_discarded} | {elapsed}s")
                if summary.signals_stored > 0:
                    success += 1
                else:
                    print(f"  WARNING: 0 signals stored")
                    failed += 1
            except Exception as e:
                print(f"  FAILED: {e}")
                failed += 1

    tasks = [
        run_one(role, company_type, market, i + 1)
        for i, (role, company_type, market) in enumerate(COMBINATIONS)
    ]
    await asyncio.gather(*tasks)

    print("\n" + "=" * 60)
    print(f"Done. {success} succeeded, {failed} failed.")

    # Invalidate all DIVE Redis snapshots so next requests get fresh data
    try:
        from backend.storage.redis_client import redis
        cursor = 0
        invalidated = 0
        while True:
            cursor, keys = redis.scan(cursor, match="snapshot:*", count=100)
            for key in keys:
                redis.delete(key)
                invalidated += 1
            if cursor == 0:
                break
        print(f"Invalidated {invalidated} DIVE snapshot(s) — users will get fresh data.")
    except Exception as e:
        print(f"Warning: Could not invalidate DIVE snapshots: {e}")


if __name__ == "__main__":
    asyncio.run(main())
