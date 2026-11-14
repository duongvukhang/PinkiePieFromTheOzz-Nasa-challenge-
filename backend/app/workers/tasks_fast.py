"""Fast-queue Celery tasks for cutout fetching, WebP/float32 rendering, and provenance."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.time import Time
from PIL import Image

from app.providers.base import Provider, StoredFrame, default_registry
from app.schemas.fields import CutoutFrame, CutoutSetResponse, FieldSummary
from app.services.fields import (
    asinh_stretch_u8,
    extract_2d_image,
    group_frames_into_epochs,
    parse_bands_query,
    parse_utc_query,
)
from app.services.fits_validator import DEFAULT_UPLOAD_MAX_BYTES, validate_fits_upload
from app.services.jobs import JobStore, compute_params_hash, get_job_store
from app.services.storage import StorageBackend, get_storage_backend
from app.workers.celery_app import celery_app
from sphx_core.wcs import healpix9_nested

_FRAME_UUID_NS = UUID("2e75b5fe-3a8a-4e4e-9f7e-2e5bc8d54a01")
CUTOUT_TASK_NAME = "app.workers.tasks_fast.build_cutouts"
UPLOAD_TASK_NAME = "app.workers.tasks_fast.validate_upload"
EXPORT_TASK_NAME = "app.workers.tasks_fast.export_project"


def render_array_products(
    arr: np.ndarray,
    storage: StorageBackend,
    prefix: str,
) -> dict[str, Any]:
    """Render 8-bit asinh-stretched WebP display tile and little-endian float32 science tile."""
    import matplotlib.cm as cm

    arr32 = np.asarray(arr, dtype=np.float32)
    u8, vmin, vmax = asinh_stretch_u8(arr32)
    # Flip vertically so astronomical Y=0 (bottom) displays correctly in web coordinates
    norm_flipped = np.flipud(u8)
    rgba = cm.inferno(norm_flipped.astype(np.float32) / 255.0)
    rgb_bytes = (rgba[:, :, :3] * 255.0).astype(np.uint8)
    webp_buf = io.BytesIO()
    Image.fromarray(rgb_bytes, mode="RGB").save(webp_buf, format="WEBP", quality=92)
    disp_obj = storage.put_bytes(
        f"{prefix}/display.webp", webp_buf.getvalue(), content_type="image/webp"
    )
    sci_obj = storage.put_bytes(
        f"{prefix}/science.bin",
        np.asarray(arr32, dtype="<f4").tobytes(),
        content_type="application/octet-stream",
    )
    return {
        "display_key": disp_obj.key,
        "science_key": sci_obj.key,
        "stretch": {
            "mode": "asinh",
            "percentile": [1.0, 99.5],
            "vmin": vmin,
            "vmax": vmax,
            "shape": list(arr32.shape),
        },
    }


def render_cutout_products(
    fits_bytes: bytes,
    storage: StorageBackend,
    prefix: str,
) -> dict[str, Any]:
    """Render 8-bit asinh-stretched WebP display tile and little-endian float32 science tile."""
    return render_array_products(extract_2d_image(fits_bytes), storage, prefix)


def _build_cutout_frame(
    cutout_id: UUID,
    epoch_label: str,
    stored: StoredFrame,
    rendered: dict[str, Any],
) -> CutoutFrame:
    ref = stored.ref
    frame_id = uuid5(_FRAME_UUID_NS, f"{cutout_id}:{ref.provider}:{ref.obs_id}:{ref.mjd_tdb:.6f}")
    utc_mid = Time(ref.mjd_tdb, format="mjd", scale="tdb").utc.to_datetime(timezone=UTC)
    flagged = float(stored.flags_summary.get("flagged_fraction", 0.0))
    return CutoutFrame(
        frame_id=frame_id,
        epoch=epoch_label,
        mjd_tdb_mid=ref.mjd_tdb,
        utc_mid=utc_mid,
        array_id=ref.array_id,
        band_label=ref.band_id,
        display_url=f"/api/v1/cutouts/{cutout_id}/frames/{frame_id}/display.webp",
        science_url=f"/api/v1/cutouts/{cutout_id}/frames/{frame_id}/science.bin",
        fits_url=f"/api/v1/cutouts/{cutout_id}/frames/{frame_id}/frame.fits",
        wcs_header=stored.wcs_header or "",
        stretch=rendered["stretch"],
        flagged_fraction=min(1.0, max(0.0, flagged)),
    )


def _generate_synthetic_cutouts(
    cutout_id: UUID,
    ra: float,
    dec: float,
    fov: float,
    storage: StorageBackend,
    hpx: int,
    params_hex: str,
    t0: Time | None = None,
    t1: Time | None = None,
) -> tuple[list[CutoutFrame], dict[str, dict[str, str]]]:
    """Generate 3-epoch synthetic FITS cutouts for visualization when live IRSA has no coverage."""
    from app.services.synthetic_fits import InjectedSource, generate_synthetic_sequence

    if t0 is not None or t1 is not None:
        s = float(t0.mjd) if t0 is not None else (float(t1.mjd) - 365.0 if t1 is not None else 60400.0)
        e = float(t1.mjd) if t1 is not None else (s + 365.0)
        if e <= s + 20.0:
            e = s + 30.0
        step = max(10.0, (e - s) / 2.0)
        epochs_mjd = [s, s + step, e]
    else:
        epochs_mjd = [60400.0, 60580.0, 60760.0]

    sources = [
        InjectedSource(
            source_id="T1",
            ra_ref_deg=ra,
            dec_ref_deg=dec,
            ref_epoch_mjd=epochs_mjd[0],
            pmra_arcsec_yr=-0.450,
            pmdec_arcsec_yr=0.320,
            base_flux=1500.0,
            kind="mover",
        ),
        InjectedSource(
            source_id="REF1",
            ra_ref_deg=ra + 0.008,
            dec_ref_deg=dec + 0.008,
            ref_epoch_mjd=epochs_mjd[0],
            base_flux=800.0,
            kind="star",
            gaia_id=1001,
        ),
        InjectedSource(
            source_id="REF2",
            ra_ref_deg=ra - 0.009,
            dec_ref_deg=dec - 0.005,
            ref_epoch_mjd=epochs_mjd[0],
            base_flux=1200.0,
            kind="star",
            gaia_id=1002,
        ),
    ]
    syn_frames = generate_synthetic_sequence(
        seed=int(abs(ra * 1000 + dec * 100)) % 100000,
        sources=sources,
        ra_center_deg=ra,
        dec_center_deg=dec,
        epochs_mjd=epochs_mjd,
        band_id="B6",
    )
    out_frames: list[CutoutFrame] = []
    artifact_keys: dict[str, dict[str, str]] = {}
    for idx, syn in enumerate(syn_frames):
        frame_uuid = uuid5(_FRAME_UUID_NS, f"{cutout_id}:synthetic:{idx}:{syn.mjd_tdb_mid:.6f}")
        fits_key = f"fits/{hpx}/{params_hex}/{frame_uuid}.fits"
        storage.put_bytes(fits_key, syn.fits_bytes, content_type="application/fits")
        rendered = render_cutout_products(
            syn.fits_bytes, storage, f"products/{hpx}/{params_hex}/{frame_uuid}"
        )
        hdr_txt = syn.wcs_reported.to_header(relax=True).tostring()
        epoch_label = f"E{idx + 1}"
        utc_mid = Time(syn.mjd_tdb_mid, format="mjd", scale="tdb").utc.to_datetime(timezone=UTC)
        cframe = CutoutFrame(
            frame_id=frame_uuid,
            epoch=epoch_label,
            mjd_tdb_mid=syn.mjd_tdb_mid,
            utc_mid=utc_mid,
            array_id=syn.array_id,
            band_label=syn.band_id,
            display_url=f"/api/v1/cutouts/{cutout_id}/frames/{frame_uuid}/display.webp",
            science_url=f"/api/v1/cutouts/{cutout_id}/frames/{frame_uuid}/science.bin",
            fits_url=f"/api/v1/cutouts/{cutout_id}/frames/{frame_uuid}/frame.fits",
            wcs_header=hdr_txt,
            stretch=rendered["stretch"],
            flagged_fraction=0.0,
        )
        out_frames.append(cframe)
        artifact_keys[str(frame_uuid)] = {
            "display": str(rendered["display_key"]),
            "science": str(rendered["science_key"]),
            "fits": fits_key,
        }
    return out_frames, artifact_keys


def execute_build_cutouts(
    job_id: UUID,
    params: dict[str, Any],
    *,
    store: JobStore,
    storage: StorageBackend,
    provider: Provider | None = None,
) -> dict[str, Any]:
    """Fetch cutouts for a field, render display/science tiles, and persist job outputs."""
    store.update_job(job_id, status="running", progress=0.05, started_at=datetime.now(UTC))
    try:
        cutout_id = UUID(str(params["cutout_id"]))
        ra = float(params["ra"])
        dec = float(params["dec"])
        fov = float(params["fov_arcmin"])
        release = str(params.get("release", "latest"))
        bands = parse_bands_query(params.get("bands"))
        t0 = parse_utc_query(params.get("from_utc"))
        t1 = parse_utc_query(params.get("to_utc"))

        prov = provider or default_registry.get("spherex_irsa")
        center = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")
        hpx = healpix9_nested(ra, dec)
        try:
            frames = prov.search_frames(
                center, (fov / 2.0) * u.arcmin, t0=t0, t1=t1, bands=bands, release=release
            )
        except Exception:
            frames = []
        store.update_job(job_id, progress=0.20)

        epoch_groups = group_frames_into_epochs(frames)
        # Select candidate frames per epoch (trying top overlapping frames per epoch, max 6 epochs)
        flat: list[tuple[Any, Any]] = []
        for ep, grp in epoch_groups[:6]:
            flat.extend([(ep, ref) for ref in grp[:2]])
        params_hex = compute_params_hash("build_cutouts", params, release).hex()[:16]

        out_frames: list[CutoutFrame] = []
        artifact_keys: dict[str, dict[str, str]] = {}
        total = max(len(flat), 1)
        for idx, (ep, ref) in enumerate(flat):
            try:
                stored = prov.fetch_to_storage(ref, storage, cutout=(center, fov * u.arcmin))
                fits_bytes = storage.get_bytes(stored.storage_path, verify_checksum=False)
                frame_uuid = uuid5(
                    _FRAME_UUID_NS, f"{cutout_id}:{ref.provider}:{ref.obs_id}:{ref.mjd_tdb:.6f}"
                )
                rendered = render_cutout_products(
                    fits_bytes,
                    storage,
                    f"products/{hpx}/{params_hex}/{frame_uuid}",
                )
                cframe = _build_cutout_frame(cutout_id, ep, stored, rendered)
                out_frames.append(cframe)
                artifact_keys[str(cframe.frame_id)] = {
                    "display": str(rendered["display_key"]),
                    "science": str(rendered["science_key"]),
                    "fits": stored.storage_path,
                }
            except Exception:
                continue
            finally:
                store.update_job(job_id, progress=0.20 + 0.75 * ((idx + 1) / total))

        warnings: list[str] = []
        if not out_frames:
            try:
                out_frames, artifact_keys = _generate_synthetic_cutouts(
                    cutout_id, ra, dec, fov, storage, hpx, params_hex, t0=t0, t1=t1
                )
                warnings.append(
                    "Live SPHEREx archive returned no frames; served synthetic field sequence."
                )
            except Exception as syn_err:
                warnings.append(
                    f"No cutouts available for the requested sky coordinates: {syn_err}"
                )

        payload = CutoutSetResponse(
            cutout_id=cutout_id,
            field=FieldSummary(ra=ra, dec=dec, fov_arcmin=fov, healpix9=hpx),
            release=frames[0].release if frames else release,
            frames=out_frames,
            warnings=warnings,
        ).model_dump(mode="json")

        store.record_provenance(
            processing_job_id=job_id,
            entity_table="processing_jobs",
            entity_id=str(job_id),
            inputs=[{"obs_id": ref.obs_id, "release": ref.release} for _, ref in flat],
            release_ids=sorted({ref.release for _, ref in flat}) or None,
            params=params,
        )
        outputs = {"cutout_set": payload, "artifacts": artifact_keys}
        store.update_job(
            job_id,
            status="succeeded",
            progress=1.0,
            outputs=outputs,
            finished_at=datetime.now(UTC),
        )
        return outputs
    except Exception as exc:
        store.update_job(
            job_id,
            status="failed",
            error={"type": type(exc).__name__, "detail": str(exc)},
            finished_at=datetime.now(UTC),
        )
        raise


@celery_app.task(name=CUTOUT_TASK_NAME, queue="fast")  # type: ignore[untyped-decorator]
def build_cutouts(job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Celery task wrapper building sky cutouts with persisted progress updates."""
    return execute_build_cutouts(
        UUID(job_id),
        params,
        store=get_job_store(),
        storage=get_storage_backend(),
    )


