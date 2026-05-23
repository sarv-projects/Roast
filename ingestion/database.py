import sqlite3
from pathlib import Path

# The database file lives next to this file in the ingestion/ folder
DB_PATH = Path(__file__).parent / "market_intel.db"


def get_connection() -> sqlite3.Connection:
    """
    Open a connection to the SQLite database.
    Every caller gets their own connection — SQLite is not thread-safe with shared connections.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["role"] instead of row[0]
    return conn


def init_db() -> None:
    """
    Create all tables if they don't already exist.
    Safe to call multiple times — IF NOT EXISTS prevents duplicate creation.
    """
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")

    # Integrity check on startup — detect corruption from container restarts
    result = conn.execute("PRAGMA integrity_check").fetchone()
    if result[0] != "ok":
        import structlog
        logger = structlog.get_logger()
        logger.error("market_intel_db_corrupt", result=result[0])
        raise RuntimeError(f"market_intel.db is corrupt: {result[0]}")

    with conn:
        # ── Main table ────────────────────────────────────────────────────────
        # One row = one scraped market signal
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                role        TEXT    NOT NULL,
                company_type TEXT   NOT NULL,
                market      TEXT    NOT NULL,
                source      TEXT    NOT NULL,
                signal_type TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                fetched_at  INTEGER NOT NULL,
                embedding   BLOB
            )
        """)

        # ── Index on the three filter columns ────────────────────────────────
        # When we query "give me all signals for SDE2, India, Indian Product Company"
        # SQLite scans the index instead of every row — much faster
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_combo
            ON market_signals (role, company_type, market)
        """)

        # ── FTS5 virtual table ────────────────────────────────────────────────
        # Indexes content and signal_type for fast full-text search
        # content="" means FTS5 is a "contentless" index — it points to market_signals
        # but doesn't duplicate the text (saves disk space)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS market_signals_fts
            USING fts5(
                content,
                signal_type,
                content="market_signals",
                content_rowid="id"
            )
        """)

        # ── Trigger: keep FTS5 in sync automatically ─────────────────────────
        # Every time a row is inserted into market_signals,
        # this trigger fires and inserts the same row into the FTS5 index
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS market_signals_ai
            AFTER INSERT ON market_signals BEGIN
                INSERT INTO market_signals_fts (rowid, content, signal_type)
                VALUES (new.id, new.content, new.signal_type);
            END
        """)

        # ── Trigger: keep FTS5 in sync on delete ─────────────────────────────
        # When a row is deleted from market_signals (e.g. monthly cron cleanup),
        # remove it from the FTS5 index too
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS market_signals_ad
            AFTER DELETE ON market_signals BEGIN
                INSERT INTO market_signals_fts (market_signals_fts, rowid, content, signal_type)
                VALUES ('delete', old.id, old.content, old.signal_type);
            END
        """)

    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database initialised at {DB_PATH}")
