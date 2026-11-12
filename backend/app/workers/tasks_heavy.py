"""Heavy-queue Celery tasks for registration, extraction, ZOGY differencing, and suggestions."""

from __future__ import annotations

import contextlib
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.time import Time

from app.db.flags import DetectionFlag
from app.providers.base import CatalogSource, Provider, default_registry
from app.schemas.objects import DetectionRead, SuggestionItem
from app.services.fields import parse_bands_query
from app.services.jobs import JobStore, get_job_store
from app.services.storage import StorageBackend, compute_sha256, get_storage_backend
from app.services.tracking import flux_to_ujy_and_mag
from app.workers.celery_app import celery_app
from sphx_core.calib import HARD_BAD_MASK, FrameData, band_group_for_wavelength, load_frame
from sphx_core.classify import classify_source
from sphx_core.consensus import evaluate_mover_consensus
from sphx_core.difference import difference_frames
from sphx_core.extraction import ExtractionResult, extract_sources
from sphx_core.identity import (
    EpochDetection,
    EpochLink,
    TrackedSource,
    TrackState,
    apply_manual_override,
    format_public_id,
    link_epochs,
)
from sphx_core.io import inspect_fits
from sphx_core.motion import MotionFitResult, fit_motion, forced_photometry_at_sky
from sphx_core.registration import register_exposure
from sphx_core.wcs import healpix9_nested, tangent_arcsec_to_sky

ANALYSIS_TASK_NAME = "app.workers.tasks_heavy.run_analysis"
TRACKS_TASK_NAME = "app.workers.tasks_heavy.run_tracks"
_ANALYSIS_FRAME_NS = UUID("9f4a21c8-7d3e-4b9a-a210-6c5e8f123401")
_MOVER_REASONS = frozenset(
    {"Mover dipole in diff", "High PM candidate", "Red+moving", "Fast-mover streak", "Known SSO"}
)


@dataclass(frozen=True)
class _LoadedFrame:
    fits_file_id: UUID
    frame: FrameData
    obs_id: str | None = None
    release: str | None = None
    observation_id: UUID | None = None
    epoch_label: str | None = None


def _lookup_external_catalogs(
    loaded: Sequence[_LoadedFrame],
    *,
    gaia_provider: Provider | None,
    wise_provider: Provider | None,
    mpc_provider: Provider | None,
) -> tuple[list[CatalogSource], list[CatalogSource], list[CatalogSource], list[str]]:
    ra_c = float(np.mean([item.frame.wcs_metrics.ra_center_deg for item in loaded])) % 360.0
    dec_c = float(np.mean([item.frame.wcs_metrics.dec_center_deg for item in loaded]))
    center = SkyCoord(ra=ra_c * u.deg, dec=dec_c * u.deg, frame="icrs")
    max_half_deg = max(
        0.05,
        max(
            max(item.frame.shape) * item.frame.wcs_metrics.pixscale_arcsec / 3600.0
            for item in loaded
        ),
    )
    radius = min(1.0, max_half_deg) * u.deg
    t_mean = Time(
        float(np.mean([item.frame.epoch.mjd_tdb_mid for item in loaded])),
        format="mjd",
        scale="tdb",
    )
    gaia_sources: list[CatalogSource] = []
    wise_sources: list[CatalogSource] = []
    mpc_sources: list[CatalogSource] = []
    warnings_out: list[str] = []
    if os.environ.get("SPHX_OFFLINE_CATALOGS", "").lower() not in {"1", "true", "yes"}:
        for label, prov_arg, reg_name, epoch_arg, target_list in (
            ("gaia", gaia_provider, "gaia", None, gaia_sources),
            ("wise", wise_provider, "wise", None, wise_sources),
            ("mpc", mpc_provider, "mpc", t_mean, mpc_sources),
        ):
            prov = prov_arg
            if prov is None:
                with contextlib.suppress(Exception):
                    prov = default_registry.get(reg_name)
            if prov is not None:
                try:
                    target_list.extend(prov.cone(center, radius, epoch=epoch_arg))
                except Exception as exc:
                    warnings_out.append(f"{label} catalog lookup skipped: {exc}")
    return gaia_sources, wise_sources, mpc_sources, warnings_out


def _register_and_load(
    store: JobStore,
    fid: UUID,
    raw: bytes,
    storage_path: str,
    *,
    provider: str,
    reg_obs_id: str,
    obs_id: str | None = None,
    release_id: str | None = None,
) -> _LoadedFrame:
    if store.get_fits_file(fid) is None:
        insp = inspect_fits(raw)
        meta = {
            "origin": "archive",
            "provider": provider,
            "obs_id": reg_obs_id,
            "release_id": release_id,
            "size_bytes": len(raw),
            "band_label": insp.band_label,
            "array_id": insp.array_id,
            "wavelength_min_um": insp.wavelength_um[0] if insp.wavelength_um else None,
            "wavelength_max_um": insp.wavelength_um[1] if insp.wavelength_um else None,
            "date_obs_utc": insp.epoch.utc_iso if insp.epoch else None,
            "mjd_tdb_mid": insp.epoch.mjd_tdb_mid if insp.epoch else None,
            "exptime_s": insp.epoch.exptime_s if insp.epoch else None,
            "ra_center": insp.wcs.ra_center_deg if insp.wcs else None,
            "dec_center": insp.wcs.dec_center_deg if insp.wcs else None,
            "naxis1": insp.wcs.naxis1 if insp.wcs else None,
            "naxis2": insp.wcs.naxis2 if insp.wcs else None,
            "pixscale_arcsec": insp.wcs.pixscale_arcsec if insp.wcs else None,
            "rotation_deg": insp.wcs.rotation_deg if insp.wcs else None,
            "wcs_header": insp.wcs.wcs_header if insp.wcs else "",
            "footprint": list(insp.wcs.footprint) if insp.wcs else None,
            "healpix9": insp.wcs.healpix9 if insp.wcs else None,
            "header_summary": insp.header_summary,
        }
        store.register_fits_file(
            fits_file_id=fid,
            metadata=meta,
            sha256_hex=compute_sha256(raw),
            storage_path=storage_path,
        )
    return _LoadedFrame(
        fits_file_id=fid, frame=load_frame(raw), obs_id=obs_id or reg_obs_id, release=release_id
    )


