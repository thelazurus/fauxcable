import re
import logging
import aiohttp
from fauxcable.utils.http import fetch_with_retry

logger = logging.getLogger(__name__)


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


async def lookup_tmdb_poster(title: str, cfg) -> str | None:
    if not cfg.tmdb_enabled or not cfg.tmdb_api_key:
        return None

    url = f"https://api.themoviedb.org/3/search/movie?api_key={cfg.tmdb_api_key}&query={title.strip()}"
    try:
        async with aiohttp.ClientSession() as session:
            data = await fetch_with_retry(session, url, cfg.retry_attempts, cfg.retry_delay)
    except Exception as e:
        logger.warning("TMDB error for %r: %s", title, e)
        return None

    if not data or "results" not in data:
        return None

    norm_q = _norm(title)
    best_url, best_score, best_name = None, 0.0, None

    for entry in data["results"]:
        name = entry.get("title") or entry.get("original_title", "")
        if not entry.get("poster_path") or not name:
            continue
        norm_name = _norm(name)
        if norm_name == norm_q:
            score = 1.0
        elif norm_q in norm_name or norm_name in norm_q:
            score = 0.8
        else:
            score = min(float(entry.get("popularity", 0)) / 100, 0.5)

        if score > best_score:
            best_score = score
            best_url = f"https://image.tmdb.org/t/p/w500{entry['poster_path']}"
            best_name = name
        if best_score >= 0.8:
            break

    if best_url:
        logger.info("TMDB: %r → %s [%.0f]", title, best_name, best_score * 100)
    return best_url


async def search_tmdb(q: str, cfg) -> list[dict]:
    """Return up to 8 candidate results for the manual match UI."""
    if not cfg.tmdb_enabled or not cfg.tmdb_api_key:
        return []

    url = f"https://api.themoviedb.org/3/search/movie?api_key={cfg.tmdb_api_key}&query={q}"
    try:
        async with aiohttp.ClientSession() as session:
            data = await fetch_with_retry(session, url, 2, 1)
    except Exception:
        return []

    if not data or "results" not in data:
        return []

    results = []
    for entry in data["results"][:8]:
        if not entry.get("poster_path"):
            continue
        results.append({
            "id": entry.get("id"),
            "name": entry.get("title") or entry.get("original_title", ""),
            "type": "movie",
            "poster_url": f"https://image.tmdb.org/t/p/w300{entry['poster_path']}",
            "year": (entry.get("release_date") or "")[:4],
            "network": "",
        })
    return results
