import re
import logging
import aiohttp
from fauxcable.utils.http import fetch_with_retry

logger = logging.getLogger(__name__)


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


async def lookup_tvmaze_poster(title: str, cfg) -> str | None:
    url = f"https://api.tvmaze.com/search/shows?q={title}"
    try:
        async with aiohttp.ClientSession() as session:
            data = await fetch_with_retry(session, url, cfg.retry_attempts, cfg.retry_delay)
    except Exception as e:
        logger.warning("TVMaze error for %r: %s", title, e)
        return None

    if not data:
        return None

    norm_q = _norm(title)
    best, best_score = None, 0.0

    for entry in data:
        show = entry.get("show", {})
        if not show.get("image") or not show.get("name"):
            continue
        score = float(entry.get("score", 0))
        norm_name = _norm(show["name"])

        if norm_name == norm_q or score >= 0.7 or norm_q in norm_name:
            best, best_score = show, score
            break
        if score > best_score:
            best, best_score = show, score

    if best and best.get("image"):
        logger.info("TVMaze: %r → %s [%.0f]", title, best["name"], best_score * 100)
        img = best["image"]
        return img.get("original") or img.get("medium")

    for entry in data:
        img = entry.get("show", {}).get("image")
        if img:
            return img.get("original") or img.get("medium")
    return None


async def search_tvmaze(q: str, cfg) -> list[dict]:
    """Return up to 8 candidate results for the manual match UI."""
    url = f"https://api.tvmaze.com/search/shows?q={q}"
    try:
        async with aiohttp.ClientSession() as session:
            data = await fetch_with_retry(session, url, 2, 1)
    except Exception:
        return []

    if not data:
        return []

    results = []
    for entry in data[:8]:
        show = entry.get("show", {})
        img = show.get("image")
        if not img:
            continue
        results.append({
            "id": show.get("id"),
            "name": show.get("name", ""),
            "type": "show",
            "poster_url": img.get("original") or img.get("medium", ""),
            "year": (show.get("premiered") or "")[:4],
            "network": (show.get("network") or {}).get("name", ""),
        })
    return results
