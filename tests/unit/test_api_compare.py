"""Offline TestClient API tests for A-vs-B FITS comparison endpoints."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import astropy.units as u
import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits
from fastapi.testclient import TestClient
from test_api_analyses import _DEC0, _RA0, _make_wcs, _render_sky_fits, _upload_fits

from app.api.deps import get_storage, get_store
from app.main import app
from app.services.jobs import InMemoryJobStore, compute_params_hash
from app.services.storage import LocalStorageBackend
from app.workers.tasks_fast import UPLOAD_TASK_NAME
from app.workers.tasks_heavy import COMPARE_TASK_NAME


@pytest.fixture()
def compare_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    store = InMemoryJobStore()
    storage = LocalStorageBackend(tmp_path / "store")
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        yield {"client": TestClient(app), "store": store, "storage": storage}
    finally:
        app.dependency_overrides.clear()


def _nearest_entry(entries: list[dict[str, Any]], ra_deg: float, dec_deg: float) -> dict[str, Any]:
    target = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    coords = SkyCoord(
        ra=[e["ra"] for e in entries] * u.deg,
        dec=[e["dec"] for e in entries] * u.deg,
        frame="icrs",
    )
    seps = target.separation(coords).arcsec
    return entries[int(np.argmin(seps))]


def test_compare_full_pipeline_products_headers_and_sources(
    compare_env: dict[str, Any],
) -> None:
    client: TestClient = compare_env["client"]
    store: InMemoryJobStore = compare_env["store"]
    wcs = _make_wcs(_RA0, _DEC0, nx=80, ny=80)

    # 10 static reference stars (kappa calibration), plus mover, variable, appeared, disappeared.
    static_px = [
        (12.0, 12.0),
        (25.0, 14.0),
        (40.0, 12.0),
        (58.0, 14.0),
        (68.0, 22.0),
        (14.0, 38.0),
        (66.0, 42.0),
        (14.0, 66.0),
        (36.0, 68.0),
        (64.0, 66.0),
    ]
    statics_a: list[tuple[float, float, float]] = []
    statics_b: list[tuple[float, float, float]] = []
    for px, py in static_px:
        ra_s, dec_s = (float(v) for v in wcs.pixel_to_world_values(px, py))
        statics_a.append((ra_s % 360.0, dec_s, 150.0))
        statics_b.append((ra_s % 360.0, dec_s, 150.0))

    mov_a = tuple(float(v) for v in wcs.pixel_to_world_values(38.0, 38.0))
    mov_b = tuple(float(v) for v in wcs.pixel_to_world_values(39.4, 38.0))
    var_pos = tuple(float(v) for v in wcs.pixel_to_world_values(28.0, 50.0))
    dis_pos = tuple(float(v) for v in wcs.pixel_to_world_values(50.0, 28.0))
    app_pos = tuple(float(v) for v in wcs.pixel_to_world_values(52.0, 52.0))

    raw_a = _render_sky_fits(
        date_obs="2025-01-15T00:00:00.000",
        sky_sources=[
            *statics_a,
            (mov_a[0] % 360.0, mov_a[1], 160.0),
            (var_pos[0] % 360.0, var_pos[1], 80.0),
            (dis_pos[0] % 360.0, dis_pos[1], 180.0),
        ],
        nx=80,
        ny=80,
        seed=41,
    )
    raw_b = _render_sky_fits(
        date_obs="2026-07-15T00:00:00.000",
        sky_sources=[
            *statics_b,
            (mov_b[0] % 360.0, mov_b[1], 160.0),
            (var_pos[0] % 360.0, var_pos[1], 240.0),
            (app_pos[0] % 360.0, app_pos[1], 180.0),
        ],
        nx=80,
        ny=80,
        seed=42,
    )
    fid_a = _upload_fits(client, "epoch_a.fits", raw_a)
    fid_b = _upload_fits(client, "epoch_b.fits", raw_b)

    res = client.post(
        "/api/v1/compare",
        json={
            "a": fid_a,
            "b": fid_b,
            "ops": ["diff", "sum", "ratio", "header_diff", "sources"],
            "psf_match": "zogy",
        },
    )
    assert res.status_code == 202, res.text
    accepted = res.json()
    assert accepted["status"] == "succeeded"
    compare_id = accepted["compare_id"]

    # Idempotent deduplication regardless of ops ordering
    dup = client.post(
        "/api/v1/compare",
        json={
            "a": fid_a,
            "b": fid_b,
            "ops": ["sources", "header_diff", "ratio", "sum", "diff"],
            "psf_match": "zogy",
        },
    )
    assert dup.status_code == 202
    assert dup.json() == accepted

    # Persisted ComparisonJob metadata
    cjob = store.get_comparison_job(UUID(compare_id))
    assert cjob is not None
    assert cjob["status"] == "succeeded"
    assert "diff" in cjob["products"]

    # GET /compare/{id}
    detail_res = client.get(f"/api/v1/compare/{compare_id}")
    assert detail_res.status_code == 200, detail_res.text
    detail = detail_res.json()
    assert detail["compare_id"] == compare_id
    assert detail["status"] == "succeeded"

    for key in (
        "diff",
        "diff_display",
        "diff_science",
        "score_corr",
        "sum",
        "sum_display",
        "sum_science",
        "ratio",
        "ratio_display",
        "ratio_science",
    ):
        assert key in detail["products"]

    hdr_diff = detail["header_diff"]
    assert hdr_diff is not None
    assert hdr_diff["time"]["delta_days"] > 500.0
    assert hdr_diff["wcs"]["overlap_pct"] == pytest.approx(100.0, abs=0.5)
    assert hdr_diff["instrument"]["band_match"] is True
    assert hdr_diff["calibration"]["kappa_n_stars"] >= 10
    assert abs(hdr_diff["calibration"]["flux_scale_kappa"] - 1.0) < 0.15
    assert "hdus" in hdr_diff and len(hdr_diff["hdus"]["a"]) >= 2

    changes = detail["source_changes"]
    assert changes is not None
    assert changes["counts"]["appeared"] >= 1
    assert changes["counts"]["disappeared"] >= 1
    assert changes["counts"]["moved"] >= 1
    assert changes["counts"]["changed_brightness"] >= 1
    assert changes["counts"]["unchanged"] >= 8
    all_entries = changes["photometry_diff"]
    assert _nearest_entry(all_entries, var_pos[0] % 360.0, var_pos[1])["status"] == (
        "changed brightness"
    )
    assert _nearest_entry(all_entries, dis_pos[0] % 360.0, dis_pos[1])["status"] == "disappeared"
    assert _nearest_entry(all_entries, app_pos[0] % 360.0, app_pos[1])["status"] == "appeared"
    assert (
        _nearest_entry(all_entries, 0.5 * (mov_a[0] + mov_b[0]) % 360.0, mov_a[1])["status"]
        == "moved"
    )

    # Product endpoints: FITS, WebP, float32 binary
    fits_resp = client.get(detail["products"]["diff"])
    assert fits_resp.status_code == 200
    assert fits_resp.headers["content-type"].startswith("application/fits")
    with fits.open(io.BytesIO(fits_resp.content)) as hdul:
        assert hdul[0].data.shape == (80, 80)
        assert "CRVAL1" in hdul[0].header
        assert "VARIANCE" in hdul

    webp_resp = client.get(detail["products"]["diff_display"])
    assert webp_resp.status_code == 200
    assert webp_resp.headers["content-type"] == "image/webp"
    assert webp_resp.content[:4] == b"RIFF" and webp_resp.content[8:12] == b"WEBP"

    bin_resp = client.get(detail["products"]["sum_science"])
    assert bin_resp.status_code == 200
    assert bin_resp.headers["x-dtype"] == "float32"
    assert bin_resp.headers["x-shape"] == "80,80"
    assert len(bin_resp.content) == 80 * 80 * 4


def test_compare_selective_ops_and_error_cases(compare_env: dict[str, Any]) -> None:
    client: TestClient = compare_env["client"]
    store: InMemoryJobStore = compare_env["store"]

    raw_a = _render_sky_fits(date_obs="2025-01-15T00:00:00.000", sky_sources=[], seed=1)
    raw_b = _render_sky_fits(date_obs="2025-06-15T00:00:00.000", sky_sources=[], seed=2)
    raw_far = _render_sky_fits(
        date_obs="2025-06-15T00:00:00.000",
        sky_sources=[],
        dither_ra_deg=2.0,
        seed=3,
    )
    fid_a = _upload_fits(client, "a.fits", raw_a)
    fid_b = _upload_fits(client, "b.fits", raw_b)
    fid_far = _upload_fits(client, "far.fits", raw_far)

    # Header diff only -> no image products or source_changes
    hdr_only = client.post(
        "/api/v1/compare",
        json={"a": fid_a, "b": fid_b, "ops": ["header_diff"]},
    )
    assert hdr_only.status_code == 202
    cid = hdr_only.json()["compare_id"]
    body = client.get(f"/api/v1/compare/{cid}").json()
    assert body["products"] == {}
    assert body["source_changes"] is None
    assert body["header_diff"] is not None
    assert client.get(f"/api/v1/compare/{cid}/products/diff.fits").status_code == 404
    assert client.get(f"/api/v1/compare/{cid}/products/invalid.png").status_code == 404

    # Validation & 404 errors
    assert client.get(f"/api/v1/compare/{uuid4()}").status_code == 404
    assert (
        client.post(
            "/api/v1/compare", json={"a": str(uuid4()), "b": fid_b, "ops": ["diff"]}
        ).status_code
        == 404
    )
    assert (
        client.post("/api/v1/compare", json={"a": fid_a, "b": fid_b, "ops": []}).status_code == 422
    )

    # 409 when upload still queued
    pending_fid = uuid4()
    store.create_or_get_job(
        task=UPLOAD_TASK_NAME,
        queue="fast",
        params={"fits_file_id": str(pending_fid)},
        params_hash=compute_params_hash(UPLOAD_TASK_NAME, {"fits_file_id": str(pending_fid)}),
    )
    assert (
        client.post(
            "/api/v1/compare", json={"a": str(pending_fid), "b": fid_b, "ops": ["diff"]}
        ).status_code
        == 409
    )

    # 409 when comparison job still running
    running_cid = uuid4()
    store.create_or_get_job(
        task=COMPARE_TASK_NAME,
        queue="heavy",
        params={"compare_id": str(running_cid)},
        params_hash=compute_params_hash(COMPARE_TASK_NAME, {"compare_id": str(running_cid)}),
    )
    assert client.get(f"/api/v1/compare/{running_cid}").status_code == 409

    # 502 when frames are disjoint (<20% sky overlap)
    bad_cmp = client.post(
        "/api/v1/compare",
        json={"a": fid_a, "b": fid_far, "ops": ["diff", "header_diff"]},
    )
    assert bad_cmp.status_code == 202
    assert bad_cmp.json()["status"] == "failed"
    fail_res = client.get(f"/api/v1/compare/{bad_cmp.json()['compare_id']}")
    assert fail_res.status_code == 502
    assert "overlap" in fail_res.json()["detail"].lower()
