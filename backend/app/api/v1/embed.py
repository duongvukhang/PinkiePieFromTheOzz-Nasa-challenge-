"""oEmbed metadata, static animated GIF render, and chrome-less embed endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, Response

from app.api.deps import COMMON_501, not_implemented
from app.schemas.common import DecDeg, RaDeg
from app.schemas.fields import OEmbedResponse

v1_router = APIRouter(tags=["embed"], responses=COMMON_501)
root_router = APIRouter(tags=["embed"], responses=COMMON_501)


@v1_router.get("/oembed")
async def get_oembed(url: Annotated[str, Query(min_length=1)]) -> OEmbedResponse:
    """Return oEmbed iframe metadata for a viewer URL."""
    not_implemented()


@v1_router.get("/render.gif")
async def render_gif(
    ra: RaDeg,
    dec: DecDeg,
    fov: Annotated[float, Query(gt=0.0, le=60.0)] = 10.0,
    bands: str = "all",
) -> Response:
    """Render cached animated GIF across epochs (ICRS deg, FOV arcmin)."""
    not_implemented()


@root_router.get("/embed", response_class=HTMLResponse)
async def get_embed(
    ra: str | None = None,
    dec: str | None = None,
    fov: float = 10.0,
    mode: str = "blink",
    bands: str = "all",
    from_utc: Annotated[str | None, Query(alias="from")] = None,
    to_utc: Annotated[str | None, Query(alias="to")] = None,
) -> HTMLResponse:
    """Serve chrome-less cookie-less iframe embed viewer."""
    not_implemented()
