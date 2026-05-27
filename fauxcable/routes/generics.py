from __future__ import annotations
import asyncio
import base64
import re
import time
from pathlib import Path
from typing import Annotated, Optional
from urllib.parse import quote as urlquote

import aiohttp
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from fauxcable import database as db
from fauxcable.config import get_config
from fauxcable.poster_builder import list_fonts, render_poster
from fauxcable.version import COMMIT_ID

_POLLINATIONS_URL = (
    "https://image.pollinations.ai/prompt/{prompt}"
    "?width=300&height=450&nologo=true&nofeed=true&model=flux"
)
_POLLINATIONS_COOLDOWN = 3.0  # seconds between requests — be polite to the free service
_last_generate_time: float = 0.0

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["commit_id"] = COMMIT_ID

_GENERICS_DIR = Path("generics")
_FONTS_DIR = Path("fonts")
_ALLOWED_FONT_EXTS = {".ttf", ".otf"}


def _safe_category(raw: str) -> str:
    return re.sub(r"[^\w\-]", "_", raw.strip().lower())


def _resp(request: Request, name: str, ctx: dict):
    return templates.TemplateResponse(request=request, name=name, context=ctx)


def _list_generics() -> list[dict]:
    if not _GENERICS_DIR.exists():
        return []
    cfg = get_config()
    return [
        {"category": f.stem.replace("generic_", ""), "url": f"{cfg.base_url}/generics/{f.name}"}
        for f in sorted(_GENERICS_DIR.glob("generic_*.png"))
    ]


@router.get("/generic-builder", response_class=HTMLResponse)
async def generic_builder_page(request: Request, font_error: bool = False):
    ctx = {
        "active": "generics",
        "unmatched_count": await db.count_unmatched(),
        "generics": _list_generics(),
        "fonts": list_fonts(),
        "font_error": font_error,
        "aliases": await db.list_category_aliases(),
    }
    return _resp(request, "generic_builder.html", ctx)


@router.post("/api/generics/preview", response_class=HTMLResponse)
async def generics_preview(
    label: Annotated[str, Form()],
    bg_color: Annotated[str, Form()] = "#1e293b",
    blur_radius: Annotated[int, Form()] = 0,
    text_color: Annotated[str, Form()] = "#ffffff",
    font_name: Annotated[str, Form()] = "",
    font_size: Annotated[int, Form()] = 40,
    text_position: Annotated[str, Form()] = "center",
    ai_generated_b64: Annotated[str, Form()] = "",
    bg_image: Optional[UploadFile] = File(default=None),
):
    bg_bytes = None
    if bg_image and bg_image.filename:
        bg_bytes = await bg_image.read()
    elif ai_generated_b64:
        bg_bytes = base64.b64decode(ai_generated_b64)

    png = render_poster(
        label=label,
        bg_color=bg_color,
        bg_image_bytes=bg_bytes,
        blur_radius=blur_radius,
        text_color=text_color,
        font_name=font_name or None,
        font_size=font_size,
        text_position=text_position,
    )
    b64 = base64.b64encode(png).decode()
    return HTMLResponse(f'<img src="data:image/png;base64,{b64}" class="w-full h-full object-cover">')


@router.post("/api/generics/save", response_class=HTMLResponse)
async def generics_save(
    label: Annotated[str, Form()],
    category: Annotated[str, Form()],
    bg_color: Annotated[str, Form()] = "#1e293b",
    blur_radius: Annotated[int, Form()] = 0,
    text_color: Annotated[str, Form()] = "#ffffff",
    font_name: Annotated[str, Form()] = "",
    font_size: Annotated[int, Form()] = 40,
    text_position: Annotated[str, Form()] = "center",
    ai_generated_b64: Annotated[str, Form()] = "",
    bg_image: Optional[UploadFile] = File(default=None),
):
    safe_cat = _safe_category(category)
    if not safe_cat:
        return HTMLResponse('<span class="text-red-400 text-sm">Invalid category name.</span>')

    bg_bytes = None
    if bg_image and bg_image.filename:
        bg_bytes = await bg_image.read()
    elif ai_generated_b64:
        bg_bytes = base64.b64decode(ai_generated_b64)

    png = render_poster(
        label=label,
        bg_color=bg_color,
        bg_image_bytes=bg_bytes,
        blur_radius=blur_radius,
        text_color=text_color,
        font_name=font_name or None,
        font_size=font_size,
        text_position=text_position,
    )
    _GENERICS_DIR.mkdir(exist_ok=True)
    (_GENERICS_DIR / f"generic_{safe_cat}.png").write_bytes(png)
    return HTMLResponse(
        f'<span class="text-green-400 text-sm">Saved as '
        f'<code class="bg-slate-700 px-1 rounded">generic_{safe_cat}.png</code></span>'
    )


@router.post("/api/generics/generate", response_class=HTMLResponse)
async def generics_generate(prompt: Annotated[str, Form()]):
    global _last_generate_time
    if not prompt.strip():
        return HTMLResponse('<span class="text-red-400 text-sm">Enter a prompt first.</span>')

    elapsed = time.monotonic() - _last_generate_time
    if elapsed < _POLLINATIONS_COOLDOWN:
        wait = int(_POLLINATIONS_COOLDOWN - elapsed) + 1
        return HTMLResponse(
            f'<span class="text-amber-400 text-sm">Please wait {wait}s before generating again.</span>'
        )
    _last_generate_time = time.monotonic()

    url = _POLLINATIONS_URL.format(prompt=urlquote(prompt.strip(), safe=""))
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    return HTMLResponse(
                        f'<span class="text-red-400 text-sm">Pollinations returned {resp.status}. Try again.</span>'
                    )
                content_type = resp.headers.get("content-type", "")
                if "image" not in content_type:
                    return HTMLResponse(
                        '<span class="text-red-400 text-sm">Unexpected response from Pollinations. Try again.</span>'
                    )
                img_bytes = await resp.read()
    except asyncio.TimeoutError:
        return HTMLResponse(
            '<span class="text-red-400 text-sm">Pollinations timed out — try again.</span>'
        )
    except aiohttp.ClientError as exc:
        return HTMLResponse(f'<span class="text-red-400 text-sm">Request failed: {exc}</span>')

    b64 = base64.b64encode(img_bytes).decode()
    return HTMLResponse(f'<img src="data:image/png;base64,{b64}" class="w-full h-full object-cover">')


# font routes registered before /{category} to avoid shadowing
@router.post("/api/generics/fonts")
async def upload_font(font_file: UploadFile = File(...)):
    suffix = Path(font_file.filename).suffix.lower()
    if suffix not in _ALLOWED_FONT_EXTS:
        return RedirectResponse("/generic-builder?font_error=1", status_code=303)
    _FONTS_DIR.mkdir(exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.\-]", "_", Path(font_file.filename).name)
    (_FONTS_DIR / safe_name).write_bytes(await font_file.read())
    return RedirectResponse("/generic-builder", status_code=303)


@router.delete("/api/generics/fonts/{name}", response_class=HTMLResponse)
async def delete_font(name: str):
    path = _FONTS_DIR / Path(name).name
    if path.exists() and path.suffix.lower() in _ALLOWED_FONT_EXTS:
        path.unlink()
    return HTMLResponse("")


@router.delete("/api/generics/{category}", response_class=HTMLResponse)
async def delete_generic(category: str):
    path = _GENERICS_DIR / f"generic_{_safe_category(category)}.png"
    if path.exists():
        path.unlink()
    return HTMLResponse("")
