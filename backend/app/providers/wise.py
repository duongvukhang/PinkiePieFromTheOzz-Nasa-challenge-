"""WISE / AllWISE / CatWISE2020 / NEOWISE-R / unWISE IRSA catalog & image provider."""

from __future__ import annotations

import gzip
import io
import tarfile
import time
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any, Literal

import astropy.units as u
import httpx
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.nddata import Cutout2D
from astropy.table import Table
from astropy.time import Time
from astropy.wcs import WCS
from astroquery.ipac.irsa.core import IrsaClass
from numpy.typing import NDArray

from app.providers.base import (
    CatalogSource,
    CircuitBreaker,
    FrameRef,
    Provider,
    ProviderQueryError,
    ProviderUnavailableError,
    RateLimiter,
    ReleaseDescriptor,
    _col_num,
    _finalize_cone,
    _first_scalar,
    _parse_s_region,
    _pm_search_radius_deg,
    _scalar,
    _to_table,
    default_registry,
)

WiseCatalogName = Literal["catwise_2020", "allwise_p3as_psd", "neowiser_p1bs_psd"]

CATWISE_REF_EPOCH = Time(57170.0, format="mjd", scale="tdb")
ALLWISE_REF_EPOCH = Time(55400.0, format="mjd", scale="tdb")
NEOWISE_MAX_RADIUS_ARCSEC = 30.0
UNWISE_CUTOUT_URL = "https://unwise.me/cutout_fits"

_WISE_BAND_UM: dict[str, tuple[float, float]] = {
    "W1": (2.8, 3.8),
    "W2": (4.0, 5.3),
    "W3": (7.5, 16.5),
    "W4": (19.0, 28.0),
}

_CATALOG_SELECT: dict[WiseCatalogName, str] = {
    "catwise_2020": (
        "source_id, ra, dec, sigra, sigdec, pmra, sigpmra, pmdec, sigpmdec, "
        "w1mpro, w1sigmpro, w2mpro, w2sigmpro, ab_flags, cc_flags"
    ),
    "allwise_p3as_psd": (
        "designation, ra, dec, sigra, sigdec, pmra, sigpmra, pmdec, sigpmdec, "
        "w1mpro, w1sigmpro, w2mpro, w2sigmpro, w3mpro, w3sigmpro, w4mpro, w4sigmpro, "
        "ph_qual, cc_flags, ext_flg"
    ),
    "neowiser_p1bs_psd": (
        "source_id, scan_id, frame_num, mjd, ra, dec, sigra, sigdec, "
        "w1mpro, w1sigmpro, w2mpro, w2sigmpro, qual_frame, qi_fact, saa_sep, moon_masked"
    ),
}


def _extract_fits_from_payload(payload: bytes, source_url: str) -> bytes:
    """Unwrap raw FITS, `.fits.gz`, or unWISE `.tar.gz` into plain FITS bytes."""
    if payload.lstrip().startswith(b"SIMPLE"):
        return payload
    if payload[:2] == b"\x1f\x8b":
        try:
            decompressed = gzip.decompress(payload)
        except OSError as exc:
            raise ProviderUnavailableError(
                f"Corrupt gzip payload from {source_url}: {exc}"
            ) from exc
        if decompressed.lstrip().startswith(b"SIMPLE"):
            return decompressed
        try:
            with tarfile.open(fileobj=io.BytesIO(decompressed), mode="r:*") as tf:
                members = [
                    m
                    for m in tf.getmembers()
                    if m.isfile() and m.name.endswith((".fits", ".fits.gz"))
                ]
                # Prefer intensity image over invvar/n/std when multiple FITS stamps exist.
                members.sort(key=lambda m: (0 if "-img-" in m.name else 1, m.name))
                for member in members:
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        continue
                    data = extracted.read()
                    if data[:2] == b"\x1f\x8b":
                        data = gzip.decompress(data)
                    if data.lstrip().startswith(b"SIMPLE"):
                        return data
        except tarfile.TarError:
            pass
    raise ProviderUnavailableError(f"Expected FITS payload from {source_url}, got non-FITS content")


