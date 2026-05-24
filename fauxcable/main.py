import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from fauxcable.config import get_config
from fauxcable.database import init_db
from fauxcable.routes.api import router as api_router
from fauxcable.routes.epg import router as epg_router
from fauxcable.routes.generics import router as generics_router
from fauxcable.routes.ui import router as ui_router
from fauxcable.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)

Path("data").mkdir(exist_ok=True)
Path("data/uploads").mkdir(exist_ok=True)
_GENERICS_DIR = Path("generics")
_GENERICS_DIR.mkdir(exist_ok=True)
_FONTS_DIR = Path("fonts")
_FONTS_DIR.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    cfg = get_config()
    if cfg.epg_url:
        start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="FauxCable", lifespan=lifespan)

app.mount("/generics", StaticFiles(directory=str(_GENERICS_DIR)), name="generics")
app.mount("/fonts", StaticFiles(directory=str(_FONTS_DIR)), name="fonts")
app.mount("/uploads", StaticFiles(directory="data/uploads"), name="uploads")

app.include_router(epg_router)
app.include_router(api_router)
app.include_router(generics_router)
app.include_router(ui_router)
