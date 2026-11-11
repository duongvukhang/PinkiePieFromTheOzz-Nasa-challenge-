"""Science acceptance tests: injection-recovery recall, motion fit error, and FP rates."""

from __future__ import annotations

import math

import numpy as np
from astropy.time import Time
from tests.science.conftest import require_science_api
from tests.science.synthetic_fits import InjectedSource, generate_synthetic_sequence


def _make_static_anchors(ra0: float, dec0: float, mjd0: float, n: int = 8) -> list[InjectedSource]:
    return [
        InjectedSource(
            source_id=f"anchor_{i}",
            ra_ref_deg=ra0 + (0.015 * ((i % 3) - 1)),
            dec_ref_deg=dec0 + (0.015 * ((i // 3) - 1)),
            ref_epoch_mjd=mjd0,
            base_flux=350.0,
            kind="static",
            gaia_id=1000 + i,
        )
        for i in range(n)
    ]


def test_injection_recovery_recall_and_motion_fit_error() -> None:
    """Injection-recovery: recall >= 0.90 for S/N >= 10, mu >= 5*sigma_mu; bounded motion error."""
    extract_sources = require_science_api(
        "sphx_core.extraction", "extract_sources", "Injection-recovery source extraction"
    )
    link_epochs = require_science_api(
        "sphx_core.identity", "link_epochs", "Injection-recovery cross-epoch linking"
    )
    fit_motion = require_science_api(
        "sphx_core.motion", "fit_motion", "Injection-recovery motion fit accuracy"
    )

    rng = np.random.default_rng(202501)
    ra0, dec0 = 150.0, 20.0
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    epochs = [mjd0 + 182.625 * k for k in range(4)]
    psf_norm = 2.0 * math.sqrt(math.pi) * 0.85 * 1.0

    eligible_total = recovered_total = 0
    motion_errs: list[float] = []
    for trial in range(20):
        mu = float(rng.uniform(0.2, 20.0))
        pa_rad = float(rng.uniform(0.0, 2.0 * math.pi))
        pmra, pmdec = mu * math.sin(pa_rad), mu * math.cos(pa_rad)
        snr = float(rng.uniform(5.0, 100.0)) if trial >= 15 else float(rng.uniform(12.0, 90.0))
        mover = InjectedSource(
            source_id=f"mover_{trial}",
            ra_ref_deg=ra0 - 0.5 * pmra / 3600.0,
            dec_ref_deg=dec0 - 0.5 * pmdec / 3600.0,
            ref_epoch_mjd=mjd0,
            pmra_arcsec_yr=pmra,
            pmdec_arcsec_yr=pmdec,
            base_flux=snr * psf_norm,
            kind="mover",
        )
        frames = generate_synthetic_sequence(
            seed=1000 + trial * 10,
            sources=[*_make_static_anchors(ra0, dec0, mjd0), mover],
            ra_center_deg=ra0,
            dec_center_deg=dec0,
            epochs_mjd=epochs,
        )
        fits = [
            fit_motion(tr) for tr in link_epochs([extract_sources(f.fits_bytes) for f in frames])
        ]
        matched = [
            f
            for f in fits
            if f.is_mover
            and math.hypot(f.ra_ref_deg - mover.ra_ref_deg, f.dec_ref_deg - mover.dec_ref_deg)
            * 3600.0
            < 5.0
        ]
        if snr >= 10.0 and mu >= 0.75:
            eligible_total += 1
            if matched:
                recovered_total += 1
                err = abs(math.hypot(matched[0].pmra_arcsec_yr, matched[0].pmdec_arcsec_yr) - mu)
                motion_errs.append(err)
                assert err <= max(3.0 * matched[0].sigma_mu_arcsec_yr, 0.3)

    assert eligible_total >= 12
    assert recovered_total / eligible_total >= 0.90
    assert float(np.median(motion_errs)) <= 0.25


def test_mover_false_positive_rate_noise_and_artifacts() -> None:
    """False-positive rate: <= 1 spurious mover across 100 noise + cosmic/persistence fields."""
    extract_sources = require_science_api(
        "sphx_core.extraction",
        "extract_sources",
        "False-positive mover rejection on noise/artifacts",
    )
    link_epochs = require_science_api(
        "sphx_core.identity",
        "link_epochs",
        "False-positive mover rejection across epochs",
    )
    classify_source = require_science_api(
        "sphx_core.classify", "classify_source", "False-positive rate <= 1 per 100 fields"
    )

    ra0, dec0 = 120.0, -15.0
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    epochs = [mjd0, mjd0 + 180.0, mjd0 + 360.0]
    false_positives = 0
    for idx in range(100):
        frames = generate_synthetic_sequence(
            seed=5000 + idx * 5,
            sources=_make_static_anchors(ra0, dec0, mjd0, n=4),
            ra_center_deg=ra0,
            dec_center_deg=dec0,
            epochs_mjd=epochs,
            n_cosmic_rays=3,
            n_persist_spots=1,
        )
        for tr in link_epochs([extract_sources(f.fits_bytes) for f in frames]):
            cls = classify_source(tr)
            if cls.label == "mover" or cls.probabilities.get("mover", 0.0) >= 0.5:
                false_positives += 1
                break

    assert false_positives <= 1


def test_variables_misclassified_as_movers_rate() -> None:
    """Variables misclassified as movers <= 1% across 100 static variables (Delta m 0.1-2 mag)."""
    difference_frames = require_science_api(
        "sphx_core.difference",
        "difference_frames",
        "Monopole vs dipole differencing for variable stars",
    )
    classify_source = require_science_api(
        "sphx_core.classify", "classify_source", "Variable-to-mover misclassification rate <= 1%"
    )

    rng = np.random.default_rng(202502)
    ra0, dec0 = 210.0, 35.0
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    epochs = [mjd0 + 120.0 * k for k in range(4)]
    misclassified = 0
    for idx in range(100):
        dm = float(rng.uniform(0.1, 2.0))
        var_star = InjectedSource(
            source_id=f"var_{idx}",
            ra_ref_deg=ra0,
            dec_ref_deg=dec0,
            ref_epoch_mjd=mjd0,
            flux_fn=lambda mjd, _b, d=dm: (
                150.0 * (10.0 ** (-0.4 * d * math.sin(0.03 * (mjd - mjd0))))
            ),
            kind="variable",
        )
        frames = generate_synthetic_sequence(
            seed=9000 + idx * 7,
            sources=[*_make_static_anchors(ra0, dec0, mjd0, n=4), var_star],
            ra_center_deg=ra0,
            dec_center_deg=dec0,
            epochs_mjd=epochs,
        )
        cls = classify_source(
            difference_frames([f.fits_bytes for f in frames]), ra_deg=ra0, dec_deg=dec0
        )
        misclassified += int(cls.label == "mover" or cls.probabilities.get("mover", 0.0) >= 0.5)

    assert misclassified / 100.0 <= 0.01
