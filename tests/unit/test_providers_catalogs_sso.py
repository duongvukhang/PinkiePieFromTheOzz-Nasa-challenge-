"""Offline network-mocked unit tests for Gaia, WISE, SIMBAD, Horizons, and MPC providers."""

from __future__ import annotations

import gzip
import io
import json
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

import astropy.units as u
import httpx
import numpy as np
import pytest
import requests
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.io.votable import from_table, writeto
from astropy.table import Column, Table
from astropy.time import Time
from astropy.wcs import WCS
from astroquery.ipac.irsa.core import IrsaClass
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from app.providers import (
    GaiaMirror,
    GaiaProvider,
    HorizonsProvider,
    MpcProvider,
    ProviderQueryError,
    ProviderUnavailableError,
    RateLimiter,
    SimbadProvider,
    WiseProvider,
    get_provider,
    list_providers,
)

BARNARD_J2016 = SkyCoord(269.45207696 * u.deg, 4.69336497 * u.deg, frame="icrs")
WISE_0855 = SkyCoord("08h55m10.831s", "-07d14m42.53s", frame="icrs")
RW_AUR = SkyCoord(76.9565 * u.deg, 30.4014 * u.deg, frame="icrs")
EROS_POS = SkyCoord(133.795 * u.deg, -7.245 * u.deg, frame="icrs")


class _FakeRaw(io.BytesIO):
    def read(self, size: int | None = -1, decode_content: bool = False) -> bytes:
        del decode_content
        return super().read(-1 if size is None else size)


class _RecordedAdapter(BaseAdapter):
    """Offline requests adapter capturing requests and serving custom responses."""

    def __init__(
        self, handler: Callable[[requests.PreparedRequest], tuple[int, dict[str, str], bytes]]
    ) -> None:
        super().__init__()
        self.handler = handler
        self.requests_seen: list[requests.PreparedRequest] = []

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: Any = None,
        verify: Any = True,
        cert: Any = None,
        proxies: Any = None,
    ) -> requests.Response:
        del stream, timeout, verify, cert, proxies
        self.requests_seen.append(request)
        status, headers, body = self.handler(request)
        resp = requests.Response()
        resp.request = request
        resp.status_code = status
        resp.url = request.url or ""
        resp.headers = CaseInsensitiveDict(headers)
        resp._content = body
        resp.raw = _FakeRaw(body)
        return resp

    def close(self) -> None:
        return None


def _session_with(
    handler: Callable[[requests.PreparedRequest], tuple[int, dict[str, str], bytes]],
) -> tuple[requests.Session, _RecordedAdapter]:
    session = requests.Session()
    adapter = _RecordedAdapter(handler)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session, adapter


def _table_to_votable_bytes(table: Table) -> bytes:
    buf = io.BytesIO()
    writeto(from_table(table), buf)
    return buf.getvalue()


def _fast_limiter(max_concurrent: int = 2) -> RateLimiter:
    return RateLimiter(max_concurrent=max_concurrent, rate_per_sec=1000.0, sleeper=lambda _: None)


def _tiny_fits_bytes(ra_deg: float = 133.795, dec_deg: float = -7.245) -> bytes:
    w = WCS(naxis=2)
    w.wcs.crpix = [16.5, 16.5]
    w.wcs.cdelt = [-2.75 / 3600.0, 2.75 / 3600.0]
    w.wcs.crval = [ra_deg, dec_deg]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    img = np.ones((32, 32), dtype=np.float32)
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(data=img, header=w.to_header())]).writeto(buf, overwrite=True)
    return buf.getvalue()


def test_all_providers_registered() -> None:
    """Verify all six providers register with the plug-in registry offline."""
    names = set(list_providers())
    assert {"gaia", "horizons", "mpc", "simbad", "spherex_irsa", "wise"}.issubset(names)
    assert isinstance(get_provider("gaia"), GaiaProvider)
    assert isinstance(get_provider("wise"), WiseProvider)
    assert isinstance(get_provider("simbad"), SimbadProvider)
    assert isinstance(get_provider("horizons"), HorizonsProvider)
    assert isinstance(get_provider("mpc"), MpcProvider)


