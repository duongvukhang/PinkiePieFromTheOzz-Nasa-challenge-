"""Analyses, tracked objects, associations, spectra, and lightcurves."""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Any
from uuid import UUID, uuid4

import astropy.units as u
from astropy.coordinates import SkyCoord
import numpy as np
from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    APIProblemError,
    enforce_fov_quota,
    get_gaia_provider,
    get_mpc_provider,
    get_optional_user_id,
    get_spherex_provider,
    get_storage,
    get_store,
    get_wise_provider,
    paginate_items,
    require_succeeded_job,
)
from app.providers import GaiaProvider, MpcProvider, SpherexIrsaProvider, WiseProvider
from app.schemas.common import JobAcceptedResponse, JobStatus, PaginatedResponse, ProblemDetail
from app.schemas.objects import (
    AnalysisAcceptedResponse,
    AnalysisCreateRequest,
    AssociationOverrideRequest,
    AssociationOverrideResponse,
    DetectionRead,
    EphemerisPredictionPointSchema,
    EphemerisRequest,
    EphemerisResponse,
    LightcurveResponse,
    MotionFitRead,
    ObjectAssociationsResponse,
    SpectrumResponse,
    SuggestionItem,
    SuggestionsResponse,
    TrackedObjectDetailResponse,
    TrackedObjectSelectRequest,
    TrackedObjectSelectResponse,
    TrackRunRequest,
    TrajectoryRecomputeRequest,
    TrajectoryRecomputeResponse,
)
from app.services.jobs import JobStore, compute_params_hash
from app.services.storage import StorageBackend
from app.services.tracking import (
    build_object_lightcurve,
    compute_object_spectrum,
    in_sky_box,
    resolve_epoch_observation,
)
from app.workers.celery_app import dispatch_task, is_eager_mode
from app.workers.tasks_fast import UPLOAD_TASK_NAME
from app.workers.tasks_heavy import (
    ANALYSIS_TASK_NAME,
    TRACKS_TASK_NAME,
    execute_run_analysis,
    execute_run_tracks,
    run_analysis,
    run_tracks,
)
from sphx_core.calib import HARD_BAD_MASK, band_group_for_wavelength
from sphx_core.orbits import predict_ephemeris

_ALLOWED_MOTION_MODELS = frozenset(
    {"static", "linear", "astrometric5", "astrometric5_prior", "keplerian", "shift_stack"}
)

router = APIRouter()
_ANALYSIS_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ProblemDetail},
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
    502: {"model": ProblemDetail},
}


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise APIProblemError(
            422,
            "Invalid Bounding Box",
            "bbox must be 'ra_min,dec_min,ra_max,dec_max' in ICRS degrees.",
            error_type="https://spherex.example/problems/invalid-bbox",
        )
    try:
        ra_min, dec_min, ra_max, dec_max = (float(p) for p in parts)
    except ValueError as exc:
        raise APIProblemError(
            422,
            "Invalid Bounding Box",
            f"bbox coordinates must be numeric: {exc}",
            error_type="https://spherex.example/problems/invalid-bbox",
        ) from exc
    if not all(math.isfinite(v) for v in (ra_min, dec_min, ra_max, dec_max)):
        raise APIProblemError(422, "Invalid Bounding Box", "bbox coordinates must be finite.")
    if not (0.0 <= ra_min < 360.0 and 0.0 <= ra_max <= 360.0):
        raise APIProblemError(422, "Invalid Bounding Box", "RA bounds must lie in [0, 360].")
    if not (-90.0 <= dec_min <= dec_max <= 90.0):
        raise APIProblemError(
            422, "Invalid Bounding Box", "Dec bounds must satisfy -90 <= dec_min <= dec_max <= 90."
        )
    return ra_min, dec_min, ra_max, dec_max


