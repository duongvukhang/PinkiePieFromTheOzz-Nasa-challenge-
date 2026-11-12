"""Offline TestClient tests for auth, API tokens, project workspaces, sharing, and exports."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import numpy as np
import pytest
from astropy.io import fits
from fastapi.testclient import TestClient
from PIL import Image

from app.api.deps import get_cache, get_storage, get_store
from app.main import app
from app.services.cache import MemoryCacheBackend
from app.services.jobs import InMemoryJobStore
from app.services.storage import LocalStorageBackend, compute_sha256


@pytest.fixture()
def workspace_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    store = InMemoryJobStore()
    storage = LocalStorageBackend(tmp_path / "store")
    cache = MemoryCacheBackend()
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    monkeypatch.setenv("SPHX_SESSION_SECRET", "test-session-secret-key")

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_cache] = lambda: cache
    try:
        yield {
            "client": TestClient(app),
            "store": store,
            "storage": storage,
            "cache": cache,
        }
    finally:
        app.dependency_overrides.clear()


def _register_and_login(
    client: TestClient, email: str, password: str = "supersecret123", display_name: str = "Alice"
) -> tuple[str, dict[str, Any]]:
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": display_name},
    )
    assert reg.status_code == 201, reg.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    body = login.json()
    return str(body["access_token"]), body["user"]


def test_auth_register_login_cookie_logout_and_api_tokens(workspace_env: dict[str, Any]) -> None:
    client: TestClient = workspace_env["client"]

    assert client.get("/api/v1/auth/me").status_code == 401

    token, user = _register_and_login(client, "Astronomer@Example.ORG")
    assert user["email"] == "astronomer@example.org"
    assert "password_hash" not in user

    # Duplicate email (case-insensitive) -> 409 RFC 9457
    dup = client.post(
        "/api/v1/auth/register",
        json={"email": "astronomer@example.org", "password": "password123"},
    )
    assert dup.status_code == 409
    assert dup.headers["content-type"].startswith("application/problem+json")

    # Bad password -> 401
    bad = client.post(
        "/api/v1/auth/login",
        json={"email": "astronomer@example.org", "password": "wrong-password"},
    )
    assert bad.status_code == 401

    # Cookie from login authenticates /auth/me without Bearer header
    me_cookie = client.get("/api/v1/auth/me")
    assert me_cookie.status_code == 200
    assert me_cookie.json()["id"] == user["id"]

    # Scoped API tokens: read-only vs read+write
    tok_ro = client.post(
        "/api/v1/auth/tokens",
        json={"name": "cli-ro", "scopes": ["read"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert tok_ro.status_code == 201
    ro_secret = tok_ro.json()["token"]
    ro_id = tok_ro.json()["id"]

    tok_rw = client.post(
        "/api/v1/auth/tokens",
        json={"name": "cli-rw", "scopes": ["read", "write"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert tok_rw.status_code == 201
    rw_secret = tok_rw.json()["token"]

    listed = client.get("/api/v1/auth/tokens", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    assert len(listed.json()) == 2
    assert all("token" not in item for item in listed.json())

    # Read-only API token can read /me and /projects, blocked (403) on POST /projects
    client.cookies.clear()
    ro_hdr = {"Authorization": f"Bearer {ro_secret}"}
    assert client.get("/api/v1/auth/me", headers=ro_hdr).status_code == 200
    assert (
        client.post("/api/v1/projects", json={"name": "Blocked"}, headers=ro_hdr).status_code == 403
    )

    # Read-write API token succeeds on POST /projects
    rw_hdr = {"Authorization": f"Bearer {rw_secret}"}
    p_res = client.post("/api/v1/projects", json={"name": "RW Token Project"}, headers=rw_hdr)
    assert p_res.status_code == 201

    # Revoking read-only token invalidates it
    assert client.delete(f"/api/v1/auth/tokens/{ro_id}", headers=rw_hdr).status_code == 204
    assert client.get("/api/v1/auth/me", headers=ro_hdr).status_code == 401
    assert client.delete(f"/api/v1/auth/tokens/{ro_id}", headers=rw_hdr).status_code == 404

    # Logout revokes session token
    tok_hdr = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/v1/auth/logout", headers=tok_hdr).status_code == 204
    assert client.get("/api/v1/auth/me", headers=tok_hdr).status_code == 401


def test_projects_crud_rbac_sharing_versions_and_consensus_gate(
    workspace_env: dict[str, Any],
) -> None:
    client: TestClient = workspace_env["client"]
    store: InMemoryJobStore = workspace_env["store"]

    tok_a, _ = _register_and_login(client, "alice@example.com")
    client.cookies.clear()
    tok_b, _ = _register_and_login(client, "bob@example.com")
    client.cookies.clear()

    hdr_a = {"Authorization": f"Bearer {tok_a}"}
    hdr_b = {"Authorization": f"Bearer {tok_b}"}

    # Create 3 projects for Alice and test pagination
    pids = []
    for idx in range(3):
        res = client.post(
            "/api/v1/projects",
            json={
                "name": f"WISE 0855 Field {idx}",
                "center_ra": 133.79513,
                "center_dec": -7.24515,
                "fov_arcmin": 10.0,
            },
            headers=hdr_a,
        )
        assert res.status_code == 201
        pids.append(res.json()["id"])

    p1 = client.get("/api/v1/projects?limit=2", headers=hdr_a).json()
    assert len(p1["items"]) == 2 and p1["next_cursor"] is not None
    p2 = client.get(f"/api/v1/projects?limit=2&cursor={p1['next_cursor']}", headers=hdr_a).json()
    assert len(p2["items"]) == 1 and p2["next_cursor"] is None

    pid = pids[0]
    # Private access enforcement
    assert client.get(f"/api/v1/projects/{pid}").status_code == 401
    assert client.get(f"/api/v1/projects/{pid}", headers=hdr_b).status_code == 403
    assert (
        client.patch(f"/api/v1/projects/{pid}", json={"name": "Hijack"}, headers=hdr_b).status_code
        == 403
    )

    # Notes, Saved Views, Findings CRUD
    note = client.post(
        f"/api/v1/projects/{pid}/notes",
        json={"body_md": "Initial observation", "attachments": [{"frame": 1}]},
        headers=hdr_a,
    )
    assert note.status_code == 201
    nid = note.json()["id"]
    assert len(client.get(f"/api/v1/projects/{pid}/notes", headers=hdr_a).json()) == 1
    assert (
        client.patch(
            f"/api/v1/projects/{pid}/notes/{nid}",
            json={"body_md": "Updated note"},
            headers=hdr_a,
        ).json()["body_md"]
        == "Updated note"
    )

    view = client.post(
        f"/api/v1/projects/{pid}/views",
        json={"name": "Blink arr6", "url_state": "ra=133.795&dec=-7.245&bands=arr6"},
        headers=hdr_a,
    )
    assert view.status_code == 201
    vid = view.json()["id"]

    # Consensus Conflict gate on Findings
    summaries = store.select_tracked_objects(
        project_id=UUID(pid),
        analysis_id=uuid4(),
        fits_file_ids=[],
        candidates=[{"ra": 133.795, "dec": -7.245, "mjd_tdb": 60850.0}],
        selection_mode="manual",
    )
    oid = summaries[0].id
    store.save_track_results(
        oid,
        obj_updates={},
        measurements=[],
        associations=[],
        motion_fits=[],
        classification={
            "top_class": "candidate_new_mover",
            "top_prob": 0.6,
            "probabilities": {"candidate_new_mover": 0.6, "artifact": 0.4},
            "evidence": ["E1 vs E3 disagreement"],
            "consensus": {"verdict": "Conflict", "disagreements": ["E1 vs E3"]},
        },
        crossmatches=[],
        caveats=["Estimator conflict"],
    )
    bad_find = client.post(
        f"/api/v1/projects/{pid}/findings",
        json={"tracked_object_id": str(oid), "title": "Mover candidate"},
        headers=hdr_a,
    )
    assert bad_find.status_code == 422
    assert bad_find.headers["content-type"].startswith("application/problem+json")

    good_find = client.post(
        f"/api/v1/projects/{pid}/findings",
        json={
            "tracked_object_id": str(oid),
            "title": "Mover candidate",
            "summary_md": "Checked stamps; E1 residual explained by saturation.",
            "status": "candidate",
        },
        headers=hdr_a,
    )
    assert good_find.status_code == 201
    fid = good_find.json()["id"]
    assert (
        client.patch(
            f"/api/v1/projects/{pid}/findings/{fid}",
            json={"summary_md": "   "},
            headers=hdr_a,
        ).status_code
        == 422
    )

    # Version snapshot v1 -> mutate -> v2 -> restore v1
    v1 = client.post(
        f"/api/v1/projects/{pid}/versions", json={"message": "baseline"}, headers=hdr_a
    )
    assert v1.status_code == 201
    assert v1.json()["version"] == 1

    assert (
        client.patch(
            f"/api/v1/projects/{pid}",
            json={"name": "Mutated Name"},
            headers=hdr_a,
        ).status_code
        == 200
    )
    assert client.delete(f"/api/v1/projects/{pid}/views/{vid}", headers=hdr_a).status_code == 204
    assert client.delete(f"/api/v1/projects/{pid}/notes/{nid}", headers=hdr_a).status_code == 204

    v2 = client.post(f"/api/v1/projects/{pid}/versions", json={"message": "cleaned"}, headers=hdr_a)
    assert v2.status_code == 201 and v2.json()["version"] == 2
    assert len(client.get(f"/api/v1/projects/{pid}/versions", headers=hdr_a).json()) == 2

    restored = client.post(f"/api/v1/projects/{pid}/versions/1/restore", headers=hdr_a)
    assert restored.status_code == 200
    assert restored.json()["name"] == "WISE 0855 Field 0"
    assert restored.json()["current_version"] == 1
    assert len(client.get(f"/api/v1/projects/{pid}/notes", headers=hdr_a).json()) == 1
    assert len(client.get(f"/api/v1/projects/{pid}/views", headers=hdr_a).json()) == 1

    # Share link creation, rotation, and revocation
    sh1 = client.post(f"/api/v1/projects/{pid}/share", headers=hdr_a)
    assert sh1.status_code == 200
    assert sh1.json()["visibility"] == "link"
    tok1 = parse_qs(urlparse(sh1.json()["share_url"]).query)["share_token"][0]
    assert client.get(f"/api/v1/projects/{pid}?share_token={tok1}").status_code == 200
    assert (
        client.get(f"/api/v1/projects/{pid}/notes", headers={"X-Share-Token": tok1}).status_code
        == 200
    )

    sh2 = client.post(f"/api/v1/projects/{pid}/share", headers=hdr_a)
    tok2 = parse_qs(urlparse(sh2.json()["share_url"]).query)["share_token"][0]
    assert client.get(f"/api/v1/projects/{pid}?share_token={tok1}").status_code == 401
    assert client.get(f"/api/v1/projects/{pid}?share_token={tok2}").status_code == 200

    assert client.delete(f"/api/v1/projects/{pid}/share", headers=hdr_a).status_code == 204
    assert client.get(f"/api/v1/projects/{pid}?share_token={tok2}").status_code == 401

    # Public visibility allows anonymous read, blocks anonymous/other-user delete
    assert (
        client.patch(
            f"/api/v1/projects/{pid}",
            json={"visibility": "public"},
            headers=hdr_a,
        ).status_code
        == 200
    )
    assert client.get(f"/api/v1/projects/{pid}").status_code == 200
    assert client.delete(f"/api/v1/projects/{pid}", headers=hdr_b).status_code == 403
    assert client.delete(f"/api/v1/projects/{pid}", headers=hdr_a).status_code == 204
    assert client.get(f"/api/v1/projects/{pid}", headers=hdr_a).status_code == 404


def test_project_exports_all_formats(workspace_env: dict[str, Any]) -> None:
    client: TestClient = workspace_env["client"]
    store: InMemoryJobStore = workspace_env["store"]
    storage: LocalStorageBackend = workspace_env["storage"]

    tok, _ = _register_and_login(client, "exporter@example.com")
    client.cookies.clear()
    hdr = {"Authorization": f"Bearer {tok}"}

    pid = UUID(
        client.post(
            "/api/v1/projects",
            json={
                "name": "WISE 0855 Export",
                "center_ra": 133.79513,
                "center_dec": -7.24515,
                "fov_arcmin": 8.0,
            },
            headers=hdr,
        ).json()["id"]
    )

    # Register two tiny FITS files linked to the project for GIF animation
    fids: list[UUID] = []
    for k in range(2):
        arr = np.full((16, 16), 10.0, dtype=np.float32)
        arr[4 + k * 4 : 7 + k * 4, 4 + k * 4 : 7 + k * 4] = 200.0
        buf = io.BytesIO()
        fits.PrimaryHDU(arr).writeto(buf)
        raw = buf.getvalue()
        fid = uuid4()
        stored = storage.put_bytes(f"uploads/{fid}.fits", raw)
        store.register_fits_file(
            fits_file_id=fid,
            metadata={"origin": "upload", "size_bytes": len(raw), "header_summary": {}},
            sha256_hex=compute_sha256(raw),
            storage_path=stored.key,
            project_id=pid,
        )
        fids.append(fid)

    summaries = store.select_tracked_objects(
        project_id=pid,
        analysis_id=uuid4(),
        fits_file_ids=fids,
        candidates=[{"ra": 133.79513, "dec": -7.24515, "mjd_tdb": 60850.41}],
        selection_mode="box",
    )
    oid = summaries[0].id
    store.save_track_results(
        oid,
        obj_updates={"identity_confidence": 0.98},
        measurements=[
            {
                "epoch": "S1",
                "source": "spherex",
                "mjd_tdb": 60850.41,
                "ra": 133.79513,
                "dec": -7.24515,
                "sig_ra_mas": 120.0,
                "sig_dec_mas": 110.0,
                "band_group": "arr6",
                "flux_ujy": 42.0,
                "flux_err_ujy": 3.0,
                "flags": 0,
            },
            {
                "epoch": "S2",
                "source": "spherex",
                "mjd_tdb": 61031.77,
                "ra": 133.79401,
                "dec": -7.24505,
                "sig_ra_mas": 125.0,
                "sig_dec_mas": 115.0,
                "band_group": "arr6",
                "flux_ujy": 41.5,
                "flux_err_ujy": 3.1,
                "flags": 0,
            },
        ],
        associations=[],
        motion_fits=[
            {
                "model": "linear",
                "ref_epoch_mjd_tdb": 60941.0,
                "params": {"pmra_masyr": -8090.0, "pmdec_masyr": 700.0},
                "pm_total_masyr": 8120.0,
                "pm_total_err": 420.0,
                "is_preferred": True,
            }
        ],
        classification={
            "top_class": "brown_dwarf",
            "top_prob": 0.88,
            "probabilities": {"brown_dwarf": 0.88, "star_pm": 0.12},
            "evidence": ["high PM"],
            "consensus": {"verdict": "Consistent"},
        },
        crossmatches=[],
        caveats=[],
    )

    # Invalid object_id -> 404
    assert (
        client.post(
            f"/api/v1/projects/{pid}/exports",
            json={"format": "csv", "object_ids": [str(uuid4())]},
            headers=hdr,
        ).status_code
        == 404
    )

    for fmt in ("csv", "json", "fits", "png", "gif", "ades"):
        res = client.post(
            f"/api/v1/projects/{pid}/exports",
            json={"format": fmt, "object_ids": [str(oid)]},
            headers=hdr,
        )
        assert res.status_code == 202, res.text
        job_id = res.json()["job_id"]
        assert res.json()["status"] == "succeeded"

        # Idempotent params_hash deduplication returns same job_id
        dup = client.post(
            f"/api/v1/projects/{pid}/exports",
            json={"format": fmt, "object_ids": [str(oid)]},
            headers=hdr,
        )
        assert dup.status_code == 202
        assert dup.json()["job_id"] == job_id

        dl = client.get(f"/api/v1/projects/{pid}/exports/{job_id}", headers=hdr)
        assert dl.status_code == 200
        if fmt == "csv":
            assert "133.79513" in dl.text and "brown_dwarf" in dl.text
        elif fmt == "json":
            data = dl.json()
            assert data["project"]["id"] == str(pid)
            assert len(data["objects"]) == 1
        elif fmt == "fits":
            with fits.open(io.BytesIO(dl.content)) as hdul:
                assert len(hdul["OBJECTS"].data) == 2
        elif fmt == "png":
            assert dl.content.startswith(b"\x89PNG\r\n\x1a\n")
        elif fmt == "gif":
            im = Image.open(io.BytesIO(dl.content))
            assert im.format == "GIF" and getattr(im, "n_frames", 1) == 2
        elif fmt == "ades":
            assert dl.text.startswith("# version=2022")
            assert "|CCD|500|" in dl.text
