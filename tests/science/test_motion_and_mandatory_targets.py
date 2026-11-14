"""Science acceptance tests: mandatory target validation from SCIENCE.md §F.3."""

from __future__ import annotations

import math

from astropy.time import Time
from tests.science.conftest import require_science_api
from tests.science.synthetic_fits import (
    InjectedSource,
    generate_synthetic_sequence,
    sky_position_at_epoch,
)

from app.db.flags import DetectionFlag


def _ang_diff(a_deg: float, b_deg: float) -> float:
    return abs((a_deg - b_deg + 180.0) % 360.0 - 180.0)


def _source_from_ref(
    source_id: str,
    ref: dict,
    *,
    ref_mjd: float,
    ra_key: str = "ra_deg",
    dec_key: str = "dec_deg",
    **kw: object,
) -> InjectedSource:
    return InjectedSource(
        source_id=source_id,
        ra_ref_deg=ref[ra_key],
        dec_ref_deg=ref[dec_key],
        ref_epoch_mjd=ref_mjd,
        pmra_arcsec_yr=ref["pmra_mas_yr"] * 1e-3,
        pmdec_arcsec_yr=ref["pmdec_mas_yr"] * 1e-3,
        parallax_mas=ref["parallax_mas"],
        gaia_id=ref.get("gaia_dr3_source_id"),
        **kw,  # type: ignore[arg-type]
    )


def test_barnards_star_acceptance_criteria(reference_values: dict) -> None:
    """Barnard's Star (§F.3): identity>=0.99, SPHEREx & anchored PM, parallax, class, consensus."""
    extract_sources = require_science_api(
        "sphx_core.extraction", "extract_sources", "Barnard's Star multi-channel extraction"
    )
    link_epochs = require_science_api(
        "sphx_core.identity", "link_epochs", "Barnard's Star persistent identity (conf >= 0.99)"
    )
    fit_motion = require_science_api(
        "sphx_core.motion", "fit_motion", "Barnard's Star 5-param PM + parallax fit"
    )
    classify_source = require_science_api(
        "sphx_core.classify", "classify_source", "Barnard's Star class star_pm P >= 0.8"
    )
    evaluate_mover_consensus = require_science_api(
        "sphx_core.consensus", "evaluate_mover_consensus", "Barnard's Star E1/E2/E3 mover consensus"
    )

    ref = reference_values["targets"]["barnards_star"]
    mjd_ref = Time(ref["ref_epoch_year"], format="jyear", scale="tdb").mjd
    epochs = [Time("2025-07-01T00:00:00", scale="tdb").mjd + 182.625 * k for k in range(4)]
    src = _source_from_ref(
        "barnards_star",
        ref,
        ref_mjd=mjd_ref,
        flux_fn=lambda _mjd, band: 500000.0 if band == "B1" else 400.0,
        kind="mover",
    )
    ra_c, dec_c = sky_position_at_epoch(
        ref["ra_deg"],
        ref["dec_deg"],
        mjd_ref,
        epochs[0],
        src.pmra_arcsec_yr,
        src.pmdec_arcsec_yr,
        src.parallax_mas,
    )
    frames_b1 = generate_synthetic_sequence(
        seed=30100,
        sources=[src],
        ra_center_deg=ra_c,
        dec_center_deg=dec_c,
        epochs_mjd=epochs,
        band_id="B1",
    )
    frames_b6 = generate_synthetic_sequence(
        seed=30200,
        sources=[src],
        ra_center_deg=ra_c,
        dec_center_deg=dec_c,
        epochs_mjd=epochs,
        band_id="B6",
    )

    cat_sources = [
        {
            "catalog": "gaiadr3",
            "source_id": str(ref["gaia_dr3_source_id"]),
            "gaia_id": ref["gaia_dr3_source_id"],
            "ra_deg": ref["ra_deg"],
            "dec_deg": ref["dec_deg"],
            "pmra_mas_yr": ref["pmra_mas_yr"],
            "pmdec_mas_yr": ref["pmdec_mas_yr"],
            "pmra_err_mas_yr": ref["pmra_err_mas_yr"],
            "pmdec_err_mas_yr": ref["pmdec_err_mas_yr"],
            "parallax_mas": ref["parallax_mas"],
            "parallax_err_mas": ref["parallax_err_mas"],
            "ref_epoch_mjd": mjd_ref,
            "mags": {
                m: ref[k]
                for m, k in [
                    ("G", "g_mag"),
                    ("J", "j_mag"),
                    ("H", "h_mag"),
                    ("Ks", "ks_mag"),
                    ("W1", "w1_mag"),
                    ("W2", "w2_mag"),
                ]
                if k in ref
            },
        }
    ]
    tracks = [
        t
        for t in link_epochs(
            [extract_sources(f.fits_bytes) for f in frames_b1 + frames_b6],
            catalog_sources=cat_sources,
        )
        if t.gaia_id == ref["gaia_dr3_source_id"]
    ]
    assert len(tracks) == 1 and tracks[0].confidence >= 0.99
    tr = tracks[0]

    fit_sphx = fit_motion(tr, fit_parallax=True)
    mu_fit = math.hypot(fit_sphx.pmra_arcsec_yr, fit_sphx.pmdec_arcsec_yr)
    pa_fit = math.degrees(math.atan2(fit_sphx.pmra_arcsec_yr, fit_sphx.pmdec_arcsec_yr)) % 360.0
    assert abs(mu_fit - ref["mu_arcsec_yr"]) < max(3.0 * fit_sphx.sigma_mu_arcsec_yr, 0.3)
    assert _ang_diff(pa_fit, ref["pa_deg"]) < max(3.0 * fit_sphx.sigma_pa_deg, 3.0)
    assert abs(fit_sphx.parallax_mas - ref["parallax_mas"]) < 3.0 * math.hypot(
        fit_sphx.sigma_parallax_mas, ref["parallax_err_mas"]
    )

    mjd_2m = Time(ref["twomass_jd"], format="jd", scale="tdb").mjd
    ra_2m, dec_2m = sky_position_at_epoch(
        ref["ra_deg"],
        ref["dec_deg"],
        mjd_ref,
        mjd_2m,
        src.pmra_arcsec_yr,
        src.pmdec_arcsec_yr,
        src.parallax_mas,
    )
    fit_anc = fit_motion(
        tr, anchors=[{"mjd_tdb": mjd_2m, "ra_deg": ra_2m, "dec_deg": dec_2m, "sigma_arcsec": 0.08}]
    )
    mu_anc = math.hypot(fit_anc.pmra_arcsec_yr, fit_anc.pmdec_arcsec_yr)
    assert abs(mu_anc - ref["mu_arcsec_yr"]) < min(3.0 * fit_anc.sigma_mu_arcsec_yr, 0.050)
    assert classify_source(tr, catalog_sources=cat_sources).probabilities["star_pm"] >= 0.8
    cons = evaluate_mover_consensus(tr, frames_b6, catalog_sources=cat_sources)
    assert cons.e1_mover and cons.e2_mover and cons.e3_mover


