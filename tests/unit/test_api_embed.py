"""Offline TestClient API tests for /embed, /api/v1/oembed, and /api/v1/render.gif."""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import astropy.units as u
import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
from fastapi.testclient import TestClient
from PIL import Image

from app.api.deps import (
    get_cache,
    get_simbad_provider,
    get_spherex_provider,
    get_storage,
)
from app.main import app
from app.providers.base import (
    CatalogSource,
    FrameRef,
    Provider,
    ProviderUnavailableError,
    ReleaseDescriptor,
)
from app.services.cache import MemoryCacheBackend
from app.services.storage import LocalStorageBackend


def _make_fits(ra_deg: float = 133.79513, dec_deg: float = -7.24515, peak: float = 40.0) -> bytes:
    w = WCS(naxis=2)
    w.wcs.crpix = [8.5, 8.5]
    w.wcs.cdelt = [-6.2 / 3600.0, 6.2 / 3600.0]
    w.wcs.crval = [ra_deg, dec_deg]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    hdr = w.to_header()
    hdr["DATE-OBS"] = "2025-06-21T09:50:24.000"
    data = np.full((16, 16), 2.0, dtype=np.float32)
    data[8, 8] = peak
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(data=data, header=hdr, name="IMAGE")]).writeto(
        buf
    )
    return buf.getvalue()


class _FakeSpherexProvider(Provider):
    name = "spherex_irsa"
    kind = {"images"}

    def __init__(self) -> None:
        self.search_calls = 0
        self.fetch_calls = 0
        self.empty = False
        self.fail_with: Exception | None = None

    def describe_releases(self) -> list[ReleaseDescriptor]:
        return [
            ReleaseDescriptor(
                release_id="spherex_qr_2026-10",
                provider=self.name,
                mission="SPHEREx",
                collection="spherex_qr",
                version_label="QR1",
                release_date=date(2026, 10, 1),
                n_frames=2,
            )
        ]

    def search_frames(
        self,
        c: SkyCoord,
        radius: u.Quantity,
        t0: Time | None = None,
        t1: Time | None = None,
        bands: list[str] | None = None,
        release: str | None = None,
    ) -> list[FrameRef]:
        del c, radius, t0, t1
        self.search_calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        if self.empty:
            return []
        rel = "spherex_qr_2026-10" if release in (None, "", "latest") else release
        refs = [
            FrameRef(
                provider=self.name,
                release=rel,
                obs_id="obs_s1_a6",
                access_url="https://irsa.example/obs_s1_a6.fits",
                t_mid=Time(60850.41, format="mjd", scale="tdb"),
                band_id="arr6",
                array_id=6,
                wavelength_um=(4.42, 5.00),
            ),
            FrameRef(
                provider=self.name,
                release=rel,
                obs_id="obs_s2_a5",
                access_url="https://irsa.example/obs_s2_a5.fits",
                t_mid=Time(61031.77, format="mjd", scale="tdb"),
                band_id="arr5",
                array_id=5,
                wavelength_um=(3.82, 4.42),
            ),
        ]
        if bands:
            low = {b.lower() for b in bands}
            refs = [r for r in refs if r.band_id and r.band_id.lower() in low]
        return refs

    def fetch(self, ref: FrameRef, cutout: tuple[SkyCoord, u.Quantity] | None = None) -> bytes:
        del cutout
        self.fetch_calls += 1
        return _make_fits(peak=30.0 if "s1" in ref.obs_id else 60.0)


class _FakeSimbadProvider(Provider):
    name = "simbad"
    kind = {"catalog"}

    def describe_releases(self) -> list[ReleaseDescriptor]:
        return []

    def lookup_object(self, name: str, **kwargs: Any) -> CatalogSource | None:
        del kwargs
        if name.strip().lower() == "wise 0855":
            return CatalogSource(
                provider=self.name,
                catalog="simbad",
                source_id="WISE J085510.83-071442.5",
                ra_deg=133.79513,
                dec_deg=-7.24515,
                epoch=Time(2000.0, format="jyear", scale="tdb"),
                pmra_masyr=-8090.0,
                pmdec_masyr=700.0,
            )
        return None


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setattr(
        SkyCoord,
        "from_name",
        classmethod(lambda cls, name: (_ for _ in ()).throw(RuntimeError(f"offline: {name}"))),
    )
    cache = MemoryCacheBackend()
    storage = LocalStorageBackend(tmp_path / "store")
    sphx = _FakeSpherexProvider()
    simbad = _FakeSimbadProvider()

    app.dependency_overrides[get_cache] = lambda: cache
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_spherex_provider] = lambda: sphx
    app.dependency_overrides[get_simbad_provider] = lambda: simbad
    try:
        with TestClient(app) as client:
            yield {"client": client, "sphx": sphx, "cache": cache, "storage": storage}
    finally:
        app.dependency_overrides.clear()