@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["analyses"],
    responses=_ANALYSIS_RESPONSES,
)
async def create_analysis(
    payload: AnalysisCreateRequest,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    spherex: Annotated[SpherexIrsaProvider, Depends(get_spherex_provider)],
    gaia: Annotated[GaiaProvider, Depends(get_gaia_provider)],
    wise: Annotated[WiseProvider, Depends(get_wise_provider)],
    mpc: Annotated[MpcProvider, Depends(get_mpc_provider)],
    user_id: Annotated[UUID | None, Depends(get_optional_user_id)] = None,
) -> AnalysisAcceptedResponse:
    """Run registration, source extraction, and object suggestion on a frame set."""
    has_files = payload.fits_file_ids is not None
    has_cutout = payload.cutout_id is not None
    has_sky = payload.ra is not None or payload.dec is not None
    mode_count = sum((has_files, has_cutout, has_sky))
    if mode_count == 0:
        raise APIProblemError(
            422,
            "Missing Analysis Input",
            "Provide fits_file_ids, cutout_id, or ra/dec coordinates.",
        )
    if mode_count > 1:
        raise APIProblemError(
            422,
            "Ambiguous Analysis Input",
            "Provide exactly one of fits_file_ids, cutout_id, or ra/dec.",
        )

    release_key = payload.release or ""
    if has_files:
        assert payload.fits_file_ids is not None
        unique_ids = list(dict.fromkeys(payload.fits_file_ids))
        if not unique_ids:
            raise APIProblemError(422, "Empty Frame Set", "fits_file_ids must not be empty.")
        for fid in unique_ids:
            if store.get_fits_file(fid) is None:
                up_job = store.get_job_by_param(UPLOAD_TASK_NAME, "fits_file_id", str(fid))
                if up_job is not None and up_job.status in {"queued", "running"}:
                    raise APIProblemError(
                        409,
                        "Upload Validation In Progress",
                        f"FITS file {fid} upload job {up_job.id} is {up_job.status}.",
                    )
                raise APIProblemError(404, "FITS File Not Found", f"No FITS file found for {fid}.")
        norm_params: dict[str, Any] = {
            "mode": "fits_files",
            "fits_file_ids": sorted(str(fid) for fid in unique_ids),
        }
        job_params: dict[str, Any] = {
            "mode": "fits_files",
            "fits_file_ids": [str(fid) for fid in unique_ids],
        }
    elif has_cutout:
        assert payload.cutout_id is not None
        require_succeeded_job(
            store.get_cutout_job(payload.cutout_id),
            payload.cutout_id,
            label="Cutout Set",
            output_key="cutout_set",
        )
        norm_params = {"mode": "cutout", "cutout_id": str(payload.cutout_id)}
        job_params = dict(norm_params)
    else:
        if payload.ra is None or payload.dec is None:
            raise APIProblemError(
                422, "Incomplete Sky Coordinates", "Both ra and dec are required together."
            )
        fov = float(payload.fov_arcmin or 10.0)
        enforce_fov_quota(fov, user_id)
        release = payload.release or "latest"
        release_key = release
        norm_params = {
            "mode": "sky_field",
            "ra": float(payload.ra),
            "dec": float(payload.dec),
            "fov_arcmin": fov,
            "release": release,
        }
        job_params = dict(norm_params)

    analysis_id = uuid4()
    job_params["analysis_id"] = str(analysis_id)
    job, deduplicated = store.create_or_get_job(
        task=ANALYSIS_TASK_NAME,
        queue="heavy",
        params=job_params,
        params_hash=compute_params_hash(ANALYSIS_TASK_NAME, norm_params, release_key),
        user_id=user_id,
    )
    if deduplicated:
        existing = store.get_job_params(job.id) or {}
        analysis_id = UUID(
            str(
                (job.outputs or {}).get("analysis_id") or existing.get("analysis_id") or analysis_id
            )
        )
        job_status: JobStatus = job.status
    else:
        if is_eager_mode():
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    execute_run_analysis,
                    job.id,
                    job_params,
                    store=store,
                    storage=storage,
                    spherex_provider=spherex,
                    gaia_provider=gaia,
                    wise_provider=wise,
                    mpc_provider=mpc,
                )
        else:
            dispatch_task(run_analysis, str(job.id), job_params, queue="heavy")
        latest = store.get_job(job.id)
        job_status = latest.status if latest else job.status

    return AnalysisAcceptedResponse(job_id=job.id, status=job_status, analysis_id=analysis_id)


