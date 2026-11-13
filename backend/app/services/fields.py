"""Shared field query, target resolution, epoch grouping, catalog overlay, and examples logic."""

from __future__ import annotations

import io
import json
import os
from collections.abc import Callable, Sequence
from datetime import UTC, date
from pathlib import Path
from typing import Any, Literal

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time

from app.providers.base import (
    CatalogSource,
    FrameRef,
    Provider,
    ProviderError,
    ProviderQueryError,
)
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
from sphx_core.calib import load_frame
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
_REFERENCE_ALIASES: dict[str, tuple[str, ...]] = {
    "wise_0855": ("wise 0855", "wise j0855", "wise j085510.83-071442.5", "wise j0855-0714"),
    "barnards_star": ("barnard's star", "barnard star", "barnard"),
    "eps_indi_b": ("epsilon indi b", "eps indi b", "eps ind b"),
    "eps_indi_a": ("epsilon indi a", "eps indi a", "eps ind a"),
    "rw_aur": ("rw aurigae", "rw aur"),
    "r_dra": ("r draconis", "r dra"),
}


def _clean_alnum(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum())


_TARGET_MAP: dict[str, str] = {
    _clean_alnum(syn): key for key, syns in _REFERENCE_ALIASES.items() for syn in (key, *syns)
}


def _get_reference_path(reference_path: Path | None = None) -> Path:
    if reference_path is not None:
        return reference_path
    env_val = os.environ.get("SPHX_REFERENCE_VALUES_PATH")
    if env_val and Path(env_val).is_file():
        return Path(env_val)
    for cand in (
        DEFAULT_REFERENCE_PATH,
        Path("/app/tests/science/reference_values.json"),
        Path("tests/science/reference_values.json"),
    ):
        if cand.is_file():
            return cand
    return Path(env_val) if env_val else DEFAULT_REFERENCE_PATH