def _crop_2d_fits(raw_bytes: bytes, center: SkyCoord, size: u.Quantity) -> bytes:
    with fits.open(io.BytesIO(raw_bytes), memmap=False) as hdul:
        img_hdu = next(
            (h for h in hdul if getattr(h, "data", None) is not None and h.data.ndim == 2), None
        )
        if img_hdu is None or img_hdu.data is None:
            return raw_bytes
        wcs = WCS(img_hdu.header)
        if not wcs.has_celestial:
            return raw_bytes
        pix_deg = float(u.Quantity(wcs.proj_plane_pixel_scales()).to_value(u.deg).mean())
        req_px = float(size.to_value(u.deg)) / max(pix_deg, 1e-9)
        if max(img_hdu.data.shape) <= req_px * 1.25:
            return raw_bytes
        cut = Cutout2D(img_hdu.data, position=center.icrs, size=(size, size), wcs=wcs, mode="trim")
        hdr = img_hdu.header.copy()
        hdr.update(cut.wcs.to_header(relax=True))
        out = fits.PrimaryHDU(data=cut.data, header=hdr)
        buf = io.BytesIO()
        fits.HDUList([out]).writeto(buf, overwrite=True)
        return buf.getvalue()


class WiseProvider(Provider):
    """IRSA WISE/AllWISE/CatWISE2020/NEOWISE-R/unWISE catalog and image provider."""

    name = "wise"
    kind = {"images", "catalog"}

    def __init__(
        self,
        *,
        default_catalog: WiseCatalogName = "catwise_2020",
        default_collection: str = "wise_allwise_p3am_cdd",
        irsa: Any | None = None,
        http_client: httpx.Client | None = None,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        max_retries: int = 3,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.default_catalog: WiseCatalogName = default_catalog
        self.default_collection = default_collection
        if irsa is None:
            client = IrsaClass()
            client.sia_url = "https://irsa.ipac.caltech.edu/SIA"
            client.tap_url = "https://irsa.ipac.caltech.edu/TAP"
            self._irsa: Any = client
        else:
            self._irsa = irsa
        self._http = http_client or httpx.Client(timeout=30.0, follow_redirects=True)
        self._rate_limiter = rate_limiter or RateLimiter(max_concurrent=4, rate_per_sec=2.0)
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        self._max_retries = max_retries
        self._sleeper = sleeper or time.sleep

    def describe_releases(self) -> list[ReleaseDescriptor]:
        """Return static WISE/AllWISE/CatWISE2020/NEOWISE-R/unWISE release catalog descriptors."""
        return [
            ReleaseDescriptor(
                release_id="allwise",
                provider=self.name,
                mission="WISE",
                collection="allwise_p3as_psd",
                version_label="AllWISE",
                release_date=date(2013, 11, 13),
                metadata={"ref_mjd_tdb": 55400.0},
            ),
            ReleaseDescriptor(
                release_id="catwise_2020",
                provider=self.name,
                mission="WISE+NEOWISE",
                collection="catwise_2020",
                version_label="CatWISE2020",
                release_date=date(2020, 8, 20),
                metadata={"ref_mjd_tdb": 57170.0},
            ),
            ReleaseDescriptor(
                release_id="neowiser",
                provider=self.name,
                mission="NEOWISE-R",
                collection="neowiser_p1bs_psd",
                version_label="Final",
                release_date=date(2024, 11, 14),
                metadata={"max_cone_arcsec": NEOWISE_MAX_RADIUS_ARCSEC},
            ),
        ]

    def cone(
        self,
        c: SkyCoord,
        radius: u.Quantity,
        epoch: Time | None = None,
        *,
        catalog: WiseCatalogName | None = None,
        max_rows: int = 2000,
        observer_bary_au: Sequence[float] | str | None = None,
    ) -> list[CatalogSource]:
        """Query `catwise_2020`, `allwise_p3as_psd`, or `neowiser_p1bs_psd` around `c`."""
        cat: WiseCatalogName = catalog or self.default_catalog
        if cat not in _CATALOG_SELECT:
            raise ProviderQueryError(f"Unsupported WISE catalog table: {cat!r}")
        r_arcsec = float(radius.to_value(u.arcsec))
        r_deg = float(radius.to_value(u.deg))
        if r_deg <= 0.0:
            raise ProviderQueryError("Search radius must be > 0 deg")
        if max_rows < 1:
            raise ProviderQueryError("max_rows must be >= 1")
        if cat == "neowiser_p1bs_psd" and r_arcsec > NEOWISE_MAX_RADIUS_ARCSEC:
            raise ProviderQueryError(
                f'NEOWISE single-exposure cone radius {r_arcsec:.1f}" exceeds 30" cap'
            )

        icrs = c.icrs
        ref_epoch = CATWISE_REF_EPOCH if cat == "catwise_2020" else ALLWISE_REF_EPOCH
        search_deg = (
            _pm_search_radius_deg(r_deg, epoch, ref_epoch)
            if cat in ("catwise_2020", "allwise_p3as_psd")
            else r_deg
        )

        adql = (
            f"SELECT TOP {max_rows} {_CATALOG_SELECT[cat]} FROM {cat} WHERE "
            f"1 = CONTAINS(POINT('ICRS', ra, dec), "
            f"CIRCLE('ICRS', {icrs.ra.deg:.8f}, {icrs.dec.deg:.8f}, {search_deg:.8f}))"
        )
        table = _to_table(self._run(lambda: self._irsa.query_tap(adql)))
        units = {col: getattr(table[col], "unit", None) for col in table.colnames}

        # CatWISE2020 pm is arcsec/yr; AllWISE pm is mas/yr; sigra/sigdec are arcsec.
        default_pm_unit = u.arcsec / u.yr if cat == "catwise_2020" else u.mas / u.yr

        sources: list[CatalogSource] = []
        for row in table:
            ra = _col_num(row, units, "ra", u.deg)
            dec = _col_num(row, units, "dec", u.deg)
            sid = _first_scalar(row, "source_id", "designation")
            if ra is None or dec is None or sid is None:
                continue

            if cat == "neowiser_p1bs_psd":
                mjd_val = _scalar(row, "mjd")
                if mjd_val is None:
                    continue
                row_epoch = Time(float(mjd_val), format="mjd", scale="utc").tdb
                scan_id, frame_num = _scalar(row, "scan_id"), _scalar(row, "frame_num")
                src_id = f"{sid}:{scan_id}:{frame_num}" if scan_id is not None else str(sid)
            else:
                row_epoch, src_id = ref_epoch, str(sid)

            bands_meta = (
                ("W1", "w1mpro", "w1sigmpro"),
                ("W2", "w2mpro", "w2sigmpro"),
                ("W3", "w3mpro", "w3sigmpro"),
                ("W4", "w4mpro", "w4sigmpro"),
            )
            mags = {b: float(v) for b, col, _ in bands_meta if (v := _scalar(row, col)) is not None}
            mag_errs = {
                b: float(v) for b, _, ecol in bands_meta if (v := _scalar(row, ecol)) is not None
            }
            primary_band = (
                "W2" if "W2" in mags and "W1" not in mags else ("W1" if "W1" in mags else None)
            )
            extra = {
                k: v
                for k in (
                    "ab_flags",
                    "cc_flags",
                    "ph_qual",
                    "ext_flg",
                    "qual_frame",
                    "qi_fact",
                    "saa_sep",
                    "moon_masked",
                )
                if (v := _scalar(row, k)) is not None
            }

            sources.append(
                CatalogSource(
                    provider=self.name,
                    catalog=cat,
                    source_id=src_id,
                    ra_deg=float(ra) % 360.0,
                    dec_deg=float(dec),
                    epoch=row_epoch,
                    ra_err_mas=_col_num(row, units, "sigra", u.mas, u.arcsec),
                    dec_err_mas=_col_num(row, units, "sigdec", u.mas, u.arcsec),
                    pmra_masyr=_col_num(row, units, "pmra", u.mas / u.yr, default_pm_unit),
                    pmra_err_masyr=_col_num(row, units, "sigpmra", u.mas / u.yr, default_pm_unit),
                    pmdec_masyr=_col_num(row, units, "pmdec", u.mas / u.yr, default_pm_unit),
                    pmdec_err_masyr=_col_num(row, units, "sigpmdec", u.mas / u.yr, default_pm_unit),
                    mag=mags[primary_band] if primary_band else None,
                    mag_band=primary_band,
                    mags=mags,
                    mag_errs=mag_errs,
                    extra=extra,
                )
            )

        if cat == "neowiser_p1bs_psd":
            return sorted(sources, key=lambda s: s.mjd_tdb)
        return _finalize_cone(sources, icrs, r_deg, epoch, observer_bary_au)

    def search_frames(
        self,
        c: SkyCoord,
        radius: u.Quantity,
        t0: Time | None = None,
        t1: Time | None = None,
        bands: list[str] | None = None,
        release: str | None = None,
    ) -> list[FrameRef]:
        """Search IRSA WISE SIAv2 frames overlapping `(c, radius)` and optional `[t0, t1]`."""
        if float(radius.to_value(u.deg)) <= 0.0:
            raise ProviderQueryError("Search radius must be > 0 deg")
        if t0 is not None and t1 is not None and t0 > t1:
            raise ProviderQueryError("Start time t0 must be <= end time t1")

        collection = release or self.default_collection
        band_filter = {b.strip().upper() for b in (bands or []) if b.strip() and b.lower() != "all"}
        time_param = (
            (
                t0 or Time("2009-12-01T00:00:00", scale="utc"),
                t1 or Time("2025-12-31T00:00:00", scale="utc"),
            )
            if (t0 is not None or t1 is not None)
            else None
        )
        table: Table = _to_table(
            self._run(
                lambda: self._irsa.query_sia(
                    pos=(c.icrs, radius), time=time_param, collection=collection
                )
            )
        )
        frames: list[FrameRef] = []
        for row in table:
            obs_id = _first_scalar(row, "obs_id", "obs_publisher_did")
            access_url = _scalar(row, "access_url")
            if not obs_id or not access_url:
                continue
            t_min, t_max = _scalar(row, "t_min"), _scalar(row, "t_max")
            t_mid_raw = _first_scalar(row, "t_mid", "mjd_obs")
            if t_min is not None and t_max is not None:
                mjd_mid = 0.5 * (float(t_min) + float(t_max))
            elif t_mid_raw is not None:
                mjd_mid = float(t_mid_raw)
            else:
                continue
            t_mid = Time(mjd_mid, format="mjd", scale="utc").tdb
            if (t0 is not None and t_mid.tdb.mjd < t0.tdb.mjd) or (
                t1 is not None and t_mid.tdb.mjd > t1.tdb.mjd
            ):
                continue

            band_raw = _first_scalar(row, "band_id", "energy_bandpassname", "filter")
            band_id = str(band_raw).upper() if band_raw is not None else None
            if band_filter and (band_id is None or band_id not in band_filter):
                continue
            ra_deg, dec_deg, fov_deg = (
                _scalar(row, "s_ra"),
                _scalar(row, "s_dec"),
                _scalar(row, "s_fov"),
            )
            exptime = _scalar(row, "t_exptime")
            frames.append(
                FrameRef(
                    provider=self.name,
                    release=str(_scalar(row, "obs_collection") or collection),
                    obs_id=str(obs_id),
                    access_url=str(access_url),
                    t_mid=t_mid,
                    band_id=band_id,
                    wavelength_um=_WISE_BAND_UM.get(band_id) if band_id else None,
                    footprint=_parse_s_region(
                        _scalar(row, "s_region"),
                        float(ra_deg) if ra_deg is not None else None,
                        float(dec_deg) if dec_deg is not None else None,
                        float(fov_deg) if fov_deg is not None else None,
                    ),
                    ra_deg=float(ra_deg) if ra_deg is not None else None,
                    dec_deg=float(dec_deg) if dec_deg is not None else None,
                    exptime_s=float(exptime) if exptime is not None else None,
                )
            )
        return sorted(frames, key=lambda f: (f.mjd_tdb, f.band_id or "", f.obs_id))

    def fetch(self, ref: FrameRef, cutout: tuple[SkyCoord, u.Quantity] | None = None) -> bytes:
        """Download WISE FITS/cutout bytes (decompressing gzipped payloads cleanly)."""
        if not ref.access_url or not ref.access_url.startswith(("http://", "https://")):
            raise ProviderQueryError(f"Invalid access_url for {ref.obs_id}: {ref.access_url!r}")

        def _download() -> bytes:
            resp = self._http.get(ref.access_url)
            resp.raise_for_status()
            return resp.content

        fits_bytes = _extract_fits_from_payload(self._run(_download), ref.access_url)
        if cutout is not None:
            if float(cutout[1].to_value(u.deg)) <= 0.0:
                raise ProviderQueryError("Cutout size must be > 0")
            return _crop_2d_fits(fits_bytes, cutout[0], cutout[1])
        return fits_bytes

    def unwise_cutout(
        self,
        c: SkyCoord,
        size_pix: int = 100,
        *,
        bands: str = "12",
        version: str = "neo7",
    ) -> bytes:
        """Fetch a FITS postage stamp from `unwise.me/cutout_fits` (<= 1024 px)."""
        if not 1 <= size_pix <= 1024:
            raise ProviderQueryError("unWISE cutout size_pix must be within [1, 1024]")
        icrs = c.icrs
        params = {
            "version": version,
            "ra": f"{icrs.ra.deg:.7f}",
            "dec": f"{icrs.dec.deg:.7f}",
            "size": str(size_pix),
            "bands": bands,
            "file_img_m": "on",
        }

        def _download() -> bytes:
            resp = self._http.get(UNWISE_CUTOUT_URL, params=params)
            resp.raise_for_status()
            return resp.content

        return _extract_fits_from_payload(self._run(_download), UNWISE_CUTOUT_URL)

    def quality_mask(self, hdul: fits.HDUList) -> NDArray[np.bool_]:
        """Return boolean good-pixel mask from WISE image finite check + optional mask/unc HDUs."""
        img_hdu = next(
            (h for h in hdul if getattr(h, "data", None) is not None and h.data.ndim == 2), None
        )
        if img_hdu is None or img_hdu.data is None:
            raise ProviderQueryError("WISE FITS HDUList has no 2D image HDU")
        good = np.isfinite(np.asarray(img_hdu.data))
        for name in ("MASK", "FLAGS", "DQ"):
            if name in hdul and getattr(hdul[name], "data", None) is not None:
                arr = np.asarray(hdul[name].data)
                if arr.shape == good.shape:
                    good &= arr.astype(np.int64) == 0
        for name in ("UNC", "VARIANCE", "IVAR"):
            if name in hdul and getattr(hdul[name], "data", None) is not None:
                arr = np.asarray(hdul[name].data)
                if arr.shape == good.shape:
                    good &= np.isfinite(arr) & (arr > 0)
        return np.asarray(good, dtype=bool)


default_registry.register(WiseProvider.name, WiseProvider, overwrite=True)
