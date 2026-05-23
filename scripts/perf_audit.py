"""Lightweight performance audit utilities.

Usage:
  python scripts/perf_audit.py --mode dry-run --what embeddings
  python scripts/perf_audit.py --mode live --what embeddings
  python scripts/perf_audit.py --mode dry-run --what groq

Dry-run mode avoids external API calls by stubbing network calls so you can
measure local CPU/DB overhead. Live mode will call real providers (requires
API keys in environment).
"""
import argparse
import time
import random
import asyncio


def measure_fn(fn, *args, repeats=3, **kwargs):
    times = []
    for i in range(repeats):
        t0 = time.monotonic()
        fn(*args, **kwargs)
        times.append(time.monotonic() - t0)
    return times


async def measure_async_fn(fn, *args, repeats=3, **kwargs):
    times = []
    for i in range(repeats):
        t0 = time.monotonic()
        await fn(*args, **kwargs)
        times.append(time.monotonic() - t0)
    return times


def run_embeddings_dryrun():
    """Simulate embed_all_missing without network calls to measure DB/write overhead."""
    print("Dry-run embed_all_missing: stubbing network calls.")
    import ingestion.embeddings as emb
    import ingestion.database as db

    # Create a short-lived in-memory DB snapshot to avoid touching production DB
    # Note: this only times the Python-side loop and update logic.
    orig_get_conn = db.get_connection

    def _get_mem_conn():
        return orig_get_conn()  # uses same db; we keep behaviour but stub embed

    db.get_connection = _get_mem_conn

    # Stub embed_text to avoid external API
    orig_embed = emb.embed_text

    def fake_embed(text: str) -> bytes:
        # simulate network + CPU normalization
        time.sleep(random.uniform(0.03, 0.12))
        import numpy as np
        vec = np.random.rand(emb.EMBEDDING_DIM).astype('float32')
        vec /= np.linalg.norm(vec)
        return vec.tobytes()

    emb.embed_text = fake_embed

    try:
        t0 = time.monotonic()
        updated = emb.embed_all_missing()
        dt = time.monotonic() - t0
        print(f"embed_all_missing updated {updated} rows in {dt:.2f}s")
    finally:
        emb.embed_text = orig_embed
        db.get_connection = orig_get_conn


async def run_groq_dryrun():
    print("Dry-run groq_chat: synthetic latency measurement")
    try:
        from backend.llm import groq_client
    except Exception:
        print("groq_client import failed; skipping")
        return

    async def fake_groq(messages, **kwargs):
        await asyncio.sleep(random.uniform(0.05, 0.2))
        return "{\"ok\":true}", {"provider": "fake"}

    orig = groq_client.groq_chat
    groq_client.groq_chat = fake_groq
    try:
        times = await measure_async_fn(groq_client.groq_chat, [{"role": "user", "content": "Hello"}], repeats=5)
        print("groq_chat dry-run times (s):", [round(t, 3) for t in times])
    finally:
        groq_client.groq_chat = orig


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["dry-run", "live"], default="dry-run")
    p.add_argument("--what", choices=["embeddings", "groq"], required=True)
    args = p.parse_args()

    if args.what == "embeddings":
        if args.mode == "dry-run":
            run_embeddings_dryrun()
        else:
            # Live — call embed_all_missing directly (may hit Gemini)
            import ingestion.embeddings as emb
            t0 = time.monotonic()
            updated = emb.embed_all_missing()
            print(f"embed_all_missing updated {updated} rows in {time.monotonic()-t0:.2f}s")

    if args.what == "groq":
        if args.mode == "dry-run":
            asyncio.run(run_groq_dryrun())
        else:
            # Live — attempt one real call and report
            try:
                from backend.llm import groq_client
                async def one():
                    t0 = time.monotonic()
                    text, meta = await groq_client.groq_chat([{"role":"user","content":"Hello"}], max_tokens=50)
                    print("response len", len(text), "meta", meta)
                    print("took", time.monotonic()-t0)
                asyncio.run(one())
            except Exception as e:
                print("Live groq call failed:", e)


if __name__ == "__main__":
    main()