@router.get(
    "/analyses/{analysis_id}/sources",
    tags=["analyses"],
    responses=_ANALYSIS_RESPONSES,
)
async def list_analysis_sources(
    analysis_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
    bbox: Annotated[
        str | None, Query(description="ICRS 'ra_min,dec_min,ra_max,dec_max' (deg)")
    ] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> PaginatedResponse[DetectionRead]:
    """List extracted sources within optional ICRS bounding box."""
    job = require_succeeded_job(
        store.get_analysis_job(analysis_id),
        analysis_id,
        label="Analysis",
        output_key="detection_ids",
    )
    assert job.outputs is not None
    detections = store.get_detections_by_ids([int(i) for i in job.outputs["detection_ids"]])
    if bbox is not None:
        ra_min, dec_min, ra_max, dec_max = _parse_bbox(bbox)
        detections = [
            d for d in detections if in_sky_box(d.ra, d.dec, ra_min, ra_max, dec_min, dec_max)
        ]
    page, next_cursor = paginate_items(detections, cursor, limit)
    return PaginatedResponse[DetectionRead](items=page, next_cursor=next_cursor)


@router.get(
    "/analyses/{analysis_id}/suggestions",
    tags=["analyses"],
    responses=_ANALYSIS_RESPONSES,
)
async def list_analysis_suggestions(
    analysis_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
) -> SuggestionsResponse:
    """List ranked moving/anomalous object suggestions with reasons."""
    job = require_succeeded_job(
        store.get_analysis_job(analysis_id),
        analysis_id,
        label="Analysis",
        output_key="suggestions",
    )
    assert job.outputs is not None
    items = [SuggestionItem.model_validate(s) for s in job.outputs["suggestions"]]
    return SuggestionsResponse(analysis_id=analysis_id, suggestions=items)


def _det_band_group(store: JobStore, det: DetectionRead) -> str | None:
    frec = store.get_fits_file(det.fits_file_id)
    if frec is None:
        return None
    if frec.get("band_label"):
        return str(frec["band_label"])
    lmin, lmax = frec.get("wavelength_min_um"), frec.get("wavelength_max_um")
    if lmin is not None and lmax is not None:
        return band_group_for_wavelength((float(lmin), float(lmax)))
    return None


def _require_object_bundle(store: JobStore, object_id: UUID) -> dict[str, Any]:
    bundle = store.get_tracked_object_bundle(object_id)
    if bundle is None:
        raise APIProblemError(404, "Tracked Object Not Found", f"No object found for {object_id}.")
    return bundle


def _resolve_detection_ids(
    detection_ids: Sequence[int], det_by_id: Mapping[int, DetectionRead]
) -> list[DetectionRead]:
    out: list[DetectionRead] = []
    for did in detection_ids:
        if did not in det_by_id:
            raise APIProblemError(404, "Detection Not Found", f"Detection {did} not found.")
        out.append(det_by_id[did])
    return out


def _cluster_box_detections(
    dets: list[DetectionRead], radius_arcsec: float = 5.0
) -> list[DetectionRead]:
    """Deduplicate multi-epoch detections of the same sky source inside a drag box."""
    ordered = sorted(dets, key=lambda d: (-(d.snr or 0.0), d.id))
    kept: list[DetectionRead] = []
    kept_coords: list[SkyCoord] = []
    for d in ordered:
        c = SkyCoord(ra=d.ra * u.deg, dec=d.dec * u.deg, frame="icrs")
        if any(float(c.separation(kc).arcsec) <= radius_arcsec for kc in kept_coords):
            continue
        kept.append(d)
        kept_coords.append(c)
    return kept


async def _enqueue_tracks_job(
    *,
    project_id: UUID,
    object_ids: list[UUID],
    models: list[str],
    run_classification: bool,
    override_rev: int = 0,
    user_id: UUID | None = None,
    store: JobStore,
    storage: StorageBackend,
    gaia: GaiaProvider,
    wise: WiseProvider,
    mpc: MpcProvider,
) -> tuple[UUID, JobStatus]:
    norm_params: dict[str, Any] = {
        "project_id": str(project_id),
        "object_ids": sorted(str(oid) for oid in object_ids),
        "models": list(models),
        "run_classification": run_classification,
        "override_rev": override_rev,
    }
    job_params: dict[str, Any] = {
        "project_id": str(project_id),
        "object_ids": [str(oid) for oid in object_ids],
        "models": list(models),
        "run_classification": run_classification,
    }
    job, deduplicated = store.create_or_get_job(
        task=TRACKS_TASK_NAME,
        queue="heavy",
        params=job_params,
        params_hash=compute_params_hash(TRACKS_TASK_NAME, norm_params),
        user_id=user_id,
        project_id=project_id,
    )
    if deduplicated:
        return job.id, job.status
    if is_eager_mode():
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                execute_run_tracks,
                job.id,
                job_params,
                store=store,
                storage=storage,
                gaia_provider=gaia,
                wise_provider=wise,
                mpc_provider=mpc,
            )
    else:
        dispatch_task(run_tracks, str(job.id), job_params, queue="heavy")
    latest = store.get_job(job.id)
    return job.id, (latest.status if latest else job.status)