def test_gaia_cone_propagation_parallax_filter_and_mirror_fallback() -> None:
    """Test Barnard's Star propagation, observer parallax, quality gate, and VizieR failover."""
    gaia_tbl = Table(
        [
            Column([4472832130942575872, 1001, 1002], name="source_id"),
            Column([269.45207696, 269.455, 269.456], name="ra", unit=u.deg),
            Column([4.69336497, 4.695, 4.697], name="dec", unit=u.deg),
            Column([0.03, 0.2, 1.5], name="ra_error", unit=u.mas),
            Column([0.03, 0.2, 1.5], name="dec_error", unit=u.mas),
            Column([-801.551, 2.0, 1.0], name="pmra", unit=u.mas / u.yr),
            Column([0.04, 0.2, 5.0], name="pmra_error", unit=u.mas / u.yr),
            Column([10362.394, -3.0, 2.0], name="pmdec", unit=u.mas / u.yr),
            Column([0.04, 0.2, 5.0], name="pmdec_error", unit=u.mas / u.yr),
            Column([546.976, 1.5, np.nan], name="parallax", unit=u.mas),
            Column([0.04, 0.2, np.nan], name="parallax_error", unit=u.mas),
            Column([-110.51, 0.0, np.nan], name="radial_velocity", unit=u.km / u.s),
            Column([8.194, 15.2, 17.5], name="phot_g_mean_mag", unit=u.mag),
            Column([11.0, 16.0, 18.2], name="phot_bp_mean_mag", unit=u.mag),
            Column([7.5, 14.5, 16.8], name="phot_rp_mean_mag", unit=u.mag),
            Column([1.08, 2.5, 1.1], name="ruwe"),
            Column([31, 31, 3], name="astrometric_params_solved"),
            Column(["false", "false", "false"], name="duplicated_source"),
        ]
    )
    gaia_xml = _table_to_votable_bytes(gaia_tbl)

    vizier_tbl = Table(
        [
            Column([4472832130942575872], name="Source"),
            Column([269.45207696], name="RA_ICRS", unit=u.deg),
            Column([4.69336497], name="DE_ICRS", unit=u.deg),
            Column([0.03], name="e_RA_ICRS", unit=u.mas),
            Column([0.03], name="e_DE_ICRS", unit=u.mas),
            Column([-801.551], name="pmRA", unit=u.mas / u.yr),
            Column([0.04], name="e_pmRA", unit=u.mas / u.yr),
            Column([10362.394], name="pmDE", unit=u.mas / u.yr),
            Column([0.04], name="e_pmDE", unit=u.mas / u.yr),
            Column([546.976], name="Plx", unit=u.mas),
            Column([0.04], name="e_Plx", unit=u.mas),
            Column([-110.51], name="RV", unit=u.km / u.s),
            Column([8.194], name="Gmag", unit=u.mag),
            Column([11.0], name="BPmag", unit=u.mag),
            Column([7.5], name="RPmag", unit=u.mag),
            Column([1.08], name="RUWE"),
            Column([31], name="Solved"),
            Column([0], name="Dup"),
        ]
    )
    vizier_xml = _table_to_votable_bytes(vizier_tbl)

    esa_attempts = [0]

    def _gaia_handler(req: requests.PreparedRequest) -> tuple[int, dict[str, str], bytes]:
        url = req.url or ""
        if "gea.esac.esa.int" in url:
            esa_attempts[0] += 1
            if esa_attempts[0] == 1:
                return 429, {"Retry-After": "1"}, b"rate limit"
            return 200, {"Content-Type": "application/x-votable+xml"}, gaia_xml
        if "gaia.ari.uni-heidelberg.de" in url:
            return 503, {}, b"ari down"
        return 200, {"Content-Type": "application/x-votable+xml"}, vizier_xml

    session, adapter = _session_with(_gaia_handler)
    sleeps: list[float] = []
    gaia = GaiaProvider(
        mirrors=["esa"],
        session=session,
        rate_limiter=_fast_limiter(),
        sleeper=sleeps.append,
    )

    # Expected 2025.5 position from Astropy apply_space_motion
    t_2025 = Time("2025-07-02T00:00:00", scale="utc")
    ref_2025 = SkyCoord(
        ra=269.45207696 * u.deg,
        dec=4.69336497 * u.deg,
        distance=(1000.0 / 546.976) * u.pc,
        pm_ra_cosdec=-801.551 * u.mas / u.yr,
        pm_dec=10362.394 * u.mas / u.yr,
        radial_velocity=-110.51 * u.km / u.s,
        obstime=Time(2016.0, format="jyear", scale="tcb"),
        frame="icrs",
    ).apply_space_motion(new_obstime=t_2025.tdb)

    # Search centered at Barnard's Star's 2025 position with 15" radius (moved ~98" since 2016!)
    sources = gaia.cone(ref_2025, 15.0 * u.arcsec, epoch=t_2025, astrometric_only=True)
    assert sleeps == [1.0]
    assert len(sources) == 1
    b = sources[0]
    assert b.source_id == "4472832130942575872"
    assert b.skycoord.separation(ref_2025).to_value(u.mas) < 0.05
    assert b.ra_err_mas is not None and b.ra_err_mas > 0.03
    assert b.mags == {"G": 8.194, "BP": 11.0, "RP": 7.5}

    # Check Earth observer parallax (~0.547 arcsec shift max depending on season)
    sources_obs = gaia.cone(
        ref_2025,
        15.0 * u.arcsec,
        epoch=t_2025,
        astrometric_only=True,
        observer_bary_au=[1.0, 0.0, 0.0],
    )
    shift_arcsec = sources_obs[0].skycoord.separation(b.skycoord).to_value(u.arcsec)
    assert 0.4 < shift_arcsec < 0.6

    # Check full mirror fallback: esa fails 503 -> ari fails 503 -> vizier succeeds!
    def _down_then_vizier(req: requests.PreparedRequest) -> tuple[int, dict[str, str], bytes]:
        url = req.url or ""
        if "tapvizier" in url:
            return 200, {"Content-Type": "application/x-votable+xml"}, vizier_xml
        return 503, {}, b"unavailable"

    s2, _ = _session_with(_down_then_vizier)
    gaia_fb = GaiaProvider(
        mirrors=["esa", "ari", "vizier"],
        session=s2,
        rate_limiter=_fast_limiter(),
        max_retries=0,
        sleeper=lambda _: None,
    )
    looked = gaia_fb.lookup_object("Gaia DR3 4472832130942575872")
    assert looked is not None
    assert looked.extra["mirror"] == "vizier"
    assert looked.parallax_mas == pytest.approx(546.976)

    with pytest.raises(ProviderQueryError):
        gaia_fb.lookup_object("not-a-gaia-id")
    del adapter

    # Verify custom invalid mirror rejects gracefully
    with pytest.raises(ProviderQueryError):
        GaiaProvider(mirrors=[], session=s2)
    assert gaia_fb.describe_releases()[0].release_id == "gaiadr3"
    assert isinstance(GaiaMirror("custom", "https://example.org/tap", "t"), GaiaMirror)