def test_oembed_builds_sanitized_iframe_and_validates_urls(env: dict[str, Any]) -> None:
    client: TestClient = env["client"]
    res = client.get(
        "/api/v1/oembed",
        params={
            "url": "https://sky.example.org/v?ra=133.79513&dec=-7.24515&fov=10&bands=arr6&utm_source=news",
            "maxwidth": 800,
            "maxheight": 500,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["type"] == "rich"
    assert body["version"] == "1.0"
    assert body["width"] == 800
    assert body["height"] == 500
    assert "https://sky.example.org/embed?" in body["html"]
    assert "ra=133.79513" in body["html"]
    assert "utm_source" not in body["html"]

    for bad_url in (
        "javascript:alert(1)",
        "https://sky.example.org/admin?ra=1",
        "https://sky.example.org/v?ra=bad&dec=0",
        "https://sky.example.org/v?ra=10&dec=0&fov=99",
    ):
        err = client.get("/api/v1/oembed", params={"url": bad_url})
        assert err.status_code == 422, bad_url
        assert err.headers["content-type"].startswith("application/problem+json")


def test_render_gif_multi_epoch_and_cache_hit(env: dict[str, Any]) -> None:
    client: TestClient = env["client"]
    sphx: _FakeSpherexProvider = env["sphx"]

    r1 = client.get("/api/v1/render.gif?ra=133.79513&dec=-7.24515&fov=10&bands=all&fps=4")
    assert r1.status_code == 200, r1.text
    assert r1.headers["content-type"] == "image/gif"
    assert r1.headers["x-cache"] == "MISS"
    assert "max-age=" in r1.headers["cache-control"]

    img = Image.open(io.BytesIO(r1.content))
    assert img.format == "GIF"
    assert getattr(img, "n_frames", 1) == 2
    assert img.size == (256, 256)
    assert sphx.search_calls == 1
    assert sphx.fetch_calls == 2

    r2 = client.get("/api/v1/render.gif?ra=133.79513&dec=-7.24515&fov=10&bands=all&fps=4")
    assert r2.status_code == 200
    assert r2.headers["x-cache"] == "HIT"
    assert r2.content == r1.content
    assert sphx.search_calls == 1
    assert sphx.fetch_calls == 2


def test_render_gif_quota_empty_and_provider_errors(env: dict[str, Any]) -> None:
    client: TestClient = env["client"]
    sphx: _FakeSpherexProvider = env["sphx"]

    anon_big = client.get("/api/v1/render.gif?ra=133.795&dec=-7.245&fov=20")
    assert anon_big.status_code == 403
    assert anon_big.headers["content-type"].startswith("application/problem+json")

    auth_big = client.get(
        "/api/v1/render.gif?ra=133.795&dec=-7.245&fov=20",
        headers={"Authorization": f"Bearer {uuid4()}"},
    )
    assert auth_big.status_code == 200

    bad_range = client.get(
        "/api/v1/render.gif?ra=133.795&dec=-7.245&from=2026-01-01T00:00:00Z&to=2025-01-01T00:00:00Z"
    )
    assert bad_range.status_code == 400

    sphx.empty = True
    missing = client.get("/api/v1/render.gif?ra=133.795&dec=-7.245&fov=5")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")

    sphx.empty = False
    sphx.fail_with = ProviderUnavailableError("IRSA maintenance")
    down = client.get("/api/v1/render.gif?ra=133.795&dec=-7.245&fov=5")
    assert down.status_code == 503
    assert down.headers["content-type"].startswith("application/problem+json")


def test_embed_html_decimal_sexagesimal_target_and_security(env: dict[str, Any]) -> None:
    client: TestClient = env["client"]

    r_dec = client.get("/embed?ra=133.79513&dec=-7.24515&fov=25&mode=blink&bands=arr6")
    assert r_dec.status_code == 200, r_dec.text
    assert r_dec.headers["content-type"].startswith("text/html")
    assert r_dec.headers["content-security-policy"] == "frame-ancestors *"
    assert "set-cookie" not in {k.lower() for k in r_dec.headers}
    assert 'data-fov="15.00"' in r_dec.text
    assert "/api/v1/render.gif?" in r_dec.text
    assert "/api/v1/fields/frames?" in r_dec.text
    assert 'aria-live="polite"' in r_dec.text

    r_sex = client.get("/embed?ra=08:55:10.831&dec=-07:14:42.53&fov=8")
    assert r_sex.status_code == 200
    assert 'data-ra="133.795129"' in r_sex.text
    assert 'data-dec="-7.245147"' in r_sex.text

    r_tgt = client.get("/embed?target=WISE+0855&fov=10")
    assert r_tgt.status_code == 200
    assert "WISE 0855" in r_tgt.text
    assert 'data-ra="133.795130"' in r_tgt.text

    r_xss = client.get(
        "/embed",
        params={"ra": "133.795", "dec": "-7.245", "bands": "</script><script>alert(1)</script>"},
    )
    assert r_xss.status_code == 200
    assert "<script>alert(1)</script>" not in r_xss.text

    assert client.get("/embed").status_code == 422
    assert client.get("/embed?ra=133.795").status_code == 422
    assert client.get("/embed?target=unknown_object_xyz").status_code == 404