def test_epsilon_indi_b_acceptance_criteria(reference_values: dict) -> None:
    """Epsilon Indi B (§F.3): unresolved Ba/Bb not split, common PM with A, CH4 brown_dwarf."""
    extract_sources = require_science_api(
        "sphx_core.extraction", "extract_sources", "Epsilon Indi B unresolved binary extraction"
    )
    link_epochs = require_science_api(
        "sphx_core.identity", "link_epochs", "Epsilon Indi B single-identity constraint"
    )
    fit_motion = require_science_api(
        "sphx_core.motion", "fit_motion", "Epsilon Indi B proper motion and common-PM check"
    )
    classify_source = require_science_api(
        "sphx_core.classify", "classify_source", "Epsilon Indi B brown_dwarf P >= 0.6 with CH4"
    )

    ref_a, ref_b = (
        reference_values["targets"]["eps_indi_a"],
        reference_values["targets"]["eps_indi_b"],
    )
    mjd_ref_b = Time(ref_b["ref_epoch_jd"], format="jd", scale="tdb").mjd
    epochs = [Time("2025-07-01T00:00:00", scale="tdb").mjd + 182.625 * k for k in range(4)]
    eps_b = _source_from_ref(
        "eps_indi_b",
        ref_b,
        ref_mjd=mjd_ref_b,
        flux_fn=lambda _mjd, band: 35.0 if band == "B4" else 140.0,
        kind="binary_unresolved",
        companion_offset_arcsec=(ref_b["binary_separation_arcsec"], 0.0),
        companion_flux_ratio=0.35,
    )
    ra_c, dec_c = sky_position_at_epoch(
        ref_b["ra_deg"],
        ref_b["dec_deg"],
        mjd_ref_b,
        epochs[0],
        eps_b.pmra_arcsec_yr,
        eps_b.pmdec_arcsec_yr,
        eps_b.parallax_mas,
    )
    eps_a = InjectedSource(
        source_id="eps_indi_a",
        ra_ref_deg=ra_c - 0.025,
        dec_ref_deg=dec_c + 0.015,
        ref_epoch_mjd=epochs[0],
        pmra_arcsec_yr=ref_a["pmra_mas_yr"] * 1e-3,
        pmdec_arcsec_yr=ref_a["pmdec_mas_yr"] * 1e-3,
        parallax_mas=ref_a["parallax_mas"],
        base_flux=600000.0,
        kind="saturated",
        gaia_id=ref_a["gaia_dr3_source_id"],
    )
    frames = [
        *generate_synthetic_sequence(
            seed=31100,
            sources=[eps_b, eps_a],
            ra_center_deg=ra_c,
            dec_center_deg=dec_c,
            epochs_mjd=epochs,
            band_id="B4",
        ),
        *generate_synthetic_sequence(
            seed=31200,
            sources=[eps_b, eps_a],
            ra_center_deg=ra_c,
            dec_center_deg=dec_c,
            epochs_mjd=epochs,
            band_id="B6",
        ),
    ]
    cat_b = [
        {
            "catalog": "wds_ir",
            "source_id": "cat_eps_indi_b",
            "gaia_id": None,
            "ra_deg": float(ref_b["ra_deg"]),
            "dec_deg": float(ref_b["dec_deg"]),
            "ref_epoch_mjd": mjd_ref_b,
            "pmra_mas_yr": float(ref_b["pmra_mas_yr"]),
            "pmdec_mas_yr": float(ref_b["pmdec_mas_yr"]),
            "pmra_err_mas_yr": float(ref_b["pmra_err_mas_yr"]),
            "pmdec_err_mas_yr": float(ref_b["pmdec_err_mas_yr"]),
            "parallax_mas": float(ref_b["parallax_mas"]),
            "parallax_err_mas": float(ref_b["parallax_err_mas"]),
            "mags": {"J": float(ref_b.get("j_mag", 13.0)), "Ks": float(ref_b.get("ks_mag", 11.2))},
            "binary_separation_arcsec": float(ref_b["binary_separation_arcsec"]),
        }
    ]
    b_tracks = [
        t
        for t in link_epochs([extract_sources(f.fits_bytes) for f in frames], catalog_sources=cat_b)
        if not (t.flags & int(DetectionFlag.SATURATED))
    ]
    assert len(b_tracks) == 1
    tr_b = b_tracks[0]
    fit_b = fit_motion(tr_b)
    mu_b = math.hypot(fit_b.pmra_arcsec_yr, fit_b.pmdec_arcsec_yr)
    pa_b = math.degrees(math.atan2(fit_b.pmra_arcsec_yr, fit_b.pmdec_arcsec_yr)) % 360.0
    assert abs(mu_b - ref_b["mu_arcsec_yr"]) < max(3.0 * fit_b.sigma_mu_arcsec_yr, 0.5)
    assert abs(mu_b - ref_a["mu_arcsec_yr"]) / ref_a["mu_arcsec_yr"] < 0.10
    assert _ang_diff(pa_b, ref_a["pa_deg"]) < 5.0

    cls = classify_source(tr_b)
    assert cls.probabilities["brown_dwarf"] >= 0.6 and "CH4" in " ".join(cls.evidence).upper()
    assert "binary unresolved; orbit not measurable" in tr_b.notes_text.lower()