def test_wise_catwise_allwise_neowise_and_unwise(tmp_path: Path) -> None:
    """Verify CatWISE arcsec/yr vs AllWISE mas/yr PM propagation, NEOWISE cap, and unWISE."""
    catwise_tbl = Table(
        [
            Column(["0855m072_b0-001234"], name="source_id"),
            Column([133.79513], name="ra", unit=u.deg),
            Column([-7.24515], name="dec", unit=u.deg),
            Column([0.06], name="sigra", unit=u.arcsec),
            Column([0.07], name="sigdec", unit=u.arcsec),
            Column([-8.10], name="pmra", unit=u.arcsec / u.yr),
            Column([0.02], name="sigpmra", unit=u.arcsec / u.yr),
            Column([0.70], name="pmdec", unit=u.arcsec / u.yr),
            Column([0.02], name="sigpmdec", unit=u.arcsec / u.yr),
            Column([17.8], name="w1mpro", unit=u.mag),
            Column([0.15], name="w1sigmpro", unit=u.mag),
            Column([13.89], name="w2mpro", unit=u.mag),
            Column([0.05], name="w2sigmpro", unit=u.mag),
        ]
    )
    allwise_tbl = Table(
        [
            Column(["J085510.83-071442.5"], name="designation"),
            Column([133.79513], name="ra", unit=u.deg),
            Column([-7.24515], name="dec", unit=u.deg),
            Column([0.09], name="sigra", unit=u.arcsec),
            Column([0.09], name="sigdec", unit=u.arcsec),
            Column([-8090.0], name="pmra", unit=u.mas / u.yr),
            Column([40.0], name="sigpmra", unit=u.mas / u.yr),
            Column([700.0], name="pmdec", unit=u.mas / u.yr),
            Column([40.0], name="sigpmdec", unit=u.mas / u.yr),
            Column([13.92], name="w2mpro", unit=u.mag),
            Column([0.06], name="w2sigmpro", unit=u.mag),
        ]
    )
    neowise_tbl = Table(
        [
            Column(["neo_1", "neo_2"], name="source_id"),
            Column(["scan1", "scan2"], name="scan_id"),
            Column([10, 22], name="frame_num"),
            Column([59180.5, 58900.2], name="mjd"),
            Column([133.790, 133.792], name="ra", unit=u.deg),
            Column([-7.245, -7.245], name="dec", unit=u.deg),
            Column([0.12, 0.13], name="sigra", unit=u.arcsec),
            Column([0.12, 0.13], name="sigdec", unit=u.arcsec),
            Column([14.0, 13.9], name="w2mpro", unit=u.mag),
            Column([0.08, 0.08], name="w2sigmpro", unit=u.mag),
        ]
    )
    sia_tbl = Table(
        [
            Column(["wise_frame_w2"], name="obs_id"),
            Column(["https://irsa.ipac.caltech.edu/wise_w2.fits.gz"], name="access_url"),
            Column([55400.0], name="t_min"),
            Column([55400.1], name="t_max"),
            Column(["W2"], name="band_id"),
            Column([133.795], name="s_ra"),
            Column([-7.245], name="s_dec"),
            Column([0.5], name="s_fov"),
        ]
    )

    # Create gzipped FITS and unWISE tar.gz archive for offline download tests
    fits_raw = _tiny_fits_bytes()
    fits_gz = gzip.compress(fits_raw)
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tf:
        for fname, content in (
            ("unwise-invvar-w2.fits", b"other"),
            ("unwise-img-w2.fits", fits_raw),
        ):
            info = tarfile.TarInfo(fname)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    unwise_tgz = tar_buf.getvalue()

    def _irsa_handler(req: requests.PreparedRequest) -> tuple[int, dict[str, str], bytes]:
        url = req.url or ""
        body = str(req.body or "")
        if url.endswith("/SIA/capabilities"):
            caps = (
                Path(__file__).resolve().parents[1]
                / "fixtures"
                / "cassettes"
                / "spherex_sia_capabilities.xml"
            ).read_bytes()
            return 200, {"Content-Type": "application/xml"}, caps
        if "/SIA" in url:
            return (
                200,
                {"Content-Type": "application/x-votable+xml"},
                _table_to_votable_bytes(sia_tbl),
            )
        if "catwise_2020" in body or "catwise_2020" in url:
            return (
                200,
                {"Content-Type": "application/x-votable+xml"},
                _table_to_votable_bytes(catwise_tbl),
            )
        if "allwise_p3as_psd" in body or "allwise_p3as_psd" in url:
            return (
                200,
                {"Content-Type": "application/x-votable+xml"},
                _table_to_votable_bytes(allwise_tbl),
            )
        if "neowiser_p1bs_psd" in body or "neowiser_p1bs_psd" in url:
            return (
                200,
                {"Content-Type": "application/x-votable+xml"},
                _table_to_votable_bytes(neowise_tbl),
            )
        return 404, {}, b"unknown"

    session, _ = _session_with(_irsa_handler)
    irsa = IrsaClass()
    irsa._session = session

    def _http_handler(req: httpx.Request) -> httpx.Response:
        if "wise_w2.fits.gz" in str(req.url):
            return httpx.Response(200, content=fits_gz)
        if "unwise.me/cutout_fits" in str(req.url):
            return httpx.Response(200, content=unwise_tgz)
        return httpx.Response(404)

    wise = WiseProvider(
        irsa=irsa,
        http_client=httpx.Client(transport=httpx.MockTransport(_http_handler)),
        rate_limiter=_fast_limiter(),
        sleeper=lambda _: None,
    )

    # CatWISE2020: arcsec/yr -> mas/yr and 10 yr propagation (~81" motion!)
    t_2025 = Time(60850.0, format="mjd", scale="tdb")
    cw = wise.cone(WISE_0855, 2.0 * u.arcmin, epoch=t_2025, catalog="catwise_2020")
    assert len(cw) == 1
    assert cw[0].pmra_masyr == pytest.approx(-8100.0)
    assert cw[0].pmdec_masyr == pytest.approx(700.0)
    assert cw[0].ra_err_mas is not None and cw[0].ra_err_mas > 60.0
    assert cw[0].mag_band == "W1" and cw[0].mags["W2"] == pytest.approx(13.89)
    assert cw[0].skycoord.separation(WISE_0855).to_value(u.arcsec) == pytest.approx(82.0, abs=1.5)

    # AllWISE stays mas/yr
    aw = wise.cone(WISE_0855, 1.0 * u.arcmin, catalog="allwise_p3as_psd")
    assert aw[0].pmra_masyr == pytest.approx(-8090.0)
    assert aw[0].mag_band == "W2"

    # NEOWISE 30" cap enforcement
    with pytest.raises(ProviderQueryError, match="exceeds 30"):
        wise.cone(WISE_0855, 35.0 * u.arcsec, catalog="neowiser_p1bs_psd")
    neo = wise.cone(WISE_0855, 20.0 * u.arcsec, catalog="neowiser_p1bs_psd")
    assert [s.source_id for s in neo] == ["neo_2:scan2:22", "neo_1:scan1:10"]  # sorted by MJD

    # Images + unWISE cutout
    frames = wise.search_frames(WISE_0855, 5.0 * u.arcmin, bands=["W2"])
    assert len(frames) == 1
    assert frames[0].wavelength_um == (4.0, 5.3)
    raw_fits = wise.fetch(frames[0], cutout=(WISE_0855, 0.5 * u.arcmin))
    with fits.open(io.BytesIO(raw_fits), memmap=False) as hdul:
        q = wise.quality_mask(hdul)
        assert q.all()
    unwise_fits = wise.unwise_cutout(WISE_0855, size_pix=64, bands="12")
    assert unwise_fits.lstrip().startswith(b"SIMPLE")
    with pytest.raises(ProviderQueryError):
        wise.unwise_cutout(WISE_0855, size_pix=2048)
    assert tmp_path.exists()


