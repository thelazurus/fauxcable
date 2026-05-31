import asyncio
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp

from fauxcable.config import Config
from fauxcable.database import (
    bulk_save_cache,
    bulk_save_unmatched,
    finish_run,
    load_cache_bulk,
    load_category_map,
    load_overrides_bulk,
    start_run,
)
from fauxcable.providers.tmdb import lookup_tmdb_poster
from fauxcable.providers.tvmaze import lookup_tvmaze_poster

logger = logging.getLogger(__name__)

_pipeline_lock = asyncio.Lock()
_run_status: dict = {"running": False}


def get_run_status() -> dict:
    return dict(_run_status)


# ---------------------------------------------------------------------------
# Title normalisation
# ---------------------------------------------------------------------------

def clean_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", title or "")
    title = re.sub(r"[\r\n\t  ]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()

    junk = {"new", "repeat", "encore", "hd", "us", "uk"}
    words = title.split()
    if words and words[-1].lower() in junk:
        words = words[:-1]

    channel_suffixes = {
        "on id", "on investigation discovery", "on oxygen", "on lifetime",
        "on a&e", "on amc", "on hallmark", "on discovery", "on tlc",
        "on food network",
    }
    lower = " ".join(words).lower()
    for suffix in channel_suffixes:
        if lower.endswith(suffix):
            words = " ".join(words).rsplit(suffix.split()[0], 1)[0].strip().split()
            break

    if len(words) > 12:
        words = words[:8]
    return " ".join(words)


def _norm(title: str) -> str:
    return unicodedata.normalize("NFKC", (title or "")).strip().lower()


def _is_movie(prog: ET.Element) -> bool:
    cats = [(c.text or "").lower() for c in prog.findall("category") if c.text]
    return any("movie" in c or "film" in c for c in cats)


def _primary_category(prog: ET.Element) -> str:
    cats = [(c.text or "").lower() for c in prog.findall("category") if c.text]
    if not cats:
        return "unknown"
    for key in ("news", "sports", "weather", "religious", "infomercial", "kids",
                "talk", "music", "reality", "documentary", "consumer", "series", "crime"):
        if any(key in c for c in cats):
            return key
    return cats[0]


def _generic_url(cat: str, cfg: Config) -> str:
    return f"{cfg.base_url}/generics/generic_{cat}.png"


# ---------------------------------------------------------------------------
# Per-programme processing
# ---------------------------------------------------------------------------

async def _lookup_poster(title: str, is_movie: bool, cfg: Config) -> Optional[str]:
    if is_movie:
        return await lookup_tmdb_poster(title, cfg) or await lookup_tvmaze_poster(title, cfg)
    return await lookup_tvmaze_poster(title, cfg) or await lookup_tmdb_poster(title, cfg)


async def _process_one(
    prog: ET.Element,
    sem: asyncio.Semaphore,
    cfg: Config,
    stats: dict,
    new_cache: dict,
    new_unmatched: list,
    cache_snap: dict,
    overrides_snap: dict,
    alias_map: dict,
):
    title_raw = (prog.findtext("title") or "").strip()
    if not title_raw or prog.find("icon") is not None:
        stats["skipped"] += 1
        return

    key = _norm(clean_title(title_raw))
    is_movie = _is_movie(prog)

    # Priority: manual override → cache (existing + newly found this run) → live lookup
    poster_url = overrides_snap.get(key)
    if poster_url:
        stats["overrides"] += 1
    else:
        poster_url = cache_snap.get(key) or new_cache.get(key, (None,))[0]
        if poster_url:
            stats["cache_hits"] += 1
        else:
            async with sem:
                poster_url = await _lookup_poster(key, is_movie, cfg)
                if cfg.rate_limit_delay > 0:
                    await asyncio.sleep(cfg.rate_limit_delay)

            if poster_url:
                source = "tmdb" if is_movie else "tvmaze"
                new_cache[key] = (poster_url, source)
                stats["new_lookups"] += 1
            else:
                cat = _primary_category(prog)
                # Apply category alias if one is configured (e.g. basketball→sports)
                resolved = alias_map.get(cat, cat)
                generic_file = Path("generics") / f"generic_{resolved}.png"
                if generic_file.exists():
                    poster_url = _generic_url(resolved, cfg)
                    new_cache[key] = (poster_url, f"generic:{resolved}")
                elif cfg.default_poster_url:
                    poster_url = cfg.default_poster_url
                    new_cache[key] = (poster_url, "default")
                else:
                    poster_url = _generic_url(resolved, cfg)
                    new_cache[key] = (poster_url, f"generic:{resolved}")
                new_unmatched.append((key, title_raw, cat))
                stats["generics"] += 1

    if poster_url:
        icon = ET.SubElement(prog, "icon")
        icon.set("src", poster_url)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(cfg: Config) -> dict:
    if _pipeline_lock.locked():
        raise RuntimeError("Pipeline already running")

    async with _pipeline_lock:
        run_id = await start_run()
        stats = {"cache_hits": 0, "new_lookups": 0, "overrides": 0, "generics": 0, "skipped": 0}
        _run_status.update({
            "running": True, "run_id": run_id, "stats": stats,
            "started": datetime.now(timezone.utc).isoformat(),
            "done": 0, "total": 0,
        })

        try:
            logger.info("Fetching EPG from %s", cfg.epg_url)
            xml_bytes = await _fetch_epg(cfg.epg_url)

            # Strip DOCTYPE if present — Python's XML parser can't handle XMLTV's DTD
            xml_bytes = re.sub(rb"<!DOCTYPE[^>]*>", b"", xml_bytes)

            root = ET.fromstring(xml_bytes)
            programmes = list(root.findall("programme"))
            total = len(programmes)
            logger.info("Loaded %d programmes", total)
            _run_status["total"] = total

            cache_snap = await load_cache_bulk()
            overrides_snap = await load_overrides_bulk()
            alias_map = await load_category_map()

            sem = asyncio.Semaphore(cfg.concurrency)
            new_cache: dict[str, tuple[str, str]] = {}
            new_unmatched: list[tuple[str, str, str]] = []

            batch_size = 500
            for start in range(0, total, batch_size):
                batch = programmes[start:start + batch_size]
                await asyncio.gather(*[
                    _process_one(p, sem, cfg, stats, new_cache, new_unmatched, cache_snap, overrides_snap, alias_map)
                    for p in batch
                ])
                _run_status.update({"done": start + len(batch), "stats": dict(stats)})

            await bulk_save_cache(new_cache)
            await bulk_save_unmatched(new_unmatched)

            output = Path("data/enriched.xml")
            output.parent.mkdir(parents=True, exist_ok=True)
            ET.ElementTree(root).write(str(output), encoding="utf-8", xml_declaration=True)

            await finish_run(run_id, "success", stats)
            logger.info(
                "Done | cache:%d new:%d overrides:%d generics:%d skipped:%d",
                stats["cache_hits"], stats["new_lookups"], stats["overrides"],
                stats["generics"], stats["skipped"],
            )

            await _trigger_jellyfin(cfg)
            return stats

        except Exception as exc:
            logger.exception("Pipeline failed: %s", exc)
            await finish_run(run_id, "error", stats)
            raise
        finally:
            _run_status["running"] = False


async def _fetch_epg(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as r:
            r.raise_for_status()
            return await r.read()


async def _trigger_jellyfin(cfg: Config):
    if not cfg.jellyfin_url or not cfg.jellyfin_api_key:
        return
    paths = ["/LiveTv/RefreshGuide", "/emby/LiveTv/RefreshGuide"]
    async with aiohttp.ClientSession() as session:
        for path in paths:
            try:
                async with session.post(
                    f"{cfg.jellyfin_url}{path}?api_key={cfg.jellyfin_api_key}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status in (200, 204):
                        logger.info("Jellyfin guide refresh triggered via %s", path)
                        return
            except Exception:
                pass
    logger.warning("Jellyfin guide refresh failed")
