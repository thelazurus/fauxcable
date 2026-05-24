from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# URL validation (SSRF defence)
# ---------------------------------------------------------------------------

_ALLOWED_URL_SCHEMES = {"http", "https"}
# Fields in the settings payload that contain URLs fetched server-side
_URL_FIELDS = ("epg_url", "jellyfin_url", "base_url")


def _validate_url(value: str, field: str) -> None:
    """Raise HTTPException 400 if *value* is not a plain http/https URL."""
    try:
        parsed = urlparse(value.strip())
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field}: invalid URL")
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise HTTPException(
            status_code=400,
            detail=f"{field}: only http:// and https:// URLs are accepted",
        )

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
    if not cfg.epg_url:
        raise HTTPException(status_code=400, detail="EPG source URL not configured")
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
async def debug_full_reset(request: Request):
    # Fetch Metadata defence against cross-origin CSRF.
    # Browsers set Sec-Fetch-Site: same-origin for HTMX requests from the same
    # host; a cross-origin form POST or fetch() will have "cross-site" /
    # "cross-origin" and is rejected.  Missing header (e.g. curl) is allowed —
    # direct LAN access is an accepted risk for an unauthenticated local tool.
    fetch_site = request.headers.get("sec-fetch-site", "")
    if fetch_site and fetch_site not in ("same-origin", "same-site", "none"):
        raise HTTPException(status_code=403, detail="Forbidden")
    await db.full_reset()
    enriched = Path("data/enriched.xml")
    if enriched.exists():
        enriched.unlink()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@router.post("/settings")
async def update_settings(request: Request):
    # HTMX submits forms as application/x-www-form-urlencoded, not JSON,
    # so we read the raw form data rather than declaring `body: dict`
    # (which FastAPI would interpret as a JSON body and silently 422).
    form_data = await request.form()
    body: dict = dict(form_data)

    # HTML checkboxes are absent from form data when unchecked — convert to bool
    body["tmdb_enabled"] = "tmdb_enabled" in form_data

    # Validate URL fields before persisting (SSRF defence)
    for field in _URL_FIELDS:
        if value := (body.get(field) or "").strip():
            _validate_url(value, field)

    save_config(body)
    cfg = get_config()
    if "schedule_interval_hours" in body:
        reschedule(float(body["schedule_interval_hours"]))

    # Return an HTML fragment — hx-target="#save-msg" swaps this into the
    # status div, and showSaved() makes it visible for 3 s.
    return HTMLResponse("Settings saved.")
