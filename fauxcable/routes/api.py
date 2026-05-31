import asyncio
import io
import re
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from PIL import Image

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
from fauxcable.providers.tmdb import search_tmdb, search_tmdb_tv
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


@router.post("/override")
async def save_override(
    title_key: str = Form(...),
    poster_url: str = Form(...),
    match_name: str = Form(""),
    match_source: str = Form("manual"),
):
    # hx-vals on <div> sends application/x-www-form-urlencoded, not JSON —
    # Form() parameters parse correctly where `body: BaseModel` would 422.
    await db.save_override(title_key, poster_url, match_name, match_source)
    # Empty response: on the matches page hx-swap="outerHTML" removes the
    # card from the DOM; on library-edit the caller redirects and ignores it.
    return HTMLResponse("")


@router.post("/override/batch", response_class=HTMLResponse)
async def batch_override(request: Request):
    form = await request.form()
    title_keys = form.getlist("title_keys")
    poster_url = form.get("poster_url", "").strip()
    match_name = form.get("match_name", "").strip()
    match_source = form.get("match_source", "manual").strip()
    if not title_keys or not poster_url:
        return HTMLResponse('<span class="text-red-400 text-sm">Select items and a generic first.</span>', status_code=400)
    await db.bulk_save_override(title_keys, poster_url, match_name, match_source)
    response = HTMLResponse("")
    response.headers["HX-Redirect"] = "/library"
    return response


_UPLOADS_DIR = Path("data/uploads")
_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


@router.post("/upload-poster")
async def upload_poster(
    title_key: str = Form(...),
    poster_file: UploadFile = File(...),
):
    suffix = Path(poster_file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_IMAGE_EXTS:
        return RedirectResponse(f"/library/edit/{quote(title_key, safe='')}?upload_error=1", status_code=303)

    content = await poster_file.read()
    try:
        # Open with Pillow: validates it's a real image and strips metadata
        img = Image.open(io.BytesIO(content)).convert("RGB")
    except Exception:
        return RedirectResponse(f"/library/edit/{quote(title_key, safe='')}?upload_error=1", status_code=303)

    _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^\w\-]", "_", title_key.strip())
    filename = f"{safe_key}.png"
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    (_UPLOADS_DIR / filename).write_bytes(buf.getvalue())

    cfg = get_config()
    poster_url = f"{cfg.base_url}/uploads/{filename}"
    await db.save_override(title_key, poster_url, "Uploaded", "manual")
    return RedirectResponse("/library", status_code=303)


@router.delete("/override/{title_key:path}")
async def delete_override(title_key: str):
    await db.delete_override(title_key)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Category alias map
# ---------------------------------------------------------------------------

@router.get("/category-map")
async def list_category_map():
    return await db.list_category_aliases()


@router.post("/category-map")
async def save_category_map(source: str = Form(...), target: str = Form(...)):
    source = source.strip().lower()
    target = target.strip().lower()
    await db.save_category_alias(source, target)
    # Immediately remap cached entries so items reflect the alias without
    # waiting for the next pipeline run
    cfg = get_config()
    new_url = f"{cfg.base_url}/generics/generic_{target}.png"
    await db.remap_generic_category(source, new_url, f"generic:{target}")
    return HTMLResponse("")


@router.delete("/category-map/{source}")
async def delete_category_map(source: str):
    await db.delete_category_alias(source)
    # Note: existing cache entries that were remapped are NOT reverted —
    # they will re-resolve correctly on the next pipeline run.
    return HTMLResponse("")


@router.post("/unmatched/batch-dismiss", response_class=HTMLResponse)
async def batch_dismiss(request: Request):
    form = await request.form()
    title_keys = form.getlist("title_keys")
    if not title_keys:
        return HTMLResponse('<span class="text-red-400 text-sm">No items selected.</span>', status_code=400)
    await db.batch_dismiss_unmatched(list(title_keys))
    response = HTMLResponse("")
    response.headers["HX-Redirect"] = "/matches"
    return response


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
    tvmaze, tmdb_tv = await asyncio.gather(search_tvmaze(q, cfg), search_tmdb_tv(q, cfg))
    seen_names = {r["name"].lower() for r in tvmaze}
    return tvmaze + [r for r in tmdb_tv if r["name"].lower() not in seen_names]


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