@router.post(
    "/projects/{project_id}/objects",
    status_code=status.HTTP_201_CREATED,
    tags=["objects"],
    responses=_ANALYSIS_RESPONSES,
)
async def select_project_objects(
    project_id: UUID,
    payload: TrackedObjectSelectRequest,
    store: Annotated[JobStore, Depends(get_store)],
) -> TrackedObjectSelectResponse:
    """Select objects via box-drag, suggestion, catalog, or manual coordinates."""
    job = require_succeeded_job(
        store.get_analysis_job(payload.analysis_id),
        payload.analysis_id,
        label="Analysis",
        output_key="detection_ids",
    )
    assert job.outputs is not None
    all_dets = store.get_detections_by_ids([int(i) for i in job.outputs["detection_ids"]])
    det_by_id = {d.id: d for d in all_dets}
    observations = store.get_analysis_observations(payload.analysis_id)
    obs_by_id = {UUID(str(o["id"])): o for o in observations}
    obs_by_fid = {UUID(str(o["fits_file_id"])): o for o in observations if o.get("fits_file_id")}
    raw_fids = job.outputs.get("fits_file_ids") or dict.fromkeys(d.fits_file_id for d in all_dets)
    fits_file_ids = [UUID(str(fid)) for fid in raw_fids]

    target_obs: dict[str, Any] | None = None
    filtered_dets = list(all_dets)
    if payload.epoch is not None:
        target_obs = resolve_epoch_observation(observations, payload.epoch)
        if target_obs is None:
            raise APIProblemError(
                404,
                "Epoch Not Found",
                f"Epoch {payload.epoch!r} not found in analysis {payload.analysis_id}.",
            )
        target_oid = UUID(str(target_obs["id"]))
        target_fid = target_obs.get("fits_file_id")
        filtered_dets = [
            d
            for d in all_dets
            if d.observation_id == target_oid or (target_fid and d.fits_file_id == target_fid)
        ]

    if payload.box is not None and payload.box.dec_min > payload.box.dec_max:
        raise APIProblemError(422, "Invalid Sky Box", "dec_min must not exceed dec_max.")

    selected_dets: list[DetectionRead] = []
    manual_seed: dict[str, Any] | None = None

    if payload.mode == "box":
        if payload.box is None:
            raise APIProblemError(422, "Missing Sky Box", "box is required when mode='box'.")
        b = payload.box
        in_box = [
            d
            for d in filtered_dets
            if in_sky_box(d.ra, d.dec, b.ra_min, b.ra_max, b.dec_min, b.dec_max)
        ]
        clean_in_box = [
            d for d in in_box if not d.is_difference and not (d.flags & HARD_BAD_MASK)
        ] or in_box
        selected_dets = (
            clean_in_box if payload.epoch is not None else _cluster_box_detections(clean_in_box)
        )
    elif payload.mode == "suggestion":
        if payload.detection_ids is not None:
            selected_dets = _resolve_detection_ids(payload.detection_ids, det_by_id)
        else:
            sug_ids = [int(s["detection_id"]) for s in job.outputs.get("suggestions", [])]
            cand_dets = [det_by_id[did] for did in sug_ids if did in det_by_id]
            if payload.epoch is not None:
                allowed = {d.id for d in filtered_dets}
                cand_dets = [d for d in cand_dets if d.id in allowed]
            if payload.box is not None:
                b = payload.box
                cand_dets = [
                    d
                    for d in cand_dets
                    if in_sky_box(d.ra, d.dec, b.ra_min, b.ra_max, b.dec_min, b.dec_max)
                ]
            selected_dets = cand_dets
    else:  # catalog or manual
        if payload.detection_ids:
            selected_dets = _resolve_detection_ids(payload.detection_ids, det_by_id)
        elif payload.box is not None:
            b = payload.box
            selected_dets = [
                d
                for d in filtered_dets
                if not d.is_difference
                and in_sky_box(d.ra, d.dec, b.ra_min, b.ra_max, b.dec_min, b.dec_max)
            ]
            if not selected_dets and payload.mode == "manual":
                ra_mid = (
                    0.5 * (b.ra_min + b.ra_max)
                    if b.ra_min <= b.ra_max
                    else ((0.5 * (b.ra_min + b.ra_max + 360.0)) % 360.0)
                )
                dec_mid = 0.5 * (b.dec_min + b.dec_max)
                ref_obs = target_obs or (observations[0] if observations else None)
                manual_seed = {
                    "detection_id": None,
                    "ra": ra_mid,
                    "dec": dec_mid,
                    "mjd_tdb": float(ref_obs["mjd_tdb_mean"]) if ref_obs else 60850.0,
                    "observation_id": ref_obs["id"] if ref_obs else None,
                    "epoch_label": ref_obs["epoch_label"] if ref_obs else "S1",
                    "flags": 0,
                }
        else:
            raise APIProblemError(
                422,
                "Selection Input Required",
                f"Provide detection_ids or box for mode='{payload.mode}'.",
            )

    candidates: list[dict[str, Any]] = []
    for d in selected_dets:
        obs = (
            obs_by_id.get(d.observation_id)
            if d.observation_id is not None
            else obs_by_fid.get(d.fits_file_id)
        )
        frec = store.get_fits_file(d.fits_file_id)
        mjd = (
            float(obs["mjd_tdb_mean"])
            if obs is not None
            else float((frec or {}).get("mjd_tdb_mid") or 60850.0)
        )
        candidates.append(
            {
                "detection_id": d.id,
                "ra": d.ra,
                "dec": d.dec,
                "mjd_tdb": mjd,
                "observation_id": obs["id"] if obs is not None else d.observation_id,
                "epoch_label": obs["epoch_label"] if obs is not None else None,
                "sig_ra_mas": d.sig_ra_mas,
                "sig_dec_mas": d.sig_dec_mas,
                "corr_radec": d.corr_radec,
                "band_group": _det_band_group(store, d),
                "flux_ujy": d.flux_ujy,
                "flux_err_ujy": d.flux_err_ujy,
                "flags": int(d.flags),
            }
        )
    if manual_seed is not None:
        candidates.append(manual_seed)

    try:
        summaries = store.select_tracked_objects(
            project_id=project_id,
            analysis_id=payload.analysis_id,
            fits_file_ids=fits_file_ids,
            candidates=candidates,
            selection_mode=payload.mode,
        )
    except KeyError as exc:
        raise APIProblemError(404, "Project Not Found", str(exc)) from exc
    return TrackedObjectSelectResponse(objects=summaries)


