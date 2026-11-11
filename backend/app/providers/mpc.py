"""IAU Minor Planet Center provider (orbits, ephemerides, observations, MPChecker cone)."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any, cast

import astropy.units as u
import httpx
import requests
from astropy.coordinates import Angle, SkyCoord
from astropy.time import Time
from astroquery.exceptions import EmptyResponseError
from astroquery.mpc import MPCClass

from app.providers.base import (
    CatalogSource,
    CircuitBreaker,
    EphemerisPoint,
    Provider,
    ProviderQueryError,
    RateLimiter,
    ReleaseDescriptor,
    _col_num,
    _scalar,
    default_registry,
)

MPCHECKER_URL = "https://minorplanetcenter.net/cgi-bin/checkmp.cgi"
_MPC_PROV_DESIG_RE = re.compile(r"^\d{4}\s+[A-Z]{2}\d*$", re.IGNORECASE)
_COMET_ID_RE = re.compile(r"^(?:\d+[PIA]|[PDCXAI]/.*)$", re.IGNORECASE)
_CHECKMP_LINE_RE = re.compile(
    r"^\s*(?P<desig>.+?)\s+"
    r"(?P<ra>\d{2}\s+\d{2}\s+\d{2}\.\d+)\s+"
    r"(?P<dec>[+-]?\d{2}\s+\d{2}\s+\d{2}(?:\.\d+)?)\s+"
    r"(?P<mag>\d+(?:\.\d+)?)\s+"
    r"(?P<rest>.*)$"
)


def _split_object_query(target: str) -> dict[str, Any]:
    cleaned = target.strip()
    if not cleaned:
        raise ProviderQueryError("Target identifier must not be empty")
    unwrapped = (
        cleaned[1:-1].strip() if cleaned.startswith("(") and cleaned.endswith(")") else cleaned
    )
    if unwrapped.isdigit():
        return {"number": int(unwrapped)}
    if _COMET_ID_RE.match(cleaned) or _MPC_PROV_DESIG_RE.match(cleaned):
        return {"designation": cleaned}
    return {"name": cleaned}


def _uniform_step(epochs: Sequence[Time]) -> u.Quantity | None:
    if len(epochs) == 1:
        return 1 * u.d
    diffs_s = [(epochs[i + 1].utc.jd - epochs[i].utc.jd) * 86400.0 for i in range(len(epochs) - 1)]
    if any(d <= 0.0 for d in diffs_s):
        raise ProviderQueryError("Ephemeris epochs must be strictly increasing")
    mean_s = sum(diffs_s) / len(diffs_s)
    if any(abs(d - mean_s) > max(1.0, 0.02 * mean_s) for d in diffs_s):
        return None
    for unit, scale in ((u.d, 86400.0), (u.h, 3600.0), (u.min, 60.0), (u.s, 1.0)):
        val = mean_s / scale
        if val >= 1.0 and abs(val - round(val)) < 1e-2:
            return int(round(val)) * unit
    return max(1, int(round(mean_s))) * u.s


class _NoCacheMpc(MPCClass):  # type: ignore[misc]
    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs["cache"] = False
        return cast(requests.Response, super()._request(method, url, **kwargs))


class MpcProvider(Provider):
    """IAU Minor Planet Center orbits, ephemerides, observations, and MPChecker provider."""

    name = "mpc"
    kind = {"ephemeris", "catalog"}

    def __init__(
        self,
        *,
        mpc: Any | None = None,
        session: requests.Session | None = None,
        http_client: httpx.Client | None = None,
        mpchecker_url: str = MPCHECKER_URL,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        max_retries: int = 3,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if mpc is not None:
            self._mpc: Any = mpc
        else:
            client = _NoCacheMpc()
            if session is not None:
                client._session = session
            self._mpc = client
        self._http = http_client or httpx.Client(timeout=30.0, follow_redirects=True)
        self._mpchecker_url = mpchecker_url
        self._rate_limiter = rate_limiter or RateLimiter(max_concurrent=1, rate_per_sec=1.0)
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        self._max_retries = max_retries
        self._sleeper = sleeper or time.sleep

    def describe_releases(self) -> list[ReleaseDescriptor]:
        """Return IAU Minor Planet Center service descriptor."""
        return [
            ReleaseDescriptor(
                release_id="mpc_web",
                provider=self.name,
                mission="IAU/MPC",
                collection="mpcorb",
                version_label="daily",
                release_date=date(2026, 1, 1),
                metadata={"mpchecker_url": self._mpchecker_url},
            )
        ]

    def lookup_object(
        self, target: str | int, *, target_type: str = "auto", **kwargs: Any
    ) -> dict[str, Any] | None:
        """Look up orbital elements for an asteroid or comet via `MPC.query_object`."""
        del kwargs
        target_str = str(target).strip()
        params = _split_object_query(target_str)
        types: tuple[str, ...]
        if target_type in ("asteroid", "comet"):
            types = (target_type,)
        elif target_type == "auto":
            types = (
                ("comet", "asteroid") if _COMET_ID_RE.match(target_str) else ("asteroid", "comet")
            )
        else:
            raise ProviderQueryError(f"Unsupported MPC target_type: {target_type!r}")

        for ttype in types:

            def _query_obj(tt: str = ttype) -> Any:
                return self._mpc.query_object(tt, **params)

            try:
                rows = self._run(_query_obj)
            except ValueError as exc:
                raise ProviderQueryError(str(exc)) from exc
            if rows:
                rec = dict(rows[0])
                rec["provider"] = self.name
                rec["target_type"] = ttype
                return rec
        return None

    def ephemeris(
        self,
        target: str | int,
        epochs: Time | Sequence[Time] | tuple[Time, Time, u.Quantity],
        location: str | Mapping[str, Any] = "500",
        *,
        proper_motion: str = "sky",
        perturbed: bool = True,
        **kwargs: Any,
    ) -> list[EphemerisPoint]:
        """Compute MPC Minor Planet Ephemeris Service sky positions and rates (`/hr`)."""
        del kwargs
        cleaned = str(target).strip()
        if not cleaned:
            raise ProviderQueryError("Target identifier must not be empty")
        loc: Any = (
            [location["lon"], location["lat"], location["elevation"]]
            if isinstance(location, Mapping)
            else location
        )
        loc_label = location if isinstance(location, str) else str(dict(location))

        chunks: list[tuple[Time, u.Quantity, int]] = []
        if isinstance(epochs, tuple) and len(epochs) == 3:
            t0, t1, step = epochs
            if t1 < t0:
                raise ProviderQueryError("Start epoch must be <= stop epoch")
            step_d = float(step.to_value(u.d))
            if step_d <= 0.0:
                raise ProviderQueryError("Ephemeris step must be > 0")
            n_pts = min(1441, max(1, int(math.floor((t1.utc.jd - t0.utc.jd) / step_d)) + 1))
            chunks.append((t0.utc, step, n_pts))
        elif isinstance(epochs, Time) and epochs.isscalar:
            chunks.append((epochs.utc, 1 * u.d, 1))
        else:
            seq = (
                [Time(t, scale=epochs.scale) for t in epochs]
                if isinstance(epochs, Time)
                else list(epochs)
            )
            if not seq:
                raise ProviderQueryError("At least one epoch is required")
            u_step = _uniform_step(seq)
            if u_step is not None and len(seq) <= 1441:
                chunks.append((seq[0].utc, u_step, len(seq)))
            else:
                chunks.extend((t.utc, 1 * u.d, 1) for t in seq)

        points: list[EphemerisPoint] = []
        for start_t, step_q, num in chunks:

            def _fetch(st: Time = start_t, sq: u.Quantity = step_q, n: int = num) -> Any:
                try:
                    return self._mpc.get_ephemeris(
                        cleaned,
                        location=loc,
                        start=st,
                        step=sq,
                        number=n,
                        proper_motion=proper_motion,
                        proper_motion_unit="arcsec/h",
                        perturbed=perturbed,
                        cache=False,
                    )
                except EmptyResponseError:
                    return None
                except ValueError as exc:
                    raise ProviderQueryError(str(exc)) from exc

            table = self._run(_fetch)
            if table is None:
                continue
            units = {c: getattr(table[c], "unit", None) for c in table.colnames}
            for row in table:
                date_val = row["Date"] if "Date" in table.colnames else None
                ra, dec = _col_num(row, units, "RA", u.deg), _col_num(row, units, "Dec", u.deg)
                if date_val is None or ra is None or dec is None:
                    continue
                t_obs = (
                    date_val if isinstance(date_val, Time) else Time(date_val, scale="utc")
                ).tdb
                vmag = _scalar(row, "V")
                points.append(
                    EphemerisPoint(
                        provider=self.name,
                        target=cleaned,
                        epoch=t_obs,
                        ra_deg=float(ra) % 360.0,
                        dec_deg=float(dec),
                        rate_ra_arcsec_hr=_col_num(
                            row, units, ("dRA cos(Dec)", "dRA"), u.arcsec / u.h
                        ),
                        rate_dec_arcsec_hr=_col_num(row, units, "dDec", u.arcsec / u.h),
                        mag_v=float(vmag) if vmag is not None else None,
                        delta_au=_col_num(row, units, "Delta", u.au),
                        r_helio_au=_col_num(row, units, "r", u.au),
                        solar_elong_deg=_col_num(row, units, "Elongation", u.deg),
                        observer_location=loc_label,
                        extra={"phase_deg": float(ph)}
                        if (ph := _scalar(row, "Phase")) is not None
                        else {},
                    )
                )
        return sorted(points, key=lambda p: p.mjd_tdb)

    def observations(
        self, target: str | int, *, id_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve published MPC astrometric observations for `target` (read-only)."""
        cleaned = str(target).strip()
        if not cleaned:
            raise ProviderQueryError("Target identifier must not be empty")

        def _fetch() -> Any:
            try:
                return self._mpc.get_observations(cleaned, id_type=id_type, cache=False)
            except EmptyResponseError:
                return None
            except ValueError as exc:
                raise ProviderQueryError(str(exc)) from exc

        table = self._run(_fetch)
        if table is None:
            return []
        units = {c: getattr(table[c], "unit", None) for c in table.colnames}
        obs: list[dict[str, Any]] = []
        for row in table:
            ep_jd = _scalar(row, "epoch")
            ra, dec = _col_num(row, units, "RA", u.deg), _col_num(row, units, "DEC", u.deg)
            if ep_jd is None or ra is None or dec is None:
                continue
            t = Time(float(ep_jd), format="jd", scale="utc")
            mag_val = _scalar(row, "mag")
            raw_obs = _scalar(row, "observatory")
            obs_str = (
                f"{raw_obs:03d}"
                if isinstance(raw_obs, int)
                else (str(raw_obs) if raw_obs is not None else None)
            )
            obs.append(
                {
                    "desig": _scalar(row, "desig") or cleaned,
                    "epoch": t.tdb,
                    "mjd_tdb": float(t.tdb.mjd),
                    "ra_deg": float(ra) % 360.0,
                    "dec_deg": float(dec),
                    "mag": float(mag_val) if mag_val is not None else None,
                    "band": _scalar(row, "band"),
                    "catalog": _scalar(row, "catalog"),
                    "observatory": obs_str,
                }
            )
        return sorted(obs, key=lambda r: r["mjd_tdb"])

    def cone(
        self,
        c: SkyCoord,
        radius: u.Quantity,
        epoch: Time | None = None,
        *,
        vmag_limit: float = 24.0,
        obs_code: str = "500",
    ) -> list[CatalogSource]:
        """Query MPChecker (`checkmp.cgi`) for known minor planets near `(c, radius)` at `epoch`."""
        r_arcmin = float(radius.to_value(u.arcmin))
        if r_arcmin <= 0.0:
            raise ProviderQueryError("Search radius must be > 0 arcmin")
        obs_t = (epoch or Time.now()).utc
        dt = obs_t.to_datetime()
        sec_with_us = dt.second + dt.microsecond / 1e6
        frac_day = dt.day + (dt.hour + (dt.minute + sec_with_us / 60.0) / 60.0) / 24.0

        icrs = c.icrs
        h, m, s = icrs.ra.hms
        sign, d, dm, ds = icrs.dec.signed_dms
        form = {
            "year": str(dt.year),
            "month": f"{dt.month:02d}",
            "day": f"{frac_day:05.2f}",
            "which": "pos",
            "ra": f"{int(h):02d} {int(m):02d} {s:05.2f}",
            "decl": f"{'-' if sign < 0 else '+'}{int(d):02d} {int(dm):02d} {ds:04.1f}",
            "TextArea": "",
            "radius": f"{r_arcmin:.2f}",
            "limit": f"{vmag_limit:.1f}",
            "oc": obs_code,
            "sort": "d",
            "mot": "h",
            "tmot": "s",
            "pdes": "u",
            "needed": "f",
            "ps": "n",
            "type": "p",
        }

        def _post() -> httpx.Response:
            resp = self._http.post(self._mpchecker_url, data=form)
            resp.raise_for_status()
            return resp

        resp = self._run(_post)
        epoch_tdb = obs_t.tdb
        sources: list[CatalogSource] = []

        if "application/json" in resp.headers.get("content-type", "").lower():
            items = resp.json()
            records = items if isinstance(items, list) else items.get("objects", [])
            for item in records:
                name = item.get("desig") or item.get("name")
                ra_deg = item.get("ra_deg")
                dec_deg = item.get("dec_deg")
                if not name or ra_deg is None or dec_deg is None:
                    continue
                sc = SkyCoord(float(ra_deg) * u.deg, float(dec_deg) * u.deg, frame="icrs")
                if float(sc.separation(icrs).to_value(u.arcmin)) > r_arcmin:
                    continue
                vmag = item.get("vmag")
                sources.append(
                    CatalogSource(
                        provider=self.name,
                        catalog="mpchecker",
                        source_id=str(name).strip(),
                        ra_deg=float(ra_deg) % 360.0,
                        dec_deg=float(dec_deg),
                        epoch=epoch_tdb,
                        mag=float(vmag) if vmag is not None else None,
                        mag_band="V" if vmag is not None else None,
                        mags={"V": float(vmag)} if vmag is not None else {},
                        object_type="sso",
                        extra={
                            k: v
                            for k, v in item.items()
                            if k not in {"desig", "name", "ra_deg", "dec_deg", "vmag"}
                        },
                    )
                )
        else:
            text = resp.text
            pre_match = re.search(r"<pre>(.*?)</pre>", text, flags=re.IGNORECASE | re.DOTALL)
            block = pre_match.group(1) if pre_match else text
            for line in block.splitlines():
                m_row = _CHECKMP_LINE_RE.match(line)
                if not m_row:
                    continue
                ra_deg = float(Angle(m_row.group("ra"), unit=u.hourangle).to_value(u.deg))
                dec_deg = float(Angle(m_row.group("dec"), unit=u.deg).to_value(u.deg))
                sc = SkyCoord(ra_deg * u.deg, dec_deg * u.deg, frame="icrs")
                if float(sc.separation(icrs).to_value(u.arcmin)) > r_arcmin:
                    continue
                vmag_f = float(m_row.group("mag"))
                sources.append(
                    CatalogSource(
                        provider=self.name,
                        catalog="mpchecker",
                        source_id=m_row.group("desig").strip(),
                        ra_deg=ra_deg % 360.0,
                        dec_deg=dec_deg,
                        epoch=epoch_tdb,
                        mag=vmag_f,
                        mag_band="V",
                        mags={"V": vmag_f},
                        object_type="sso",
                        extra={"raw_tail": m_row.group("rest").strip(), "obs_code": obs_code},
                    )
                )

        return sorted(sources, key=lambda s: float(s.skycoord.separation(icrs).to_value(u.arcsec)))


default_registry.register(MpcProvider.name, MpcProvider, overwrite=True)
