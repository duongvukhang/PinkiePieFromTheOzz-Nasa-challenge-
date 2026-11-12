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
from app.workers.celery_app import celery_app
from sphx_core.calib import HARD_BAD_MASK, FrameData, band_group_for_wavelength, load_frame
from sphx_core.classify import classify_source
from sphx_core.difference import difference_frames
from sphx_core.extraction import ExtractionResult, extract_sources
from sphx_core.identity import link_epochs
from sphx_core.io import inspect_fits
from sphx_core.registration import register_exposure
from sphx_core.wcs import healpix9_nested

ANALYSIS_TASK_NAME = "app.workers.tasks_heavy.run_analysis"
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


def _flux_to_ujy_and_mag(
    flux: float, flux_err: float, frame: FrameData
) -> tuple[float, float, float | None]:
    bunit = (frame.bunit or "").strip().lower()
    pix_area_sr = (frame.wcs_metrics.pixscale_arcsec * math.pi / (180.0 * 3600.0)) ** 2
    scale = 1.0
    if not bunit:
        scale = 1e12 * pix_area_sr
    else:
        for token, factor in (
            ("mjy/sr", 1e12 * pix_area_sr),
            ("jy/sr", 1e6 * pix_area_sr),
            ("mjy", 1e3),
            ("ujy", 1.0),
            ("jy", 1e6),
        ):
            if token in bunit:
                scale = factor
                break
    flux_ujy, err_ujy = float(flux * scale), float(flux_err * scale)
    mag_ab: float | None = None
    if flux_ujy > 0.0 and math.isfinite(flux_ujy):
        mag_val = float((flux_ujy * u.uJy).to_value(u.ABmag))
        if math.isfinite(mag_val):
            mag_ab = mag_val
    return flux_ujy, err_ujy, mag_ab


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

        store.update_job(job_id, progress=0.15)
        warnings_out: list[str] = []

        # Best-effort external catalog queries covering the union field of view.
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
                flux_ujy, err_ujy, mag_ab = _flux_to_ujy_and_mag(src.flux, src.flux_err, item.frame)
                rows.append(
                    {
                        "healpix9": healpix9_nested(src.ra_deg, src.dec_deg),
                        "fits_file_id": item.fits_file_id,
                        "observation_id": None,
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
                                    "observation_id": None,
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