def _load_analysis_frames(
    params: dict[str, Any],
    *,
    store: JobStore,
    storage: StorageBackend,
    spherex_provider: Provider | None,
) -> list[_LoadedFrame]:
    mode = str(params["mode"])
    loaded: list[_LoadedFrame] = []

    if mode == "fits_files":
        for fid_str in params["fits_file_ids"]:
            fid = UUID(str(fid_str))
            rec = store.get_fits_file(fid)
            if rec is None:
                raise ValueError(f"FITS file {fid} is not registered.")
            raw = storage.get_bytes(str(rec["storage_path"]), verify_checksum=False)
            loaded.append(
                _LoadedFrame(
                    fid, load_frame(raw, user_epoch=rec.get("mjd_tdb_mid")), rec.get("obs_id")
                )
            )
        return loaded

    if mode == "cutout":
        cutout_id = UUID(str(params["cutout_id"]))
        cjob = store.get_cutout_job(cutout_id)
        if cjob is None or not cjob.outputs or "cutout_set" not in cjob.outputs:
            raise ValueError(f"Completed cutout set {cutout_id} not found.")
        artifacts, cset = cjob.outputs.get("artifacts", {}), cjob.outputs["cutout_set"]
        for cframe in cset.get("frames", []):
            fid = UUID(str(cframe["frame_id"]))
            fits_key = (artifacts.get(str(fid)) or {}).get("fits")
            if fits_key:
                raw = storage.get_bytes(str(fits_key), verify_checksum=False)
                loaded.append(
                    _register_and_load(
                        store,
                        fid,
                        raw,
                        str(fits_key),
                        provider="spherex_irsa",
                        reg_obs_id=str(fid),
                        release_id=cset.get("release"),
                    )
                )
        return loaded

    if mode == "sky_field":
        prov = spherex_provider or default_registry.get("spherex_irsa")
        fov = float(params.get("fov_arcmin", 10.0))
        release = str(params.get("release", "latest"))
        center = SkyCoord(float(params["ra"]) * u.deg, float(params["dec"]) * u.deg, frame="icrs")
        refs = prov.search_frames(
            center,
            (fov / 2.0) * u.arcmin,
            bands=parse_bands_query(params.get("bands")),
            release=release,
        )
        for ref in refs:
            stored = prov.fetch_to_storage(ref, storage, cutout=(center, fov * u.arcmin))
            raw = storage.get_bytes(stored.storage_path, verify_checksum=False)
            fid = uuid5(_ANALYSIS_FRAME_NS, f"{ref.provider}:{ref.obs_id}:{ref.mjd_tdb:.6f}")
            loaded.append(
                _register_and_load(
                    store,
                    fid,
                    raw,
                    stored.storage_path,
                    provider=ref.provider,
                    reg_obs_id=f"{ref.obs_id}:{fid}",
                    obs_id=ref.obs_id,
                    release_id=ref.release,
                )
            )
        return loaded

    raise ValueError(f"Unsupported analysis mode: {mode!r}")


