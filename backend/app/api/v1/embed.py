"""oEmbed metadata, static animated GIF render, and chrome-less embed endpoints."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import HTMLResponse, Response

from app.api.deps import (
    APIProblemError,
    enforce_fov_quota,
    get_cache,
    get_optional_user_id,
    get_simbad_provider,
    get_spherex_provider,
    get_storage,
)
from app.providers import Provider
from app.schemas.common import DecDeg, ProblemDetail, RaDeg
from app.schemas.fields import OEmbedResponse
from app.services.cache import CacheBackend
from app.services.embed import (
    build_oembed_response,
    render_embed_html,
    render_epochs_gif,
    resolve_embed_coords,
)
from app.services.fields import ANONYMOUS_MAX_FOV_ARCMIN, parse_utc_query
from app.services.storage import StorageBackend

_ERR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ProblemDetail},
    403: {"model": ProblemDetail},
    404: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
    429: {"model": ProblemDetail},
    503: {"model": ProblemDetail},
}

v1_router = APIRouter(tags=["embed"], responses=_ERR_RESPONSES)
root_router = APIRouter(tags=["embed"], responses=_ERR_RESPONSES)


@v1_router.get("/oembed")
async def get_oembed(
    url: Annotated[str, Query(min_length=1)],
    maxwidth: Annotated[int | None, Query(ge=160, le=1920)] = None,
    maxheight: Annotated[int | None, Query(ge=120, le=1200)] = None,
) -> OEmbedResponse:
    """Return oEmbed iframe metadata for a viewer URL."""
    return build_oembed_response(url, maxwidth=maxwidth, maxheight=maxheight)


@v1_router.get("/render.gif")
async def render_gif(
    ra: RaDeg,
    dec: DecDeg,
    provider: Annotated[Provider, Depends(get_spherex_provider)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    fov: Annotated[float, Query(gt=0.0, le=60.0)] = 10.0,
    bands: str = "all",
    release: str = "latest",
    from_utc: Annotated[str | None, Query(alias="from")] = None,
    to_utc: Annotated[str | None, Query(alias="to")] = None,
    fps: Annotated[float, Query(gt=0.1, le=20.0)] = 2.0,
    user_id: Annotated[UUID | None, Depends(get_optional_user_id)] = None,
) -> Response:
    """Render cached animated GIF across epochs (ICRS deg, FOV arcmin)."""
    enforce_fov_quota(fov, user_id)
    gif_bytes, hit = render_epochs_gif(
        ra=ra,
        dec=dec,
        fov=fov,
        bands=bands,
        release=release,
        from_utc=from_utc,
        to_utc=to_utc,
        fps=fps,
        provider=provider,
        cache=cache,
        storage=storage,
    )
    return Response(
        content=gif_bytes,
        media_type="image/gif",
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Cache": "HIT" if hit else "MISS",
        },
    )


@root_router.get("/embed", response_class=HTMLResponse)
async def get_embed(
    simbad: Annotated[Provider, Depends(get_simbad_provider)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
    ra: str | None = None,
    dec: str | None = None,
    target: str | None = None,
    fov: Annotated[float, Query(gt=0.0, le=60.0)] = 10.0,
    mode: Literal["blink", "compare", "planetx"] = "blink",
    bands: str = "all",
    release: str = "latest",
    from_utc: Annotated[str | None, Query(alias="from")] = None,
    to_utc: Annotated[str | None, Query(alias="to")] = None,
    overlay: str | None = None,
    track: str | None = None,
) -> HTMLResponse:
    """Serve chrome-less cookie-less iframe embed viewer."""
    t0 = parse_utc_query(from_utc)
    t1 = parse_utc_query(to_utc)
    if t0 is not None and t1 is not None and t0 > t1:
        raise APIProblemError(
            status.HTTP_400_BAD_REQUEST,
            "Invalid Time Range",
            "`from` timestamp must be <= `to` timestamp.",
        )
    ra_deg, dec_deg, label = resolve_embed_coords(
        ra=ra, dec=dec, target=target, simbad=simbad, cache=cache
    )
    clamped_fov = min(fov, ANONYMOUS_MAX_FOV_ARCMIN)
    body = render_embed_html(
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        fov=clamped_fov,
        mode=mode,
        bands=bands,
        release=release,
        from_utc=from_utc,
        to_utc=to_utc,
        label=label,
        overlay=overlay,
        track=track,
    )
    return HTMLResponse(
        content=body,
        headers={
            "Content-Security-Policy": "frame-ancestors *",
            "Cache-Control": "public, max-age=300",
        },
    )