def test_wise_0855_acceptance_criteria(reference_values: dict) -> None:
    """WISE 0855-0714 (§F.3): B5/B6 >= 5sig, B1-B3 < 3sig, no Gaia hijack, Y dwarf."""
    search_target = require_science_api(
        "sphx_core.identity",
        "run_target_pipeline",
        "WISE 0855-0714 end-to-end FOV >= 6', B5/B6 >=5sig, B1-B3 <3sig",
    )

    ref = reference_values["targets"]["wise_0855"]
    mjd_j2000 = Time(ref["challenge_epoch_year"], format="jyear", scale="tdb").mjd
    epochs = [Time("2025-07-01T00:00:00", scale="tdb").mjd + 182.625 * k for k in range(3)]
    wise_src = _source_from_ref(
        "wise_0855",
        ref,
        ref_mjd=mjd_j2000,
        ra_key="challenge_ra_deg",
        dec_key="challenge_dec_deg",
        flux_fn=lambda _mjd, band: 45.0 if band in ("B5", "B6") else 0.0,
        kind="mover",
    )
    ra_25, dec_25 = sky_position_at_epoch(
        ref["challenge_ra_deg"],
        ref["challenge_dec_deg"],
        mjd_j2000,
        epochs[1],
        wise_src.pmra_arcsec_yr,
        wise_src.pmdec_arcsec_yr,
        ref["parallax_mas"],
    )
    bg_stars = [
        InjectedSource(
            source_id=f"bg_{i}",
            ra_ref_deg=ra_25 + 0.008 * (i - 2),
            dec_ref_deg=dec_25 + 0.003 * ((i % 2) - 0.5),
            ref_epoch_mjd=epochs[0],
            base_flux=120.0,
            kind="static",
            gaia_id=int(s["source_id"]),
        )
        for i, s in enumerate(reference_values["gaia_qa_stars"][:5])
    ]
    frames = [
        *generate_synthetic_sequence(
            seed=32100,
            sources=[*bg_stars, wise_src],
            ra_center_deg=ra_25,
            dec_center_deg=dec_25,
            epochs_mjd=epochs,
            band_id="B2",
        ),
        *generate_synthetic_sequence(
            seed=32200,
            sources=[*bg_stars, wise_src],
            ra_center_deg=ra_25,
            dec_center_deg=dec_25,
            epochs_mjd=epochs,
            band_id="B6",
        ),
    ]
    res = search_target(
        ra_query_deg=ref["challenge_ra_deg"],
        dec_query_deg=ref["challenge_dec_deg"],
        frames=frames,
        min_fov_arcmin=6.0,
    )
    assert res.detected_epochs_b56 >= 2 and res.snr_b56 >= 5.0 and res.snr_b13 < 3.0
    assert res.gaia_id is None and res.spectral_subtype == "Y"
    assert abs(res.mu_arcsec_yr - ref["mu_arcsec_yr"]) < max(3.0 * res.sigma_mu_arcsec_yr, 1.0)
    assert res.class_probabilities["brown_dwarf"] >= 0.7
    assert all(d.confidence >= 0.9 or d.ambiguous_match for d in res.detections)

    empty_b56 = generate_synthetic_sequence(
        seed=32300,
        sources=bg_stars,
        ra_center_deg=ra_25,
        dec_center_deg=dec_25,
        epochs_mjd=epochs,
        band_id="B6",
    )
    res_empty = search_target(
        ra_query_deg=ref["challenge_ra_deg"],
        dec_query_deg=ref["challenge_dec_deg"],
        frames=empty_b56,
        min_fov_arcmin=6.0,
    )
    assert not res_empty.detected and res_empty.upper_limit_flux > 0.0