def test_simbad_cone_and_lookup_object() -> None:
    """Verify SIMBAD TAP parsing, ADQL single-quote escaping, fluxes, and propagation."""
    simbad_tbl = Table(
        [
            Column([12345], name="oid"),
            Column(["Barnard's Star"], name="main_id"),
            Column([269.45565], name="ra", unit=u.deg),
            Column([4.64731], name="dec", unit=u.deg),
            Column([0.1], name="coo_err_maj", unit=u.mas),
            Column([0.1], name="coo_err_min", unit=u.mas),
            Column([90], name="coo_err_angle"),
            Column(["2020yCat.1350....0G"], name="coo_bibcode"),
            Column([-801.55], name="pmra", unit=u.mas / u.yr),
            Column([10362.39], name="pmdec", unit=u.mas / u.yr),
            Column([0.05], name="pm_err_maj", unit=u.mas / u.yr),
            Column([0.05], name="pm_err_min", unit=u.mas / u.yr),
            Column([546.98], name="plx_value", unit=u.mas),
            Column([0.04], name="plx_err", unit=u.mas),
            Column([-110.5], name="rvz_radvel", unit=u.km / u.s),
            Column([0.2], name="rvz_err", unit=u.km / u.s),
            Column(["PM*"], name="otype"),
            Column(["High Proper Motion Star"], name="otype_txt"),
            Column(["M4V"], name="sp_type"),
            Column([9.51], name="V", unit=u.mag),
            Column([5.24], name="J", unit=u.mag),
            Column([4.52], name="K", unit=u.mag),
            Column(["NAME Barnard's Star|GJ 699|HIP 87937"], name="ids"),
        ]
    )
    xml_bytes = _table_to_votable_bytes(simbad_tbl)
    seen_queries: list[str] = []

    def _simbad_handler(req: requests.PreparedRequest) -> tuple[int, dict[str, str], bytes]:
        seen_queries.append(str(req.body or ""))
        return 200, {"Content-Type": "application/x-votable+xml"}, xml_bytes

    session, _ = _session_with(_simbad_handler)
    from astroquery.simbad.core import SimbadClass

    sim = SimbadClass()
    sim._session = session
    prov = SimbadProvider(simbad=sim, rate_limiter=_fast_limiter(), sleeper=lambda _: None)

    obj = prov.lookup_object("Barnard's Star")
    assert obj is not None
    assert "Barnard''s Star" in unquote_plus(seen_queries[-1])
    assert obj.object_type == "PM*"
    assert obj.extra["sp_type"] == "M4V"
    assert obj.extra["aliases"] == ["NAME Barnard's Star", "GJ 699", "HIP 87937"]
    assert obj.mags == {"V": 9.51, "J": 5.24, "K": 4.52}
    assert obj.radial_velocity_kms == pytest.approx(-110.5)

    # Propagate 25 yr from J2000.0
    t_2025 = Time(2025.0, format="jyear", scale="tdb")
    cone_hits = prov.cone(BARNARD_J2016, 2.0 * u.arcmin, epoch=t_2025)
    assert len(cone_hits) == 1
    assert cone_hits[0].extra["pm_propagated"] is True
    assert cone_hits[0].dec_deg > 4.71  # +10.36"/yr * 25 yr ~ +0.072 deg from 4.6473

    with pytest.raises(ProviderQueryError):
        prov.lookup_object("   ")


