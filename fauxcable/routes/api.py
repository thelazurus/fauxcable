from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from fauxcable import database as db
from fauxcable.config import get_config, save_config
from fauxcable.pipeline import get_run_status, run_pipeline
from fauxcable.providers.tmdb import search_tmdb
from fauxcable.providers.tvmaze import search_tvmaze
from fauxcable.scheduler import next_run_time, reschedule

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Pipeline control
# ---------------------------------------------------------------------------

@router.post("/run")
async def trigger_run(background_tasks: BackgroundTasks):
    status = get_run_status()
    if status.get("running"):
        raise HTTPException(status_code=409, detail="Pipeline already running")
    cfg = get_config()
    if not cfg.dispatcharr_epg_url:
        raise HTTPException(status_code=400, detail="dispatcharr_epg_url not configured")
    background_tasks.add_task(run_pipeline, cfg)
    return {"status": "started"}


@router.get("/status")
async def pipeline_status():
    status = get_run_status()
    status["next_run"] = next_run_time()
    return status


@router.get("/history")
async def run_history():
    return await db.list_runs()


@router.get("/stats")
async def summary_stats():
    return await db.get_summary_stats()


# ---------------------------------------------------------------------------
# Manual match overrides
# ---------------------------------------------------------------------------

@router.get("/unmatched")
async def list_unmatched(limit: int = 100, offset: int = 0):
    return await db.list_unmatched(limit, offset)


@router.get("/overrides")
async def list_overrides():
    return await db.list_overrides()


class OverrideIn(BaseModel):
    title_key: str
    poster_url: str
    match_name: str
    match_source: str


@router.post("/override")
async def save_override(body: OverrideIn):
    await db.save_override(body.title_key, body.poster_url, body.match_name, body.match_source)
    return {"status": "ok"}


@router.delete("/override/{title_key:path}")
async def delete_override(title_key: str):
    await db.delete_override(title_key)
    return {"status": "ok"}


@router.delete("/unmatched/all")
async def dismiss_all():
    count = await db.dismiss_all()
    return {"status": "ok", "dismissed": count}


@router.delete("/unmatched/category/{category}")
async def dismiss_category(category: str):
    count = await db.dismiss_category(category)
    return {"status": "ok", "dismissed": count}


@router.delete("/unmatched/{title_key:path}")
async def dismiss_unmatched(title_key: str):
    """Dismiss a single item from the review queue (keeps generic in cache)."""
    await db.dismiss_unmatched(title_key)
    return {"status": "ok"}


@router.get("/categories")
async def list_categories():
    return await db.list_categories()


# ---------------------------------------------------------------------------
# Live search (for manual match UI)
# ---------------------------------------------------------------------------

@router.get("/search")
async def search(q: str, type: str = "show"):
    if not q or len(q) < 2:
        return []
    cfg = get_config()
    if type == "movie":
        return await search_tmdb(q, cfg)
    return await search_tvmaze(q, cfg)


# ---------------------------------------------------------------------------
# Debug
# ---------------------------------------------------------------------------

@router.post("/debug/full-reset")
async def debug_full_reset():
    await db.full_reset()
    enriched = Path("data/enriched.xml")
    if enriched.exists():
        enriched.unlink()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@router.post("/settings")
async def update_settings(body: dict):
    save_config(body)
    cfg = get_config()
    if "schedule_interval_hours" in body:
        reschedule(float(body["schedule_interval_hours"]))
    return {"status": "ok", "config": cfg.__dict__}