def test_rw_aurigae_acceptance_criteria(reference_values: dict) -> None:
    """RW Aurigae (§F.3): mover P<0.05, monopole BIC, VIM flagged, YSO P>=0.7, A-vs-B label."""
    difference_frames = require_science_api(
        "sphx_core.difference", "difference_frames", "RW Aurigae monopole vs dipole BIC test"
    )
    fit_motion = require_science_api(
        "sphx_core.motion", "fit_motion", "RW Aurigae zero anomalous motion & VIM check"
    )
    classify_source = require_science_api(
        "sphx_core.classify", "classify_source", "RW Aurigae variable_star (YSO) P >= 0.7"
    )
    compare_epochs = require_science_api(
        "sphx_core.comparison", "compare_epochs", "RW Aurigae A-vs-B changed brightness label"
    )

    ref = reference_values["targets"]["rw_aur"]
    mjd_ref = Time(ref["ref_epoch_year"], format="jyear", scale="tdb").mjd
    epochs = [Time("2025-07-01T00:00:00", scale="tdb").mjd + 120.0 * k for k in range(4)]
    flux_mod = [250.0, 120.0, 260.0, 110.0]
    rw_src = _source_from_ref(
        "rw_aur",
        ref,
        ref_mjd=mjd_ref,
        flux_fn=lambda mjd, _b: flux_mod[min(range(4), key=lambda i: abs(epochs[i] - mjd))],
        kind="variable",
        companion_offset_arcsec=(ref["binary_separation_arcsec"], 0.0),
        companion_flux_fn=lambda _mjd, _b: 85.0,
    )
    frames = generate_synthetic_sequence(
        seed=33100,
        sources=[rw_src],
        ra_center_deg=ref["ra_deg"],
        dec_center_deg=ref["dec_deg"],
        epochs_mjd=epochs,
        band_id="B3",
    )

    diff = difference_frames([f.fits_bytes for f in frames])
    assert diff.monopole_favored and diff.delta_bic_mv_over_mm > 0.0

    fit = fit_motion(frames, target_ra_deg=ref["ra_deg"], target_dec_deg=ref["dec_deg"])
    gaia_err = 1e-3 * math.hypot(ref["pmra_err_mas_yr"], ref["pmdec_err_mas_yr"])
    mu_fit = math.hypot(fit.pmra_arcsec_yr, fit.pmdec_arcsec_yr)
    assert abs(mu_fit - ref["mu_arcsec_yr"]) < 3.0 * math.hypot(fit.sigma_mu_arcsec_yr, gaia_err)
    assert fit.vim_flagged

    cls = classify_source(diff, ra_deg=ref["ra_deg"], dec_deg=ref["dec_deg"])
    assert cls.probabilities["mover"] < 0.05 and cls.probabilities["variable_star"] >= 0.7

    entry = compare_epochs(frames[0].fits_bytes, frames[1].fits_bytes).find_nearest(
        ref["ra_deg"], ref["dec_deg"]
    )
    assert entry.status == "changed brightness" and entry.status != "moved"


