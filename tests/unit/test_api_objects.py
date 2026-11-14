"""Offline TestClient API tests for tracked objects, tracks, associations, SED, and lightcurves."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import astropy.units as u
import pytest
from astropy.coordinates import SkyCoord
from astropy.time import Time
from fastapi.testclient import TestClient
from test_api_analyses import (
    _DEC0,
    _RA0,
    _FakeCatalog,
    _FakeSpherex,
    _make_wcs,
    _render_sky_fits,
    _upload_fits,
)

from app.api.deps import (
    get_cache,
    get_gaia_provider,
    get_mpc_provider,
    get_spherex_provider,
    get_storage,
    get_store,
    get_wise_provider,
)
from app.db.flags import DetectionFlag
from app.main import app
from app.providers.base import CatalogSource
from app.services.cache import MemoryCacheBackend
from app.services.jobs import InMemoryJobStore
from app.services.storage import LocalStorageBackend


@pytest.fixture()
def objects_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    store = InMemoryJobStore()
    storage = LocalStorageBackend(tmp_path / "store")
    cache = MemoryCacheBackend()
    gaia = _FakeCatalog("gaia")
    wise = _FakeCatalog("wise")
    mpc = _FakeCatalog("mpc")
    spherex = _FakeSpherex()
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_cache] = lambda: cache
    app.dependency_overrides[get_gaia_provider] = lambda: gaia
    app.dependency_overrides[get_wise_provider] = lambda: wise
    app.dependency_overrides[get_mpc_provider] = lambda: mpc
    app.dependency_overrides[get_spherex_provider] = lambda: spherex
    try:
        yield {
            "client": TestClient(app),
            "store": store,
            "gaia": gaia,
            "wise": wise,
            "mpc": mpc,
        }
    finally:
        app.dependency_overrides.clear()


def test_select_track_override_spectrum_and_lightcurve_flow(
    objects_env: dict[str, Any],
) -> None:
    client: TestClient = objects_env["client"]
    gaia: _FakeCatalog = objects_env["gaia"]
    base_wcs = _make_wcs(_RA0, _DEC0)

    # 5 Gaia reference stars + 1 neighbor star near mover + 1 mover (+2.0 px/yr E over 3 epochs)
    ref_px = [(14.0, 14.0), (50.0, 14.0), (14.0, 50.0), (50.0, 50.0), (20.0, 40.0)]
    static_sky: list[tuple[float, float, float]] = []
    for idx, (px, py) in enumerate(ref_px):
        ra_s, dec_s = (float(v) for v in base_wcs.pixel_to_world_values(px, py))
        static_sky.append((ra_s % 360.0, dec_s, 140.0))
        gaia.rows.append(
            CatalogSource(
                provider="gaia",
                catalog="gaiadr3",
                source_id=str(3000 + idx),
                ra_deg=ra_s % 360.0,
                dec_deg=dec_s,
                epoch=Time(2016.0, format="jyear", scale="tdb"),
                mag=14.5,
                mag_band="G",
            )
        )

    # Distractor neighbor 2.5 px north of mover at epoch 1 for alternative association testing
    nbr_ra, nbr_dec = (float(v) for v in base_wcs.pixel_to_world_values(32.0, 32.5))
    static_sky.append((nbr_ra % 360.0, nbr_dec, 90.0))

    m_coords = [
        tuple(float(v) for v in base_wcs.pixel_to_world_values(30.0 + 1.5 * k, 30.0))
        for k in range(3)
    ]
    dates = [
        "2025-01-15T00:00:00.000",
        "2025-07-15T00:00:00.000",
        "2026-01-15T00:00:00.000",
    ]
    bands = [("arr5", (3.8, 4.2)), ("arr6", (4.2, 5.0)), ("arr6", (4.2, 5.0))]
    fids: list[str] = []
    for idx, (date_s, (m_ra, m_dec), (bname, wlen)) in enumerate(
        zip(dates, m_coords, bands, strict=True)
    ):
        raw = _render_sky_fits(
            date_obs=date_s,
            sky_sources=[*static_sky, (m_ra % 360.0, m_dec, 125.0)],
            band=bname,
            wavelength_um=wlen,
            seed=40 + idx,
        )
        fids.append(_upload_fits(client, f"ep{idx}.fits", raw))

    an_res = client.post("/api/v1/analyses", json={"fits_file_ids": fids})
    assert an_res.status_code == 202, an_res.text
    analysis_id = an_res.json()["analysis_id"]

    project_id = str(uuid4())
    # 1. Select mover via box at epoch S1 -> persistent label "A"
    m0_ra, m0_dec = m_coords[0][0] % 360.0, m_coords[0][1]
    sel_a = client.post(
        f"/api/v1/projects/{project_id}/objects",
        json={
            "analysis_id": analysis_id,
            "mode": "box",
            "epoch": "S1",
            "box": {
                "ra_min": m0_ra - 0.002,
                "ra_max": m0_ra + 0.002,
                "dec_min": m0_dec - 0.002,
                "dec_max": m0_dec + 0.002,
            },
        },
    )
    assert sel_a.status_code == 201, sel_a.text
    objs_a = sel_a.json()["objects"]
    assert len(objs_a) == 1
    obj_a_id = objs_a[0]["id"]
    assert objs_a[0]["label"] == "A"
    assert objs_a[0]["selection_mode"] == "box"
    assert objs_a[0]["flux_ujy"]

    # Re-selecting same detection returns persistent label "A"
    sel_dup = client.post(
        f"/api/v1/projects/{project_id}/objects",
        json={
            "analysis_id": analysis_id,
            "mode": "box",
            "epoch": "S1",
            "box": {
                "ra_min": m0_ra - 0.002,
                "ra_max": m0_ra + 0.002,
                "dec_min": m0_dec - 0.002,
                "dec_max": m0_dec + 0.002,
            },
        },
    )
    assert sel_dup.status_code == 201
    assert sel_dup.json()["objects"][0]["id"] == obj_a_id
    assert sel_dup.json()["objects"][0]["label"] == "A"

    # 2. Select neighbor star via catalog/box -> label "B", plus manual empty-sky -> label "C"
    nbr_ra_n = nbr_ra % 360.0
    sel_b = client.post(
        f"/api/v1/projects/{project_id}/objects",
        json={
            "analysis_id": analysis_id,
            "mode": "catalog",
            "epoch": "S1",
            "box": {
                "ra_min": nbr_ra_n - 0.0015,
                "ra_max": nbr_ra_n + 0.0015,
                "dec_min": nbr_dec - 0.0015,
                "dec_max": nbr_dec + 0.0015,
            },
        },
    )
    assert sel_b.status_code == 201
    assert sel_b.json()["objects"][0]["label"] == "B"

    empty_ra, empty_dec = (float(v) for v in base_wcs.pixel_to_world_values(8.0, 8.0))
    empty_ra %= 360.0
    sel_c = client.post(
        f"/api/v1/projects/{project_id}/objects",
        json={
            "analysis_id": analysis_id,
            "mode": "manual",
            "epoch": "S1",
            "box": {
                "ra_min": empty_ra - 0.0005,
                "ra_max": empty_ra + 0.0005,
                "dec_min": empty_dec - 0.0005,
                "dec_max": empty_dec + 0.0005,
            },
        },
    )
    assert sel_c.status_code == 201
    assert sel_c.json()["objects"][0]["label"] == "C"

    # 3. Run tracking on object A
    track_req = {
        "object_ids": [obj_a_id],
        "models": ["linear", "astrometric5"],
        "run_classification": True,
    }
    tr_res = client.post(f"/api/v1/projects/{project_id}/tracks", json=track_req)
    assert tr_res.status_code == 202, tr_res.text
    assert tr_res.json()["status"] == "succeeded", (
        client.get(f"/api/v1/jobs/{tr_res.json()['job_id']}").json().get("error")
    )

    # Repeated call deduplicates
    tr_dup = client.post(f"/api/v1/projects/{project_id}/tracks", json=track_req)
    assert tr_dup.json()["job_id"] == tr_res.json()["job_id"]

    # 4. Inspect full tracked object dossier
    det_res = client.get(f"/api/v1/objects/{obj_a_id}")
    assert det_res.status_code == 200, det_res.text
    detail = det_res.json()
    assert detail["label"] == "A"
    assert detail["public_id"].startswith("SPHX J")
    assert len(detail["measurements"]) >= 2
    assert all(m["epoch"] is not None for m in detail["measurements"])
    mfit = detail["motion_fit"]
    assert mfit is not None
    # ~3.0 px/yr * 6.2"/px = 18.6"/yr = 18600 mas/yr West
    assert mfit["pm_total_masyr"] > 10000.0
    assert mfit["chi2_red"] is not None
    assert detail["classification"] is not None
    assert detail["classification"]["top_class"]
    assert detail["classification"]["consensus"] is not None

    # 5. Inspect associations & alternatives
    assoc_res = client.get(f"/api/v1/objects/{obj_a_id}/associations")
    assert assoc_res.status_code == 200
    assocs = assoc_res.json()["associations"]
    assert len(assocs) >= 3
    assert any(a["is_chosen"] for a in assocs)
    alts = [a for a in assocs if not a["is_chosen"] and a["detection_id"] is not None]
    assert alts

    # Find neighbor detection ID in epoch S2 from analysis sources
    sources_s2 = client.get(
        f"/api/v1/analyses/{analysis_id}/sources",
        params={
            "bbox": f"{nbr_ra_n - 0.002},{nbr_dec - 0.002},{nbr_ra_n + 0.002},{nbr_dec + 0.002}"
        },
    ).json()["items"]
    assert sources_s2
    alt_det_id = sources_s2[0]["id"]

    # 6. Manual override (reassign S2 -> accept S1 -> reject S3) triggers refit
    ov_res = client.patch(
        f"/api/v1/objects/{obj_a_id}/associations/S2",
        json={"action": "reassign", "detection_id": alt_det_id, "reason": "visual: swap to blob"},
    )
    assert ov_res.status_code == 200, ov_res.text
    assert ov_res.json()["decided_by"] == "user"
    assert ov_res.json()["refit_job_id"]

    assocs_after = client.get(f"/api/v1/objects/{obj_a_id}/associations").json()["associations"]
    user_rows = [a for a in assocs_after if a["decided_by"] == "user"]
    assert user_rows
    assert any(
        a["is_chosen"]
        and a["detection_id"] == alt_det_id
        and (a["flags"] & int(DetectionFlag.USER_OVERRIDE))
        for a in user_rows
    )

    acc_res = client.patch(
        f"/api/v1/objects/{obj_a_id}/associations/S1",
        json={"action": "accept", "reason": "confirmed visually"},
    )
    assert acc_res.status_code == 200

    rej_res = client.patch(
        f"/api/v1/objects/{obj_a_id}/associations/S3",
        json={"action": "reject", "reason": "cosmic ray contaminated"},
    )
    assert rej_res.status_code == 200

    # 7. On-the-fly forced-PSF spectrum (full + single epoch)
    spec_res = client.get(f"/api/v1/objects/{obj_a_id}/spectrum")
    assert spec_res.status_code == 200, spec_res.text
    spec = spec_res.json()
    assert spec["method"] == "forced_psf"
    assert len(spec["wavelength_um"]) == 3
    assert len(spec["flux_ujy"]) == 3
    assert spec["wavelength_um"] == sorted(spec["wavelength_um"])

    spec_s1 = client.get(f"/api/v1/objects/{obj_a_id}/spectrum", params={"epoch": "S1"})
    assert spec_s1.status_code == 200
    assert len(spec_s1.json()["wavelength_um"]) == 1

    # 8. Lightcurve endpoint
    lc_res = client.get(f"/api/v1/objects/{obj_a_id}/lightcurve")
    assert lc_res.status_code == 200
    lc = lc_res.json()
    assert len(lc["points"]) >= 2
    assert lc["variability_indices"]["n_epochs"] >= 2.0


def test_variable_star_suggestion_tracking_and_lightcurve(objects_env: dict[str, Any]) -> None:
    client: TestClient = objects_env["client"]
    gaia: _FakeCatalog = objects_env["gaia"]
    base_wcs = _make_wcs(_RA0, _DEC0)

    star_px = [(15.0, 15.0), (48.0, 15.0), (15.0, 48.0), (48.0, 48.0), (20.0, 38.0)]
    static_sky: list[tuple[float, float, float]] = []
    for idx, (px, py) in enumerate(star_px):
        ra_s, dec_s = (float(v) for v in base_wcs.pixel_to_world_values(px, py))
        static_sky.append((ra_s % 360.0, dec_s, 140.0))
        gaia.rows.append(
            CatalogSource(
                provider="gaia",
                catalog="gaiadr3",
                source_id=str(4000 + idx),
                ra_deg=ra_s % 360.0,
                dec_deg=dec_s,
                epoch=Time(2016.0, format="jyear", scale="tdb"),
                mag=14.5,
                mag_band="G",
            )
        )

    var_ra, var_dec = (float(v) for v in base_wcs.pixel_to_world_values(32.0, 32.0))
    var_ra %= 360.0
    gaia.rows.append(
        CatalogSource(
            provider="gaia",
            catalog="gaiadr3",
            source_id="4999",
            ra_deg=var_ra,
            dec_deg=var_dec,
            epoch=Time(2016.0, format="jyear", scale="tdb"),
            mag=14.0,
            mag_band="G",
        )
    )
    f0 = _upload_fits(
        client,
        "var0.fits",
        _render_sky_fits(
            date_obs="2025-03-01T00:00:00.000",
            sky_sources=[*static_sky, (var_ra, var_dec, 40.0)],
            seed=71,
        ),
    )
    f1 = _upload_fits(
        client,
        "var1.fits",
        _render_sky_fits(
            date_obs="2025-09-01T00:00:00.000",
            sky_sources=[*static_sky, (var_ra, var_dec, 240.0)],
            seed=72,
        ),
    )

    an = client.post("/api/v1/analyses", json={"fits_file_ids": [f0, f1]}).json()
    aid = an["analysis_id"]
    sugs = client.get(f"/api/v1/analyses/{aid}/suggestions").json()["suggestions"]
    var_c = SkyCoord(var_ra * u.deg, var_dec * u.deg, frame="icrs")
    var_sug = next(
        s
        for s in sugs
        if var_c.separation(SkyCoord(s["ra"] * u.deg, s["dec"] * u.deg, frame="icrs")).arcsec < 8.0
    )

    pid = str(uuid4())
    sel = client.post(
        f"/api/v1/projects/{pid}/objects",
        json={"analysis_id": aid, "mode": "suggestion", "detection_ids": [var_sug["detection_id"]]},
    )
    assert sel.status_code == 201
    oid = sel.json()["objects"][0]["id"]

    tr = client.post(f"/api/v1/projects/{pid}/tracks", json={"models": ["linear"]})
    assert tr.status_code == 202
    assert tr.json()["status"] == "succeeded", (
        client.get(f"/api/v1/jobs/{tr.json()['job_id']}").json().get("error")
    )

    lc = client.get(f"/api/v1/objects/{oid}/lightcurve").json()
    assert len(lc["points"]) == 2
    assert lc["variability_indices"]["chi2_var"] > 10.0
    assert lc["variability_indices"]["amplitude_mag"] > 1.5

    det = client.get(f"/api/v1/objects/{oid}").json()
    assert det["motion_fit"]["params"]["is_mover"] is False


def test_objects_error_states(objects_env: dict[str, Any]) -> None:
    client: TestClient = objects_env["client"]
    missing = uuid4()
    pid = uuid4()

    assert client.get(f"/api/v1/objects/{missing}").status_code == 404
    assert client.get(f"/api/v1/objects/{missing}/associations").status_code == 404
    assert client.get(f"/api/v1/objects/{missing}/spectrum").status_code == 404
    assert client.get(f"/api/v1/objects/{missing}/lightcurve").status_code == 404
    assert (
        client.patch(
            f"/api/v1/objects/{missing}/associations/S1",
            json={"action": "accept", "reason": "ok"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/projects/{pid}/objects",
            json={"analysis_id": str(missing), "mode": "box"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/projects/{pid}/tracks",
            json={"models": ["bogus_model"]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/projects/{pid}/tracks",
            json={"models": ["linear"]},
        ).status_code
        == 422
    )


def test_select_analysis_objects_uuid5_and_predict_ephemeris_covariance(
    objects_env: dict[str, Any],
) -> None:
    client: TestClient = objects_env["client"]
    store: InMemoryJobStore = objects_env["store"]

    f0 = _upload_fits(
        client,
        "f0.fits",
        _render_sky_fits(
            date_obs="2025-01-01T00:00:00.000",
            sky_sources=[(_RA0, _DEC0, 100.0)],
            seed=1,
        ),
    )
    f1 = _upload_fits(
        client,
        "f1.fits",
        _render_sky_fits(
            date_obs="2025-07-01T00:00:00.000",
            sky_sources=[(_RA0 + 0.001, _DEC0 + 0.001, 100.0)],
            seed=2,
        ),
    )
    an_res = client.post("/api/v1/analyses", json={"fits_file_ids": [f0, f1]}).json()
    aid = an_res["analysis_id"]

    sel = client.post(
        f"/api/v1/analyses/{aid}/select",
        json={
            "mode": "box",
            "box": {
                "ra_min": _RA0 - 0.01,
                "ra_max": _RA0 + 0.01,
                "dec_min": _DEC0 - 0.01,
                "dec_max": _DEC0 + 0.01,
            },
        },
    )
    assert sel.status_code == 201
    objs = sel.json()["objects"]
    assert len(objs) >= 1
    oid = objs[0]["id"]

    cov_5x5 = [0.01 if i == j else 0.0 for i in range(5) for j in range(5)]
    fit_rec = {
        "model": "linear",
        "ref_epoch_mjd_tdb": 60800.0,
        "params": {
            "ra_deg": _RA0,
            "dec_deg": _DEC0,
            "pmra_masyr": 500.0,
            "pmdec_masyr": 200.0,
            "parallax_mas": 50.0,
        },
        "covariance": cov_5x5,
        "bic": 12.0,
    }
    store._motion_fits[UUID(str(oid))] = [fit_rec]

    eph_res = client.post(
        f"/api/v1/objects/{oid}/ephemeris",
        json={"start_mjd": 60800.0, "extrapolation_days": 30.0, "steps": 5},
    )
    assert eph_res.status_code == 200
    traj = eph_res.json()["trajectory"]
    assert len(traj) == 6
    assert traj[0]["ra_deg"] == pytest.approx(_RA0, abs=1e-3)
    assert traj[-1]["uncertainty_semi_major_arcsec"] > 0.0

