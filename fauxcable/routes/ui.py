from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from fauxcable import database as db
from fauxcable.config import get_config
from fauxcable.pipeline import get_run_status
from fauxcable.providers.tmdb import search_tmdb
from fauxcable.providers.tvmaze import search_tvmaze
from fauxcable.scheduler import next_run_time

from fauxcable.version import COMMIT_ID

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["commit_id"] = COMMIT_ID


def _resp(request: Request, name: str, ctx: dict):
    """Starlette 1.x TemplateResponse: request is first arg, not in context."""
    return templates.TemplateResponse(request=request, name=name, context=ctx)


async def _base_ctx(active: str) -> dict:
    return {
        "active": active,
        "unmatched_count": await db.count_unmatched(),
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    cfg = get_config()
    ctx = await _base_ctx("dashboard")
    ctx.update({
        "stats": await db.get_summary_stats(),
        "history": await db.list_runs(10),
        "run_status": get_run_status(),
        "next_run": next_run_time(),
        "epg_configured": bool(cfg.epg_url),
        "cfg_base_url": cfg.base_url,
    })
    return _resp(request, "index.html", ctx)


@router.get("/matches", response_class=HTMLResponse)
async def matches_page(request: Request, category: str = ""):
    ctx = await _base_ctx("matches")
    ctx["items"] = await db.list_unmatched(100, category=category or None)
    ctx["categories"] = await db.list_categories()
    ctx["current_category"] = category

    # Determine which queue categories have no generic and no alias.
    generics_dir = Path("generics")
    existing_generics = (
        {f.stem.replace("generic_", "") for f in generics_dir.glob("generic_*.png")}
        if generics_dir.exists() else set()
    )
    alias_map = await db.load_category_map()
    ctx["missing_generics"] = (
        {cat["category"] for cat in ctx["categories"]}
        - existing_generics
        - set(alias_map.keys())   # aliased categories are considered covered
    )
    # Available generics for the inline alias dropdown in the banner and batch assign
    cfg = get_config()
    ctx["generics"] = [
        {"category": f.stem.replace("generic_", ""), "url": f"{cfg.base_url}/generics/{f.name}"}
        for f in sorted(generics_dir.glob("generic_*.png"))
    ] if generics_dir.exists() else []

    return _resp(request, "matches.html", ctx)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    ctx = await _base_ctx("settings")
    ctx["cfg"] = get_config()
    return _resp(request, "settings.html", ctx)


@router.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    return _resp(request, "about.html", await _base_ctx("about"))


_PER_PAGE = 60


@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request, source: str = "", search: str = "", page: int = 1):
    offset = (page - 1) * _PER_PAGE
    total = await db.count_cache(source or None, search or None)
    cfg = get_config()
    generics = [
        {"category": f.stem.replace("generic_", ""), "url": f"{cfg.base_url}/generics/{f.name}"}
        for f in sorted(Path("generics").glob("generic_*.png"))
    ] if Path("generics").exists() else []
    ctx = await _base_ctx("library")
    ctx.update({
        "items": await db.list_cache(_PER_PAGE, offset, source or None, search or None),
        "sources": await db.list_cache_sources(),
        "total": total,
        "total_pages": max(1, (total + _PER_PAGE - 1) // _PER_PAGE),
        "page": page,
        "current_source": source,
        "search": search,
        "generics": generics,
    })
    return _resp(request, "library.html", ctx)


@router.get("/library/edit/{title_key:path}", response_class=HTMLResponse)
async def library_edit(request: Request, title_key: str, upload_error: bool = False):
    entry = await db.get_cache_entry(title_key)
    if not entry:
        return HTMLResponse("Not found", status_code=404)
    cfg = get_config()
    generics = []
    for f in sorted(Path("generics").glob("generic_*.png")):
        cat = f.stem.replace("generic_", "")
        generics.append({
            "category": cat,
            "url": f"{cfg.base_url}/generics/{f.name}",
        })
    ctx = await _base_ctx("library")
    ctx["entry"] = entry
    ctx["generics"] = generics
    ctx["upload_error"] = upload_error
    return _resp(request, "library_edit.html", ctx)


@router.get("/search-results", response_class=HTMLResponse)
async def search_results(
    request: Request,
    q: str = "",
    type: str = "show",
    title_key: str = "",
    redirect_to: str = "",
):
    cfg = get_config()
    results = []
    if q and len(q) >= 2:
        results = await search_tmdb(q, cfg) if type == "movie" else await search_tvmaze(q, cfg)
    return _resp(request, "_search_results.html", {
        "results": results,
        "title_key": title_key,
        "redirect_to": redirect_to,
    })
