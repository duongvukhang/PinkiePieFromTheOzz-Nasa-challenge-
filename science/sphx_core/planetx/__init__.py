"""Planet X / outer Solar System trans-Neptunian object search and parallactic recovery (§I)."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Sequence

import astropy.units as u
from astropy.coordinates import SkyCoord
import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize_scalar

from sphx_core.calib import FrameData, load_frame
from sphx_core.extraction import extract_sources
from sphx_core.motion import observer_barycentric_au, parallax_factors
from sphx_core.wcs import sky_to_tangent_arcsec

_RAD_TO_ARCSEC = 206264.806247


@dataclass(frozen=True)
class PlanetXCandidate:
    """Outer Solar System Planet X / TNO candidate detection."""

    recovered: bool
    ra_deg: float
    dec_deg: float
    sigma_pos_arcsec: float
    parallactic_offset_arcsec: float
    sigma_parallax_arcsec: float
    target_name: str | None = None
    distance_au: float | None = None
    snr: float = 0.0
    flux: float = 0.0
    n_epochs: int = 0


def _fit_candidate_parallax(
    detections: list[tuple[float, Any]],
) -> tuple[float, float, float] | None:
    """Fit parallactic offset pi [arcsec = 1/delta] and proper motion for linked candidate epochs.

    Model: Delta r(t) = Delta r_0 + mu * (t - t0) + pi * P(t).
    Returns (pi_arcsec, sigma_pi, loss) if physical outer Solar System distance (delta >= 15 AU), else None.
    """
    if len(detections) < 2:
        return None

    t_ref, d_ref = detections[-1]
    b_ref = observer_barycentric_au(t_ref)

    c = SkyCoord(d_ref.ra_deg * u.deg, d_ref.dec_deg * u.deg, frame="icrs")
    c_ecl = c.geocentricmeanecliptic
    cos_d = max(math.cos(math.radians(d_ref.dec_deg)), 1e-4)

    def _ecl_unit(d_lon: float, d_lat: float) -> NDArray[np.float64]:
        c2 = SkyCoord(
            lon=c_ecl.lon + d_lon * u.deg, lat=c_ecl.lat + d_lat * u.deg, frame="geocentricmeanecliptic"
        ).icrs
        v = np.array([(c2.ra.deg - d_ref.ra_deg) * cos_d, c2.dec.deg - d_ref.dec_deg], dtype=np.float64)
        norm = float(np.linalg.norm(v))
        return v / norm if norm > 0 else v

    e_lam, e_bet = _ecl_unit(0.01, 0.0), _ecl_unit(0.0, 0.01)

    t0, d0 = detections[0]
    b0 = observer_barycentric_au(t0)
    p0 = np.array(parallax_factors(b0, d_ref.ra_deg, d_ref.dec_deg))
    p_ref = np.array(parallax_factors(b_ref, d_ref.ra_deg, d_ref.dec_deg))
    dp = p_ref - p0
    P = dp * _RAD_TO_ARCSEC
    dt = (t_ref - t0) / 365.25

    if abs(dt) < 1e-4 or np.linalg.norm(P) < 1e-2:
        return None

    xi, eta = sky_to_tangent_arcsec(d_ref.ra_deg, d_ref.dec_deg, d0.ra_deg, d0.dec_deg)
    dr = np.array([float(xi), float(eta)])

    def _obj(pi_val: float) -> float:
        mu = (dr - pi_val * P) / dt
        mu_mag = np.linalg.norm(mu)
        mu_lam = np.dot(mu, e_lam)
        mu_bet = np.dot(mu, e_bet)
        mu_kepler = 1296000.0 * (pi_val**1.5)
        return float(
            (mu_mag - mu_kepler) ** 2
            + min(0.0, mu_lam) ** 2 * 1e4
            + max(0.0, abs(mu_bet) - 0.5 * mu_kepler) ** 2
        )

    res = minimize_scalar(_obj, bounds=(0.002, 0.08), method="bounded")
    if not res.success:
        return None

    pi_fit = float(res.x)
    if not (0.001 <= pi_fit <= 0.07):
        return None

    sig_pi = max(0.005, 0.15 * pi_fit)
    return pi_fit, sig_pi, float(res.fun)


def search_planetx(
    frames: Sequence[Any],
    *,
    detect_thresh_sigma: float = 1.5,
) -> PlanetXCandidate:
    """Search multi-epoch frames for Planet X / slow trans-Neptunian outer Solar System candidates (§I).

    Recovers candidate positions at the latest epoch and determines topocentric parallactic
    distance (parallactic offset 1 / delta [arcsec]) via Keplerian/barycentric constraint.
    """
    if not frames or len(frames) < 2:
        return PlanetXCandidate(
            recovered=False,
            ra_deg=0.0,
            dec_deg=0.0,
            sigma_pos_arcsec=1.0,
            parallactic_offset_arcsec=0.0,
            sigma_parallax_arcsec=1.0,
        )

    loaded_frames = [f if isinstance(f, FrameData) else load_frame(f) for f in frames]
    sorted_frames = sorted(loaded_frames, key=lambda f: float(f.epoch.mjd_tdb_mid))

    frame_dets = [
        extract_sources(f, detect_thresh_sigma=detect_thresh_sigma).detections
        for f in sorted_frames
    ]

    last_dets = frame_dets[-1]
    first_dets = frame_dets[0]
    if not last_dets or not first_dets:
        return PlanetXCandidate(
            recovered=False,
            ra_deg=0.0,
            dec_deg=0.0,
            sigma_pos_arcsec=1.0,
            parallactic_offset_arcsec=0.0,
            sigma_parallax_arcsec=1.0,
            n_epochs=len(sorted_frames),
        )

    t_first = float(sorted_frames[0].epoch.mjd_tdb_mid)
    t_last = float(sorted_frames[-1].epoch.mjd_tdb_mid)

    best_cand: tuple[float, Any, tuple[float, float, float]] | None = None
    for d_last in last_dets:
        for d_first in first_dets:
            fit = _fit_candidate_parallax([(t_first, d_first), (t_last, d_last)])
            if fit is not None:
                loss = fit[2]
                if best_cand is None or loss < best_cand[0]:
                    best_cand = (loss, d_last, fit)

    if best_cand is None:
        return PlanetXCandidate(
            recovered=False,
            ra_deg=float(last_dets[0].ra_deg),
            dec_deg=float(last_dets[0].dec_deg),
            sigma_pos_arcsec=1.0,
            parallactic_offset_arcsec=0.0,
            sigma_parallax_arcsec=1.0,
            n_epochs=len(sorted_frames),
        )

    _, d_best, (pi_fit, sig_pi, _) = best_cand
    sig_pos = max(float(d_best.sig_ra_arcsec), float(d_best.sig_dec_arcsec), 0.05)
    return PlanetXCandidate(
        recovered=True,
        ra_deg=float(d_best.ra_deg),
        dec_deg=float(d_best.dec_deg),
        sigma_pos_arcsec=sig_pos,
        parallactic_offset_arcsec=pi_fit,
        sigma_parallax_arcsec=sig_pi,
        distance_au=1.0 / pi_fit,
        snr=float(d_best.snr),
        flux=float(d_best.flux),
        n_epochs=len(sorted_frames),
    )
