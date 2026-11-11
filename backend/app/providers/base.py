"""Provider plug-in interface, entry-point registry, retry/rate-limit, and storage helpers."""

from __future__ import annotations

import hashlib
import io
import math
import re
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from importlib import metadata as importlib_metadata
from types import TracebackType
from typing import Any, TypeVar

import astropy.units as u
import httpx
import numpy as np
import requests
from astropy.coordinates import Distance, SkyCoord, get_body_barycentric
from astropy.io import fits
from astropy.table import Table
from astropy.time import Time
from astropy.wcs import WCS
from astroquery.exceptions import InvalidQueryError
from numpy.typing import NDArray
from pyvo.dal import DALQueryError, DALRateLimitError, DALServiceError

from app.services.storage import ChecksumMismatchError, StorageBackend

T = TypeVar("T")


class ProviderError(RuntimeError):
    """Base error for external archive provider failures."""


class ProviderQueryError(ProviderError):
    """Client/query error (invalid coordinates, SQL syntax, HTTP 400/404); never retried."""


class ProviderRateLimitError(ProviderError):
    """Raised when an archive provider rejects requests with HTTP 429 past retries."""

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class ProviderUnavailableError(ProviderError):
    """Raised when a provider is unreachable, returns 5xx/corrupt FITS, or circuit is open."""


@dataclass(frozen=True)
class FrameRef:
    """Normalized archive frame descriptor (ICRS deg, MJD TDB, um, s)."""

    provider: str
    release: str
    obs_id: str
    access_url: str
    t_mid: Time
    band_id: str | None = None
    array_id: int | None = None
    wavelength_um: tuple[float, float] | None = None
    footprint: list[tuple[float, float]] = field(default_factory=list)
    flags: int = 0
    flag_labels: list[str] = field(default_factory=list)
    ra_deg: float | None = None
    dec_deg: float | None = None
    exptime_s: float | None = None
    calib_level: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def mjd_tdb(self) -> float:
        """Mid-exposure epoch as MJD in TDB scale (days)."""
        return float(self.t_mid.tdb.mjd)


@dataclass(frozen=True)
class ReleaseDescriptor:
    """Archive data release metadata reported by a provider."""

    release_id: str
    provider: str
    mission: str
    collection: str
    version_label: str | None = None
    release_date: date | None = None
    n_frames: int | None = None
    profile_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NewReleaseReport:
    """Diff between live archive releases and known local release state."""

    provider: str
    checked_at: datetime
    new_releases: list[ReleaseDescriptor]
    updated_releases: list[ReleaseDescriptor]
    known_releases: list[ReleaseDescriptor]

    @property
    def detected(self) -> bool:
        """Return True if at least one new or updated release was detected."""
        return bool(self.new_releases or self.updated_releases)


@dataclass(frozen=True)
class StoredFrame:
    """FITS file or cutout persisted in the storage backend with SHA-256 checksum."""

    ref: FrameRef
    storage_path: str
    sha256: str
    size_bytes: int
    is_cutout: bool
    cache_hit: bool
    wcs_header: str | None = None
    header_summary: dict[str, Any] = field(default_factory=dict)
    flags_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogSource:
    """Normalized external catalog entry (ICRS deg, MJD TDB, mas, mas/yr, km/s, mag)."""

    provider: str
    catalog: str
    source_id: str
    ra_deg: float
    dec_deg: float
    epoch: Time
    ra_err_mas: float | None = None
    dec_err_mas: float | None = None
    pmra_masyr: float | None = None
    pmra_err_masyr: float | None = None
    pmdec_masyr: float | None = None
    pmdec_err_masyr: float | None = None
    parallax_mas: float | None = None
    parallax_err_mas: float | None = None
    radial_velocity_kms: float | None = None
    mag: float | None = None
    mag_band: str | None = None
    mags: dict[str, float] = field(default_factory=dict)
    mag_errs: dict[str, float] = field(default_factory=dict)
    object_type: str | None = None
    ref_ra_deg: float | None = None
    ref_dec_deg: float | None = None
    ref_epoch: Time | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def mjd_tdb(self) -> float:
        """Position epoch as MJD in TDB scale (days)."""
        return float(self.epoch.tdb.mjd)

    @property
    def skycoord(self) -> SkyCoord:
        """ICRS `SkyCoord` at `epoch` (deg)."""
        return SkyCoord(self.ra_deg * u.deg, self.dec_deg * u.deg, obstime=self.epoch, frame="icrs")