@router.post(
    "/analyses/{analysis_id}/select",
    status_code=status.HTTP_201_CREATED,
    tags=["analyses", "objects"],
    responses=_ANALYSIS_RESPONSES,
)
async def select_and_track_analysis_object(
    analysis_id: UUID,
    payload: TrackedObjectSelectRequest,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    gaia: Annotated[GaiaProvider, Depends(get_gaia_provider)],
    wise: Annotated[WiseProvider, Depends(get_wise_provider)],
    mpc: Annotated[MpcProvider, Depends(get_mpc_provider)],
    user_id: Annotated[UUID | None, Depends(get_optional_user_id)] = None,
) -> TrackedObjectSelectResponse:
    """Select objects from an analysis and automatically fit multi-epoch motion & spectrum."""
    payload_copy = payload.model_copy(update={"analysis_id": analysis_id})
    default_proj_id = uuid5(UUID("2e75b5fe-3a8a-4e4e-9f7e-2e5bc8d54a01"), f"analysis:{analysis_id}")
    resp = await select_project_objects(default_proj_id, payload_copy, store)
    if resp.objects:
        obj_ids = [o.id for o in resp.objects]
        with contextlib.suppress(Exception):
            await _enqueue_tracks_job(
                project_id=default_proj_id,
                object_ids=obj_ids,
                models=["linear", "astrometric5"],
                run_classification=True,
                override_rev=0,
                user_id=user_id,
                store=store,
                storage=storage,
                gaia=gaia,
                wise=wise,
                mpc=mpc,
            )
    return resp


