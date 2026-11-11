"""JPL Horizons, SBDB, and SB-Identification provider for solar-system objects and observers."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

import astropy.units as u
import httpx
import requests
from astropy.coordinates import Angle, SkyCoord
from astropy.time import Time
from astroquery.jplhorizons import HorizonsClass

from app.providers.base import (
    CatalogSource,
    CircuitBreaker,
    EphemerisPoint,
    Provider,
    ProviderQueryError,
    RateLimiter,
    ReleaseDescriptor,
    _col_num,
    _first_scalar,
    _scalar,
    _to_table,
    default_registry,
)

SBDB_URL = "https://ssd-api.jpl.nasa.gov/sbdb.api"
SB_IDENT_URL = "https://ssd-api.jpl.nasa.gov/sb_ident.api"


def _step_to_horizons(step: u.Quantity) -> str:
    sec = int(round(float(step.to_value(u.s))))
    if sec <= 0:
        raise ProviderQueryError("Ephemeris step must be > 0")
    for unit_sec, suffix in ((86400, "d"), (3600, "h"), (60, "m")):
        if sec % unit_sec == 0:
            return f"{sec // unit_sec}{suffix}"
    raise ProviderQueryError(f"Horizons range step must be a multiple of 1m; got {sec}s")


def _format_horizons_epochs(
    epochs: Time | Sequence[Time] | tuple[Time, Time, u.Quantity],
    *,
    scale: str,
) -> dict[str, str] | list[float]:
    if isinstance(epochs, tuple) and len(epochs) == 3:
        t0, t1, step = epochs
        if t1 < t0:
            raise ProviderQueryError("Start epoch must be <= stop epoch")
        t0_s = t0.utc if scale == "utc" else t0.tdb
        t1_s = t1.utc if scale == "utc" else t1.tdb
        return {
            "start": t0_s.iso.split(".")[0],
            "stop": t1_s.iso.split(".")[0],
            "step": _step_to_horizons(step),
        }
    if isinstance(epochs, Time):
        t_conv = epochs.utc if scale == "utc" else epochs.tdb
        if t_conv.isscalar:
            return [float(t_conv.jd)]
        return [float(x) for x in t_conv.jd]
    seq = list(epochs)
    if not seq:
        raise ProviderQueryError("At least one epoch is required")
    return [float((t.utc if scale == "utc" else t.tdb).jd) for t in seq]


def _sb_ident_ra_dec(icrs: SkyCoord) -> tuple[str, str]:
    h, m, s = icrs.ra.hms
    ra_str = f"{int(h):02d}-{int(m):02d}-{s:05.2f}"
    sign, d, dm, ds = icrs.dec.signed_dms
    prefix = "M" if sign < 0 else ""
    dec_str = f"{prefix}{int(d):02d}-{int(dm):02d}-{ds:04.1f}"
    return ra_str, dec_str


def _parse_sky_angle(val: Any, unit: u.UnitBase) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    text = str(val).strip()
    if not text:
        return None
    try:
        return float(Angle(text, unit=unit).to_value(u.deg))
    except Exception:
        return None


class HorizonsProvider(Provider):
    """JPL Horizons, SBDB, and SB-Identification provider (strictly serial rate limit)."""

    name = "horizons"
    kind = {"ephemeris", "catalog"}

    def __init__(
        self,
        *,
        horizons_cls: Any = HorizonsClass,
        session: requests.Session | None = None,
        http_client: httpx.Client | None = None,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        max_retries: int = 3,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._horizons_cls = horizons_cls
        self._session = session or requests.Session()
        self._http = http_client or httpx.Client(timeout=30.0, follow_redirects=True)
        # JPL asks for serial requests with no parallel flooding (docs/API.md).
        self._rate_limiter = rate_limiter or RateLimiter(max_concurrent=1, rate_per_sec=1.0)
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        self._max_retries = max_retries
        self._sleeper = sleeper or time.sleep

    def _new_query(
        self,
        target: str,
        location: str | Mapping[str, Any],
        epochs: dict[str, str] | list[float],
        id_type: str | None,
    ) -> Any:
        q = self._horizons_cls(id=target, location=location, epochs=epochs, id_type=id_type)
        if hasattr(q, "_session"):
            q._session = self._session
        return q

    def describe_releases(self) -> list[ReleaseDescriptor]:
        """Return JPL SSD Horizons/SBDB service descriptor."""
        return [
            ReleaseDescriptor(
                release_id="jpl_horizons",
                provider=self.name,
                mission="JPL/SSD",
                collection="horizons",
                version_label="DE440/441",
                release_date=date(2026, 1, 1),
                metadata={"sbdb_url": SBDB_URL, "sb_ident_url": SB_IDENT_URL},
            )
        ]

    def ephemeris(
        self,
        target: str,
        epochs: Time | Sequence[Time] | tuple[Time, Time, u.Quantity],
        location: str | Mapping[str, Any] = "500",
        *,
        id_type: str | None = None,
        refsystem: str = "ICRF",
        extra_precision: bool = True,
        **kwargs: Any,
    ) -> list[EphemerisPoint]:
        """Query JPL Horizons observer ephemerides (RA/Dec deg, sky rates arcsec/hr, 1-sig err)."""
        del kwargs
        cleaned = target.strip()
        if not cleaned:
            raise ProviderQueryError("Target identifier must not be empty")
        h_epochs = _format_horizons_epochs(epochs, scale="utc")

        def _fetch() -> Any:
            q = self._new_query(cleaned, location, h_epochs, id_type)
            try:
                return q.ephemerides(
                    refsystem=refsystem, extra_precision=extra_precision, cache=False
                )
            except ValueError as exc:
                raise ProviderQueryError(str(exc)) from exc

        table = _to_table(self._run(_fetch))
        units = {c: getattr(table[c], "unit", None) for c in table.colnames}
        loc_label = location if isinstance(location, str) else str(dict(location))

        points: list[EphemerisPoint] = []
        for row in table:
            jd_utc = _scalar(row, "datetime_jd")
            ra, dec = _col_num(row, units, "RA", u.deg), _col_num(row, units, "DEC", u.deg)
            if jd_utc is None or ra is None or dec is None:
                continue

            ra_3sig = _col_num(row, units, "RA_3sigma", u.arcsec)
            dec_3sig = _col_num(row, units, "DEC_3sigma", u.arcsec)
            extra: dict[str, Any] = {
                k: v
                for k in ("targetname", "solar_presence", "flags")
                if (v := _scalar(row, k)) is not None
            }
            if (rss := _col_num(row, units, ("POS_3sigma", "RSS_3sigma"), u.arcsec)) is not None:
                extra["rss_3sigma_arcsec"] = rss

            points.append(
                EphemerisPoint(
                    provider=self.name,
                    target=str(_scalar(row, "targetname") or cleaned),
                    epoch=Time(float(jd_utc), format="jd", scale="utc").tdb,
                    ra_deg=float(ra) % 360.0,
                    dec_deg=float(dec),
                    ra_err_arcsec=(ra_3sig / 3.0) if ra_3sig is not None else None,
                    dec_err_arcsec=(dec_3sig / 3.0) if dec_3sig is not None else None,
                    rate_ra_arcsec_hr=_col_num(row, units, "RA_rate", u.arcsec / u.h),
                    rate_dec_arcsec_hr=_col_num(row, units, "DEC_rate", u.arcsec / u.h),
                    mag_v=float(m)
                    if (m := _first_scalar(row, "V", "Tmag", "Nmag")) is not None
                    else None,
                    delta_au=_col_num(row, units, "delta", u.au),
                    r_helio_au=_col_num(row, units, "r", u.au),
                    solar_elong_deg=_col_num(row, units, "elong", u.deg),
                    observer_location=loc_label,
                    extra=extra,
                )
            )
        return sorted(points, key=lambda p: p.mjd_tdb)

    def observer_vectors(
        self,
        target: str,
        epochs: Time | Sequence[Time] | tuple[Time, Time, u.Quantity],
        location: str = "@ssb",
        *,
        aberrations: str = "geometric",
    ) -> list[dict[str, Any]]:
        """Query ICRF state vectors `(x, y, z [au], vx, vy, vz [au/d])` (e.g. SPHEREx)."""
        cleaned = target.strip()
        if not cleaned:
            raise ProviderQueryError("Target identifier must not be empty")
        h_epochs = _format_horizons_epochs(epochs, scale="tdb")

        def _fetch() -> Any:
            q = self._new_query(cleaned, location, h_epochs, None)
            try:
                return q.vectors(refplane="earth", aberrations=aberrations, cache=False)
            except ValueError as exc:
                raise ProviderQueryError(str(exc)) from exc

        table = _to_table(self._run(_fetch))
        units = {c: getattr(table[c], "unit", None) for c in table.colnames}
        out: list[dict[str, Any]] = []
        for row in table:
            jd_tdb = _scalar(row, "datetime_jd")
            if jd_tdb is None:
                continue
            t = Time(float(jd_tdb), format="jd", scale="tdb")
            x, y, z = (_col_num(row, units, k, u.au) for k in ("x", "y", "z"))
            vx, vy, vz = (_col_num(row, units, k, u.au / u.d) for k in ("vx", "vy", "vz"))
            if x is None or y is None or z is None:
                continue
            out.append(
                {
                    "target": str(_scalar(row, "targetname") or cleaned),
                    "location": location,
                    "epoch": t,
                    "mjd_tdb": float(t.mjd),
                    "xyz_au": (x, y, z),
                    "vxyz_au_d": (vx, vy, vz),
                    "range_au": _col_num(row, units, "range", u.au),
                    "lighttime_d": _col_num(row, units, "lighttime", u.d),
                }
            )
        return sorted(out, key=lambda r: r["mjd_tdb"])

    def lookup_object(self, target: str, **kwargs: Any) -> dict[str, Any] | None:
        """Query JPL SBDB API for orbital elements, 1-sigma uncertainties, and physical params."""
        del kwargs
        cleaned = target.strip()
        if not cleaned:
            raise ProviderQueryError("Target identifier must not be empty")

        def _get() -> dict[str, Any] | None:
            resp = self._http.get(SBDB_URL, params={"sstr": cleaned, "cov": "mat", "phys-par": "1"})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json()
            return payload

        data = self._run(_get)
        if not data:
            return None
        code = str(data.get("code", "200"))
        if code == "300":
            cands = [item.get("name", item.get("pdes", "")) for item in data.get("list", [])[:5]]
            raise ProviderQueryError(f"Ambiguous SBDB target {target!r}: matches {cands}")
        obj = data.get("object")
        if not obj:
            return None

        orbit = data.get("orbit", {})
        elements: dict[str, float] = {}
        uncertainties: dict[str, float] = {}
        for item in orbit.get("elements", []):
            name = item.get("name")
            if not name:
                continue
            if item.get("value") is not None:
                elements[str(name)] = float(item["value"])
            if item.get("sigma") is not None:
                uncertainties[str(name)] = float(item["sigma"])

        phys = {
            str(p["name"]): p.get("value")
            for p in data.get("phys_par", [])
            if p.get("name") is not None and p.get("value") is not None
        }
        return {
            "provider": self.name,
            "target": cleaned,
            "fullname": obj.get("fullname"),
            "des": obj.get("des"),
            "spkid": obj.get("spkid"),
            "kind": obj.get("kind"),
            "neo": bool(obj.get("neo", False)),
            "pha": bool(obj.get("pha", False)),
            "orbit_class": (obj.get("orbit_class") or {}).get("name"),
            "epoch_jd": float(orbit["epoch"]) if orbit.get("epoch") is not None else None,
            "elements": elements,
            "uncertainties": uncertainties,
            "phys_par": phys,
        }

    def cone(
        self,
        c: SkyCoord,
        radius: u.Quantity,
        epoch: Time | None = None,
        *,
        mpc_code: str = "500",
        vmag_lim: float = 24.0,
    ) -> list[CatalogSource]:
        """Identify known small bodies inside `(c, radius)` at `epoch` via JPL `sb_ident.api`."""
        r_deg = float(radius.to_value(u.deg))
        if r_deg <= 0.0:
            raise ProviderQueryError("Search radius must be > 0 deg")
        obs_epoch = (epoch or Time.now()).utc
        icrs = c.icrs
        ra_str, dec_str = _sb_ident_ra_dec(icrs)
        cos_dec = max(0.1, math.cos(math.radians(icrs.dec.deg)))
        params = {
            "obs-time": obs_epoch.iso.split(".")[0].replace(" ", "_"),
            "mpc-code": mpc_code,
            "fov-ra-center": ra_str,
            "fov-dec-center": dec_str,
            "fov-ra-hwidth": f"{min(45.0, r_deg / cos_dec):.6f}",
            "fov-dec-hwidth": f"{min(45.0, r_deg):.6f}",
            "vmag-lim": f"{vmag_lim:.2f}",
            "two-pass": "true",
        }

        def _get() -> dict[str, Any]:
            resp = self._http.get(SB_IDENT_URL, params=params)
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json()
            return payload

        payload = self._run(_get)
        rows = (
            payload.get("data_second_pass")
            or payload.get("data_first_pass")
            or payload.get("data")
            or []
        )
        fields = (
            payload.get("fields_second")
            or payload.get("fields_first")
            or payload.get("fields")
            or []
        )
        idx_map = {str(name).lower(): i for i, name in enumerate(fields)}

        def _pick(rec: Sequence[Any], *patterns: str) -> Any:
            for col_name, idx in idx_map.items():
                if any(p in col_name for p in patterns) and idx < len(rec):
                    return rec[idx]
            return None

        epoch_tdb = obs_epoch.tdb
        sources: list[CatalogSource] = []
        for rec in rows:
            name = _pick(rec, "object name", "name", "des")
            ra_deg = _parse_sky_angle(_pick(rec, "astrometric ra", "ra"), u.hourangle)
            dec_deg = _parse_sky_angle(_pick(rec, "astrometric dec", "dec"), u.deg)
            if not name or ra_deg is None or dec_deg is None:
                continue
            sc = SkyCoord(ra_deg * u.deg, dec_deg * u.deg, frame="icrs")
            if float(sc.separation(icrs).to_value(u.deg)) > r_deg:
                continue
            vmag_raw = _pick(rec, "visual mag", "vmag", "mag")
            mag = float(vmag_raw) if vmag_raw not in (None, "", "n.a.") else None
            ra_rate = _pick(rec, "ra rate", "dra")
            dec_rate = _pick(rec, "dec rate", "ddec")
            extra: dict[str, Any] = {"mpc_code": mpc_code}
            if ra_rate is not None:
                extra["rate_ra_arcsec_hr"] = float(ra_rate)
            if dec_rate is not None:
                extra["rate_dec_arcsec_hr"] = float(dec_rate)
            sources.append(
                CatalogSource(
                    provider=self.name,
                    catalog="sb_ident",
                    source_id=str(name).strip(),
                    ra_deg=ra_deg % 360.0,
                    dec_deg=dec_deg,
                    epoch=epoch_tdb,
                    mag=mag,
                    mag_band="V" if mag is not None else None,
                    mags={"V": mag} if mag is not None else {},
                    object_type="sso",
                    extra=extra,
                )
            )
        return sorted(sources, key=lambda s: float(s.skycoord.separation(icrs).to_value(u.arcsec)))


default_registry.register(HorizonsProvider.name, HorizonsProvider, overwrite=True)