@dataclass(frozen=True)
class EphemerisPoint:
    """Normalized solar-system ephemeris prediction (ICRS deg, MJD TDB, arcsec, arcsec/hr, au)."""

    provider: str
    target: str
    epoch: Time
    ra_deg: float
    dec_deg: float
    ra_err_arcsec: float | None = None
    dec_err_arcsec: float | None = None
    rate_ra_arcsec_hr: float | None = None
    rate_dec_arcsec_hr: float | None = None
    mag_v: float | None = None
    delta_au: float | None = None
    r_helio_au: float | None = None
    solar_elong_deg: float | None = None
    observer_location: str = "500"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def mjd_tdb(self) -> float:
        """Ephemeris epoch as MJD in TDB scale (days)."""
        return float(self.epoch.tdb.mjd)

    @property
    def skycoord(self) -> SkyCoord:
        """ICRS `SkyCoord` at `epoch` (deg)."""
        return SkyCoord(self.ra_deg * u.deg, self.dec_deg * u.deg, obstime=self.epoch, frame="icrs")


class RateLimiter:
    """Concurrency-bounded, token-paced politeness limiter for external archives."""

    def __init__(
        self,
        *,
        max_concurrent: int = 4,
        rate_per_sec: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_concurrent < 1 or rate_per_sec <= 0.0:
            raise ValueError("max_concurrent must be >= 1 and rate_per_sec > 0")
        self._sem = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self._min_interval = 1.0 / rate_per_sec
        self._next_allowed = 0.0
        self._clock = clock
        self._sleeper = sleeper

    def __enter__(self) -> RateLimiter:
        self._sem.acquire()
        try:
            with self._lock:
                now = self._clock()
                wait = max(0.0, self._next_allowed - now)
                self._next_allowed = max(now, self._next_allowed) + self._min_interval
            if wait > 0.0:
                self._sleeper(wait)
        except Exception:
            self._sem.release()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._sem.release()


class CircuitBreaker:
    """Consecutive-failure circuit breaker for external provider calls."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None

    def before_call(self) -> None:
        """Raise ProviderUnavailableError when circuit is open and cooldown not elapsed."""
        with self._lock:
            if self._opened_at is not None:
                if (self._clock() - self._opened_at) < self._cooldown_s:
                    raise ProviderUnavailableError("Provider circuit breaker is open")
                self._opened_at = None

    def record_success(self) -> None:
        """Reset consecutive failure count after a successful call."""
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        """Increment failure count and open circuit once threshold is reached."""
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold:
                self._opened_at = self._clock()


def parse_retry_after(value: str | None) -> float | None:
    """Parse HTTP Retry-After header (seconds or HTTP-date) to delay in seconds."""
    if not value or not value.strip():
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (dt - datetime.now(UTC)).total_seconds())


def _classify_http(
    status: int, headers: Mapping[str, str], exc: Exception
) -> tuple[bool, float | None, bool, ProviderError | None]:
    if status == 429 or 500 <= status < 600:
        return True, parse_retry_after(headers.get("Retry-After")), status == 429, None
    if 400 <= status < 500:
        return False, None, False, ProviderQueryError(f"HTTP {status}: {exc}")
    return False, None, False, None


def _classify_exception(exc: Exception) -> tuple[bool, float | None, bool, ProviderError | None]:
    """Return `(retryable, retry_after_s, is_rate_limit, terminal_err)`."""
    if isinstance(exc, (ProviderQueryError, DALQueryError, InvalidQueryError)):
        err = exc if isinstance(exc, ProviderQueryError) else ProviderQueryError(str(exc))
        return False, None, False, err
    if isinstance(exc, DALRateLimitError):
        wait = float(exc.retry_after_seconds) if exc.retry_after_seconds is not None else None
        return True, wait, True, None
    if isinstance(exc, DALServiceError):
        code = getattr(exc, "code", 0) or 0
        return (
            (False, None, False, ProviderQueryError(str(exc)))
            if 400 <= code < 500 and code != 429
            else (True, None, code == 429, None)
        )
    if isinstance(exc, httpx.HTTPStatusError):
        return _classify_http(exc.response.status_code, exc.response.headers, exc)
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return _classify_http(exc.response.status_code, exc.response.headers, exc)
    if isinstance(exc, (httpx.TransportError, requests.RequestException, TimeoutError, OSError)):
        return True, None, False, None
    return False, None, False, None


def call_with_retry(
    fn: Callable[[], T],
    *,
    rate_limiter: RateLimiter | None = None,
    circuit_breaker: CircuitBreaker | None = None,
    max_retries: int = 3,
    base_backoff_s: float = 0.5,
    max_backoff_s: float = 30.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> T:
    """Execute `fn` under rate limit, circuit breaker, and exponential-backoff retries."""
    if circuit_breaker is not None:
        circuit_breaker.before_call()

    last_exc: Exception | None = None
    last_rate_limit = False
    last_retry_after: float | None = None

    for attempt in range(max_retries + 1):
        try:
            if rate_limiter is not None:
                with rate_limiter:
                    result = fn()
            else:
                result = fn()
        except Exception as exc:
            retryable, retry_after, is_rate_limit, terminal = _classify_exception(exc)
            if terminal is not None:
                raise terminal from exc
            if not retryable:
                raise
            last_exc, last_rate_limit, last_retry_after = exc, is_rate_limit, retry_after
            if attempt >= max_retries:
                break
            delay = max(retry_after or 0.0, min(max_backoff_s, base_backoff_s * (2**attempt)))
            if delay > 0.0:
                sleeper(delay)
        else:
            if circuit_breaker is not None:
                circuit_breaker.record_success()
            return result

    if circuit_breaker is not None:
        circuit_breaker.record_failure()
    if last_rate_limit:
        raise ProviderRateLimitError(
            f"Rate limit exceeded after {max_retries + 1} attempts: {last_exc}",
            retry_after_s=last_retry_after,
        ) from last_exc
    raise ProviderUnavailableError(
        f"Provider unavailable after {max_retries + 1} attempts: {last_exc}"
    ) from last_exc


_STCS_COORD_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _col_num(
    row: Any,
    units: Mapping[str, Any],
    keys: str | Sequence[str],
    target_unit: u.UnitBase,
    default_unit: u.UnitBase | None = None,
) -> float | None:
    """Extract first matching key from `row` and convert to `target_unit`."""
    key_tuple = (keys,) if isinstance(keys, str) else tuple(keys)
    val = _first_scalar(row, *key_tuple)
    if val is None:
        return None
    col_unit = next((units.get(k) for k in key_tuple if units.get(k) is not None), None)
    return _convert_unit(
        val, col_unit, target_unit, target_unit if default_unit is None else default_unit
    )


def _pm_search_radius_deg(
    r_deg: float,
    epoch: Time | None,
    ref_epoch: Time,
    pm_pad_arcsec: float | None = None,
    *,
    max_pm_arcsec_yr: float = 12.0,
    max_pad_arcsec: float = 180.0,
) -> float:
    """Inflate cone search radius (deg) for proper-motion drift between `ref_epoch` and `epoch`."""
    if pm_pad_arcsec is not None:
        pad = max(0.0, float(pm_pad_arcsec))
    elif epoch is not None:
        pad = min(
            max_pad_arcsec, max_pm_arcsec_yr * abs(float(epoch.tdb.jd - ref_epoch.tdb.jd)) / 365.25
        )
    else:
        pad = 0.0
    return r_deg + pad / 3600.0


def _apply_observer_parallax(
    coords: SkyCoord, plx_mas: NDArray[np.float64], bary_xyz: NDArray[np.float64]
) -> SkyCoord:
    """Shift ICRS `coords` by annual/spacecraft parallax (mas) for `bary_xyz` [au]."""
    ra_r, dec_r = np.deg2rad(coords.ra.deg), np.deg2rad(coords.dec.deg)
    pa = bary_xyz[0] * np.sin(ra_r) - bary_xyz[1] * np.cos(ra_r)
    pd = (
        bary_xyz[0] * np.cos(ra_r) * np.sin(dec_r)
        + bary_xyz[1] * np.sin(ra_r) * np.sin(dec_r)
        - bary_xyz[2] * np.cos(dec_r)
    )
    plx = np.where(np.isfinite(plx_mas) & (plx_mas > 0.0), plx_mas, 0.0)
    return coords.spherical_offsets_by((plx * pa) * u.mas, (plx * pd) * u.mas)


def _finalize_cone(
    sources: list[CatalogSource],
    center: SkyCoord,
    radius_deg: float,
    epoch: Time | None = None,
    observer_bary_au: Sequence[float] | str | None = None,
) -> list[CatalogSource]:
    """Optionally propagate `sources` to `epoch`, trim to `radius_deg`, and sort by separation."""
    icrs = center.icrs
    if epoch is not None:
        sources = propagate_catalog_sources(sources, epoch, observer_bary_au=observer_bary_au)
        sources = [
            s for s in sources if float(s.skycoord.separation(icrs).to_value(u.deg)) <= radius_deg
        ]
    return sorted(sources, key=lambda s: float(s.skycoord.separation(icrs).to_value(u.arcsec)))


def _parse_date(val: Any) -> date | None:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str) and val.strip():
        return date.fromisoformat(val.strip()[:10])
    return None


def _safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return cleaned or hashlib.sha256(value.encode()).hexdigest()[:16]


def _scalar(row: Any, key: str) -> Any:
    colnames = getattr(row, "colnames", None) or row.keys()
    if key not in colnames:
        return None
    val = row[key]
    if val is None or np.ma.is_masked(val):
        return None
    if isinstance(val, u.Quantity):
        val = val.value
    if isinstance(val, np.generic):
        val = val.item()
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return None if isinstance(val, float) and math.isnan(val) else val


def _first_scalar(row: Any, *keys: str) -> Any:
    """Return first non-null/unmasked scalar value matching any key in `keys`."""
    for key in keys:
        val = _scalar(row, key)
        if val is not None:
            return val
    return None


def _convert_unit(
    val: Any,
    col_unit: Any,
    target_unit: u.UnitBase,
    default_unit: u.UnitBase | None = None,
) -> float | None:
    """Convert numeric scalar from `col_unit` (or `default_unit`) to `target_unit`."""
    if val is None:
        return None
    fval = float(val)
    unit_obj = col_unit or default_unit
    if unit_obj is not None:
        try:
            parsed = u.Unit(unit_obj)
            if parsed.is_equivalent(target_unit):
                return float((fval * parsed).to_value(target_unit))
        except Exception:
            pass
    return fval


def _to_table(result: Any) -> Table:
    if isinstance(result, Table):
        return result
    if hasattr(result, "to_table"):
        table: Table = result.to_table()
        return table
    return Table(result)


def _parse_s_region(
    s_region: Any,
    ra_deg: float | None = None,
    dec_deg: float | None = None,
    fov_deg: float | None = None,
) -> list[tuple[float, float]]:
    """Parse IVOA STC-S `s_region` polygon/circle into ICRS `(ra_deg, dec_deg)` vertices."""
    if isinstance(s_region, (list, tuple)):
        return [(float(p[0]), float(p[1])) for p in s_region]
    if isinstance(s_region, str) and s_region.strip():
        text = s_region.strip().upper()
        nums = [float(x) for x in _STCS_COORD_RE.findall(text)]
        if "POLYGON" in text and len(nums) >= 6:
            return [(nums[2 * i], nums[2 * i + 1]) for i in range(len(nums) // 2)]
        if "CIRCLE" in text and len(nums) >= 3:
            ring = SkyCoord(nums[0] * u.deg, nums[1] * u.deg, frame="icrs").directional_offset_by(
                np.linspace(0.0, 360.0, 16, endpoint=False) * u.deg, nums[2] * u.deg
            )
            return [(float(r), float(d)) for r, d in zip(ring.ra.deg, ring.dec.deg, strict=True)]
    if ra_deg is not None and dec_deg is not None and fov_deg is not None and fov_deg > 0.0:
        half = 0.5 * fov_deg
        dra = half / max(0.1, math.cos(math.radians(dec_deg)))
        return [
            ((ra_deg - dra) % 360.0, max(-90.0, dec_deg - half)),
            ((ra_deg + dra) % 360.0, max(-90.0, dec_deg - half)),
            ((ra_deg + dra) % 360.0, min(90.0, dec_deg + half)),
            ((ra_deg - dra) % 360.0, min(90.0, dec_deg + half)),
        ]
    return []


def propagate_catalog_sources(
    sources: Sequence[CatalogSource],
    epoch: Time,
    *,
    observer_bary_au: Sequence[float] | str | None = None,
) -> list[CatalogSource]:
    """Propagate `CatalogSource` ICRS coordinates, errors, and optional parallax to `epoch`."""
    if not sources:
        return []
    target_tdb = epoch.tdb
    bary_xyz: NDArray[np.float64] | None = None
    if isinstance(observer_bary_au, str):
        if observer_bary_au.lower() != "earth":
            raise ProviderQueryError(f"Unsupported observer_bary_au preset: {observer_bary_au!r}")
        bary_xyz = np.asarray(
            get_body_barycentric("earth", target_tdb).xyz.to_value(u.au), dtype=np.float64
        )
    elif observer_bary_au is not None:
        bary_xyz = np.asarray(observer_bary_au, dtype=np.float64)
        if bary_xyz.shape != (3,):
            raise ProviderQueryError("observer_bary_au must be a 3-element ICRS vector [au]")

    mov_idx = [
        i for i, s in enumerate(sources) if s.pmra_masyr is not None and s.pmdec_masyr is not None
    ]
    out = list(sources)
    if mov_idx:
        ra = np.array([sources[i].ra_deg for i in mov_idx], dtype=np.float64)
        dec = np.array([sources[i].dec_deg for i in mov_idx], dtype=np.float64)
        pmra = np.array([sources[i].pmra_masyr for i in mov_idx], dtype=np.float64)
        pmdec = np.array([sources[i].pmdec_masyr for i in mov_idx], dtype=np.float64)
        plx_raw = np.array(
            [
                sources[i].parallax_mas if sources[i].parallax_mas is not None else np.nan
                for i in mov_idx
            ],
            dtype=np.float64,
        )
        plx_eff = np.where(np.isfinite(plx_raw) & (plx_raw > 0.0), plx_raw, 0.01)
        rv = np.array([sources[i].radial_velocity_kms or 0.0 for i in mov_idx], dtype=np.float64)
        ref_times = Time([sources[i].epoch.tdb.jd for i in mov_idx], format="jd", scale="tdb")
        sc = SkyCoord(
            ra=ra * u.deg,
            dec=dec * u.deg,
            distance=Distance(parallax=plx_eff * u.mas),
            pm_ra_cosdec=pmra * u.mas / u.yr,
            pm_dec=pmdec * u.mas / u.yr,
            radial_velocity=rv * u.km / u.s,
            obstime=ref_times,
            frame="icrs",
        )
        prop = sc.apply_space_motion(new_obstime=target_tdb)
        if bary_xyz is not None and np.any(np.isfinite(plx_raw) & (plx_raw > 0.0)):
            prop = _apply_observer_parallax(prop, plx_raw, bary_xyz)

        dt_yr = np.abs(float(target_tdb.jd) - ref_times.jd) / 365.25
        for k, idx in enumerate(mov_idx):
            s, dt = sources[idx], float(dt_yr[k])
            ra_err = (
                math.hypot(s.ra_err_mas or 0.0, dt * (s.pmra_err_masyr or 0.0))
                if (s.ra_err_mas is not None or (s.pmra_err_masyr is not None and dt > 0.0))
                else None
            )
            dec_err = (
                math.hypot(s.dec_err_mas or 0.0, dt * (s.pmdec_err_masyr or 0.0))
                if (s.dec_err_mas is not None or (s.pmdec_err_masyr is not None and dt > 0.0))
                else None
            )
            out[idx] = replace(
                s,
                ra_deg=float(prop[k].ra.deg) % 360.0,
                dec_deg=float(prop[k].dec.deg),
                epoch=target_tdb,
                ra_err_mas=ra_err,
                dec_err_mas=dec_err,
                ref_ra_deg=s.ref_ra_deg if s.ref_ra_deg is not None else s.ra_deg,
                ref_dec_deg=s.ref_dec_deg if s.ref_dec_deg is not None else s.dec_deg,
                ref_epoch=s.ref_epoch or s.epoch,
                extra={
                    **s.extra,
                    "pm_propagated": True,
                    "observer_parallax_applied": bool(
                        bary_xyz is not None and s.parallax_mas is not None and s.parallax_mas > 0.0
                    ),
                },
            )

    if bary_xyz is not None:
        static_idx = [
            i
            for i, s in enumerate(out)
            if i not in mov_idx and s.parallax_mas is not None and s.parallax_mas > 0.0
        ]
        if static_idx:
            s_coords = SkyCoord(
                [out[i].ra_deg * u.deg for i in static_idx],
                [out[i].dec_deg * u.deg for i in static_idx],
                frame="icrs",
            )
            s_plx = np.array([out[i].parallax_mas for i in static_idx], dtype=np.float64)
            shifted = _apply_observer_parallax(s_coords, s_plx, bary_xyz)
            for k, idx in enumerate(static_idx):
                s = out[idx]
                out[idx] = replace(
                    s,
                    ra_deg=float(shifted[k].ra.deg) % 360.0,
                    dec_deg=float(shifted[k].dec.deg),
                    epoch=target_tdb,
                    ref_ra_deg=s.ref_ra_deg if s.ref_ra_deg is not None else s.ra_deg,
                    ref_dec_deg=s.ref_dec_deg if s.ref_dec_deg is not None else s.dec_deg,
                    ref_epoch=s.ref_epoch or s.epoch,
                    extra={**s.extra, "observer_parallax_applied": True},
                )
    return out


def _summarize_fits_bytes(
    data: bytes, provider: Provider
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Parse FITS bytes into `(wcs_header, header_summary, flags_summary)`."""
    with fits.open(io.BytesIO(data), memmap=False) as hdul:
        hdus = [
            {
                "index": idx,
                "name": hdu.name or "PRIMARY",
                "type": type(hdu).__name__,
                "shape": list(hdu.data.shape) if getattr(hdu, "data", None) is not None else [],
                "bunit": hdu.header.get("BUNIT"),
            }
            for idx, hdu in enumerate(hdul)
        ]
        wcs_header = ""
        for hdu in hdul:
            if hdu.header.get("NAXIS", 0) >= 2:
                try:
                    w = WCS(hdu.header)
                    if w.has_celestial:
                        wcs_header = w.to_header(relax=True).tostring(sep="\n")
                        break
                except Exception:
                    continue
        if not wcs_header and len(hdul) > 0:
            wcs_header = hdul[0].header.tostring(sep="\n")

        hdr = hdul[0].header
        header_summary: dict[str, Any] = {
            "hdus": hdus,
            "date_obs": hdr.get("DATE-OBS"),
            "mjd_obs": hdr.get("MJD-OBS"),
            "exptime": hdr.get("EXPTIME", hdr.get("XPOSURE")),
            "filter": hdr.get("FILTER", hdr.get("BAND")),
        }
        return wcs_header, header_summary, provider.flags_summary(hdul)


class Provider(ABC):
    """Abstract plug-in interface for astronomical image and catalog archives."""

    name: str
    kind: set[str]

    @abstractmethod
    def describe_releases(self) -> list[ReleaseDescriptor]:
        """Return available data releases from the archive."""

    def _run(self, fn: Callable[[], T]) -> T:
        sleeper = getattr(self, "_sleeper", None)
        return call_with_retry(
            fn,
            rate_limiter=getattr(self, "_rate_limiter", None),
            circuit_breaker=getattr(self, "_circuit_breaker", None),
            max_retries=getattr(self, "_max_retries", 3),
            sleeper=sleeper if sleeper is not None else time.sleep,
        )

    def check_new_releases(
        self,
        known: Mapping[str, ReleaseDescriptor | Mapping[str, Any]] | Sequence[str] | None = None,
    ) -> NewReleaseReport:
        """Compare live archive releases against `known` releases to detect updates."""
        live = self.describe_releases()
        now = datetime.now(UTC)
        if known is None:
            return NewReleaseReport(self.name, now, list(live), [], [])
        if isinstance(known, Sequence) and not isinstance(known, (str, bytes)):
            known_ids = set(known)
            return NewReleaseReport(
                self.name,
                now,
                [r for r in live if r.release_id not in known_ids],
                [],
                [r for r in live if r.release_id in known_ids],
            )

        assert isinstance(known, Mapping)
        new_rel: list[ReleaseDescriptor] = []
        upd_rel: list[ReleaseDescriptor] = []
        ok_rel: list[ReleaseDescriptor] = []
        for rel in live:
            prev = known.get(rel.release_id)
            if prev is None:
                new_rel.append(rel)
                continue
            prev_map = vars(prev) if isinstance(prev, ReleaseDescriptor) else prev
            p_date = _parse_date(prev_map.get("release_date"))
            p_n = prev_map.get("n_frames")
            p_ver = prev_map.get("version_label")
            updated = any(
                [
                    rel.release_date and (p_date is None or rel.release_date > p_date),
                    rel.n_frames is not None and p_n is not None and rel.n_frames > int(p_n),
                    rel.version_label and p_ver and rel.version_label != p_ver,
                ]
            )
            (upd_rel if updated else ok_rel).append(rel)
        return NewReleaseReport(self.name, now, new_rel, upd_rel, ok_rel)

    def search_frames(
        self,
        c: SkyCoord,
        radius: u.Quantity,
        t0: Time | None = None,
        t1: Time | None = None,
        bands: list[str] | None = None,
        release: str | None = None,
    ) -> list[FrameRef]:
        """Search archive image frames intersecting `(c, radius)` and optional `[t0, t1]`."""
        raise NotImplementedError

    def fetch(self, ref: FrameRef, cutout: tuple[SkyCoord, u.Quantity] | None = None) -> bytes:
        """Download full FITS or angular cutout `(center, size)` as FITS bytes."""
        raise NotImplementedError

    def fetch_to_storage(
        self,
        ref: FrameRef,
        storage: StorageBackend,
        cutout: tuple[SkyCoord, u.Quantity] | None = None,
        *,
        force_refresh: bool = False,
    ) -> StoredFrame:
        """Fetch cutout or full FITS to `storage` with content-addressed caching and SHA-256."""
        is_cutout = cutout is not None
        if cutout is not None:
            icrs = cutout[0].icrs
            size_arcsec = float(cutout[1].to_value(u.arcsec))
            key_src = (
                f"{ref.provider}:{ref.release}:{ref.obs_id}:"
                f"{icrs.ra.deg:.7f}:{icrs.dec.deg:.7f}:{size_arcsec:.3f}"
            )
            key = f"cache/cutouts/{hashlib.sha256(key_src.encode()).hexdigest()}.fits"
        else:
            key = (
                f"raw/{_safe_slug(ref.provider)}/{_safe_slug(ref.release)}/"
                f"{_safe_slug(ref.obs_id)}.fits"
            )

        data: bytes | None = None
        sha: str | None = None
        cache_hit = False
        if not force_refresh and storage.exists(key):
            try:
                data = storage.get_bytes(key, verify_checksum=True)
                sha = storage.get_sha256(key) or hashlib.sha256(data).hexdigest()
                cache_hit = True
            except (ChecksumMismatchError, OSError, ValueError):
                data = None

        if data is None:
            data = self.fetch(ref, cutout=cutout)
            wcs_hdr, hdr_sum, flag_sum = _summarize_fits_bytes(data, self)
            stored = storage.put_bytes(
                key,
                data,
                content_type="application/fits",
                metadata={"provider": ref.provider, "release": ref.release, "obs_id": ref.obs_id},
            )
            sha = stored.sha256
        else:
            wcs_hdr, hdr_sum, flag_sum = _summarize_fits_bytes(data, self)

        return StoredFrame(
            ref=ref,
            storage_path=key,
            sha256=sha or hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            is_cutout=is_cutout,
            cache_hit=cache_hit,
            wcs_header=wcs_hdr,
            header_summary=hdr_sum,
            flags_summary=flag_sum,
        )

    def cone(
        self, c: SkyCoord, radius: u.Quantity, epoch: Time | None = None
    ) -> list[CatalogSource]:
        """Query catalog sources within `(c, radius)` at optional `epoch`."""
        raise NotImplementedError

    def lookup_object(self, target: str, **kwargs: Any) -> Any:
        """Look up metadata, aliases, or orbital elements for `target`."""
        raise NotImplementedError

    def ephemeris(
        self,
        target: str,
        epochs: Time | Sequence[Time] | tuple[Time, Time, u.Quantity],
        location: str | Mapping[str, Any] = "500",
        **kwargs: Any,
    ) -> list[EphemerisPoint]:
        """Compute ephemeris positions/rates for solar-system `target`."""
        raise NotImplementedError

    def quality_mask(self, hdul: fits.HDUList) -> NDArray[np.bool_]:
        """Return boolean good-pixel quality mask (True = usable, False = masked/flagged)."""
        raise NotImplementedError

    def flags_summary(self, hdul: fits.HDUList) -> dict[str, Any]:
        """Return summary dict of flagged-pixel fractions and bitmasks for an HDUList."""
        return {}


class ProviderRegistry:
    """Plug-in registry backed by explicit factories and `sphx.providers` entry points."""

    def __init__(self, entry_point_group: str = "sphx.providers") -> None:
        self._group = entry_point_group
        self._factories: dict[str, Callable[..., Provider]] = {}
        self._discovered = False

    def register(
        self, name: str, factory: Callable[..., Provider], *, overwrite: bool = False
    ) -> None:
        """Register a provider factory by name."""
        if not overwrite and name in self._factories:
            raise ValueError(f"Provider {name!r} is already registered")
        self._factories[name] = factory

    def discover_entry_points(self, *, force: bool = False) -> None:
        """Discover installed `sphx.providers` entry-point plug-ins."""
        if self._discovered and not force:
            return
        for ep in importlib_metadata.entry_points(group=self._group):
            if ep.name not in self._factories:
                self._factories[ep.name] = ep.load()
        self._discovered = True

    def names(self) -> list[str]:
        """Return sorted list of registered provider names."""
        self.discover_entry_points()
        return sorted(self._factories)

    def get(self, name: str, **kwargs: Any) -> Provider:
        """Instantiate registered provider `name` with optional constructor kwargs."""
        self.discover_entry_points()
        if name not in self._factories:
            raise KeyError(f"Unknown provider {name!r}; available: {sorted(self._factories)}")
        return self._factories[name](**kwargs)


default_registry = ProviderRegistry()


def get_provider(name: str, **kwargs: Any) -> Provider:
    """Instantiate a provider from the default registry."""
    return default_registry.get(name, **kwargs)


def list_providers() -> list[str]:
    """Return available provider plug-in names."""
    return default_registry.names()