@router.post(
    "/projects/{project_id}/tracks",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["objects"],
    responses=_ANALYSIS_RESPONSES,
)
async def run_project_tracks(
    project_id: UUID,
    payload: TrackRunRequest,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    gaia: Annotated[GaiaProvider, Depends(get_gaia_provider)],
    wise: Annotated[WiseProvider, Depends(get_wise_provider)],
    mpc: Annotated[MpcProvider, Depends(get_mpc_provider)],
    user_id: Annotated[UUID | None, Depends(get_optional_user_id)] = None,
) -> JobAcceptedResponse:
    """Associate detections across epochs, fit astrometric motion, and classify."""
    if not payload.models or any(m not in _ALLOWED_MOTION_MODELS for m in payload.models):
        raise APIProblemError(
            422,
            "Invalid Motion Models",
            f"models must be non-empty and within {sorted(_ALLOWED_MOTION_MODELS)}.",
        )
    try:
        objs = store.list_project_objects(project_id, payload.object_ids)
    except KeyError as exc:
        raise APIProblemError(404, "Tracked Object Not Found", str(exc)) from exc
    if not objs:
        raise APIProblemError(
            422, "No Tracked Objects", "Select at least one object in the project before tracking."
        )

    object_ids = [UUID(str(o["id"])) for o in objs]
    override_rev = sum(int(o.get("override_rev", 0)) for o in objs)
    job_id, job_status = await _enqueue_tracks_job(
        project_id=project_id,
        object_ids=object_ids,
        models=list(payload.models),
        run_classification=payload.run_classification,
        override_rev=override_rev,
        user_id=user_id,
        store=store,
        storage=storage,
        gaia=gaia,
        wise=wise,
        mpc=mpc,
    )
    return JobAcceptedResponse(job_id=job_id, status=job_status)


@router.get("/objects/{object_id}", tags=["objects"], responses=_ANALYSIS_RESPONSES)
async def get_tracked_object(
    object_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
) -> TrackedObjectDetailResponse:
    """Retrieve full tracked object measurements, motion fit, classification, and caveats."""
    bundle = _require_object_bundle(store, object_id)
    fits_list = bundle.get("motion_fits") or []
    pref_raw = next(
        (f for f in fits_list if f.get("is_preferred")), fits_list[0] if fits_list else None
    )
    return TrackedObjectDetailResponse(
        id=bundle["id"],
        label=bundle["label"],
        public_id=bundle.get("public_id"),
        status=bundle.get("status") or "active",
        identity_confidence=bundle.get("identity_confidence"),
        measurements=bundle.get("measurements") or [],
        motion_fit=MotionFitRead.model_validate(pref_raw) if pref_raw is not None else None,
        classification=bundle.get("classification"),
        caveats=bundle.get("caveats") or [],
    )


@router.get("/objects/{object_id}/associations", tags=["objects"], responses=_ANALYSIS_RESPONSES)
async def get_object_associations(
    object_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
) -> ObjectAssociationsResponse:
    """Retrieve per-epoch detection match hypotheses and alternatives."""
    bundle = _require_object_bundle(store, object_id)
    return ObjectAssociationsResponse(
        object_id=object_id, associations=bundle.get("associations") or []
    )


