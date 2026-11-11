"""Offline unit tests for provider plug-in interface, SPHEREx IRSA provider, and storage."""

from __future__ import annotations

import base64
import hashlib
import io
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import astropy.units as u
import httpx
import numpy as np
import pytest
import requests
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
from astroquery.ipac.irsa.core import IrsaClass
from botocore.exceptions import ClientError
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from app.db.flags import DetectionFlag
from app.providers import (
    CircuitBreaker,
    FrameRef,
    Provider,
    ProviderQueryError,
    ProviderRateLimitError,
    ProviderRegistry,
    ProviderUnavailableError,
    RateLimiter,
    ReleaseDescriptor,
    SpherexIrsaProvider,
    get_provider,
    list_providers,
)
from app.services.storage import (
    ChecksumMismatchError,
    LocalStorageBackend,
    S3StorageBackend,
    compute_sha256,
)

CASSETTES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cassettes"
WISE_0855 = SkyCoord("08h55m10.831s", "-07d14m42.53s", frame="icrs")


class _FakeRaw(io.BytesIO):
    def read(self, size: int | None = -1, decode_content: bool = False) -> bytes:
        del decode_content
        return super().read(-1 if size is None else size)


class _RecordedIrsaAdapter(BaseAdapter):
    """Offline requests adapter serving recorded IVOA capabilities, SIAv2, and TAP XML."""

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
        resp.status_code = status
        resp.url = request.url or ""
        resp.headers = CaseInsensitiveDict(headers)
        resp._content = body
        resp.raw = _FakeRaw(body)
        return resp

    def close(self) -> None:
        return None


def _make_mef_fits(
    *,
    nx: int = 32,
    ny: int = 32,
    ra_deg: float = 133.79513,
    dec_deg: float = -7.24515,
    missing_hdu: str | None = None,
    omit_wcs: bool = False,
) -> bytes:
    w = WCS(naxis=2)
    w.wcs.crpix = [(nx + 1) / 2.0, (ny + 1) / 2.0]
    w.wcs.cdelt = [-6.2 / 3600.0, 6.2 / 3600.0]
    w.wcs.crval = [ra_deg, dec_deg]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]

    primary = fits.PrimaryHDU()
    primary.header["DATE-OBS"] = "2025-06-21T09:50:24.000"
    primary.header["MJD-OBS"] = 60847.41
    primary.header["EXPTIME"] = 112.5
    primary.header["ARRAY"] = 6
    primary.header["BAND"] = "arr6"

    hdr_img = fits.Header() if omit_wcs else w.to_header()
    hdr_img["DATE-OBS"] = "2025-06-21T09:50:24.000"
    hdr_img["MJD-OBS"] = 60847.41
    hdr_img["BUNIT"] = "MJy/sr"

    img_data = np.ones((ny, nx), dtype=np.float32) * 2.5
    img_data[1, 1] = np.nan
    flags_data = np.zeros((ny, nx), dtype=np.int32)
    flags_data[0, 0] = 1  # native bit 0 -> BAD_PIX
    flags_data[0, 1] = 2  # native bit 1 -> SATURATED
    var_data = np.ones((ny, nx), dtype=np.float32) * 0.04
    var_data[2, 2] = 0.0  # invalid variance -> masked
    psf_data = np.ones((7, 7), dtype=np.float32) / 49.0

    hdus: list[fits.hdu.base.ExtensionHDU | fits.PrimaryHDU] = [primary]
    candidates = [
        ("IMAGE", fits.ImageHDU(data=img_data, header=hdr_img, name="IMAGE")),
        ("FLAGS", fits.ImageHDU(data=flags_data, header=hdr_img, name="FLAGS")),
        ("VARIANCE", fits.ImageHDU(data=var_data, header=hdr_img, name="VARIANCE")),
        ("PSF", fits.ImageHDU(data=psf_data, name="PSF")),
    ]
    for name, hdu in candidates:
        if name != missing_hdu:
            hdus.append(hdu)
    buf = io.BytesIO()
    fits.HDUList(hdus).writeto(buf, overwrite=True)
    return buf.getvalue()


