"""Gaia DR3/CRF3 astrometric catalog provider with mirror fallback and epoch propagation."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import astropy.units as u
import requests
from astropy.coordinates import SkyCoord
from astropy.time import Time
from pyvo.dal import TAPService

from app.providers.base import (
    CatalogSource,
    CircuitBreaker,
    Provider,
    ProviderError,
    ProviderQueryError,
    RateLimiter,
    ReleaseDescriptor,
    _col_num,
    _finalize_cone,
    _first_scalar,
    _pm_search_radius_deg,
    _to_table,
    default_registry,
    propagate_catalog_sources,
)

GAIA_REF_EPOCH = Time(2016.0, format="jyear", scale="tcb")
_GAIA_ID_RE = re.compile(r"(?:gaia\s*(?:dr3)?\s*)?(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class GaiaMirror:
    """TAP mirror endpoint and schema mapping for Gaia DR3."""

    name: str
    tap_url: str
    table: str
    vizier_schema: bool = False


DEFAULT_GAIA_MIRRORS: tuple[GaiaMirror, ...] = (
    GaiaMirror("esa", "https://gea.esac.esa.int/tap-server/tap", "gaiadr3.gaia_source"),
    GaiaMirror("ari", "https://gaia.ari.uni-heidelberg.de/tap", "gaiadr3.gaia_source"),
    GaiaMirror(
        "vizier",
        "https://tapvizier.cds.unistra.fr/TAPVizieR/tap",
        '"I/355/gaiadr3"',
        vizier_schema=True,
    ),
)

_ESA_SELECT = (
    "source_id, ra, dec, ra_error, dec_error, pmra, pmra_error, pmdec, pmdec_error, "
    "parallax, parallax_error, radial_velocity, phot_g_mean_mag, phot_bp_mean_mag, "
    "phot_rp_mean_mag, ruwe, astrometric_params_solved, duplicated_source"
)

_VIZIER_SELECT = (
    "Source AS source_id, RA_ICRS AS ra, DE_ICRS AS dec, "
    "e_RA_ICRS AS ra_error, e_DE_ICRS AS dec_error, "
    "pmRA AS pmra, e_pmRA AS pmra_error, pmDE AS pmdec, e_pmDE AS pmdec_error, "
    "Plx AS parallax, e_Plx AS parallax_error, RV AS radial_velocity, "
    "Gmag AS phot_g_mean_mag, BPmag AS phot_bp_mean_mag, RPmag AS phot_rp_mean_mag, "
    "RUWE AS ruwe, Solved AS astrometric_params_solved, Dup AS duplicated_source"
)


def _normalize_mirrors(
    mirrors: Sequence[GaiaMirror | str | Mapping[str, Any]] | None,
) -> tuple[GaiaMirror, ...]:
    if mirrors is None:
        return DEFAULT_GAIA_MIRRORS
    by_name = {m.name: m for m in DEFAULT_GAIA_MIRRORS}
    resolved: list[GaiaMirror] = []
    for item in mirrors:
        if isinstance(item, GaiaMirror):
            resolved.append(item)
        elif isinstance(item, str):
            key = item.lower().strip()
            if key in by_name:
                resolved.append(by_name[key])
            else:
                resolved.append(GaiaMirror(key, item, "gaiadr3.gaia_source"))
        else:
            resolved.append(
                GaiaMirror(
                    name=str(item["name"]),
                    tap_url=str(item["tap_url"]),
                    table=str(item.get("table", "gaiadr3.gaia_source")),
                    vizier_schema=bool(item.get("vizier_schema", False)),
                )
            )
    if not resolved:
        raise ProviderQueryError("At least one Gaia mirror must be configured")
    return tuple(resolved)


def _parse_bool_flag(val: Any) -> bool | None:
    if val is None:
        return None
    if isinstance(val, (bool, int)):
        return bool(val)
    text = str(val).strip().lower()
    if text in {"1", "true", "t", "yes"}:
        return True
    if text in {"0", "false", "f", "no", ""}:
        return False
    return None


class GaiaProvider(Provider):
    """Gaia DR3 TAP catalog provider with mirror fallback and proper-motion propagation."""

    name = "gaia"
    kind = {"catalog"}

    def __init__(
        self,
        *,
        mirrors: Sequence[GaiaMirror | str | Mapping[str, Any]] | None = None,
        tap_services: Mapping[str, Any] | None = None,
        session: requests.Session | None = None,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        max_retries: int = 3,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._mirrors = _normalize_mirrors(mirrors)
        self._session = session or requests.Session()
        self._tap_services: dict[str, Any] = dict(tap_services or {})
        self._rate_limiter = rate_limiter or RateLimiter(max_concurrent=2, rate_per_sec=2.0)
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        self._max_retries = max_retries
        self._sleeper = sleeper

    def _service(self, mirror: GaiaMirror) -> Any:
        if mirror.name not in self._tap_services:
            self._tap_services[mirror.name] = TAPService(mirror.tap_url, session=self._session)
        return self._tap_services[mirror.name]

    def describe_releases(self) -> list[ReleaseDescriptor]:
        """Return configured Gaia DR3 astrometric release descriptor."""
        return [
            ReleaseDescriptor(
                release_id="gaiadr3",
                provider=self.name,
                mission="Gaia",
                collection="gaiadr3.gaia_source",
                version_label="DR3",
                release_date=date(2022, 6, 13),
                metadata={
                    "ref_epoch": "J2016.0",
                    "mirrors": [m.name for m in self._mirrors],
                },
            )
        ]

    def _query_tap_with_fallback(
        self, build_adql: Callable[[GaiaMirror], str], maxrec: int
    ) -> tuple[Any, GaiaMirror]:
        last_err: ProviderError | None = None
        for mirror in self._mirrors:
            adql, svc = build_adql(mirror), self._service(mirror)

            def _search(s: Any = svc, q: str = adql) -> Any:
                return s.search(q, maxrec=maxrec)

            try:
                return _to_table(self._run(_search)), mirror
            except ProviderQueryError:
                raise
            except ProviderError as exc:
                last_err = exc
        assert last_err is not None
        raise last_err

    def _parse_rows(
        self,
        table: Any,
        mirror: GaiaMirror,
        *,
        astrometric_only: bool,
        ruwe_max: float,
        min_solved_params: int,
    ) -> list[CatalogSource]:
        units = {c: getattr(table[c], "unit", None) for c in table.colnames}
        sources: list[CatalogSource] = []
        for row in table:
            sid = _first_scalar(row, "source_id", "Source")
            ra = _col_num(row, units, ("ra", "RA_ICRS"), u.deg)
            dec = _col_num(row, units, ("dec", "DE_ICRS"), u.deg)
            if sid is None or ra is None or dec is None:
                continue

            ruwe = _first_scalar(row, "ruwe", "RUWE")
            solved = _first_scalar(row, "astrometric_params_solved", "Solved")
            dup = _parse_bool_flag(_first_scalar(row, "duplicated_source", "Dup"))
            if astrometric_only and any(
                [
                    ruwe is not None and float(ruwe) >= ruwe_max,
                    solved is not None and int(solved) < min_solved_params,
                    dup is True,
                ]
            ):
                continue

            g_mag = _first_scalar(row, "phot_g_mean_mag", "Gmag")
            bp_mag = _first_scalar(row, "phot_bp_mean_mag", "BPmag")
            rp_mag = _first_scalar(row, "phot_rp_mean_mag", "RPmag")
            mags = {
                k: float(v)
                for k, v in (("G", g_mag), ("BP", bp_mag), ("RP", rp_mag))
                if v is not None
            }
            extra: dict[str, Any] = {"mirror": mirror.name, "ref_epoch": "J2016.0"}
            if ruwe is not None:
                extra["ruwe"] = float(ruwe)
            if solved is not None:
                extra["astrometric_params_solved"] = int(solved)
            if dup is not None:
                extra["duplicated_source"] = dup
            if bp_mag is not None and rp_mag is not None:
                extra["bp_rp"] = float(bp_mag) - float(rp_mag)

            sources.append(
                CatalogSource(
                    provider=self.name,
                    catalog="gaiadr3",
                    source_id=str(sid),
                    ra_deg=float(ra) % 360.0,
                    dec_deg=float(dec),
                    epoch=GAIA_REF_EPOCH,
                    ra_err_mas=_col_num(row, units, ("ra_error", "e_RA_ICRS"), u.mas),
                    dec_err_mas=_col_num(row, units, ("dec_error", "e_DE_ICRS"), u.mas),
                    pmra_masyr=_col_num(row, units, ("pmra", "pmRA"), u.mas / u.yr),
                    pmra_err_masyr=_col_num(row, units, ("pmra_error", "e_pmRA"), u.mas / u.yr),
                    pmdec_masyr=_col_num(row, units, ("pmdec", "pmDE"), u.mas / u.yr),
                    pmdec_err_masyr=_col_num(row, units, ("pmdec_error", "e_pmDE"), u.mas / u.yr),
                    parallax_mas=_col_num(row, units, ("parallax", "Plx"), u.mas),
                    parallax_err_mas=_col_num(row, units, ("parallax_error", "e_Plx"), u.mas),
                    radial_velocity_kms=_col_num(row, units, ("radial_velocity", "RV"), u.km / u.s),
                    mag=float(g_mag) if g_mag is not None else None,
                    mag_band="G" if g_mag is not None else None,
                    mags=mags,
                    object_type="star",
                    extra=extra,
                )
            )
        return sources

    def cone(
        self,
        c: SkyCoord,
        radius: u.Quantity,
        epoch: Time | None = None,
        *,
        astrometric_only: bool = False,
        max_rows: int = 5000,
        observer_bary_au: Sequence[float] | str | None = None,
        pm_pad_arcsec: float | None = None,
        ruwe_max: float = 1.4,
        min_solved_params: int = 31,
    ) -> list[CatalogSource]:
        """Query Gaia DR3 around `c` within `radius`, optionally propagating to `epoch`."""
        r_deg = float(radius.to_value(u.deg))
        if r_deg <= 0.0:
            raise ProviderQueryError("Search radius must be > 0 deg")
        if max_rows < 1:
            raise ProviderQueryError("max_rows must be >= 1")

        icrs = c.icrs
        search_deg = _pm_search_radius_deg(r_deg, epoch, GAIA_REF_EPOCH, pm_pad_arcsec)

        def _build(m: GaiaMirror) -> str:
            cols = _VIZIER_SELECT if m.vizier_schema else _ESA_SELECT
            ra_c, dec_c = ("RA_ICRS", "DE_ICRS") if m.vizier_schema else ("ra", "dec")
            where = [
                f"1 = CONTAINS(POINT('ICRS', {ra_c}, {dec_c}), "
                f"CIRCLE('ICRS', {icrs.ra.deg:.8f}, {icrs.dec.deg:.8f}, {search_deg:.8f}))"
            ]
            if astrometric_only:
                if m.vizier_schema:
                    where += [
                        f"RUWE < {ruwe_max}",
                        f"Solved >= {min_solved_params}",
                        "(Dup = 0 OR Dup IS NULL)",
                    ]
                else:
                    where += [
                        f"ruwe < {ruwe_max}",
                        f"astrometric_params_solved >= {min_solved_params}",
                        "(duplicated_source = 'false' OR duplicated_source IS NULL)",
                    ]
            return f"SELECT TOP {max_rows} {cols} FROM {m.table} WHERE " + " AND ".join(where)

        table, mirror = self._query_tap_with_fallback(_build, maxrec=max_rows)
        sources = self._parse_rows(
            table,
            mirror,
            astrometric_only=astrometric_only,
            ruwe_max=ruwe_max,
            min_solved_params=min_solved_params,
        )
        return _finalize_cone(sources, icrs, r_deg, epoch, observer_bary_au)

    def lookup_object(
        self,
        target: str,
        *,
        epoch: Time | None = None,
        observer_bary_au: Sequence[float] | str | None = None,
        **kwargs: Any,
    ) -> CatalogSource | None:
        """Look up a single Gaia DR3 source by integer `source_id` (optionally propagated)."""
        del kwargs
        match = _GAIA_ID_RE.fullmatch(target.strip())
        if not match:
            raise ProviderQueryError(f"Invalid Gaia DR3 source identifier: {target!r}")
        source_id = int(match.group(1))

        def _build(mirror: GaiaMirror) -> str:
            if mirror.vizier_schema:
                return f"SELECT {_VIZIER_SELECT} FROM {mirror.table} WHERE Source = {source_id}"
            return f"SELECT {_ESA_SELECT} FROM {mirror.table} WHERE source_id = {source_id}"

        table, mirror = self._query_tap_with_fallback(_build, maxrec=5)
        rows = self._parse_rows(
            table, mirror, astrometric_only=False, ruwe_max=99.0, min_solved_params=0
        )
        if not rows:
            return None
        return (
            propagate_catalog_sources(rows[:1], epoch, observer_bary_au=observer_bary_au)[0]
            if epoch is not None
            else rows[0]
        )


default_registry.register(GaiaProvider.name, GaiaProvider, overwrite=True)