@router.patch(
    "/objects/{object_id}/associations/{epoch}",
    tags=["objects"],
    responses=_ANALYSIS_RESPONSES,
)
async def override_object_association(
    object_id: UUID,
    epoch: str,
    payload: AssociationOverrideRequest,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    gaia: Annotated[GaiaProvider, Depends(get_gaia_provider)],
    wise: Annotated[WiseProvider, Depends(get_wise_provider)],
    mpc: Annotated[MpcProvider, Depends(get_mpc_provider)],
    user_id: Annotated[UUID | None, Depends(get_optional_user_id)] = None,
) -> AssociationOverrideResponse:
    """Accept, reject, or reassign a per-epoch detection match and refit motion."""
    bundle = _require_object_bundle(store, object_id)
    if payload.action == "reassign" and payload.detection_id is None:
        raise APIProblemError(
            422, "Missing Detection ID", "detection_id is required when action='reassign'."
        )
    if payload.detection_id is not None and not store.get_detections_by_ids([payload.detection_id]):
        raise APIProblemError(
            404, "Detection Not Found", f"No detection found for {payload.detection_id}."
        )
    try:
        ov = store.apply_association_override(
            object_id,
            epoch,
            action=payload.action,
            detection_id=payload.detection_id,
            reason=payload.reason,
            user_id=user_id,
        )
    except LookupError as exc:
        raise APIProblemError(404, "Epoch Or Detection Not Found", str(exc)) from exc

    existing_models = [str(f["model"]) for f in (bundle.get("motion_fits") or [])] or [
        "linear",
        "astrometric5",
    ]
    refit_job_id, _ = await _enqueue_tracks_job(
        project_id=bundle["project_id"],
        object_ids=[object_id],
        models=existing_models,
        run_classification=True,
        override_rev=int(ov["override_rev"]),
        user_id=user_id,
        store=store,
        storage=storage,
        gaia=gaia,
        wise=wise,
        mpc=mpc,
    )
    return AssociationOverrideResponse(refit_job_id=refit_job_id, decided_by="user")


@router.get("/objects/{object_id}/spectrum", tags=["objects"], responses=_ANALYSIS_RESPONSES)
async def get_object_spectrum(
    object_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    epoch: str | None = None,
) -> SpectrumResponse:
    """Fetch on-the-fly forced-PSF SED across channels (um, uJy)."""
    bundle = _require_object_bundle(store, object_id)
    obs_id: UUID | None = None
    if epoch is not None:
        obs = resolve_epoch_observation(bundle.get("observations") or [], epoch)
        if obs is None:
            raise APIProblemError(404, "Epoch Not Found", f"Epoch {epoch!r} not found for object.")
        obs_id = UUID(str(obs["id"]))
    return compute_object_spectrum(bundle, store=store, storage=storage, observation_id=obs_id)


@router.get("/objects/{object_id}/lightcurve", tags=["objects"], responses=_ANALYSIS_RESPONSES)
async def get_object_lightcurve(
    object_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
) -> LightcurveResponse:
    """Fetch multi-epoch photometry lightcurve and variability indices."""
    bundle = _require_object_bundle(store, object_id)
    return build_object_lightcurve(bundle)