def execute_validate_upload(
    job_id: UUID,
    params: dict[str, Any],
    *,
    store: JobStore,
    storage: StorageBackend,
) -> dict[str, Any]:
    """Validate staged FITS upload, persist valid files, and record provenance."""
    store.update_job(job_id, status="running", progress=0.10, started_at=datetime.now(UTC))
    staged_key = str(params["staged_key"])
    try:
        upload_id = UUID(str(params["upload_id"]))
        fits_file_id = UUID(str(params["fits_file_id"]))
        max_bytes = int(params.get("max_bytes", DEFAULT_UPLOAD_MAX_BYTES))
        user_id = UUID(str(params["user_id"])) if params.get("user_id") else None
        project_id = UUID(str(params["project_id"])) if params.get("project_id") else None

        reference: dict[str, Any] | None = None
        if params.get("reference_upload_id"):
            ref_job = store.get_upload_job(UUID(str(params["reference_upload_id"])))
            if ref_job and ref_job.outputs:
                reference = ref_job.outputs.get("fits_file_metadata")
        elif params.get("reference_field"):
            reference = dict(params["reference_field"])

        raw_bytes = storage.get_bytes(staged_key, verify_checksum=True)
        store.update_job(job_id, progress=0.45)

        res = validate_fits_upload(
            raw_bytes,
            reference=reference,
            upload_id=upload_id,
            fits_file_id=fits_file_id,
            max_bytes=max_bytes,
            user_epoch=params.get("user_epoch"),
            storage=storage,
            storage_key=str(params["final_key"]),
        )
        store.update_job(job_id, progress=0.80)

        if res.compatibility.valid and res.storage_path and res.sha256 and res.fits_file_metadata:
            store.register_fits_file(
                fits_file_id=fits_file_id,
                metadata=res.fits_file_metadata,
                sha256_hex=res.sha256,
                storage_path=res.storage_path,
                uploader_id=user_id,
                project_id=project_id,
            )

        store.record_provenance(
            processing_job_id=job_id,
            entity_table="fits_files" if res.compatibility.valid else "processing_jobs",
            entity_id=str(fits_file_id if res.compatibility.valid else job_id),
            inputs=[
                {
                    "filename": params.get("filename"),
                    "payload_sha256": params.get("payload_sha256"),
                    "reference_upload_id": params.get("reference_upload_id"),
                    "reference_field": params.get("reference_field"),
                }
            ],
            params=params,
        )

        outputs = {
            "report": res.report.model_dump(mode="json"),
            "fits_file_metadata": res.fits_file_metadata,
            "storage_path": res.storage_path,
            "sha256": res.sha256,
        }
        store.update_job(
            job_id,
            status="succeeded",
            progress=1.0,
            outputs=outputs,
            finished_at=datetime.now(UTC),
        )
        return outputs
    except Exception as exc:
        store.update_job(
            job_id,
            status="failed",
            error={"type": type(exc).__name__, "detail": str(exc)},
            finished_at=datetime.now(UTC),
        )
        raise
    finally:
        storage.delete(staged_key)


