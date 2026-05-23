"""
Embeddings using Google Gemini gemini-embedding-001.
Replaces sentence-transformers to avoid shipping PyTorch/CUDA in production.
3072 dimensions, free tier: 1000 req/day per key.
"""

import os
import time
import numpy as np
from ingestion.database import get_connection

EMBEDDING_DIM = 3072
GEMINI_EMBEDDING_DAILY_LIMIT = 1_000_000  # 1M tokens/day free tier

_key_index = 0


def _track_embedding_usage(token_count: int) -> None:
    """Track embedding token usage in Redis. Warn if approaching free tier limit."""
    try:
        from backend.storage.redis_client import redis
        key = "gemini:embedding:tokens:today"
        total = redis.incrby(key, token_count)
        if total == 1:
            # Set TTL to midnight UTC
            from datetime import datetime, timezone, timedelta
            import random
            now = datetime.now(timezone.utc)
            midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            base_ttl = int((midnight - now).total_seconds())
            redis.expire(key, base_ttl + random.randint(0, 300))
        if total > GEMINI_EMBEDDING_DAILY_LIMIT * 0.8:
            import structlog
            logger = structlog.get_logger()
            logger.warning("gemini_embedding_near_limit", tokens_used=total, limit=GEMINI_EMBEDDING_DAILY_LIMIT)
    except Exception:
        pass  # Never let tracking break embeddings


def embed_text(text: str) -> bytes:
    """
    Convert a string into a 3072-dimensional embedding vector via Gemini.
    Returns the vector as raw bytes (BLOB) for SQLite storage.
    Rotates API keys on 429.
    """
    global _key_index
    from google import genai
    api_keys = os.getenv("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in api_keys.split(",") if k.strip()]

    for attempt in range(len(keys)):
        key = keys[_key_index % len(keys)]
        client = genai.Client(api_key=key)
        try:
            result = client.models.embed_content(
                model="gemini-embedding-001",
                contents=text,
            )
            vector = np.array(result.embeddings[0].values, dtype=np.float32)
            norm = np.linalg.norm(vector)
            if norm > 0:
                vector = vector / norm
            # Track usage — estimate tokens as ~4 chars per token
            _track_embedding_usage(max(1, len(text) // 4))
            return vector.tobytes()
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                # Rotate to next key
                _key_index += 1
                if attempt < len(keys) - 1:
                    continue  # try next key immediately
            raise  # non-429 error or all keys exhausted


def bytes_to_vector(blob: bytes) -> np.ndarray:
    """Convert raw bytes from SQLite back into a numpy array."""
    return np.frombuffer(blob, dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity via dot product (vectors are pre-normalized)."""
    return float(np.dot(a, b))


def update_embedding(row_id: int, text: str) -> None:
    """Generate an embedding for text and store it for the given row_id."""
    embedding_bytes = embed_text(text)
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE market_signals SET embedding = ? WHERE id = ?",
            (embedding_bytes, row_id),
        )
    conn.close()


def embed_all_missing() -> int:
    """
    Find all rows with no embedding and generate one for each.
    Called after bulk inserts. Rate-limited to stay within Gemini free tier.
    Returns number of rows updated.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, content FROM market_signals WHERE embedding IS NULL"
    ).fetchall()
    conn.close()

    for i, row in enumerate(rows):
        update_embedding(row["id"], row["content"])
        # 1500 RPM = 25 RPS — small sleep to avoid bursting
        if i > 0 and i % 20 == 0:
            time.sleep(1)

    return len(rows)


def search_by_embedding(
    query: str,
    role: str,
    company_type: str,
    market: str,
    limit: int = 15,
) -> list[dict]:
    """
    Find the most semantically similar signals to query
    for a given role + company_type + market combination.
    """
    query_vector = bytes_to_vector(embed_text(query))

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, role, company_type, market, source, signal_type, content, embedding
        FROM market_signals
        WHERE role = ? AND company_type = ? AND market = ?
        AND embedding IS NOT NULL
        AND fetched_at > (strftime('%s', 'now') - 3888000)
        """,
        (role, company_type, market),
    ).fetchall()
    conn.close()

    scored = []
    for row in rows:
        if not row["embedding"]:
            continue
        vector = bytes_to_vector(row["embedding"])
        # Dimension mismatch guard — old 384-dim embeddings vs new 768-dim
        if len(vector) != EMBEDDING_DIM:
            continue
        score = cosine_similarity(query_vector, vector)
        scored.append({
            "id": row["id"],
            "role": row["role"],
            "company_type": row["company_type"],
            "market": row["market"],
            "source": row["source"],
            "signal_type": row["signal_type"],
            "content": row["content"],
            "score": score,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]