@router.post(
    "/objects/{object_id}/recompute",
    tags=["objects"],
    response_model=TrajectoryRecomputeResponse,
    responses=_ANALYSIS_RESPONSES,
)
async def recompute_object_trajectory(
    object_id: UUID,
    payload: TrajectoryRecomputeRequest,
    store: Annotated[JobStore, Depends(get_store)],
    storage: Annotated[StorageBackend, Depends(get_storage)],
    gaia: Annotated[GaiaProvider, Depends(get_gaia_provider)],
    wise: Annotated[WiseProvider, Depends(get_wise_provider)],
    mpc: Annotated[MpcProvider, Depends(get_mpc_provider)],
    user_id: Annotated[UUID | None, Depends(get_optional_user_id)] = None,
) -> TrajectoryRecomputeResponse:
    """Recompute trajectory motion models with optional excluded detection IDs."""
    bundle = _require_object_bundle(store, object_id)
    for det_id in payload.excluded_detection_ids:
        for obs in bundle.get("observations") or []:
            if obs.get("detection_id") == det_id:
                store.apply_association_override(
                    object_id,
                    str(obs.get("epoch") or obs.get("observation_id")),
                    action="reject",
                    reason="User excluded detection in trajectory recompute",
                    user_id=user_id,
                )

    existing_models = payload.models or [
        str(f["model"]) for f in (bundle.get("motion_fits") or [])
    ] or ["linear", "astrometric5"]

    refit_job_id, _ = await _enqueue_tracks_job(
        project_id=bundle["project_id"],
        object_ids=[object_id],
        models=existing_models,
        run_classification=True,
        user_id=user_id,
        store=store,
        storage=storage,
        gaia=gaia,
        wise=wise,
        mpc=mpc,
    )
    return TrajectoryRecomputeResponse(
        object_id=object_id, refit_job_id=refit_job_id, status="recomputed"
    )


@router.post(
    "/objects/{object_id}/ephemeris",
    tags=["objects"],
    response_model=EphemerisResponse,
    responses=_ANALYSIS_RESPONSES,
)
async def predict_object_ephemeris(
    object_id: UUID,
    payload: EphemerisRequest,
    store: Annotated[JobStore, Depends(get_store)],
) -> EphemerisResponse:
    """Compute future celestial motion predictions and propagated covariance uncertainty ellipses."""
    bundle = _require_object_bundle(store, object_id)
    obj = bundle.get("object") or {}
    motion_fits = bundle.get("motion_fits") or []

    best_fit = None
    if motion_fits:
        best_fit = min(motion_fits, key=lambda f: float(f.get("bic") or 0.0))

    ref_ra = float(obj.get("ra_deg") or (best_fit.get("ra_ref_deg") if best_fit else 0.0) or 0.0)
    ref_dec = float(obj.get("dec_deg") or (best_fit.get("dec_ref_deg") if best_fit else 0.0) or 0.0)
    ref_mjd = float(payload.start_mjd or (best_fit.get("ref_epoch_mjd_tdb") if best_fit else None) or 60800.0)

    pm_ra = float(best_fit.get("pmra_arcsec_yr") or 0.0) if best_fit else 0.0
    pm_dec = float(best_fit.get("pmdec_arcsec_yr") or 0.0) if best_fit else 0.0
    plx = float(best_fit.get("parallax_mas") or 0.0) if best_fit else 0.0

    cov_matrix = None
    if best_fit and best_fit.get("cov_params"):
        with contextlib.suppress(Exception):
            cov_matrix = np.asarray(best_fit["cov_params"], dtype=np.float64)

    target_dict = {
        "ra_deg": ref_ra,
        "dec_deg": ref_dec,
        "ref_epoch_mjd": ref_mjd,
        "pmra_arcsec_yr": pm_ra,
        "pmdec_arcsec_yr": pm_dec,
        "parallax_mas": plx,
    }

    pts = predict_ephemeris(
        target=target_dict,
        days_forward=payload.extrapolation_days,
        n_steps=payload.steps,
        cov_matrix=cov_matrix,
    )

    trajectory = [
        EphemerisPredictionPointSchema(
            time_mjd=p.time_mjd,
            date_iso=p.date_iso,
            ra_deg=p.ra_deg,
            dec_deg=p.dec_deg,
            uncertainty_semi_major_arcsec=p.uncertainty_semi_major_arcsec,
            uncertainty_semi_minor_arcsec=p.uncertainty_semi_minor_arcsec,
            pa_deg=p.pa_deg,
            is_extrapolated=p.is_extrapolated,
            method=p.method,
        )
        for p in pts
    ]

    return EphemerisResponse(
        object_id=object_id,
        trajectory=trajectory,
        preferred_model=str(best_fit.get("model")) if best_fit else None,
        baseline_days=payload.extrapolation_days,
    )

