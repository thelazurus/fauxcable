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
    "?width=512&height=768&nologo=true&nofeed=true&model=flux"
)
_TOGETHER_URL = "https://api.together.xyz/v1/images/generations"
_FAL_URL = "https://fal.run/fal-ai/flux/schnell"
_GENERATE_COOLDOWN = 3.0
_last_generate_time: float = 0.0


async def _fetch_pollinations(prompt: str) -> bytes:
    url = _POLLINATIONS_URL.format(prompt=urlquote(prompt, safe=""))
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Pollinations returned {resp.status}")
            if "image" not in resp.headers.get("content-type", ""):
                raise RuntimeError("Unexpected response from Pollinations")
            return await resp.read()


async def _fetch_together(prompt: str, api_key: str) -> bytes:
    payload = {
        "model": "black-forest-labs/FLUX.1-schnell-Free",
        "prompt": prompt,
        "width": 512,
        "height": 768,
        "steps": 4,
        "n": 1,
        "response_format": "b64_json",
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            _TOGETHER_URL, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Together AI returned {resp.status}: {body[:120]}")
            data = await resp.json()
            return base64.b64decode(data["data"][0]["b64_json"])


async def _fetch_fal(prompt: str, api_key: str) -> bytes:
    payload = {
        "prompt": prompt,
        "image_size": {"width": 512, "height": 768},
        "num_images": 1,
        "output_format": "jpeg",
    }
    headers = {"Authorization": f"Key {api_key}"}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            _FAL_URL, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"fal.ai returned {resp.status}: {body[:120]}")
            data = await resp.json()
            img_url = data["images"][0]["url"]
        async with session.get(img_url, timeout=aiohttp.ClientTimeout(total=30)) as img_resp:
            img_resp.raise_for_status()
            return await img_resp.read()

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["commit_id"] = COMMIT_ID

_GENERICS_DIR = Path("generics")
_FONTS_DIR = Path("fonts")
_AI_TEMP_FILE = Path("data/uploads/_ai_temp.png")
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
    cfg = get_config()
    ctx = {
        "active": "generics",
        "unmatched_count": await db.count_unmatched(),
        "generics": _list_generics(),
        "fonts": list_fonts(),
        "font_error": font_error,
        "aliases": await db.list_category_aliases(),
        "ai_provider": cfg.ai_provider,
        "ai_configured": bool(cfg.ai_api_key),
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
    bg_image: Optional[UploadFile] = File(default=None),
):
    bg_bytes = None
    if bg_image and bg_image.filename:
        bg_bytes = await bg_image.read()
    elif _AI_TEMP_FILE.exists():
        bg_bytes = _AI_TEMP_FILE.read_bytes()

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
    bg_image: Optional[UploadFile] = File(default=None),
):
    safe_cat = _safe_category(category)
    if not safe_cat:
        return HTMLResponse('<span class="text-red-400 text-sm">Invalid category name.</span>')

    bg_bytes = None
    if bg_image and bg_image.filename:
        bg_bytes = await bg_image.read()
    elif _AI_TEMP_FILE.exists():
        bg_bytes = _AI_TEMP_FILE.read_bytes()

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
    response = HTMLResponse("")
    response.headers["HX-Redirect"] = "/generic-builder"
    return response


@router.post("/api/generics/generate", response_class=HTMLResponse)
async def generics_generate(prompt: Annotated[str, Form()]):
    global _last_generate_time
    if not prompt.strip():
        return HTMLResponse('<span class="text-red-400 text-sm">Enter a prompt first.</span>')

    elapsed = time.monotonic() - _last_generate_time
    if elapsed < _GENERATE_COOLDOWN:
        wait = int(_GENERATE_COOLDOWN - elapsed) + 1
        return HTMLResponse(
            f'<span class="text-amber-400 text-sm">Please wait {wait}s before generating again.</span>'
        )
    _last_generate_time = time.monotonic()

    cfg = get_config()
    try:
        if cfg.ai_api_key:
            if cfg.ai_provider == "fal":
                img_bytes = await _fetch_fal(prompt.strip(), cfg.ai_api_key)
            else:
                img_bytes = await _fetch_together(prompt.strip(), cfg.ai_api_key)
        else:
            img_bytes = await _fetch_pollinations(prompt.strip())
    except asyncio.TimeoutError:
        return HTMLResponse('<span class="text-red-400 text-sm">Request timed out — try again.</span>')
    except Exception as exc:
        return HTMLResponse(f'<span class="text-red-400 text-sm">Generation failed: {exc}</span>')

    _AI_TEMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    _AI_TEMP_FILE.write_bytes(img_bytes)

    t = int(time.time())
    response = HTMLResponse(f'<img src="/uploads/_ai_temp.png?t={t}" class="w-full h-full object-cover">')
    response.headers["HX-Trigger"] = "showRegenerateBtn"
    return response


@router.delete("/api/generics/ai-temp", response_class=HTMLResponse)
async def clear_ai_temp():
    if _AI_TEMP_FILE.exists():
        _AI_TEMP_FILE.unlink()
    return HTMLResponse("")


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