def _catalog_to_ref_dicts(sources: Sequence[CatalogSource]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in sources:
        g_mag = s.mag if s.mag_band == "G" else s.mags.get("G")
        out.append(
            {
                "source_id": s.source_id,
                "ra_deg": s.ref_ra_deg if s.ref_ra_deg is not None else s.ra_deg,
                "dec_deg": s.ref_dec_deg if s.ref_dec_deg is not None else s.dec_deg,
                "ref_epoch_mjd": s.ref_epoch.tdb.mjd if s.ref_epoch is not None else s.mjd_tdb,
                "pmra": s.pmra_masyr or 0.0,
                "pmdec": s.pmdec_masyr or 0.0,
                "parallax": s.parallax_mas or 0.0,
                "g_mag": g_mag,
                "ra_error": s.ra_err_mas,
                "dec_error": s.dec_err_mas,
                "pmra_error": s.pmra_err_masyr,
                "pmdec_error": s.pmdec_err_masyr,
                "parallax_error": s.parallax_err_mas,
            }
        )
    return out


def _nearest_detection(
    ra_deg: float, dec_deg: float, detections: Sequence[DetectionRead], max_arcsec: float
) -> DetectionRead | None:
    if not detections:
        return None
    target = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    coords = SkyCoord(
        ra=[d.ra for d in detections] * u.deg,
        dec=[d.dec for d in detections] * u.deg,
        frame="icrs",
    )
    seps = target.separation(coords).arcsec
    best_idx = int(np.argmin(seps))
    return detections[best_idx] if float(seps[best_idx]) <= max_arcsec else None


def _upsert_suggestion(
    bucket: dict[int, dict[str, Any]],
    det: DetectionRead,
    score: float,
    reasons: Sequence[str],
    summary: str,
) -> None:
    entry = bucket.setdefault(
        det.id,
        {
            "detection_id": det.id,
            "ra": det.ra,
            "dec": det.dec,
            "score": 0.0,
            "reasons": [],
            "summaries": [],
        },
    )
    entry["score"] = max(float(entry["score"]), float(min(1.0, max(0.0, score))))
    for r in reasons:
        if r not in entry["reasons"]:
            entry["reasons"].append(r)
    if summary and summary not in entry["summaries"]:
        entry["summaries"].append(summary)


def execute_run_analysis(
    job_id: UUID,
    params: dict[str, Any],
    *,
    store: JobStore,
    storage: StorageBackend,
    spherex_provider: Provider | None = None,
    gaia_provider: Provider | None = None,
    wise_provider: Provider | None = None,
    mpc_provider: Provider | None = None,
) -> dict[str, Any]:
    """Run WCS registration, PSF extraction, differencing, linking, and candidate suggestions."""
    store.update_job(job_id, status="running", progress=0.05, started_at=datetime.now(UTC))
    try:
        analysis_id = UUID(str(params["analysis_id"]))
        loaded = _load_analysis_frames(
            params, store=store, storage=storage, spherex_provider=spherex_provider
        )
        if not loaded:
            outputs: dict[str, Any] = {
                "analysis_id": str(analysis_id),
                "detection_ids": [],
                "suggestions": [],
                "warnings": ["No frames available for analysis."],
            }
            store.update_job(
                job_id,
                status="succeeded",
                progress=1.0,
                outputs=outputs,
                finished_at=datetime.now(UTC),
            )
            return outputs

        loaded.sort(key=lambda item: (item.frame.epoch.mjd_tdb_mid, str(item.fits_file_id)))
        obs_records = store.save_analysis_observations(
            analysis_id,
            [
                {
                    "id": uuid5(_ANALYSIS_FRAME_NS, f"obs:{analysis_id}:{idx}:{item.fits_file_id}"),
                    "epoch_label": f"S{idx + 1}",
                    "mjd_tdb_mean": item.frame.epoch.mjd_tdb_mid,
                    "fits_file_id": item.fits_file_id,
                    "date_obs_utc": item.frame.epoch.utc_iso,
                    "ra_center": item.frame.wcs_metrics.ra_center_deg,
                    "dec_center": item.frame.wcs_metrics.dec_center_deg,
                    "release_id": item.release,
                }
                for idx, item in enumerate(loaded)
            ],
        )
        obs_id_by_idx = [UUID(str(o["id"])) for o in obs_records]

        store.update_job(job_id, progress=0.15)
        gaia_sources, wise_sources, mpc_sources, warnings_out = _lookup_external_catalogs(
            loaded,
            gaia_provider=gaia_provider,
            wise_provider=wise_provider,
            mpc_provider=mpc_provider,
        )

        gaia_refs = _catalog_to_ref_dicts(gaia_sources)
        store.update_job(job_id, progress=0.25)

        extractions: list[ExtractionResult] = []
        reg_sigmas: list[float] = []
        all_detections: list[DetectionRead] = []
        det_map: dict[tuple[int, int], DetectionRead] = {}

        total_f = max(len(loaded), 1)
        for idx, item in enumerate(loaded):
            reg = register_exposure(item.frame, reference_catalog=gaia_refs or None)
            reg_sigmas.append(reg.sigma_reg_arcsec)
            ext = extract_sources(item.frame, wcs=reg.wcs, sigma_reg_arcsec=reg.sigma_reg_arcsec)
            extractions.append(ext)
            warnings_out.extend(reg.warnings)
            warnings_out.extend(ext.warnings)

            rows: list[dict[str, Any]] = []
            for src in ext.detections:
                flux_ujy, err_ujy, mag_ab = flux_to_ujy_and_mag(src.flux, src.flux_err, item.frame)
                rows.append(
                    {
                        "healpix9": healpix9_nested(src.ra_deg, src.dec_deg),
                        "fits_file_id": item.fits_file_id,
                        "observation_id": obs_id_by_idx[idx],
                        "ra": src.ra_deg,
                        "dec": src.dec_deg,
                        "x_pix": src.x_px,
                        "y_pix": src.y_px,
                        "sig_ra_mas": src.sig_ra_arcsec * 1000.0,
                        "sig_dec_mas": src.sig_dec_arcsec * 1000.0,
                        "corr_radec": src.corr_radec,
                        "flux_ujy": flux_ujy,
                        "flux_err_ujy": err_ujy,
                        "mag_ab": mag_ab,
                        "snr": src.snr,
                        "fwhm_arcsec": src.fwhm_arcsec,
                        "elongation": src.elongation,
                        "theta_deg": src.theta_deg,
                        "psf_chi2": src.psf_chi2_nu,
                        "sharpness": src.sharpness,
                        "roundness": src.roundness,
                        "flags": int(src.flags),
                        "real_bogus": src.real_bogus,
                        "is_difference": False,
                        "diff_sign": None,
                    }
                )
            saved = store.save_detections(rows)
            for src, det in zip(ext.detections, saved, strict=True):
                det_map[(idx, src.source_index)] = det
            all_detections.extend(saved)
            store.update_job(job_id, progress=0.25 + 0.35 * ((idx + 1) / total_f))

        clean_dets = [
            d
            for d in all_detections
            if not (d.flags & (HARD_BAD_MASK | int(DetectionFlag.PERSIST)))
            and (d.real_bogus is None or d.real_bogus >= 0.5)
        ]
        suggestions_map: dict[int, dict[str, Any]] = {}

        # Multi-epoch ZOGY differencing for dipole movers and stationary variables.
        mjds = [item.frame.epoch.mjd_tdb_mid for item in loaded]
        has_time_span = len(loaded) >= 2 and (max(mjds) - min(mjds)) > 1e-4
        if has_time_span:
            by_band: dict[str, list[int]] = {}
            for idx, item in enumerate(loaded):
                b = (
                    item.frame.band_id
                    or band_group_for_wavelength(item.frame.wavelength_um)
                    or "ALL"
                )
                by_band.setdefault(b, []).append(idx)

            diff_groups = [
                idxs
                for idxs in by_band.values()
                if len(idxs) >= 2
                and (max(mjds[i] for i in idxs) - min(mjds[i] for i in idxs)) > 1e-4
            ]
            if not diff_groups:
                diff_groups = [list(range(len(loaded)))]

            mean_sig_reg = float(np.median(reg_sigmas)) if reg_sigmas else 0.05
            for grp_idxs in diff_groups:
                try:
                    diff_res = difference_frames(
                        [loaded[i].frame for i in grp_idxs],
                        sigma_reg_arcsec=mean_sig_reg,
                        require_band_match=False,
                    )
                except Exception as exc:
                    warnings_out.append(f"ZOGY differencing skipped: {exc}")
                    continue

                for st in diff_res.stamp_tests:
                    pr_idx = min(max(st.pair_indices[1], 0), len(grp_idxs) - 1)
                    target_frame_idx = grp_idxs[pr_idx]
                    target_item = loaded[target_frame_idx]
                    match_rad = max(15.0, 2.5 * target_item.frame.wcs_metrics.pixscale_arcsec)
                    det_match = _nearest_detection(st.ra_deg, st.dec_deg, clean_dets, match_rad)
                    if det_match is None and (
                        st.dipole_favored or (st.monopole_favored and abs(st.snr_corr) >= 5.0)
                    ):
                        saved_diff = store.save_detections(
                            [
                                {
                                    "healpix9": healpix9_nested(st.ra_deg, st.dec_deg),
                                    "fits_file_id": target_item.fits_file_id,
                                    "observation_id": obs_id_by_idx[target_frame_idx],
                                    "ra": st.ra_deg,
                                    "dec": st.dec_deg,
                                    "x_pix": st.x_px,
                                    "y_pix": st.y_px,
                                    "sig_ra_mas": 500.0,
                                    "sig_dec_mas": 500.0,
                                    "corr_radec": 0.0,
                                    "snr": abs(st.snr_corr),
                                    "flags": 0,
                                    "real_bogus": 0.85 if st.dipole_favored else 0.75,
                                    "is_difference": True,
                                    "diff_sign": 1 if st.snr_corr >= 0 else -1,
                                }
                            ]
                        )
                        det_match = saved_diff[0]
                        all_detections.append(det_match)

                    if det_match is None:
                        continue
                    if st.dipole_favored:
                        sc = min(0.99, 0.72 + 0.025 * min(abs(st.snr_corr), 10.0))
                        _upsert_suggestion(
                            suggestions_map,
                            det_match,
                            sc,
                            ["Mover dipole in diff"],
                            f"ZOGY dipole S/N={st.snr_corr:.1f}, "
                            f"dBIC={st.delta_bic_mm_over_mv:.1f}",
                        )
                    elif st.monopole_favored and abs(st.snr_corr) >= 5.0:
                        pair_res = next(
                            (p for p in diff_res.pair_results if st in p.stamp_tests), None
                        )
                        if pair_res is not None and pair_res.wavelength_mismatch:
                            continue
                        sc = min(0.95, 0.60 + 0.02 * min(abs(st.snr_corr), 10.0))
                        _upsert_suggestion(
                            suggestions_map,
                            det_match,
                            sc,
                            ["Variable"],
                            f"ZOGY monopole S/N={st.snr_corr:.1f}",
                        )

        store.update_job(job_id, progress=0.75)

        # Cross-epoch track linking, proper motion, red movers, and SED anomalies.
        cat_combined = [*gaia_sources, *wise_sources]
        persist_coords: list[tuple[float, float]] = []
        if has_time_span and extractions:
            try:
                tracks = link_epochs(
                    extractions,
                    catalog_sources=cat_combined,
                    frames=[item.frame for item in loaded],
                    high_pm_mode=True,
                )
            except Exception as exc:
                warnings_out.append(f"Epoch linking skipped: {exc}")
                tracks = []

            for tr in tracks:
                for d_tr in tr.detections:
                    if d_tr.flags & int(DetectionFlag.PERSIST):
                        dr = det_map.get((d_tr.epoch_index, d_tr.source_index))
                        if dr is not None:
                            persist_coords.append((dr.ra, dr.dec))
                            suggestions_map.pop(dr.id, None)
                good_dets = [
                    d
                    for d in tr.detections
                    if not (d.flags & (HARD_BAD_MASK | int(DetectionFlag.PERSIST)))
                    and d.real_bogus >= 0.5
                ]
                if not good_dets:
                    continue
                rep = max(good_dets, key=lambda d: (d.mjd_tdb, d.snr))
                det_read = det_map.get((rep.epoch_index, rep.source_index))
                if det_read is None:
                    continue

                t_state = tr.state
                mu_as = math.hypot(t_state.theta_5[2], t_state.theta_5[3])
                pa_deg = math.degrees(math.atan2(t_state.theta_5[2], t_state.theta_5[3])) % 360.0
                baseline_yr = (
                    max(d.mjd_tdb for d in good_dets) - min(d.mjd_tdb for d in good_dets)
                ) / 365.25
                is_mover = t_state.model_type != "STATIC" and t_state.chi2_mu > 9.0
                vim_flagged = bool(t_state.vim_r is not None and abs(t_state.vim_r) > 0.80) or (
                    "VIM" in tr.notes_text
                )
                reasons: list[str] = []
                notes: list[str] = []
                score = 0.0

                if is_mover and not vim_flagged and baseline_yr > 0.0:
                    if mu_as >= 0.1:
                        reasons.append("High PM candidate")
                        notes.append(f'mu={mu_as:.2f}"/yr (PA={pa_deg:.1f} deg)')
                        score = max(score, min(0.98, 0.68 + 0.05 * min(mu_as, 6.0)))
                    has_red = any(
                        b in ("B5", "B6", "arr5", "arr6")
                        for d in good_dets
                        for b in ([d.band_id] if d.band_id else [])
                    ) or any(
                        loaded[d.epoch_index].frame.wavelength_um is not None
                        and loaded[d.epoch_index].frame.wavelength_um[1] >= 3.5  # type: ignore[index]
                        for d in good_dets
                    )
                    has_gaia = any(
                        cm.catalog.lower().startswith("gaia") for cm in tr.catalog_matches
                    )
                    if has_red and not has_gaia:
                        reasons.append("Red+moving")
                        notes.append("IR-only mover without Gaia counterpart")
                        score = max(score, 0.90)
                elif vim_flagged:
                    reasons.append("Variable")
                    notes.append("VIM variable binary candidate")
                    score = max(score, 0.68)
                else:
                    fluxes = [d.flux for d in good_dets if d.flux is not None and d.flux > 0]
                    if len(fluxes) >= 2 and (max(fluxes) / min(fluxes)) >= 1.58:
                        reasons.append("Variable")
                        notes.append(f"Flux ratio {max(fluxes) / min(fluxes):.2f} across epochs")
                        score = max(score, 0.70)

                with contextlib.suppress(Exception):
                    cls_res = classify_source(tr, catalog_sources=cat_combined)
                    if (
                        cls_res.top_class in {"brown_dwarf", "candidate_new_mover"}
                        and cls_res.top_prob >= 0.5
                    ):
                        reasons.append("Anomalous SED")
                        notes.append(f"{cls_res.top_class} (p={cls_res.top_prob:.2f})")
                        score = max(score, 0.65 + 0.30 * cls_res.top_prob)

                if reasons:
                    _upsert_suggestion(suggestions_map, det_read, score, reasons, "; ".join(notes))

        # Detector-fixed persistence exclusion across dithered frames (FPR = 0.0).
        if len(loaded) >= 2:
            for (i_a, _), d_a in det_map.items():
                if d_a.x_pix is None or d_a.y_pix is None:
                    continue
                for (i_b, _), d_b in det_map.items():
                    if i_b <= i_a or d_b.x_pix is None or d_b.y_pix is None:
                        continue
                    if math.hypot(d_a.x_pix - d_b.x_pix, d_a.y_pix - d_b.y_pix) <= 0.75:
                        sep_sky = (
                            SkyCoord(d_a.ra * u.deg, d_a.dec * u.deg, frame="icrs")
                            .separation(SkyCoord(d_b.ra * u.deg, d_b.dec * u.deg, frame="icrs"))
                            .arcsec
                        )
                        if sep_sky >= 1.5 * loaded[i_a].frame.wcs_metrics.pixscale_arcsec:
                            persist_coords.extend([(d_a.ra, d_a.dec), (d_b.ra, d_b.dec)])
                            suggestions_map.pop(d_a.id, None)
                            suggestions_map.pop(d_b.id, None)

        if persist_coords:
            p_sky = SkyCoord(
                ra=[c[0] for c in persist_coords] * u.deg,
                dec=[c[1] for c in persist_coords] * u.deg,
                frame="icrs",
            )
            for sid in list(suggestions_map):
                s_item = suggestions_map[sid]
                s_sky = SkyCoord(s_item["ra"] * u.deg, s_item["dec"] * u.deg, frame="icrs")
                if float(np.min(s_sky.separation(p_sky).arcsec)) <= 15.0:
                    suggestions_map.pop(sid, None)

        # Single-frame streaks, known MPC SSOs, and Gaia high-PM catalog matches.
        for det in clean_dets:
            if det.flags & int(DetectionFlag.STREAK):
                _upsert_suggestion(
                    suggestions_map,
                    det,
                    0.85,
                    ["Fast-mover streak"],
                    "Elongated intra-exposure trail",
                )
        for sso in mpc_sources:
            sso_det = _nearest_detection(sso.ra_deg, sso.dec_deg, clean_dets, 15.0)
            if sso_det is not None:
                _upsert_suggestion(
                    suggestions_map, sso_det, 0.92, ["Known SSO"], f"MPC {sso.source_id}"
                )
        for gsrc in gaia_sources:
            pm_tot = math.hypot(gsrc.pmra_masyr or 0.0, gsrc.pmdec_masyr or 0.0)
            if pm_tot >= 50.0:
                g_det = _nearest_detection(gsrc.ra_deg, gsrc.dec_deg, clean_dets, 5.0)
                if g_det is not None:
                    _upsert_suggestion(
                        suggestions_map,
                        g_det,
                        min(0.90, 0.65 + pm_tot / 1000.0),
                        ["High PM candidate"],
                        f"Gaia PM={pm_tot:.1f} mas/yr",
                    )

        # Enforce mover-vs-variable separation and rank descending.
        ranked: list[dict[str, Any]] = []
        for item_s in suggestions_map.values():
            reasons = list(item_s["reasons"])
            if "Variable" in reasons and any(r in _MOVER_REASONS for r in reasons):
                reasons = [r for r in reasons if r != "Variable"]
            ranked.append(
                SuggestionItem(
                    detection_id=int(item_s["detection_id"]),
                    ra=float(item_s["ra"]),
                    dec=float(item_s["dec"]),
                    score=round(float(item_s["score"]), 4),
                    reasons=reasons,
                    summary="; ".join(item_s["summaries"]) if item_s["summaries"] else None,
                ).model_dump(mode="json")
            )
        ranked.sort(key=lambda s: (-float(s["score"]), int(s["detection_id"])))

        store.record_provenance(
            processing_job_id=job_id,
            entity_table="processing_jobs",
            entity_id=str(analysis_id),
            inputs=[
                {"fits_file_id": str(item.fits_file_id), "obs_id": item.obs_id} for item in loaded
            ],
            release_ids=sorted({item.release for item in loaded if item.release}) or None,
            params=params,
        )
        outputs = {
            "analysis_id": str(analysis_id),
            "detection_ids": [d.id for d in all_detections],
            "fits_file_ids": [str(item.fits_file_id) for item in loaded],
            "observation_ids": [str(oid) for oid in obs_id_by_idx],
            "suggestions": ranked,
            "warnings": list(dict.fromkeys(warnings_out)),
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


@celery_app.task(name=ANALYSIS_TASK_NAME, queue="heavy")  # type: ignore[untyped-decorator]
def run_analysis(job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Celery task wrapper executing frame registration, extraction, and suggestions."""
    return execute_run_analysis(
        UUID(job_id),
        params,
        store=get_job_store(),
        storage=get_storage_backend(),
    )


def _get_detection(store: JobStore, det_id: Any) -> DetectionRead | None:
    if det_id is None:
        return None
    rows = store.get_detections_by_ids([int(det_id)])
    return rows[0] if rows else None


def _forced_phot_ujy(
    frame: FrameData, ra: float, dec: float
) -> tuple[float, float, float, int, str | None]:
    f_flux, f_err, f_snr, f_flags = forced_photometry_at_sky(frame, ra, dec)
    f_ujy, ferr_ujy, _ = flux_to_ujy_and_mag(f_flux, f_err, frame)
    band = frame.band_id or band_group_for_wavelength(frame.wavelength_um)
    return f_ujy, ferr_ujy, f_snr, int(f_flags), band


def _det_read_to_epoch_det(
    det: DetectionRead, epoch_index: int, epoch_label: str, frame: FrameData, mjd_tdb: float
) -> EpochDetection:
    band = frame.band_id or band_group_for_wavelength(frame.wavelength_um)
    sig_ra = (det.sig_ra_mas or 150.0) * 1e-3
    sig_dec = (det.sig_dec_mas or 150.0) * 1e-3
    pix = max(frame.wcs_metrics.pixscale_arcsec, 1e-3)
    x_px, y_px = (
        (float(det.x_pix), float(det.y_pix))
        if det.x_pix is not None and det.y_pix is not None
        else tuple(float(v) for v in frame.wcs.world_to_pixel_values(det.ra, det.dec))
    )
    return EpochDetection(
        detection_id=str(det.id),
        epoch_index=epoch_index,
        epoch_id=epoch_label,
        mjd_tdb=mjd_tdb,
        detector_id=frame.array_id,
        x_px=x_px,
        y_px=y_px,
        ra_deg=det.ra,
        dec_deg=det.dec,
        cov_sky_arcsec2=(sig_ra**2, sig_dec**2, 0.0),
        cov_px=((sig_ra / pix) ** 2, (sig_dec / pix) ** 2, 0.0),
        sig_ra_arcsec=sig_ra,
        sig_dec_arcsec=sig_dec,
        flux=det.flux_ujy,
        flux_err=det.flux_err_ujy,
        flux_band_mjy={band: (det.flux_ujy * 1e-3, (det.flux_err_ujy or 1.0) * 1e-3)}
        if band and det.flux_ujy is not None
        else {},
        snr=float(det.snr or 5.0),
        real_bogus=float(det.real_bogus if det.real_bogus is not None else 0.8),
        flags=int(det.flags),
        band_id=band,
        fwhm_arcsec=float(det.fwhm_arcsec or 6.2),
        source_index=-1,
    )


def _motion_fit_record(
    model_name: str, mfit: MotionFitResult, *, is_preferred: bool
) -> dict[str, Any]:
    pmra_masyr = float(mfit.pmra_arcsec_yr * 1000.0)
    pmdec_masyr = float(mfit.pmdec_arcsec_yr * 1000.0)
    return {
        "model": model_name,
        "ref_epoch_mjd_tdb": float(mfit.ref_epoch_mjd_tdb),
        "params": {
            "ra_deg": float(mfit.ra_ref_deg),
            "dec_deg": float(mfit.dec_ref_deg),
            "pmra_masyr": pmra_masyr,
            "pmdec_masyr": pmdec_masyr,
            "pm_total_masyr": float(mfit.mu_arcsec_yr * 1000.0),
            "pm_err_masyr": float(mfit.sigma_mu_arcsec_yr * 1000.0),
            "pa_deg": float(mfit.pa_deg),
            "pa_err_deg": float(mfit.sigma_pa_deg),
            "parallax_mas": float(mfit.parallax_mas),
            "parallax_err_mas": float(mfit.sigma_parallax_mas),
            "chi2_mu": float(mfit.chi2_mu),
            "is_mover": bool(mfit.is_mover),
            "vim_flagged": bool(mfit.vim_flagged),
        },
        "covariance": [float(v) for row in mfit.cov_params for v in row],
        "pm_total_masyr": float(mfit.mu_arcsec_yr * 1000.0),
        "pm_total_err": float(mfit.sigma_mu_arcsec_yr * 1000.0),
        "pa_deg": float(mfit.pa_deg),
        "pa_err": float(mfit.sigma_pa_deg),
        "plx_mas": float(mfit.parallax_mas),
        "plx_err": float(mfit.sigma_parallax_mas),
        "chi2": float(mfit.chi2),
        "dof": int(mfit.dof),
        "chi2_red": float(mfit.chi2_nu),
        "bic": float(mfit.bic),
        "n_epochs": int(mfit.n_epochs),
        "baseline_yr": float(mfit.baseline_yr),
        "is_preferred": is_preferred,
    }


def execute_run_tracks(
    job_id: UUID,
    params: dict[str, Any],
    *,
    store: JobStore,
    storage: StorageBackend,
    gaia_provider: Provider | None = None,
    wise_provider: Provider | None = None,
    mpc_provider: Provider | None = None,
) -> dict[str, Any]:
    """Associate detections across epochs, fit astrometric motion, and classify objects."""
    store.update_job(job_id, status="running", progress=0.05, started_at=datetime.now(UTC))
    try:
        object_ids = [UUID(str(oid)) for oid in params["object_ids"]]
        models = [str(m) for m in (params.get("models") or ["linear", "astrometric5"])]
        run_classification = bool(params.get("run_classification", True))

        bundles: list[dict[str, Any]] = []
        for oid in object_ids:
            b = store.get_tracked_object_bundle(oid)
            if b is None:
                raise ValueError(f"Tracked object {oid} not found.")
            bundles.append(b)

        # Collect unique frames/observations across all selected objects.
        obs_by_fid: dict[UUID, dict[str, Any]] = {}
        analysis_ids: set[UUID] = set()
        for b in bundles:
            for aid_str in b.get("analysis_ids", []):
                analysis_ids.add(UUID(str(aid_str)))
            for obs in b.get("observations", []):
                if obs.get("fits_file_id"):
                    obs_by_fid[UUID(str(obs["fits_file_id"]))] = obs

        loaded: list[_LoadedFrame] = []
        for fid, obs in obs_by_fid.items():
            frec = store.get_fits_file(fid)
            if frec is None:
                continue
            raw = storage.get_bytes(str(frec["storage_path"]), verify_checksum=False)
            frame = load_frame(raw, user_epoch=frec.get("mjd_tdb_mid"))
            loaded.append(
                _LoadedFrame(
                    fits_file_id=fid,
                    frame=frame,
                    obs_id=frec.get("obs_id"),
                    observation_id=UUID(str(obs["id"])),
                    epoch_label=str(obs.get("epoch_label") or "S1"),
                )
            )
        loaded.sort(key=lambda item: (item.frame.epoch.mjd_tdb_mid, str(item.fits_file_id)))
        if not loaded:
            raise ValueError("No frames available to track objects.")

        dets_by_fid: dict[UUID, list[DetectionRead]] = {}
        for aid in analysis_ids:
            ajob = store.get_analysis_job(aid)
            if ajob and ajob.outputs and ajob.outputs.get("detection_ids"):
                for d in store.get_detections_by_ids(ajob.outputs["detection_ids"]):
                    dets_by_fid.setdefault(d.fits_file_id, []).append(d)

        store.update_job(job_id, progress=0.20)
        gaia_sources, wise_sources, mpc_sources, _ = _lookup_external_catalogs(
            loaded,
            gaia_provider=gaia_provider,
            wise_provider=wise_provider,
            mpc_provider=mpc_provider,
        )
        gaia_refs = _catalog_to_ref_dicts(gaia_sources)
        cat_combined = [*gaia_sources, *wise_sources, *mpc_sources]

        extractions: list[ExtractionResult] = []
        for item in loaded:
            reg = register_exposure(item.frame, reference_catalog=gaia_refs or None)
            extractions.append(
                extract_sources(item.frame, wcs=reg.wcs, sigma_reg_arcsec=reg.sigma_reg_arcsec)
            )

        store.update_job(job_id, progress=0.40)
        tracks = link_epochs(
            extractions,
            catalog_sources=cat_combined,
            frames=[item.frame for item in loaded],
            high_pm_mode=True,
        )

        obs_idx_by_id = {
            item.observation_id: idx for idx, item in enumerate(loaded) if item.observation_id
        }
        updated_ids: list[str] = []
        total_objs = max(len(bundles), 1)

        for b_idx, b in enumerate(bundles):
            obj_id = UUID(str(b["id"]))
            seed_det = _get_detection(store, b.get("seed_detection_id"))
            seed_ra = float(seed_det.ra if seed_det is not None else (b.get("ref_ra") or 0.0))
            seed_dec = float(seed_det.dec if seed_det is not None else (b.get("ref_dec") or 0.0))
            seed_ep_idx = (
                obs_idx_by_id.get(seed_det.observation_id, 0)
                if seed_det is not None and seed_det.observation_id
                else 0
            )

            # Match object to auto-linked track via seed detection or nearest sky coordinate.
            matched_track: TrackedSource | None = None
            best_sep = 1e9
            seed_coord = SkyCoord(ra=seed_ra * u.deg, dec=seed_dec * u.deg, frame="icrs")
            max_sep = 3.0 if seed_det is not None else 5.0
            for tr in tracks:
                for d_tr in tr.detections:
                    if d_tr.ra_deg is None or d_tr.dec_deg is None:
                        continue
                    if seed_det is not None and d_tr.epoch_index != seed_ep_idx:
                        continue
                    sep = float(
                        seed_coord.separation(
                            SkyCoord(d_tr.ra_deg * u.deg, d_tr.dec_deg * u.deg, frame="icrs")
                        ).arcsec
                    )
                    if sep <= max_sep and sep < best_sep:
                        best_sep, matched_track = sep, tr

            if matched_track is None:
                seed_edets: list[EpochDetection] = []
                seed_links: list[EpochLink] = []
                for idx, item in enumerate(loaded):
                    ep_lbl = item.epoch_label or f"S{idx + 1}"
                    if seed_det is not None and idx == seed_ep_idx:
                        ed = _det_read_to_epoch_det(
                            seed_det, idx, ep_lbl, item.frame, item.frame.epoch.mjd_tdb_mid
                        )
                        seed_edets.append(ed)
                        seed_links.append(
                            EpochLink(
                                str(obj_id),
                                idx,
                                ep_lbl,
                                ed.detection_id,
                                "LINKED",
                                0.0,
                                1.0,
                                1.0,
                                (0.0, 0.0),
                                "auto",
                            )
                        )
                    else:
                        f_ujy, ferr_ujy, f_snr, f_flags, band = _forced_phot_ujy(
                            item.frame, seed_ra, seed_dec
                        )
                        forced_ok = f_snr >= 3.0
                        ed = EpochDetection(
                            detection_id=f"forced_{idx}",
                            epoch_index=idx,
                            epoch_id=ep_lbl,
                            mjd_tdb=item.frame.epoch.mjd_tdb_mid,
                            detector_id=item.frame.array_id,
                            x_px=0.0,
                            y_px=0.0,
                            ra_deg=seed_ra if forced_ok else None,
                            dec_deg=seed_dec if forced_ok else None,
                            cov_sky_arcsec2=(0.25, 0.25, 0.0),
                            cov_px=(0.01, 0.01, 0.0),
                            sig_ra_arcsec=0.5,
                            sig_dec_arcsec=0.5,
                            flux=f_ujy,
                            flux_err=ferr_ujy,
                            flux_band_mjy={band: (f_ujy * 1e-3, ferr_ujy * 1e-3)} if band else {},
                            snr=f_snr,
                            real_bogus=0.7 if forced_ok else 0.4,
                            flags=f_flags,
                            band_id=band,
                            is_upper_limit=not forced_ok,
                            status="LOW_SNR_FORCED" if forced_ok else "UPPER_LIMIT",
                        )
                        if forced_ok:
                            seed_edets.append(ed)
                        seed_links.append(
                            EpochLink(
                                str(obj_id),
                                idx,
                                ep_lbl,
                                ed.detection_id if forced_ok else None,
                                "LOW_SNR_FORCED" if forced_ok else "UPPER_LIMIT",
                                0.0,
                                1.0,
                                0.85 if forced_ok else 1.0,
                                (0.0, 0.0),
                                "auto",
                            )
                        )
                matched_track = TrackedSource(
                    object_id=str(obj_id),
                    label=str(b["label"]),
                    public_id=b.get("public_id"),
                    status="active",
                    detections=seed_edets,
                    links=seed_links,
                    state=TrackState(
                        object_id=str(obj_id),
                        label=str(b["label"]),
                        ref_epoch_jyear=2025.5,
                        ra0_deg=seed_ra,
                        dec0_deg=seed_dec,
                        theta_5=(0.0, 0.0, 0.0, 0.0, 0.0),
                        cov_5x5=tuple(tuple(0.0 for _ in range(5)) for _ in range(5)),
                        mag_bands={},
                        sigma_var_mag=0.0,
                        model_type="STATIC",
                        chi2_nu=1.0,
                        chi2_mu=0.0,
                        delta_bic=0.0,
                        vim_r=None,
                        flags=0,
                    ),
                    confidence=1.0,
                    gaia_id=None,
                )

            # Apply any user-locked association overrides before fitting/classification.
            user_by_obs: dict[UUID, list[dict[str, Any]]] = {}
            for raw_a in b.get("raw_associations", []):
                if raw_a.get("decided_by") == "user":
                    user_by_obs.setdefault(UUID(str(raw_a["observation_id"])), []).append(raw_a)

            for obs_id, rows in user_by_obs.items():
                ep_idx = obs_idx_by_id.get(obs_id)
                if ep_idx is None:
                    continue
                item = loaded[ep_idx]
                chosen_row = next((r for r in rows if r.get("is_chosen")), None)
                det_override = _get_detection(
                    store, chosen_row.get("detection_id") if chosen_row is not None else None
                )
                if det_override is not None:
                    assert chosen_row is not None
                    edet = _det_read_to_epoch_det(
                        det_override,
                        ep_idx,
                        item.epoch_label or f"S{ep_idx + 1}",
                        item.frame,
                        item.frame.epoch.mjd_tdb_mid,
                    )
                    matched_track = apply_manual_override(
                        [matched_track],
                        object_id=matched_track.object_id,
                        epoch_index=ep_idx,
                        action="reassign",
                        new_detection=edet,
                        reason=str(chosen_row.get("reason") or "user override"),
                    )[0]
                elif chosen_row is None:
                    matched_track = apply_manual_override(
                        [matched_track],
                        object_id=matched_track.object_id,
                        epoch_index=ep_idx,
                        action="reject",
                        reason=str(rows[0].get("reason") or "user rejected"),
                    )[0]

            # Recover stationary variable-star epochs if link_epochs split on a large flux jump.
            if len(matched_track.detections) == 1 and len(loaded) >= 2:
                present_eps = {d.epoch_index for d in matched_track.detections}
                for idx, item in enumerate(loaded):
                    if idx in present_eps or item.observation_id in user_by_obs:
                        continue
                    f_dets = [
                        d for d in dets_by_fid.get(item.fits_file_id, []) if not d.is_difference
                    ]
                    coincident = _nearest_detection(seed_ra, seed_dec, f_dets, 2.0)
                    if coincident is not None and not (coincident.flags & HARD_BAD_MASK):
                        ep_lbl = item.epoch_label or f"S{idx + 1}"
                        ed = _det_read_to_epoch_det(
                            coincident, idx, ep_lbl, item.frame, item.frame.epoch.mjd_tdb_mid
                        )
                        matched_track.detections.append(ed)
                        matched_track.links.append(
                            EpochLink(
                                matched_track.object_id,
                                idx,
                                ep_lbl,
                                ed.detection_id,
                                "LINKED",
                                0.0,
                                1.0,
                                0.92,
                                (0.0, 0.0),
                                "auto",
                            )
                        )
                matched_track.detections.sort(key=lambda d: d.epoch_index)

            # Fit requested motion models.
            clean_track_dets = [
                d
                for d in matched_track.detections
                if not (d.flags & HARD_BAD_MASK) and d.ra_deg is not None and d.dec_deg is not None
            ]
            fit_records: list[dict[str, Any]] = []
            mfit_pref: MotionFitResult | None = None
            if len(clean_track_dets) >= 2:
                candidates_fit: list[tuple[str, MotionFitResult]] = []
                for model_req in models:
                    if model_req in {"linear", "static"}:
                        with contextlib.suppress(Exception):
                            candidates_fit.append(
                                (model_req, fit_motion(matched_track, fit_parallax=False))
                            )
                    elif (
                        model_req in {"astrometric5", "astrometric5_prior"}
                        and len(clean_track_dets) >= 3
                    ):
                        with contextlib.suppress(Exception):
                            candidates_fit.append(
                                (model_req, fit_motion(matched_track, fit_parallax=True))
                            )
                if not candidates_fit:
                    with contextlib.suppress(Exception):
                        candidates_fit.append(("linear", fit_motion(matched_track)))
                if candidates_fit:
                    best_idx = int(np.argmin([mf.bic for _, mf in candidates_fit]))
                    mfit_pref = candidates_fit[best_idx][1]
                    for idx_f, (mname, mf) in enumerate(candidates_fit):
                        fit_records.append(
                            _motion_fit_record(mname, mf, is_preferred=(idx_f == best_idx))
                        )

            # Build persisted measurements and per-epoch association hypotheses.
            meas_rows: list[dict[str, Any]] = []
            assoc_rows: list[dict[str, Any]] = []
            chosen_confs: list[float] = []
            caveats: list[str] = []

            link_by_ep = {lk.epoch_index: lk for lk in matched_track.links}
            det_by_ep = {d.epoch_index: d for d in matched_track.detections}

            for idx, item in enumerate(loaded):
                assert item.observation_id is not None
                obs_id = item.observation_id
                ep_lbl = item.epoch_label or f"S{idx + 1}"
                mjd_ep = float(item.frame.epoch.mjd_tdb_mid)
                if mfit_pref is not None:
                    dt_yr = (mjd_ep - mfit_pref.ref_epoch_mjd_tdb) / 365.25
                    pr_ra, pr_dec = tangent_arcsec_to_sky(
                        mfit_pref.pmra_arcsec_yr * dt_yr,
                        mfit_pref.pmdec_arcsec_yr * dt_yr,
                        mfit_pref.ra_ref_deg,
                        mfit_pref.dec_ref_deg,
                    )
                    pred_ra, pred_dec = float(pr_ra) % 360.0, float(pr_dec)
                else:
                    pred_ra, pred_dec = seed_ra, seed_dec

                frame_dets = [
                    d for d in dets_by_fid.get(item.fits_file_id, []) if not d.is_difference
                ] or dets_by_fid.get(item.fits_file_id, [])
                lk = link_by_ep.get(idx)
                tr_det = det_by_ep.get(idx)
                is_user_locked = obs_id in user_by_obs

                matched_read: DetectionRead | None = None
                if tr_det is not None and tr_det.ra_deg is not None and tr_det.dec_deg is not None:
                    matched_read = _nearest_detection(
                        tr_det.ra_deg, tr_det.dec_deg, frame_dets, 2.0
                    )

                if tr_det is not None and tr_det.ra_deg is not None and tr_det.dec_deg is not None:
                    band = (
                        tr_det.band_id
                        or item.frame.band_id
                        or band_group_for_wavelength(item.frame.wavelength_um)
                    )
                    f_ujy_m: float | None
                    ferr_ujy_m: float | None
                    if matched_read is not None and matched_read.flux_ujy is not None:
                        f_ujy_m, ferr_ujy_m = matched_read.flux_ujy, matched_read.flux_err_ujy
                        sig_ra_m = matched_read.sig_ra_mas or tr_det.sig_ra_arcsec * 1000.0
                        sig_dec_m = matched_read.sig_dec_mas or tr_det.sig_dec_arcsec * 1000.0
                        corr_rd = matched_read.corr_radec or 0.0
                    else:
                        f_ujy_m, ferr_ujy_m, _ = flux_to_ujy_and_mag(
                            float(tr_det.flux or 0.0), float(tr_det.flux_err or 0.0), item.frame
                        )
                        sig_ra_m = tr_det.sig_ra_arcsec * 1000.0
                        sig_dec_m = tr_det.sig_dec_arcsec * 1000.0
                        corr_rd = 0.0

                    conf = (
                        1.0
                        if is_user_locked
                        else float(lk.match_prob if lk is not None else tr_det.confidence)
                    )
                    chosen_confs.append(conf)
                    if conf < 0.9:
                        caveats.append(f"Confidence < 0.9 at epoch {ep_lbl} ({conf:.2f})")

                    meas_rows.append(
                        {
                            "epoch": ep_lbl,
                            "observation_id": obs_id,
                            "source": "spherex",
                            "mjd_tdb": mjd_ep,
                            "ra": float(tr_det.ra_deg),
                            "dec": float(tr_det.dec_deg),
                            "sig_ra_mas": float(sig_ra_m),
                            "sig_dec_mas": float(sig_dec_m),
                            "corr_radec": float(corr_rd),
                            "band_group": band,
                            "flux_ujy": float(f_ujy_m) if f_ujy_m is not None else None,
                            "flux_err_ujy": float(ferr_ujy_m) if ferr_ujy_m is not None else None,
                            "is_upper_limit": bool(tr_det.is_upper_limit),
                            "flags": int(tr_det.flags),
                            "association_confidence": conf,
                            "used_in_fit": True,
                        }
                    )
                    if not is_user_locked:
                        assoc_rows.append(
                            {
                                "observation_id": obs_id,
                                "detection_id": matched_read.id if matched_read else None,
                                "pred_ra": pred_ra,
                                "pred_dec": pred_dec,
                                "cost": float(lk.cost_ll) if lk is not None else 0.0,
                                "confidence": conf,
                                "rank": 1,
                                "is_chosen": True,
                                "decided_by": "auto",
                                "reason": lk.status if lk is not None else "LINKED",
                                "flags": int(tr_det.flags),
                            }
                        )
                elif not is_user_locked:
                    if lk is not None:
                        assoc_rows.append(
                            {
                                "observation_id": obs_id,
                                "detection_id": None,
                                "pred_ra": pred_ra,
                                "pred_dec": pred_dec,
                                "cost": float(lk.cost_ll),
                                "confidence": float(lk.match_prob),
                                "rank": 1,
                                "is_chosen": False,
                                "decided_by": "auto",
                                "reason": lk.status,
                                "flags": 0,
                            }
                        )
                    x_p, y_p = (
                        float(v) for v in item.frame.wcs.world_to_pixel_values(pred_ra, pred_dec)
                    )
                    ny, nx = item.frame.shape
                    if -0.5 <= x_p <= nx - 0.5 and -0.5 <= y_p <= ny - 0.5:
                        f_ujy, ferr_ujy, f_snr, f_flags, band = _forced_phot_ujy(
                            item.frame, pred_ra, pred_dec
                        )
                        meas_rows.append(
                            {
                                "epoch": ep_lbl,
                                "observation_id": obs_id,
                                "source": "forced_psf",
                                "mjd_tdb": mjd_ep,
                                "ra": None,
                                "dec": None,
                                "sig_ra_mas": None,
                                "sig_dec_mas": None,
                                "corr_radec": None,
                                "band_group": band,
                                "flux_ujy": float(f_ujy),
                                "flux_err_ujy": float(ferr_ujy),
                                "is_upper_limit": f_snr < 3.0,
                                "flags": f_flags,
                                "association_confidence": None,
                                "used_in_fit": False,
                            }
                        )

                if not is_user_locked and frame_dets:
                    p_coord = SkyCoord(ra=pred_ra * u.deg, dec=pred_dec * u.deg, frame="icrs")
                    chosen_id = matched_read.id if matched_read is not None else None
                    alts: list[tuple[float, DetectionRead]] = []
                    for fd in frame_dets:
                        if fd.id == chosen_id:
                            continue
                        sep_a = float(
                            p_coord.separation(
                                SkyCoord(ra=fd.ra * u.deg, dec=fd.dec * u.deg, frame="icrs")
                            ).arcsec
                        )
                        if sep_a <= 25.0:
                            alts.append((sep_a, fd))
                    alts.sort(key=lambda pair: pair[0])
                    for alt_rank, (sep_a, fd) in enumerate(alts[:3], start=2):
                        alt_conf = round(
                            max(0.05, min(0.94, math.exp(-0.5 * (sep_a / 2.5) ** 2))), 4
                        )
                        assoc_rows.append(
                            {
                                "observation_id": obs_id,
                                "detection_id": fd.id,
                                "pred_ra": pred_ra,
                                "pred_dec": pred_dec,
                                "cost": round(sep_a, 3),
                                "confidence": alt_conf,
                                "rank": alt_rank,
                                "is_chosen": False,
                                "decided_by": "auto",
                                "reason": f"alternative ({sep_a:.2f} arcsec)",
                                "flags": int(fd.flags),
                            }
                        )

            cls_summary: dict[str, Any] | None = None
            if run_classification and matched_track.detections:
                cls_res = classify_source(
                    matched_track,
                    catalog_sources=cat_combined,
                )
                cons_dict: dict[str, Any] | None = None
                if len(clean_track_dets) >= 2:
                    with contextlib.suppress(Exception):
                        cons = evaluate_mover_consensus(
                            matched_track,
                            frames=[item.frame for item in loaded],
                            catalog_sources=cat_combined,
                        )
                        e1_str = (
                            "n/a"
                            if cons.e1 is None
                            else ("agree" if cons.e1.is_mover == cons.e3_mover else "disagree")
                        )
                        e2_str = (
                            "n/a (no catalog)"
                            if cons.e2 is None
                            else ("agree" if cons.e2.is_mover == cons.e3_mover else "disagree")
                        )
                        cons_dict = {
                            "verdict": cons.verdict,
                            "agreement_score": round(float(cons.agreement_score), 4),
                            "estimators": {
                                "E1": e1_str,
                                "E2": e2_str,
                                "E3": "mover" if cons.e3_mover else "static",
                                "E6": cons.e6_class,
                            },
                            "disagreements": list(cons.disagreements),
                        }
                        caveats.extend(cons.disagreements)
                cls_summary = {
                    "top_class": cls_res.top_class,
                    "top_prob": round(float(cls_res.top_prob), 4),
                    "probabilities": {
                        k: round(float(v), 4) for k, v in cls_res.probabilities.items()
                    },
                    "evidence": list(cls_res.evidence),
                    "consensus": cons_dict,
                }
                caveats.extend(cls_res.notes)

            if len(meas_rows) < 2:
                caveats.append("Single-epoch detection only; proper motion unconstrained.")
            if mfit_pref is not None and mfit_pref.vim_flagged:
                caveats.append("Possible Variability-Induced Mover (VIM) in unresolved blend.")

            ref_ra = float(mfit_pref.ra_ref_deg) if mfit_pref else seed_ra
            ref_dec = float(mfit_pref.dec_ref_deg) if mfit_pref else seed_dec
            ref_mjd = (
                float(mfit_pref.ref_epoch_mjd_tdb)
                if mfit_pref
                else float(b.get("ref_mjd_tdb") or loaded[0].frame.epoch.mjd_tdb_mid)
            )
            id_conf = float(min(chosen_confs)) if chosen_confs else None
            xm_rows = [
                {
                    "catalog": cm.catalog,
                    "catalog_id": cm.source_id,
                    "sep_arcsec": cm.sep_arcsec,
                    "match_prob": cm.match_prob,
                    "payload": {"gaia_id": cm.gaia_id, "mags": cm.mags},
                }
                for cm in matched_track.catalog_matches
            ]

            store.save_track_results(
                obj_id,
                obj_updates={
                    "public_id": format_public_id(ref_ra, ref_dec),
                    "ref_ra": ref_ra,
                    "ref_dec": ref_dec,
                    "ref_mjd_tdb": ref_mjd,
                    "status": "active" if meas_rows else "lost",
                    "identity_confidence": id_conf,
                },
                measurements=meas_rows,
                associations=assoc_rows,
                motion_fits=fit_records,
                classification=cls_summary,
                crossmatches=xm_rows,
                caveats=list(dict.fromkeys(caveats)),
            )
            store.record_provenance(
                processing_job_id=job_id,
                entity_table="tracked_objects",
                entity_id=str(obj_id),
                inputs=[str(item.fits_file_id) for item in loaded],
                params={"models": models, "run_classification": run_classification},
            )
            updated_ids.append(str(obj_id))
            store.update_job(job_id, progress=0.40 + 0.55 * ((b_idx + 1) / total_objs))

        outputs = {"object_ids": updated_ids, "models": models}
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


@celery_app.task(name=TRACKS_TASK_NAME, queue="heavy")  # type: ignore[untyped-decorator]
def run_tracks(job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Celery task wrapper executing multi-epoch tracking, fitting, and classification."""
    return execute_run_tracks(
        UUID(job_id),
        params,
        store=get_job_store(),
        storage=get_storage_backend(),
    )