@celery_app.task(name=UPLOAD_TASK_NAME, queue="fast")  # type: ignore[untyped-decorator]
def validate_upload(job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Celery task wrapper validating an uploaded FITS file."""
    return execute_validate_upload(
        UUID(job_id),
        params,
        store=get_job_store(),
        storage=get_storage_backend(),
    )


def execute_export_project(
    job_id: UUID,
    params: dict[str, Any],
    *,
    store: JobStore,
    storage: StorageBackend,
) -> dict[str, Any]:
    """Render project export artifact (CSV/JSON/FITS/PNG/GIF/ADES) to object storage."""
    from app.services.exports import build_project_export

    store.update_job(job_id, status="running", progress=0.1, started_at=datetime.now(UTC))
    try:
        project_id = UUID(str(params["project_id"]))
        fmt = str(params["format"])
        obj_ids = [UUID(str(o)) for o in params["object_ids"]] if params.get("object_ids") else None
        proj = store.get_project(project_id)
        if proj is None:
            raise KeyError(f"Project not found: {project_id}")
        data, ext, ctype = build_project_export(
            project_id=project_id,
            fmt=fmt,
            object_ids=obj_ids,
            store=store,
            storage=storage,
        )
        store.update_job(job_id, progress=0.8)
        ver = int(proj.get("current_version", 1))
        stored = storage.put_bytes(
            f"exports/{project_id}/{ver}/{job_id}.{ext}", data, content_type=ctype
        )
        filename = f"project-{project_id}-v{ver}.{ext}"
        outputs = {
            "export_id": str(job_id),
            "project_id": str(project_id),
            "format": fmt,
            "storage_key": stored.key,
            "filename": filename,
            "content_type": ctype,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
            "download_url": f"/api/v1/projects/{project_id}/exports/{job_id}",
        }
        store.record_provenance(
            processing_job_id=job_id,
            entity_table="projects",
            entity_id=str(project_id),
            inputs=[{"project_id": str(project_id), "version": ver, "format": fmt}],
            params=params,
        )
        store.update_job(
            job_id,
            status="succeeded",
            progress=1.0,
            outputs=outputs,
            finished_at=datetime.now(UTC),
        )
        return outputs
    except Exception as exc:
        store.update_job(
            job_id,
            status="failed",
            error={"type": type(exc).__name__, "detail": str(exc)},
            finished_at=datetime.now(UTC),
        )
        raise


@celery_app.task(name=EXPORT_TASK_NAME, queue="fast")  # type: ignore[untyped-decorator]
def export_project(job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Celery task wrapper exporting project workspace artifacts."""
    return execute_export_project(
        UUID(job_id),
        params,
        store=get_job_store(),
        storage=get_storage_backend(),
    )