def test_r_draconis_acceptance_criteria(reference_values: dict) -> None:
    """R Draconis (§F.3): saturated honesty test + unsaturated >=8-epoch Mira period within 10%."""
    classify_source = require_science_api(
        "sphx_core.classify", "classify_source", "R Draconis SATURATED honesty & Mira class"
    )
    measure_period = require_science_api(
        "sphx_core.motion", "measure_variability_period", "R Draconis period within 10% of 245.6 d"
    )

    ref = reference_values["targets"]["r_dra"]
    mjd_ref = Time(ref["ref_epoch_year"], format="jyear", scale="tdb").mjd
    period_true = float(ref["period_days"])
    t0 = Time("2025-07-01T00:00:00", scale="tdb").mjd

    sat_src = _source_from_ref(
        "r_dra_sat", ref, ref_mjd=mjd_ref, base_flux=600000.0, kind="saturated"
    )
    sat_frames = generate_synthetic_sequence(
        seed=34100,
        sources=[sat_src],
        ra_center_deg=ref["ra_deg"],
        dec_center_deg=ref["dec_deg"],
        epochs_mjd=[t0 + 90.0 * k for k in range(4)],
    )
    cls_sat = classify_source(sat_frames, ra_deg=ref["ra_deg"], dec_deg=ref["dec_deg"])
    assert cls_sat.probabilities["mover"] < 0.05
    assert cls_sat.flags & int(DetectionFlag.SATURATED)
    assert "photometry unreliable" in cls_sat.notes_text.lower()

    unsat_epochs = [t0 + 60.0 * k for k in range(10)]
    mira_src = _source_from_ref(
        "r_dra_mira",
        ref,
        ref_mjd=mjd_ref,
        flux_fn=lambda mjd, _b: 220.0 + 140.0 * math.sin(2.0 * math.pi * (mjd - t0) / period_true),
        kind="variable",
    )
    unsat_frames = generate_synthetic_sequence(
        seed=34200,
        sources=[mira_src],
        ra_center_deg=ref["ra_deg"],
        dec_center_deg=ref["dec_deg"],
        epochs_mjd=unsat_epochs,
    )
    cls_mira = classify_source(unsat_frames, ra_deg=ref["ra_deg"], dec_deg=ref["dec_deg"])
    assert cls_mira.probabilities["mover"] < 0.05 and cls_mira.probabilities["variable_star"] >= 0.7
    p_est = measure_period(unsat_frames, ra_deg=ref["ra_deg"], dec_deg=ref["dec_deg"])
    assert abs(p_est.period_days - period_true) / period_true <= 0.10