def test_horizons_ephemeris_vectors_sbdb_and_sb_ident() -> None:
    """Verify Horizons ephemerides (1-sig errors), observer vectors, SBDB, and sb_ident."""
    eph_hdr = (
        "Date__(UT)__HR:MN, Date_________JDUT, , , R.A._(ICRF), DEC_(ICRF), dRA*cosD, "
        "d(DEC)/dt, APmag, S-brt, delta, deldot, S-O-T, /r, S-T-O, "
        "RA_3sigma, DEC_3sigma, POS_3sigma,"
    )
    eph_row = (
        "2025-Jun-21 09:50, 2460847.910000000, , , 133.79500, -7.24500, -18.500, "
        "12.250, 12.40, 18.1, 1.1234, -2.5, 92.5, /L, 28.4, 0.450, 0.300, 0.541,"
    )
    eph_txt = (
        "Target body name: 433 Eros (A898 PA)              {source: JPL#659}\n"
        "Center body name: Earth (399)                     {source: DE441}\n"
        f"{eph_hdr}\n$$SOE\n{eph_row}\n$$EOE\n"
    )
    vec_row = (
        "2460847.910000000, A.D. 2025-Jun-21 09:50:24.0000, -0.0500000, -0.9900000, "
        "-0.0200000, 0.0170000, -0.0010000, 0.0002000, 0.0057, 0.9914, -0.0001,"
    )
    vec_txt = (
        "Target body name: SPHEREx (spacecraft) (-165)\n"
        "Center body name: Solar System Barycenter (0)\n"
        "JDTDB, Calendar Date (TDB), X, Y, Z, VX, VY, VZ, LT, RG, RR,\n"
        f"$$SOE\n{vec_row}\n$$EOE\n"
    )

    def _hor_handler(req: requests.PreparedRequest) -> tuple[int, dict[str, str], bytes]:
        url = req.url or ""
        if "Unknown" in url:
            return (
                200,
                {"Content-Type": "text/plain"},
                b"Matching small-bodies:\nNo matches found\n",
            )
        if "EPHEM_TYPE=VECTORS" in url:
            return 200, {"Content-Type": "text/plain"}, vec_txt.encode()
        return 200, {"Content-Type": "text/plain"}, eph_txt.encode()

    session, _ = _session_with(_hor_handler)

    sbdb_ok = {
        "object": {
            "fullname": "433 Eros (A898 PA)",
            "des": "433",
            "spkid": "20000433",
            "kind": "an",
            "neo": True,
            "pha": False,
            "orbit_class": {"name": "Amor"},
        },
        "orbit": {
            "epoch": "2460600.5",
            "elements": [
                {"name": "e", "value": "0.2227", "sigma": "1.2e-8"},
                {"name": "a", "value": "1.4581", "sigma": "2.1e-9"},
            ],
        },
        "phys_par": [{"name": "H", "value": "10.31"}, {"name": "G", "value": "0.46"}],
    }
    sb_ident_ok = {
        "fields_second": [
            "Object name",
            "Astrometric RA (hh:mm:ss.ss)",
            "Astrometric Dec (dd mm ss)",
            "Visual mag (V)",
            'RA rate ("/h)',
            'Dec rate ("/h)',
        ],
        "data_second_pass": [
            ["433 Eros (A898 PA)", "08 55 10.80", "-07 14 42.0", "12.4", "-18.5", "12.25"],
            ["99999 Faraway", "09 20 00.00", "-07 14 42.0", "19.0", "10.0", "1.0"],
        ],
    }

    def _http_handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "sbdb.api" in url:
            if "ambig" in url:
                return httpx.Response(
                    200, json={"code": "300", "list": [{"name": "A"}, {"name": "B"}]}
                )
            if "missing" in url:
                return httpx.Response(404)
            return httpx.Response(200, json=sbdb_ok)
        if "sb_ident.api" in url:
            assert "fov-dec-center=M07-14-42" in url
            return httpx.Response(200, json=sb_ident_ok)
        return httpx.Response(404)

    hor = HorizonsProvider(
        session=session,
        http_client=httpx.Client(transport=httpx.MockTransport(_http_handler)),
        rate_limiter=_fast_limiter(1),
        sleeper=lambda _: None,
    )

    t0 = Time(2460847.91, format="jd", scale="utc")
    pts = hor.ephemeris("433", t0, location="500")
    assert len(pts) == 1
    p = pts[0]
    assert p.ra_deg == pytest.approx(133.795)
    assert p.ra_err_arcsec == pytest.approx(0.15)  # 0.45 / 3.0
    assert p.dec_err_arcsec == pytest.approx(0.10)  # 0.30 / 3.0
    assert p.rate_ra_arcsec_hr == pytest.approx(-18.5)
    assert p.mag_v == pytest.approx(12.4)
    assert p.epoch.scale == "tdb"

    vecs = hor.observer_vectors("-165", t0.tdb)
    assert len(vecs) == 1
    assert vecs[0]["xyz_au"] == pytest.approx((-0.05, -0.99, -0.02))
    assert vecs[0]["vxyz_au_d"] == pytest.approx((0.017, -0.001, 0.0002))

    obj = hor.lookup_object("433")
    assert obj is not None and obj["neo"] is True
    assert obj["elements"]["e"] == pytest.approx(0.2227)
    assert obj["uncertainties"]["e"] == pytest.approx(1.2e-8)
    assert hor.lookup_object("missing") is None
    with pytest.raises(ProviderQueryError, match="Ambiguous SBDB"):
        hor.lookup_object("ambig")

    cone_objs = hor.cone(WISE_0855, 5.0 * u.arcmin, epoch=t0)
    assert [s.source_id for s in cone_objs] == ["433 Eros (A898 PA)"]
    assert cone_objs[0].object_type == "sso"

    with pytest.raises(ProviderQueryError):
        hor.ephemeris("UnknownObject", t0)


