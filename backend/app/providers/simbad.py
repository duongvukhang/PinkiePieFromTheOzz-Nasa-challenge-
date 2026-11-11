"""SIMBAD TAP provider for identifier resolution, OTYPEs, RVs, PMs, and optical/IR fluxes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time
from astroquery.simbad.core import SimbadClass

from app.providers.base import (
    CatalogSource,
    CircuitBreaker,
    Provider,
    ProviderQueryError,
    RateLimiter,
    ReleaseDescriptor,
    _col_num,
    _finalize_cone,
    _first_scalar,
    _pm_search_radius_deg,
    _scalar,
    _to_table,
    default_registry,
    propagate_catalog_sources,
)

SIMBAD_REF_EPOCH = Time(2000.0, format="jyear", scale="tt")

_SIMBAD_COLS = (
    "basic.oid, basic.main_id, basic.ra, basic.dec, "
    "basic.coo_err_maj, basic.coo_err_min, basic.coo_err_angle, basic.coo_bibcode, "
    "basic.pmra, basic.pmdec, basic.pm_err_maj, basic.pm_err_min, "
    "basic.plx_value, basic.plx_err, basic.rvz_radvel, basic.rvz_err, "
    "basic.otype, basic.otype_txt, basic.sp_type, "
    "allfluxes.U, allfluxes.B, allfluxes.V, allfluxes.I, "
    "allfluxes.J, allfluxes.H, allfluxes.K, "
    "allfluxes.u_ AS flux_u, allfluxes.g_ AS flux_g, "
    "allfluxes.r_ AS flux_r, allfluxes.i_ AS flux_i, allfluxes.z_ AS flux_z"
)

_FLUX_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    *((b, (b,)) for b in ("U", "B", "V", "I", "J", "H", "K")),
    *((b, (f"flux_{b}", b, f"{b}_")) for b in ("u", "g", "r", "i", "z")),
)


class SimbadProvider(Provider):
    """CDS SIMBAD TAP catalog provider (rate-limited <= 4 req/s per CDS policy)."""

    name = "simbad"
    kind = {"catalog"}

    def __init__(
        self,
        *,
        simbad: Any | None = None,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        max_retries: int = 3,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._simbad: Any = simbad or SimbadClass()
        self._rate_limiter = rate_limiter or RateLimiter(max_concurrent=2, rate_per_sec=4.0)
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        self._max_retries = max_retries
        self._sleeper = sleeper

    def _query_tap(self, adql: str, *, maxrec: int) -> Any:
        return _to_table(self._run(lambda: self._simbad.tap.search(adql, maxrec=maxrec)))

    def describe_releases(self) -> list[ReleaseDescriptor]:
        """Return CDS SIMBAD service release descriptor."""
        return [
            ReleaseDescriptor(
                release_id="simbad_tap",
                provider=self.name,
                mission="CDS",
                collection="simbad",
                version_label="continuous",
                release_date=date(2026, 1, 1),
                metadata={"server": getattr(self._simbad, "server", "simbad.cds.unistra.fr")},
            )
        ]

    def _row_to_source(self, row: Any, units: dict[str, Any]) -> CatalogSource | None:
        main_id = _scalar(row, "main_id")
        ra = _col_num(row, units, "ra", u.deg)
        dec = _col_num(row, units, "dec", u.deg)
        if not main_id or ra is None or dec is None:
            return None

        mags = {
            label: float(val)
            for label, cols in _FLUX_KEYS
            if (val := _first_scalar(row, *cols)) is not None
        }
        primary_band = "V" if "V" in mags else ("K" if "K" in mags else next(iter(mags), None))
        ra_err = _col_num(row, units, "coo_err_maj", u.mas)
        pm_err_maj = _col_num(row, units, "pm_err_maj", u.mas / u.yr)

        extra: dict[str, Any] = {
            k: v
            for k in ("oid", "otype_txt", "sp_type", "coo_bibcode", "coo_err_angle", "rvz_err")
            if (v := _scalar(row, k)) is not None
        }
        if (ids_raw := _scalar(row, "ids")) is not None:
            extra["aliases"] = [tok.strip() for tok in str(ids_raw).split("|") if tok.strip()]

        return CatalogSource(
            provider=self.name,
            catalog="simbad",
            source_id=str(main_id).strip(),
            ra_deg=float(ra) % 360.0,
            dec_deg=float(dec),
            epoch=SIMBAD_REF_EPOCH,
            ra_err_mas=ra_err,
            dec_err_mas=_col_num(row, units, "coo_err_min", u.mas) or ra_err,
            pmra_masyr=_col_num(row, units, "pmra", u.mas / u.yr),
            pmra_err_masyr=pm_err_maj,
            pmdec_masyr=_col_num(row, units, "pmdec", u.mas / u.yr),
            pmdec_err_masyr=_col_num(row, units, "pm_err_min", u.mas / u.yr) or pm_err_maj,
            parallax_mas=_col_num(row, units, "plx_value", u.mas),
            parallax_err_mas=_col_num(row, units, "plx_err", u.mas),
            radial_velocity_kms=_col_num(row, units, "rvz_radvel", u.km / u.s),
            mag=mags[primary_band] if primary_band else None,
            mag_band=primary_band,
            mags=mags,
            object_type=str(ot).strip() if (ot := _scalar(row, "otype")) else None,
            extra=extra,
        )

    def cone(
        self,
        c: SkyCoord,
        radius: u.Quantity,
        epoch: Time | None = None,
        *,
        max_rows: int = 1000,
        observer_bary_au: Sequence[float] | str | None = None,
        pm_pad_arcsec: float | None = None,
    ) -> list[CatalogSource]:
        """Query SIMBAD `basic` + `allfluxes` within `radius` of `c`."""
        r_deg = float(radius.to_value(u.deg))
        if r_deg <= 0.0:
            raise ProviderQueryError("Search radius must be > 0 deg")
        if max_rows < 1:
            raise ProviderQueryError("max_rows must be >= 1")

        icrs = c.icrs
        search_deg = _pm_search_radius_deg(r_deg, epoch, SIMBAD_REF_EPOCH, pm_pad_arcsec)
        adql = (
            f"SELECT TOP {max_rows} {_SIMBAD_COLS} "
            "FROM basic LEFT JOIN allfluxes ON basic.oid = allfluxes.oidref "
            f"WHERE 1 = CONTAINS(POINT('ICRS', basic.ra, basic.dec), "
            f"CIRCLE('ICRS', {icrs.ra.deg:.8f}, {icrs.dec.deg:.8f}, {search_deg:.8f}))"
        )
        table = self._query_tap(adql, maxrec=max_rows)
        units = {col: getattr(table[col], "unit", None) for col in table.colnames}
        sources = [src for row in table if (src := self._row_to_source(row, units)) is not None]
        return _finalize_cone(sources, icrs, r_deg, epoch, observer_bary_au)

    def lookup_object(
        self,
        target: str,
        *,
        epoch: Time | None = None,
        observer_bary_au: Sequence[float] | str | None = None,
        **kwargs: Any,
    ) -> CatalogSource | None:
        """Resolve an astronomical identifier or alias via SIMBAD `ident` + `basic` + `ids`."""
        del kwargs
        cleaned = target.strip()
        if not cleaned:
            raise ProviderQueryError("Target identifier must not be empty")
        escaped = cleaned.replace("'", "''")
        adql = (
            f"SELECT TOP 5 {_SIMBAD_COLS}, ids.ids "
            "FROM ident JOIN basic ON basic.oid = ident.oidref "
            "LEFT JOIN allfluxes ON basic.oid = allfluxes.oidref "
            "LEFT JOIN ids ON basic.oid = ids.oidref "
            f"WHERE ident.id = '{escaped}'"
        )
        table = self._query_tap(adql, maxrec=5)
        units = {col: getattr(table[col], "unit", None) for col in table.colnames}
        rows = [src for row in table if (src := self._row_to_source(row, units)) is not None]
        if not rows:
            return None
        return (
            propagate_catalog_sources(rows[:1], epoch, observer_bary_au=observer_bary_au)[0]
            if epoch is not None
            else rows[0]
        )


default_registry.register(SimbadProvider.name, SimbadProvider, overwrite=True)
