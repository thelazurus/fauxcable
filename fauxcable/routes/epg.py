from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

router = APIRouter()
_EPG_PATH = Path("data/enriched.xml")


@router.get("/epg.xml", response_class=Response)
async def serve_epg():
    if not _EPG_PATH.exists():
        return Response(
            content="EPG not yet generated — trigger a run from the dashboard.",
            status_code=503,
            media_type="text/plain",
        )
    return FileResponse(
        _EPG_PATH,
        media_type="application/xml",
        headers={"Content-Disposition": 'inline; filename="epg.xml"'},
    )