def _make_offline_irsa(
    handler: Callable[[requests.PreparedRequest], tuple[int, dict[str, str], bytes]],
) -> tuple[IrsaClass, _RecordedIrsaAdapter]:
    irsa = IrsaClass()
    session = requests.Session()
    adapter = _RecordedIrsaAdapter(handler)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    irsa._session = session
    return irsa, adapter


def _default_cassette_handler(
    request: requests.PreparedRequest,
) -> tuple[int, dict[str, str], bytes]:
    url = request.url or ""
    if url.endswith("/SIA/capabilities"):
        return (
            200,
            {"Content-Type": "application/xml"},
            (CASSETTES_DIR / "spherex_sia_capabilities.xml").read_bytes(),
        )
    if "/SIA" in url:
        return (
            200,
            {"Content-Type": "application/x-votable+xml"},
            (CASSETTES_DIR / "spherex_sia_wise0855.xml").read_bytes(),
        )
    if "/TAP" in url:
        return (
            200,
            {"Content-Type": "application/x-votable+xml"},
            (CASSETTES_DIR / "spherex_tap_releases.xml").read_bytes(),
        )
    return 404, {"Content-Type": "text/plain"}, b"not found"


def test_provider_registry_and_entry_point_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify entry-point plug-in discovery and built-in SPHEREx provider lookup."""
    assert "spherex_irsa" in list_providers()
    assert isinstance(get_provider("spherex_irsa"), SpherexIrsaProvider)

    class DummyProvider(Provider):
        name = "dummy_archive"
        kind = {"images"}

        def describe_releases(self) -> list[ReleaseDescriptor]:
            return []

    class _FakeEP:
        name = "dummy_archive"

        def load(self) -> type[Provider]:
            return DummyProvider

    monkeypatch.setattr(
        "app.providers.base.importlib_metadata.entry_points",
        lambda group: [_FakeEP()] if group == "sphx.providers" else [],
    )
    registry = ProviderRegistry()
    assert registry.names() == ["dummy_archive"]
    assert isinstance(registry.get("dummy_archive"), DummyProvider)


def test_search_frames_metadata_date_and_band_filters() -> None:
    """Query WISE 0855 offline and verify MJD TDB, band, array, footprint, flags, and filters."""
    irsa, adapter = _make_offline_irsa(_default_cassette_handler)
    provider = SpherexIrsaProvider(irsa=irsa, sleeper=lambda _: None)

    all_frames = provider.search_frames(WISE_0855, 5.0 * u.arcmin)
    assert len(all_frames) == 3

    sia_reqs = [r for r in adapter.requests_seen if "/SIA?" in (r.url or "")]
    assert len(sia_reqs) == 1
    assert "COLLECTION=spherex_qr" in (sia_reqs[0].url or "")
    assert "POS=CIRCLE" in (sia_reqs[0].url or "")

    f0, f1, f2 = all_frames
    assert f0.obs_id == "sphx_l2_2025_172_arr5_001"
    assert f0.t_mid.scale == "tdb"
    assert f0.mjd_tdb == pytest.approx(Time(60850.41, format="mjd", scale="utc").tdb.mjd)
    assert f0.array_id == 5
    assert f0.band_id == "arr5"
    assert f0.wavelength_um == pytest.approx((3.82, 4.42))
    assert len(f0.footprint) == 4
    assert f0.flags == 0

    assert f2.obs_id == "sphx_l2_2025_353_arr6_009"
    assert f2.array_id == 6
    assert f2.band_id == "arr6"
    assert len(f2.footprint) == 16  # CIRCLE expanded to 16-vertex ICRS polygon
    assert f2.flags == int(DetectionFlag.SATURATED)
    assert f2.flag_labels == ["SATURATED"]

    filtered = provider.search_frames(
        WISE_0855,
        5.0 * u.arcmin,
        t0=Time("2025-11-01T00:00:00", scale="utc"),
        t1=Time("2026-01-15T00:00:00", scale="utc"),
        bands=["arr6"],
    )
    assert [f.obs_id for f in filtered] == ["sphx_l2_2025_353_arr6_009"]

    with pytest.raises(ProviderQueryError):
        provider.search_frames(WISE_0855, -1.0 * u.arcmin)
    with pytest.raises(ProviderQueryError):
        provider.search_frames(
            WISE_0855,
            5.0 * u.arcmin,
            t0=Time(61000.0, format="mjd"),
            t1=Time(60900.0, format="mjd"),
        )


def test_fetch_cutout_and_full_fits_with_cache_and_checksum(tmp_path: Path) -> None:
    """Verify full FITS & Cutout2D download, LocalStorage caching, and SHA-256 checks."""
    full_fits = _make_mef_fits(nx=48, ny=48)
    expected_sha = compute_sha256(full_fits)
    expected_md5 = base64.b64encode(hashlib.md5(full_fits).digest()).decode("ascii")
    urls_requested: list[str] = []

    def _fits_transport(request: httpx.Request) -> httpx.Response:
        urls_requested.append(str(request.url))
        return httpx.Response(
            200,
            content=full_fits,
            headers={
                "Content-Type": "application/fits",
                "X-Checksum-Sha256": expected_sha,
                "Content-MD5": expected_md5,
            },
        )

    irsa, _ = _make_offline_irsa(_default_cassette_handler)
    http_client = httpx.Client(transport=httpx.MockTransport(_fits_transport))
    provider = SpherexIrsaProvider(irsa=irsa, http_client=http_client, sleeper=lambda _: None)
    storage = LocalStorageBackend(tmp_path / "store")

    ref = FrameRef(
        provider="spherex_irsa",
        release="spherex_qr",
        obs_id="sphx_l2_2025_172_arr6_002",
        access_url="https://irsa.ipac.caltech.edu/ibe/data/spherex/qr/frame.fits",
        t_mid=Time(60850.42, format="mjd", scale="utc").tdb,
        band_id="arr6",
        array_id=6,
    )

    stored_full = provider.fetch_to_storage(ref, storage)
    assert not stored_full.cache_hit
    assert not stored_full.is_cutout
    assert stored_full.sha256 == expected_sha
    assert stored_full.storage_path == "raw/spherex_irsa/spherex_qr/sphx_l2_2025_172_arr6_002.fits"
    assert stored_full.flags_summary["per_flag"] == {"BAD_PIX": 1, "SATURATED": 1}
    assert stored_full.flags_summary["detection_flags"] == int(
        DetectionFlag.BAD_PIX | DetectionFlag.SATURATED
    )
    assert len(urls_requested) == 1

    cached_full = provider.fetch_to_storage(ref, storage)
    assert cached_full.cache_hit
    assert cached_full.sha256 == expected_sha
    assert len(urls_requested) == 1  # zero extra HTTP requests on cache hit

    # Corrupt local file and verify checksum catches it and refreshes cleanly
    cached_disk_path = tmp_path / "store" / stored_full.storage_path
    cached_disk_path.write_bytes(b"corrupted-fits-bytes")
    with pytest.raises(ChecksumMismatchError):
        storage.get_bytes(stored_full.storage_path, verify_checksum=True)
    repaired = provider.fetch_to_storage(ref, storage)
    assert not repaired.cache_hit
    assert repaired.sha256 == expected_sha
    assert len(urls_requested) == 2

    # Cutout request appends IRSA query params AND trims oversized 48x48 MEF via Cutout2D
    cutout_stored = provider.fetch_to_storage(ref, storage, cutout=(WISE_0855, 2.0 * u.arcmin))
    assert not cutout_stored.cache_hit
    assert cutout_stored.is_cutout
    assert cutout_stored.storage_path.startswith("cache/cutouts/")
    assert "center=" in urls_requested[-1] and "size=" in urls_requested[-1]

    cutout_bytes = storage.get_bytes(cutout_stored.storage_path)
    with fits.open(io.BytesIO(cutout_bytes), memmap=False) as hdul:
        assert hdul["IMAGE"].data.shape == (19, 19)
        assert hdul["FLAGS"].data.shape == (19, 19)
        assert hdul["VARIANCE"].data.shape == (19, 19)
        assert hdul["PSF"].data.shape == (7, 7)  # PSF stamp preserved uncropped
        assert WCS(hdul["IMAGE"].header).has_celestial

    cutout_cached = provider.fetch_to_storage(ref, storage, cutout=(WISE_0855, 2.0 * u.arcmin))
    assert cutout_cached.cache_hit
    assert cutout_cached.sha256 == cutout_stored.sha256
    assert len(urls_requested) == 3


def test_s3_storage_backend_checksum_roundtrip() -> None:
    """Verify S3StorageBackend stores and validates SHA-256 object metadata."""
    objects: dict[str, tuple[bytes, dict[str, str]]] = {}

    class _FakeBody:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

    class _FakeS3Client:
        def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:
            del Bucket
            if Key not in objects:
                raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
            return {"Metadata": objects[Key][1]}

        def put_object(
            self, Bucket: str, Key: str, Body: bytes, ContentType: str, Metadata: dict[str, str]
        ) -> None:
            del Bucket, ContentType
            objects[Key] = (Body, dict(Metadata))

        def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
            del Bucket
            if Key not in objects:
                raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
            data, meta = objects[Key]
            return {"Body": _FakeBody(data), "Metadata": meta}

        def delete_object(self, Bucket: str, Key: str) -> None:
            del Bucket
            objects.pop(Key, None)

    s3 = S3StorageBackend("sphx", client=_FakeS3Client())
    assert not s3.exists("cache/cutouts/a.fits")
    obj = s3.put_bytes("cache/cutouts/a.fits", b"SIMPLE  =                    T")
    assert s3.exists("cache/cutouts/a.fits")
    assert s3.get_sha256("cache/cutouts/a.fits") == obj.sha256
    assert s3.get_bytes("cache/cutouts/a.fits") == b"SIMPLE  =                    T"

    objects["cache/cutouts/a.fits"] = (b"TAMPERED", {"sha256": obj.sha256})
    with pytest.raises(ChecksumMismatchError):
        s3.get_bytes("cache/cutouts/a.fits", verify_checksum=True)


def test_quality_mask_and_profile_validation() -> None:
    """Verify good-pixel quality mask semantics and release schema smoke test."""
    irsa, _ = _make_offline_irsa(_default_cassette_handler)
    provider = SpherexIrsaProvider(irsa=irsa, sleeper=lambda _: None)

    valid_bytes = _make_mef_fits(nx=16, ny=16)
    with fits.open(io.BytesIO(valid_bytes), memmap=False) as hdul:
        q = provider.quality_mask(hdul)
        assert q.dtype == bool and q.shape == (16, 16)
        assert not q[0, 0]  # BAD_PIX
        assert not q[0, 1]  # SATURATED
        assert not q[1, 1]  # NaN IMAGE
        assert not q[2, 2]  # zero VARIANCE
        assert q[8, 8]
        ok, issues = provider.validate_release_profile(hdul)
        assert ok and issues == []

    bad_bytes = _make_mef_fits(nx=16, ny=16, missing_hdu="VARIANCE", omit_wcs=True)
    with fits.open(io.BytesIO(bad_bytes), memmap=False) as hdul:
        ok, issues = provider.validate_release_profile(hdul)
        assert not ok
        assert "Missing required HDU 'VARIANCE'" in issues
        assert "No celestial WCS found" in issues


def test_rate_limit_retries_and_circuit_breaker() -> None:
    """Verify RateLimiter pacing, 429 Retry-After / 503 backoff, and CircuitBreaker."""
    now = [100.0]
    sleeps: list[float] = []

    def fake_clock() -> float:
        return now[0]

    def fake_sleep(dt: float) -> None:
        sleeps.append(dt)
        now[0] += dt

    limiter = RateLimiter(max_concurrent=4, rate_per_sec=2.0, clock=fake_clock, sleeper=fake_sleep)
    for _ in range(3):
        with limiter:
            pass
    assert sleeps == pytest.approx([0.5, 0.5])

    # 1) IRSA SIA 429 Retry-After -> succeeds on retry
    sia_calls = [0]

    def _flaky_irsa(req: requests.PreparedRequest) -> tuple[int, dict[str, str], bytes]:
        if (req.url or "").endswith("/SIA/capabilities"):
            return _default_cassette_handler(req)
        sia_calls[0] += 1
        if sia_calls[0] == 1:
            return 429, {"Retry-After": "3"}, b"slow down"
        return _default_cassette_handler(req)

    irsa, _ = _make_offline_irsa(_flaky_irsa)
    retry_sleeps: list[float] = []
    provider = SpherexIrsaProvider(
        irsa=irsa,
        rate_limiter=RateLimiter(max_concurrent=4, rate_per_sec=100.0, sleeper=lambda _: None),
        sleeper=retry_sleeps.append,
    )
    frames = provider.search_frames(WISE_0855, 5.0 * u.arcmin)
    assert len(frames) == 3
    assert sia_calls[0] == 2
    assert retry_sleeps == [3.0]

    # 2) HTTP FITS fetch: persistent 429 exhausts retries and opens circuit breaker
    fits_calls = [0]

    def _always_429(request: httpx.Request) -> httpx.Response:
        del request
        fits_calls[0] += 1
        return httpx.Response(429, headers={"Retry-After": "1"}, text="rate limited")

    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=60.0, clock=fake_clock)
    http_sleeps: list[float] = []
    p2 = SpherexIrsaProvider(
        irsa=irsa,
        http_client=httpx.Client(transport=httpx.MockTransport(_always_429)),
        rate_limiter=RateLimiter(max_concurrent=4, rate_per_sec=100.0, sleeper=lambda _: None),
        circuit_breaker=breaker,
        sleeper=http_sleeps.append,
    )
    ref = frames[0]
    with pytest.raises(ProviderRateLimitError) as exc_info:
        p2.fetch(ref)
    assert exc_info.value.retry_after_s == 1.0
    assert fits_calls[0] == 4  # 1 initial + 3 retries
    assert http_sleeps == [1.0, 1.0, 2.0]

    with pytest.raises(ProviderUnavailableError, match="circuit breaker"):
        p2.fetch(ref)

    # 3) Non-FITS payload and bad checksum never cache and raise ProviderUnavailableError
    bad_http = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"<html>error</html>"))
    )
    p3 = SpherexIrsaProvider(
        irsa=irsa,
        http_client=bad_http,
        rate_limiter=RateLimiter(max_concurrent=4, rate_per_sec=100.0, sleeper=lambda _: None),
        sleeper=lambda _: None,
    )
    with pytest.raises(ProviderUnavailableError, match="non-FITS"):
        p3.fetch(ref)

    corrupt_http = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=_make_mef_fits(nx=16, ny=16),
                headers={"X-Checksum-Sha256": "0" * 64},
            )
        )
    )
    p4 = SpherexIrsaProvider(
        irsa=irsa,
        http_client=corrupt_http,
        rate_limiter=RateLimiter(max_concurrent=4, rate_per_sec=100.0, sleeper=lambda _: None),
        sleeper=lambda _: None,
    )
    with pytest.raises(ProviderUnavailableError, match="SHA-256 mismatch"):
        p4.fetch(ref)


def test_new_release_detection() -> None:
    """Verify TAP release listing and detection of new or expanded SPHEREx releases."""
    irsa, _ = _make_offline_irsa(_default_cassette_handler)
    provider = SpherexIrsaProvider(irsa=irsa, sleeper=lambda _: None)

    live = provider.describe_releases()
    assert [r.release_id for r in live] == ["spherex_dr1", "spherex_qr"]
    assert live[1].release_date == date(2026, 10, 15)
    assert live[1].n_frames == 124500
    assert live[1].profile_name == "spherex_qr_v1"

    # Case 1: Only spherex_qr known -> detects spherex_dr1 new + spherex_qr updated
    report = provider.check_new_releases(
        {"spherex_qr": {"release_date": "2026-10-15", "n_frames": 100000}}
    )
    assert report.detected
    assert [r.release_id for r in report.new_releases] == ["spherex_dr1"]
    assert [r.release_id for r in report.updated_releases] == ["spherex_qr"]

    # Case 2: Both releases up to date -> detected is False
    report_clean = provider.check_new_releases(
        {
            "spherex_qr": {"release_date": "2026-10-15", "n_frames": 124500},
            "spherex_dr1": {"release_date": "2026-11-10", "n_frames": 250000},
        }
    )
    assert not report_clean.detected
    assert len(report_clean.known_releases) == 2
