"""Science acceptance tests: known SSO residual vs Horizons and Planet X stretch recovery."""

from __future__ import annotations

import math

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time
from tests.science.conftest import require_science_api
from tests.science.synthetic_fits import InjectedSource, generate_synthetic_cutout


def test_sso_eros_residuals_against_horizons(reference_values: dict) -> None:
    """SSO QA: known asteroid (433) Eros residuals vs JPL Horizons < 3 sigma for >= 95%."""
    evaluate_sso_residuals = require_science_api(
        "sphx_core.orbits",
        "evaluate_sso_residuals",
        "SSO residual vs JPL Horizons < 3 sigma for >= 95%",
    )

    ep0 = reference_values["sso"]["eros_433"]["epochs"][0]
    mjd0 = Time(ep0["epoch_ut"], scale="utc").tdb.mjd
    cos_dec = math.cos(math.radians(ep0["dec_deg"]))
    dra_deg_day = (ep0["dra_cosdec_arcsec_hr"] * 24.0 / 3600.0) / cos_dec
    ddec_deg_day = ep0["ddec_arcsec_hr"] * 24.0 / 3600.0

    frames, truth_ephem = [], []
    for idx in range(20):
        dt = idx * 0.02
        ra_e, dec_e, mjd_e = (
            ep0["ra_deg"] + dra_deg_day * dt,
            ep0["dec_deg"] + ddec_deg_day * dt,
            mjd0 + dt,
        )
        src = InjectedSource("eros_433", ra_e, dec_e, mjd_e, base_flux=250.0, kind="sso")
        frames.append(
            generate_synthetic_cutout(
                seed=40000 + idx,
                sources=[src],
                ra_center_deg=ra_e + 0.001,
                dec_center_deg=dec_e,
                mjd_tdb_mid=mjd_e,
            )
        )
        truth_ephem.append({"mjd_tdb": mjd_e, "ra_deg": ra_e, "dec_deg": dec_e})

    residuals = evaluate_sso_residuals([f.fits_bytes for f in frames], ephemeris=truth_ephem)
    assert len(residuals) == 20
    assert sum(1 for r in residuals if r.chi_residual < 3.0) / len(residuals) >= 0.95


def test_planetx_mode_stretch_tno_recovery(reference_values: dict) -> None:
    """Planet X stretch test: recover Pluto, Makemake, Haumea at correct parallactic offsets."""
    search_planetx = require_science_api(
        "sphx_core.planetx",
        "search_planetx",
        "Planet X stretch test: recover Pluto, Makemake, Haumea parallactic offsets",
    )

    for idx, key in enumerate(("pluto", "makemake", "haumea")):
        epochs = reference_values["planetx"][key]["epochs"]
        flux = 220.0 if key == "pluto" else 65.0
        frames = [
            generate_synthetic_cutout(
                seed=41000 + idx * 10 + j,
                sources=[
                    InjectedSource(
                        source_id=key,
                        ra_ref_deg=ep["ra_deg"],
                        dec_ref_deg=ep["dec_deg"],
                        ref_epoch_mjd=Time(ep["epoch_ut"], scale="utc").tdb.mjd,
                        base_flux=flux,
                        kind="sso",
                    )
                ],
                ra_center_deg=ep["ra_deg"],
                dec_center_deg=ep["dec_deg"],
                mjd_tdb_mid=Time(ep["epoch_ut"], scale="utc").tdb.mjd,
            )
            for j, ep in enumerate(epochs[:2])
        ]
        cand = search_planetx([f.fits_bytes for f in frames])
        assert cand.recovered
        ep_b = epochs[1]
        c_fit = SkyCoord(cand.ra_deg * u.deg, cand.dec_deg * u.deg, frame="icrs")
        c_ref = SkyCoord(ep_b["ra_deg"] * u.deg, ep_b["dec_deg"] * u.deg, frame="icrs")
        assert float(c_fit.separation(c_ref).arcsec) < 3.0 * cand.sigma_pos_arcsec
        expected_plx = 1.0 / ep_b["delta_au"]
        assert abs(cand.parallactic_offset_arcsec - expected_plx) < 3.0 * cand.sigma_parallax_arcsec


def test_search_planetx_integrity_no_ref_lookup_and_no_default_recovered() -> None:
    """Planet X integrity: search_planetx must not read reference_values.json and must not return recovered=True on noise."""
    import sphx_core.planetx as px_mod

    assert not hasattr(px_mod, "_REF_PATH"), "sphx_core.planetx must not define or use _REF_PATH"

    noise_frames = [
        generate_synthetic_cutout(
            seed=55000 + j,
            sources=[],
            ra_center_deg=180.0,
            dec_center_deg=10.0,
            mjd_tdb_mid=60800.0 + j * 180.0,
        )
        for j in range(2)
    ]
    cand = px_mod.search_planetx([f.fits_bytes for f in noise_frames])
    assert not cand.recovered, "search_planetx must not report recovered=True on noise-only frames"


def test_evaluate_sso_residuals_missed_epoch_not_zero() -> None:
    """Missed detections (no source within 15\") must be marked matched=False and not score 0.0\"."""
    from sphx_core.orbits import evaluate_sso_residuals

    noise_frame = generate_synthetic_cutout(
        seed=55100,
        sources=[],
        ra_center_deg=180.0,
        dec_center_deg=10.0,
        mjd_tdb_mid=60800.0,
    )
    eph = [{"mjd_tdb": 60800.0, "ra_deg": 180.0, "dec_deg": 10.0}]
    residuals = evaluate_sso_residuals([noise_frame.fits_bytes], ephemeris=eph)
    assert len(residuals) == 1
    r = residuals[0]
    assert not getattr(r, "matched", True)
    assert r.sep_arcsec != 0.0


