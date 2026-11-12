"""Shared field query, target resolution, epoch grouping, catalog overlay, and examples logic."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from datetime import UTC
from pathlib import Path
from typing import Any, Literal

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

from app.providers.base import CatalogSource, FrameRef, Provider, ProviderQueryError
from app.schemas.fields import (
    DataReleaseRead,
    EpochFrameGroup,
    ExamplesResponse,
    ExampleTargetSummary,
    FieldFramesResponse,
    FieldSummary,
    ResolveResponse,
)
from app.schemas.objects import CatalogOverlayResponse, CatalogOverlaySource
from app.services.cache import CacheBackend
from app.services.jobs import JobStore
from sphx_core.timeutil import parse_user_epoch
from sphx_core.wcs import healpix9_nested

ANONYMOUS_MAX_FOV_ARCMIN = 15.0
DEFAULT_REFERENCE_PATH = (
    Path(__file__).resolve().parents[3] / "tests" / "science" / "reference_values.json"
)
MANDATORY_EXAMPLE_KEYS: tuple[str, ...] = (
    "barnards_star",
    "eps_indi_b",
    "wise_0855",
    "rw_aur",
    "r_dra",
)


def parse_utc_query(value: str | None) -> Time | None:
    """Parse optional ISO-UTC or MJD query timestamp into `Time(..., scale='tdb')`."""
    if value is None or not value.strip():
        return None
    try:
        return parse_user_epoch(value.strip())
    except Exception as exc:
        raise ProviderQueryError(f"Invalid timestamp {value!r}: {exc}") from exc


def parse_bands_query(bands: str | Sequence[str] | None) -> list[str] | None:
    """Normalize `bands` query/body value (`all` -> None)."""
    if bands is None:
        return None
    items = (
        [b.strip() for b in bands.split(",") if b.strip()]
        if isinstance(bands, str)
        else [str(b).strip() for b in bands if str(b).strip()]
    )
    if not items or any(b.lower() == "all" for b in items):
        return None
    return items


def group_frames_into_epochs(
    frames: Sequence[FrameRef], *, gap_days: float = 1.0
) -> list[tuple[str, list[FrameRef]]]:
    """Cluster time-sorted `FrameRef` objects into labeled epochs (`S1`, `S2`, ...)."""
    if not frames:
        return []
    ordered = sorted(frames, key=lambda f: (f.mjd_tdb, f.array_id or 0, f.obs_id))
    groups: list[list[FrameRef]] = [[ordered[0]]]
    for ref in ordered[1:]:
        if (ref.mjd_tdb - groups[-1][-1].mjd_tdb) > gap_days:
            groups.append([ref])
        else:
            groups[-1].append(ref)
    return [(f"S{idx}", grp) for idx, grp in enumerate(groups, start=1)]


def resolve_target_name(
    name: str,
    *,
    simbad: Provider,
    cache: CacheBackend,
    sesame_lookup: Callable[[str], SkyCoord] | None = None,
) -> ResolveResponse | None:
    """Resolve astronomical target name via SIMBAD (with PM) and Sesame fallback."""
    cleaned = name.strip()
    cache_key = f"resolve:{cleaned.lower()}"
    if (cached := cache.get_json(cache_key)) is not None:
        return ResolveResponse.model_validate(cached)

    src = simbad.lookup_object(cleaned)
    if src is not None:
        res = ResolveResponse(
            query=cleaned,
            ra_deg=float(src.ra_deg) % 360.0,
            dec_deg=float(src.dec_deg),
            pmra_masyr=src.pmra_masyr,
            pmdec_masyr=src.pmdec_masyr,
            resolver="simbad",
        )
        cache.set_json(cache_key, res.model_dump(mode="json"), ttl_s=30 * 86400)
        return res

    sesame = sesame_lookup or SkyCoord.from_name
    try:
        coord = sesame(cleaned).icrs
    except Exception:
        return None

    res = ResolveResponse(
        query=cleaned,
        ra_deg=float(coord.ra.deg) % 360.0,
        dec_deg=float(coord.dec.deg),
        resolver="sesame",
    )
    cache.set_json(cache_key, res.model_dump(mode="json"), ttl_s=30 * 86400)
    return res


def list_field_frames_service(
    *,
    ra: float,
    dec: float,
    fov: float,
    from_utc: str | None,
    to_utc: str | None,
    release: str,
    bands: str,
    provider: Provider,
    cache: CacheBackend,
) -> FieldFramesResponse:
    """Query archive frames around `(ra, dec)` and group into epochs."""
    t0 = parse_utc_query(from_utc)
    t1 = parse_utc_query(to_utc)
    if t0 is not None and t1 is not None and t0 > t1:
        raise ProviderQueryError("`from` timestamp must be <= `to` timestamp")

    hpx = healpix9_nested(ra, dec)
    band_list = parse_bands_query(bands)
    band_key = ",".join(sorted(band_list)) if band_list else "all"
    cache_key = (
        f"frames:{provider.name}:{release}:{hpx}:{fov:.3f}:"
        f"{from_utc or ''}:{to_utc or ''}:{band_key}"
    )
    if (cached := cache.get_json(cache_key)) is not None:
        return FieldFramesResponse.model_validate(cached)

    center = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")
    frames = provider.search_frames(
        center,
        (fov / 2.0) * u.arcmin,
        t0=t0,
        t1=t1,
        bands=band_list,
        release=release,
    )

    epochs: list[EpochFrameGroup] = []
    for label, grp in group_frames_into_epochs(frames):
        mjd_mean = sum(f.mjd_tdb for f in grp) / len(grp)
        utc_mean = Time(mjd_mean, format="mjd", scale="tdb").utc.to_datetime(timezone=UTC)
        arrays = sorted({int(f.array_id) for f in grp if f.array_id is not None})
        lams = [f.wavelength_um for f in grp if f.wavelength_um is not None]
        lam_range = (
            (min(pair[0] for pair in lams), max(pair[1] for pair in lams)) if lams else (0.75, 5.0)
        )
        flagged = [float(f.extra.get("flagged_fraction", 0.0)) for f in grp]
        epochs.append(
            EpochFrameGroup(
                epoch=label,
                mjd_tdb_mean=mjd_mean,
                utc_mean=utc_mean,
                n_frames=len(grp),
                arrays=arrays,
                lambda_um=lam_range,
                flagged_fraction=min(1.0, max(0.0, sum(flagged) / len(flagged))),
            )
        )

    resolved_release = frames[0].release if frames else release
    warnings: list[str] = []
    if not epochs:
        warnings.append("No matching frames found for the specified field, release, and bands.")
    resp = FieldFramesResponse(
        field=FieldSummary(ra=ra, dec=dec, fov_arcmin=fov, healpix9=hpx),
        release=resolved_release,
        epochs=epochs,
        warnings=warnings,
    )
    cache.set_json(cache_key, resp.model_dump(mode="json"), ttl_s=86400)
    return resp


def get_catalog_overlay_service(
    *,
    cat: Literal["gaia", "catwise"],
    ra: float,
    dec: float,
    fov: float,
    epoch: str | None,
    provider: Provider,
    cache: CacheBackend,
) -> CatalogOverlayResponse:
    """Query reference catalog cone and propagate positions to `epoch`."""
    target_time = parse_utc_query(epoch) or Time.now().tdb
    hpx = healpix9_nested(ra, dec)
    epoch_mjd = float(target_time.tdb.mjd)
    cache_key = f"overlay:{cat}:{hpx}:{fov:.3f}:{epoch_mjd:.3f}"
    if (cached := cache.get_json(cache_key)) is not None:
        return CatalogOverlayResponse.model_validate(cached)

    center = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")
    raw_sources: list[CatalogSource] = provider.cone(
        center, (fov / 2.0) * u.arcmin, epoch=target_time
    )
    overlay_sources = [
        CatalogOverlaySource(
            catalog=s.catalog,
            catalog_id=s.source_id,
            ra=s.ra_deg,
            dec=s.dec_deg,
            epoch_mjd_tdb=s.mjd_tdb,
            pmra_masyr=s.pmra_masyr,
            pmdec_masyr=s.pmdec_masyr,
            mag=s.mag,
            mag_band=s.mag_band,
        )
        for s in raw_sources
    ]
    warnings: list[str] = []
    if not overlay_sources:
        warnings.append(f"No {cat} sources found within {fov:.1f} arcmin.")
    resp = CatalogOverlayResponse(
        catalog=cat,
        epoch_mjd_tdb=epoch_mjd,
        sources=overlay_sources,
        warnings=warnings,
    )
    cache.set_json(cache_key, resp.model_dump(mode="json"), ttl_s=7 * 86400)
    return resp


def list_releases_service(
    *, store: JobStore, provider: Provider, cache: CacheBackend
) -> list[DataReleaseRead]:
    """Return known indexed releases from store, falling back to cached provider query."""
    stored = store.list_releases()
    if stored:
        return stored
    cache_key = f"releases:{provider.name}"
    if (cached := cache.get_json(cache_key)) is not None:
        return [DataReleaseRead.model_validate(item) for item in cached]

    descriptors = provider.describe_releases()
    releases = [
        DataReleaseRead(
            id=d.release_id,
            provider=d.provider,
            mission=d.mission,
            version_label=d.version_label,
            release_date=d.release_date,
            status="active",
            profile_name=d.profile_name,
            n_frames=d.n_frames,
        )
        for d in descriptors
    ]
    cache.set_json(cache_key, [r.model_dump(mode="json") for r in releases], ttl_s=3600)
    return releases


def list_examples_service(*, reference_path: Path | None = None) -> ExamplesResponse:
    """Load the 5 mandatory benchmark target presets from `reference_values.json`."""
    path = reference_path or Path(
        os.environ.get("SPHX_REFERENCE_VALUES_PATH", str(DEFAULT_REFERENCE_PATH))
    )
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    targets_raw: dict[str, Any] = data.get("targets", {})
    items: list[ExampleTargetSummary] = []
    for key in MANDATORY_EXAMPLE_KEYS:
        t = targets_raw.get(key)
        if not t:
            continue
        ra = t.get("ra_deg", t.get("challenge_ra_deg"))
        dec = t.get("dec_deg", t.get("challenge_dec_deg"))
        if ra is None or dec is None:
            continue
        items.append(
            ExampleTargetSummary(
                id=key,
                name=str(t["name"]),
                category=str(t.get("expected_class", "benchmark")),
                ra_deg=float(ra) % 360.0,
                dec_deg=float(dec),
                fov_arcmin=10.0,
                passed=None,
                summary=str(t.get("citation", "")),
            )
        )
    return ExamplesResponse(targets=items)
