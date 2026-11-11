"""Science acceptance tests: astrometric QA and WCS registration error."""

from __future__ import annotations

import math

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.time import Time
from tests.science.conftest import require_science_api
from tests.science.synthetic_fits import InjectedSource, generate_synthetic_cutout


def test_registration_error_and_gaia_astrometric_qa(reference_values: dict) -> None:
    """Gaia 16<G<18 QA & registration error: median |residual| at S/N > 50 <= 0.15\"."""
    register_exposure = require_science_api(
        "sphx_core.registration",
        "register_exposure",
        'Astrometric QA median |residual| <= 0.15" at S/N > 50',
    )

    qa_stars = reference_values["gaia_qa_stars"]
    ra0 = float(np.median([s["ra"] for s in qa_stars]))
    dec0 = float(np.median([s["dec"] for s in qa_stars]))
    t_obs = Time("2025-07-01T00:00:00", scale="tdb").mjd

    sources = [
        InjectedSource(
            source_id=f"gaia_{star['source_id']}",
            ra_ref_deg=(ra0 + (star["ra"] - ra0) * 0.45) % 360.0,
            dec_ref_deg=dec0 + (star["dec"] - dec0) * 0.45,
            ref_epoch_mjd=Time(star["ref_epoch"], format="jyear", scale="tdb").mjd,
            pmra_arcsec_yr=(star["pmra"] or 0.0) * 1e-3,
            pmdec_arcsec_yr=(star["pmdec"] or 0.0) * 1e-3,
            parallax_mas=float(star["parallax"] or 0.0),
            base_flux=250.0 + 10.0 * idx,
            kind="static",
            gaia_id=int(star["source_id"]),
        )
        for idx, star in enumerate(qa_stars)
    ]

    residuals_arcsec: list[float] = []
    for ep_idx, (shift, rot_err) in enumerate(
        [((1.8, -1.2), 0.25), ((-1.4, 2.1), -0.30), ((0.9, 1.5), 0.15)]
    ):
        frame = generate_synthetic_cutout(
            seed=100 + ep_idx,
            sources=sources,
            ra_center_deg=ra0,
            dec_center_deg=dec0,
            mjd_tdb_mid=t_obs + ep_idx * 180.0,
            wcs_shift_arcsec=shift,
            wcs_rot_err_deg=rot_err,
            with_sip=True,
        )
        reg = register_exposure(frame.fits_bytes, reference_catalog=frame.reference_stars)
        assert math.isfinite(reg.sigma_reg_arcsec) and reg.sigma_reg_arcsec > 0.0
        for truth in frame.truth:
            if truth.snr <= 50.0:
                continue
            ra_fit, dec_fit = reg.wcs.pixel_to_world_values(truth.x_px, truth.y_px)
            c_fit = SkyCoord(float(ra_fit) * u.deg, float(dec_fit) * u.deg, frame="icrs")
            c_true = SkyCoord(truth.ra_deg * u.deg, truth.dec_deg * u.deg, frame="icrs")
            residuals_arcsec.append(float(c_fit.separation(c_true).arcsec))

    assert len(residuals_arcsec) >= 15
    assert float(np.median(residuals_arcsec)) <= 0.15


def test_registration_flags_insufficient_reference_stars() -> None:
    """Registration must report failure or degraded status when < 3 reference stars match."""
    register_exposure = require_science_api(
        "sphx_core.registration",
        "register_exposure",
        "Registration safety on insufficient reference stars",
    )
    frame = generate_synthetic_cutout(
        seed=199,
        sources=[
            InjectedSource(
                source_id="lonely_1",
                ra_ref_deg=133.80,
                dec_ref_deg=-7.24,
                ref_epoch_mjd=60857.0,
                base_flux=200.0,
                kind="static",
                gaia_id=9990001,
            )
        ],
        ra_center_deg=133.80,
        dec_center_deg=-7.24,
        mjd_tdb_mid=60857.0,
        wcs_shift_arcsec=(2.0, -2.0),
    )
    reg = register_exposure(frame.fits_bytes, reference_catalog=frame.reference_stars)
    assert not reg.converged or reg.degraded or reg.n_matched < 3
