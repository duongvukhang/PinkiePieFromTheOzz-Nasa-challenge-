"""Science acceptance tests: crowded-field cross-epoch identity matching accuracy."""

from __future__ import annotations

import math

import numpy as np
from astropy.time import Time
from tests.science.conftest import require_science_api
from tests.science.synthetic_fits import InjectedSource, generate_synthetic_sequence


def test_crowded_field_identity_matching_accuracy() -> None:
    """Identity matching: >= 99% at S/N>=10, >= 95% overall, silent errors (conf>=0.9) <= 0.5%."""
    extract_sources = require_science_api(
        "sphx_core.extraction",
        "extract_sources",
        "Crowded-field source extraction for identity matching",
    )
    link_epochs = require_science_api(
        "sphx_core.identity",
        "link_epochs",
        "Identity accuracy >=99% (S/N>=10), >=95% overall, silent errors <=0.5%",
    )

    rng = np.random.default_rng(202503)
    ra0, dec0 = 260.0, -29.0
    mjd0 = Time("2025-07-01T00:00:00", scale="tdb").mjd
    epochs = [mjd0 + 182.625 * k for k in range(4)]
    psf_norm = 2.0 * math.sqrt(math.pi) * 0.85 * 1.0
    cos_d = math.cos(math.radians(dec0))

    total_links = correct_links = high_snr_total = high_snr_correct = silent_errors = 0
    for field_idx in range(10):
        sources: list[InjectedSource] = []
        for s_idx in range(20):
            dx, dy = float(rng.uniform(-150.0, 150.0)), float(rng.uniform(-150.0, 150.0))
            is_mover = s_idx < 5
            snr = float(rng.uniform(12.0, 80.0)) if s_idx < 16 else float(rng.uniform(5.5, 9.5))
            sources.append(
                InjectedSource(
                    source_id=f"f{field_idx}_s{s_idx}",
                    ra_ref_deg=ra0 + (dx / 3600.0) / cos_d,
                    dec_ref_deg=dec0 + dy / 3600.0,
                    ref_epoch_mjd=mjd0,
                    pmra_arcsec_yr=float(rng.uniform(-6.0, 6.0)) if is_mover else 0.0,
                    pmdec_arcsec_yr=float(rng.uniform(-6.0, 6.0)) if is_mover else 0.0,
                    base_flux=snr * psf_norm,
                    kind="mover" if is_mover else "static",
                    gaia_id=None if is_mover else (field_idx * 100 + s_idx),
                )
            )
        frames = generate_synthetic_sequence(
            seed=20000 + field_idx * 10,
            sources=sources,
            ra_center_deg=ra0,
            dec_center_deg=dec0,
            epochs_mjd=epochs,
        )
        for tr in link_epochs([extract_sources(f.fits_bytes) for f in frames]):
            truth_ids = [
                min(
                    frames[d.epoch_index].truth,
                    key=lambda t, d=d: math.hypot(t.x_px - d.x_px, t.y_px - d.y_px),
                ).source_id
                for d in tr.detections
            ]
            if not truth_ids:
                continue
            majority_id = max(set(truth_ids), key=truth_ids.count)
            for det, tid in zip(tr.detections, truth_ids, strict=True):
                total_links += 1
                is_ok = tid == majority_id
                correct_links += int(is_ok)
                if not is_ok:
                    assert det.confidence < 0.9 or det.ambiguous_match
                    silent_errors += int(det.confidence >= 0.9)
                if det.snr >= 10.0:
                    high_snr_total += 1
                    high_snr_correct += int(is_ok)

    assert high_snr_total > 0 and total_links > 0
    assert high_snr_correct / high_snr_total >= 0.99
    assert correct_links / total_links >= 0.95
    assert silent_errors / total_links <= 0.005