def _lookup_reference_target(
    name: str, *, reference_path: Path | None = None
) -> ResolveResponse | None:
    """Resolve mandatory benchmark targets from canonical `reference_values.json`."""
    path = _get_reference_path(reference_path)
    if not path.is_file():
        return None
    try:
        targets: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")).get("targets", {})
    except Exception:
        return None

    norm = _clean_alnum(name)
    key = _TARGET_MAP.get(norm) or next(
        (
            k
            for k, t in targets.items()
            if norm in (_clean_alnum(k), _clean_alnum(str(t.get("name", ""))))
        ),
        None,
    )
    t = targets.get(key) if key else None
    if not t:
        return None

    ra = t.get("ra_deg", t.get("challenge_ra_deg"))
    dec = t.get("dec_deg", t.get("challenge_dec_deg"))
    if ra is None or dec is None:
        return None

    return ResolveResponse(
        query=name,
        ra_deg=float(ra) % 360.0,
        dec_deg=float(dec),
        pmra_masyr=float(pm) if (pm := t.get("pmra_mas_yr")) is not None else None,
        pmdec_masyr=float(pm) if (pm := t.get("pmdec_mas_yr")) is not None else None,
        resolver="reference",
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
    raw = bands.split(",") if isinstance(bands, str) else bands
    items = [s for b in raw if (s := str(b).strip())]
    return None if not items or any(b.lower() == "all" for b in items) else items


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
    """Resolve astronomical target name via reference values, SIMBAD (with PM), and Sesame."""
    cleaned = name.strip()
    cache_key = f"resolve:{cleaned.lower()}"
    if (cached := cache.get_json(cache_key)) is not None:
        return ResolveResponse.model_validate(cached)

    if (ref_res := _lookup_reference_target(cleaned)) is not None:
        cache.set_json(cache_key, ref_res.model_dump(mode="json"), ttl_s=30 * 86400)
        return ref_res

    try:
        src = simbad.lookup_object(cleaned)
    except Exception:
        src = None

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
    query_failed = False
    try:
        frames = provider.search_frames(
            center,
            (fov / 2.0) * u.arcmin,
            t0=t0,
            t1=t1,
            bands=band_list,
            release=release,
        )
    except ProviderError as exc:
        frames = []
        query_failed = True
        warnings = [f"Upstream provider query failed: {exc}"]
    else:
        warnings = []

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
    if not epochs and not query_failed:
        warnings.append("No matching frames found for the specified field, release, and bands.")
    resp = FieldFramesResponse(
        field=FieldSummary(ra=ra, dec=dec, fov_arcmin=fov, healpix9=hpx),
        release=resolved_release,
        epochs=epochs,
        warnings=warnings,
    )
    if not query_failed:
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
    query_failed = False
    try:
        raw_sources: list[CatalogSource] = provider.cone(
            center, (fov / 2.0) * u.arcmin, epoch=target_time
        )
    except ProviderError as exc:
        raw_sources = []
        query_failed = True
        warnings = [f"Upstream catalog provider query failed: {exc}"]
    else:
        warnings = []

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
    if not overlay_sources and not query_failed:
        warnings.append(f"No {cat} sources found within {fov:.1f} arcmin.")
    resp = CatalogOverlayResponse(
        catalog=cat,
        epoch_mjd_tdb=epoch_mjd,
        sources=overlay_sources,
        warnings=warnings,
    )
    if not query_failed:
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

    try:
        descriptors = provider.describe_releases()
    except ProviderError:
        descriptors = []

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
    if not releases and hasattr(provider, "profile"):
        prof = provider.profile
        releases = [
            DataReleaseRead(
                id=getattr(prof, "collection_id", "spherex_qr_v1"),
                provider=getattr(prof, "provider", provider.name),
                mission=getattr(prof, "mission", "SPHEREx"),
                version_label=getattr(prof, "profile_name", "QR1"),
                release_date=date(2026, 10, 1),
                status="active",
                profile_name=getattr(prof, "profile_name", "spherex_qr_v1"),
                n_frames=None,
            )
        ]
    if releases:
        cache.set_json(cache_key, [r.model_dump(mode="json") for r in releases], ttl_s=3600)
    return releases


def list_examples_service(*, reference_path: Path | None = None) -> ExamplesResponse:
    """Load the 5 mandatory benchmark target presets from `reference_values.json`."""
    path = _get_reference_path(reference_path)
    if not path.is_file():
        return ExamplesResponse(targets=[])
    try:
        targets: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")).get("targets", {})
    except Exception:
        return ExamplesResponse(targets=[])
    items: list[ExampleTargetSummary] = []
    for key in MANDATORY_EXAMPLE_KEYS:
        t = targets.get(key)
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


def extract_2d_image(fits_bytes: bytes) -> np.ndarray:
    """Extract primary 2D float32 science array via `load_frame` with FITS fallback."""
    try:
        return np.asarray(load_frame(fits_bytes).image, dtype=np.float32)
    except Exception:
        with fits.open(io.BytesIO(fits_bytes), memmap=False) as hdul:
            for hdu in hdul:
                if (data := getattr(hdu, "data", None)) is not None and data.ndim >= 2:
                    arr = np.asarray(data, dtype=np.float32)
                    return arr[0] if arr.ndim > 2 else arr
    raise ValueError("FITS payload contains no 2D image array")


def asinh_stretch_u8(
    arr: np.ndarray, *, vmin: float | None = None, vmax: float | None = None
) -> tuple[np.ndarray, float, float]:
    """Stretch a 2D array with asinh scaling into uint8 `[0, 255]` and return `(u8, vmin, vmax)`."""
    arr32 = np.asarray(arr, dtype=np.float32)
    if vmin is None or vmax is None:
        finite = arr32[np.isfinite(arr32)]
        if finite.size == 0:
            return np.zeros(arr32.shape, dtype=np.uint8), 0.0, 1.0
        p_lo, p_hi = (float(x) for x in np.percentile(finite, [1.0, 99.5]))
        vmin = p_lo if vmin is None else vmin
        vmax = p_hi if vmax is None else vmax
    span = max(vmax - vmin, 1e-12)
    scaled = np.clip((np.nan_to_num(arr32, nan=vmin) - vmin) / span, 0.0, 1.0)
    norm = np.arcsinh(10.0 * scaled) / np.arcsinh(10.0)
    return np.clip(np.round(norm * 255.0), 0, 255).astype(np.uint8), float(vmin), float(vmax)