def test_mpc_object_ephemeris_observations_and_mpchecker() -> None:
    """Verify MPC orbit lookup, MPES sky-motion ephemeris, observations, and MPChecker."""
    mpes_html = """<html><body><pre>
Date       UT      R.A. (J2000) Decl.    Delta     r     El.    Ph.    V      Motion     Alt  Az
            h m s        d m s                                                "/hr    "/hr
2025 06 21 000000 08 55 10.8 -07 14 43   1.123   1.456  92.5  28.4   12.4  -18.5   +12.3
</pre></body></html>"""
    mpes_empty = "<html><body><pre>No ephemeris lines</pre></body></html>"

    obs_json = [
        {
            "object_type": "M",
            "original_record": (
                "12893         C2025 06 21.50000 08 55 10.80 -07 14 42.5          14.2 Vq     568"
            ),
        },
        {
            "object_type": "M",
            "original_record": (
                "12893         C2025 06 22.50000 08 55 20.80 -07 14 30.5          14.3 Vq     568"
            ),
        },
    ]
    orbit_json = [
        {"name": "Eros", "number": 433, "eccentricity": "0.2227", "semimajor_axis": "1.458"}
    ]

    def _mpc_handler(req: requests.PreparedRequest) -> tuple[int, dict[str, str], bytes]:
        url = req.url or ""
        body = str(req.body or "")
        if "search_orbits" in url:
            if "comet" in url or "number=999999" in url:
                return 200, {"Content-Type": "application/json"}, b"[]"
            return 200, {"Content-Type": "application/json"}, json.dumps(orbit_json).encode()
        if "mpeph2.cgi" in url:
            if "TextArea=missing" in body:
                return 200, {"Content-Type": "text/html"}, mpes_empty.encode()
            return 200, {"Content-Type": "text/html"}, mpes_html.encode()
        if "table=observations" in url:
            return 200, {"Content-Type": "application/json"}, json.dumps(obs_json).encode()
        return 404, {}, b"not found"

    session, _ = _session_with(_mpc_handler)

    checkmp_html = (
        "<html><body><pre>\n"
        " Object designation         R.A.      Decl.     V       Offsets     Motion/hr\n"
        ' (433) Eros                08 55 10.8 -07 14 43  12.4  0.0E   0.0N    -19"   +12"  ...\n'
        ' 99999 Faraway             09 20 00.0 -07 14 43  18.0  99.0E  0.0N    -10"   +05"  ...\n'
        "</pre></body></html>"
    )

    def _checkmp_http(req: httpx.Request) -> httpx.Response:
        if "json" in str(req.url):
            return httpx.Response(
                200,
                json={
                    "objects": [
                        {"desig": "2024 AA", "ra_deg": 133.80, "dec_deg": -7.25, "vmag": 21.1}
                    ]
                },
            )
        return httpx.Response(200, text=checkmp_html)

    mpc = MpcProvider(
        session=session,
        http_client=httpx.Client(transport=httpx.MockTransport(_checkmp_http)),
        rate_limiter=_fast_limiter(1),
        sleeper=lambda _: None,
    )

    orbit = mpc.lookup_object("(433)")
    assert orbit is not None and orbit["name"] == "Eros"
    assert mpc.lookup_object("999999", target_type="asteroid") is None

    t0 = Time("2025-06-21T00:00:00", scale="utc")
    ephs = mpc.ephemeris("433", t0, location="500")
    assert len(ephs) == 1
    assert ephs[0].rate_ra_arcsec_hr == pytest.approx(-18.5)
    assert ephs[0].rate_dec_arcsec_hr == pytest.approx(12.3)
    assert ephs[0].mag_v == pytest.approx(12.4)
    assert mpc.ephemeris("missing", t0) == []

    obs = mpc.observations(12893)
    assert len(obs) == 2
    assert obs[0]["observatory"] == "568"
    assert obs[0]["mag"] == pytest.approx(14.2)

    hits = mpc.cone(WISE_0855, 10.0 * u.arcmin, epoch=t0)
    assert [s.source_id for s in hits] == ["(433) Eros"]
    assert hits[0].mag == pytest.approx(12.4)

    # JSON mirror response fallback check
    mpc_json = MpcProvider(
        session=session,
        http_client=httpx.Client(transport=httpx.MockTransport(_checkmp_http)),
        mpchecker_url="https://minorplanetcenter.net/checkmp.json",
        rate_limiter=_fast_limiter(1),
        sleeper=lambda _: None,
    )
    json_hits = mpc_json.cone(WISE_0855, 10.0 * u.arcmin, epoch=t0)
    assert [s.source_id for s in json_hits] == ["2024 AA"]

    # Confirm Providers remain resilient on hard failures
    with pytest.raises(ProviderUnavailableError):
        mpc_down = MpcProvider(
            session=_session_with(lambda _: (500, {}, b"err"))[0],
            rate_limiter=_fast_limiter(1),
            max_retries=0,
            sleeper=lambda _: None,
        )
        mpc_down.lookup_object("433")
