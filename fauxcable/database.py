import json
import aiosqlite
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/fauxcable.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS poster_cache (
    title_key   TEXT PRIMARY KEY,
    poster_url  TEXT NOT NULL,
    source      TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manual_overrides (
    title_key    TEXT PRIMARY KEY,
    poster_url   TEXT NOT NULL,
    match_name   TEXT,
    match_source TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unmatched (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title_key     TEXT UNIQUE NOT NULL,
    display_title TEXT NOT NULL,
    category      TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    run_count     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS run_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    stats       TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


# ---------------------------------------------------------------------------
# Bulk load helpers (used by pipeline to avoid per-row DB round-trips)
# ---------------------------------------------------------------------------

async def load_cache_bulk() -> dict[str, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT title_key, poster_url FROM poster_cache") as cur:
            return {r[0]: r[1] for r in await cur.fetchall()}


async def load_overrides_bulk() -> dict[str, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT title_key, poster_url FROM manual_overrides") as cur:
            return {r[0]: r[1] for r in await cur.fetchall()}


async def bulk_save_cache(entries: dict[str, tuple[str, str]]):
    """entries: {title_key: (poster_url, source)}"""
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT OR REPLACE INTO poster_cache (title_key, poster_url, source, updated_at) VALUES (?,?,?,?)",
            [(k, v[0], v[1], now) for k, v in entries.items()],
        )
        await db.commit()


async def bulk_save_unmatched(entries: list[tuple[str, str, str]]):
    """entries: [(title_key, display_title, category), ...]"""
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        for title_key, display_title, category in entries:
            await db.execute(
                """INSERT INTO unmatched (title_key, display_title, category, last_seen, run_count)
                   VALUES (?,?,?,?,1)
                   ON CONFLICT(title_key) DO UPDATE SET
                     last_seen=excluded.last_seen,
                     run_count=run_count+1""",
                (title_key, display_title, category, now),
            )
        await db.commit()


# ---------------------------------------------------------------------------
# Manual override management
# ---------------------------------------------------------------------------

async def save_override(title_key: str, poster_url: str, match_name: str, match_source: str):
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO manual_overrides
               (title_key, poster_url, match_name, match_source, created_at)
               VALUES (?,?,?,?,?)""",
            (title_key, poster_url, match_name, match_source, now),
        )
        # Also update cache so next EPG serve picks it up immediately
        await db.execute(
            "INSERT OR REPLACE INTO poster_cache (title_key, poster_url, source, updated_at) VALUES (?,?,?,?)",
            (title_key, poster_url, "manual", now),
        )
        await db.execute("DELETE FROM unmatched WHERE title_key=?", (title_key,))
        await db.commit()


async def delete_override(title_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM manual_overrides WHERE title_key=?", (title_key,))
        await db.execute("DELETE FROM poster_cache WHERE title_key=?", (title_key,))
        await db.commit()


async def dismiss_all() -> int:
    """Dismiss every item in the review queue (keeps generics in cache)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM unmatched")
        await db.commit()
        return cur.rowcount


async def full_reset():
    """Wipe poster cache, manual overrides, match queue, and run history for a clean slate."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM poster_cache")
        await db.execute("DELETE FROM manual_overrides")
        await db.execute("DELETE FROM unmatched")
        await db.execute("DELETE FROM run_history")
        await db.commit()


async def dismiss_unmatched(title_key: str):
    """Remove from review queue; keeps generic poster in cache so it doesn't re-queue."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM unmatched WHERE title_key=?", (title_key,))
        await db.commit()


async def dismiss_category(category: str) -> int:
    """Dismiss all unmatched items in a category. Returns count dismissed."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM unmatched WHERE category=?", (category,))
        await db.commit()
        return cur.rowcount


async def list_categories() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT category, COUNT(*) as count FROM unmatched GROUP BY category ORDER BY count DESC"
        ) as cur:
            return [{"category": r[0], "count": r[1]} for r in await cur.fetchall()]


async def list_unmatched(limit: int = 100, offset: int = 0, category: str | None = None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if category:
            q = "SELECT * FROM unmatched WHERE category=? ORDER BY run_count DESC, last_seen DESC LIMIT ? OFFSET ?"
            params = (category, limit, offset)
        else:
            q = "SELECT * FROM unmatched ORDER BY run_count DESC, last_seen DESC LIMIT ? OFFSET ?"
            params = (limit, offset)
        async with db.execute(q, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def list_overrides() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM manual_overrides ORDER BY created_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def count_unmatched() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM unmatched") as cur:
            return (await cur.fetchone())[0]


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------

async def start_run() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO run_history (started_at, status) VALUES (?,?)",
            (_now(), "running"),
        )
        await db.commit()
        return cur.lastrowid


async def finish_run(run_id: int, status: str, stats: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE run_history SET finished_at=?, status=?, stats=? WHERE id=?",
            (_now(), status, json.dumps(stats), run_id),
        )
        await db.commit()


async def list_runs(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM run_history ORDER BY started_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        if r.get("stats"):
            r["stats"] = json.loads(r["stats"])
    return rows


async def get_cache_entry(title_key: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM poster_cache WHERE title_key=?", (title_key,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def list_cache(
    limit: int = 60,
    offset: int = 0,
    source: str | None = None,
    search: str | None = None,
) -> list[dict]:
    conditions, params = [], []
    if source == "generic":
        conditions.append("source LIKE 'generic:%'")
    elif source:
        conditions.append("source=?")
        params.append(source)
    if search:
        conditions.append("title_key LIKE ?")
        params.append(f"%{search.lower()}%")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM poster_cache {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params,
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def count_cache(source: str | None = None, search: str | None = None) -> int:
    conditions, params = [], []
    if source == "generic":
        conditions.append("source LIKE 'generic:%'")
    elif source:
        conditions.append("source=?")
        params.append(source)
    if search:
        conditions.append("title_key LIKE ?")
        params.append(f"%{search.lower()}%")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"SELECT COUNT(*) FROM poster_cache {where}", params) as cur:
            return (await cur.fetchone())[0]


async def list_cache_sources() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT CASE WHEN source LIKE 'generic:%' THEN 'generic' ELSE source END as src,
                   COUNT(*) as count
            FROM poster_cache GROUP BY src ORDER BY count DESC
        """) as cur:
            return [{"source": r[0], "count": r[1]} for r in await cur.fetchall()]


async def get_summary_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM poster_cache") as c:
            cached = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM manual_overrides") as c:
            overrides = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM unmatched") as c:
            unmatched = (await c.fetchone())[0]
    return {"cached": cached, "overrides": overrides, "unmatched": unmatched}
