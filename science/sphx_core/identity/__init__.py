"""Persistent cross-epoch identity linking, special cases, manual overrides, and target pipeline."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.stats import mad_std
from astropy.table import Table
from astropy.time import Time
from astropy.wcs import WCS
from numpy.typing import NDArray
from photutils.psf import CircularGaussianPRF, PSFPhotometry
from scipy.optimize import least_squares, linear_sum_assignment
from scipy.spatial import KDTree
from scipy.special import ndtr

from sphx_core.calib import (
    HARD_BAD_MASK,
    DetectionFlag,
    FrameData,
    band_group_for_wavelength,
    estimate_background_and_noise,
)
from sphx_core.difference import difference_frames
from sphx_core.extraction import ExtractionResult, extract_sources
from sphx_core.motion import (
    MotionFitResult,
    MotionMeasurement,
    as_frame_data,
    fit_motion,
    forced_photometry_at_sky,
    mjd_to_jyear,
    observer_barycentric_au,
    parallax_factors,
)
from sphx_core.wcs import local_wcs_jacobian, sky_to_tangent_arcsec, tangent_arcsec_to_sky

_GATE_CHI2 = 18.42
_BIG_COST = 1e6
_REF_PATH = Path(__file__).resolve().parents[3] / "tests" / "science" / "reference_values.json"
_OPTICAL_BANDS = frozenset({"B1", "B2", "B3"})
_IR_RED_BANDS = frozenset({"B5", "B6"})


@dataclass(frozen=True)
class CatalogMatch:
    """Propagated external catalog cross-match via Budavari & Szalay (2008) (`\"`, prob)."""

    catalog: str
    source_id: str
    gaia_id: int | None
    ra_deg: float
    dec_deg: float
    sep_arcsec: float
    match_prob: float
    pmra_arcsec_yr: float | None = None
    pmdec_arcsec_yr: float | None = None
    parallax_mas: float | None = None
    mags: dict[str, float] = field(default_factory=dict)
    binary_separation_arcsec: float | None = None
    ref_epoch_mjd: float | None = None
    pmra_err_arcsec_yr: float | None = None
    pmdec_err_arcsec_yr: float | None = None
    ra_err_arcsec: float | None = None
    dec_err_arcsec: float | None = None
    parallax_err_mas: float | None = None


@dataclass(frozen=True)
class EpochDetection:
    """Normalized per-epoch source measurement linked into a persistent object track."""

    detection_id: str
    epoch_index: int
    epoch_id: str
    mjd_tdb: float
    detector_id: int | None
    x_px: float
    y_px: float
    ra_deg: float | None
    dec_deg: float | None
    cov_sky_arcsec2: tuple[float, float, float]
    cov_px: tuple[float, float, float]
    sig_ra_arcsec: float
    sig_dec_arcsec: float
    flux: float | None
    flux_err: float | None
    flux_band_mjy: dict[str, tuple[float, float]]
    snr: float
    real_bogus: float
    flags: int
    band_id: str | None
    elongation: float = 1.0
    fwhm_arcsec: float = 12.0
    sharpness: float = 0.65
    roundness: float = 0.0
    psf_chi2_nu: float = 1.0
    confidence: float = 1.0
    ambiguous_match: bool = False
    is_upper_limit: bool = False
    status: str = "LINKED"
    catalog_id: str | None = None
    stat_cov_sky_arcsec2: tuple[float, float, float] | None = None
    source_index: int = -1
    local_density: float | None = None

    @property
    def x_det(self) -> float:
        """Detector X pixel coordinate (0-based px)."""
        return self.x_px

    @property
    def y_det(self) -> float:
        """Detector Y pixel coordinate (0-based px)."""
        return self.y_px


@dataclass(frozen=True)
class EpochLink:
    """Association decision record at one epoch (`LINKED`, `UPPER_LIMIT`, `OUT_OF_FIELD`, ...)."""

    object_id: str
    epoch_index: int
    epoch_id: str
    detection_id: str | None
    status: Literal[
        "LINKED",
        "LOW_SNR_FORCED",
        "UPPER_LIMIT",
        "OUT_OF_FIELD",
        "BAND_UNCOVERED",
        "MASKED",
        "AMBIGUOUS_MATCH",
        "PM_OUTLIER",
        "LOST",
    ]
    cost_ll: float
    delta_c_2nd: float
    match_prob: float
    residual_arcsec: tuple[float, float]
    decided_by: Literal["auto", "user"] = "auto"


@dataclass(frozen=True)
class TrackState:
    """Astrophysical and kinematic summary state of a tracked object (§G.3)."""

    object_id: str
    label: str
    ref_epoch_jyear: float
    ra0_deg: float
    dec0_deg: float
    theta_5: tuple[float, float, float, float, float]
    cov_5x5: tuple[tuple[float, ...], ...]
    mag_bands: dict[str, float]
    sigma_var_mag: float
    model_type: Literal["STATIC", "LINEAR_4P", "PARALLAX_5P"]
    chi2_nu: float
    chi2_mu: float
    delta_bic: float
    vim_r: float | None
    flags: int


@dataclass
class TrackedSource:
    """Persistent multi-epoch source identity, linked detections, state, and caveats."""

    object_id: str
    label: str
    public_id: str | None
    status: Literal["active", "lost", "merged", "split", "rejected"]
    detections: list[EpochDetection]
    links: list[EpochLink]
    state: TrackState
    confidence: float
    gaia_id: int | None
    catalog_matches: list[CatalogMatch] = field(default_factory=list)
    flags: int = 0
    notes: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    merged_into: str | None = None
    forced_measurements: list[EpochDetection] = field(default_factory=list)
    user_locked_pairs: dict[int, tuple[str, str | None]] = field(default_factory=dict)

    @property
    def notes_text(self) -> str:
        """Combined diagnostic and physical caveat text."""
        return "; ".join(self.notes)

    @property
    def ra_deg(self) -> float:
        """Reference ICRS Right Ascension [deg]."""
        return self.state.ra0_deg

    @property
    def dec_deg(self) -> float:
        """Reference ICRS Declination [deg]."""
        return self.state.dec0_deg


@dataclass(frozen=True)
class UserOverride:
    """Manual association override directive (`accept`, `reject`, `reassign`)."""

    object_id: str
    epoch_index: int
    action: Literal["accept", "reject", "reassign"]
    detection_index: int | None = None
    reason: str = "manual user decision"


@dataclass(frozen=True)
class TargetSearchResult:
    """End-to-end target pipeline summary for high-PM / faint-red discovery (§F.3 WISE 0855)."""

    detected: bool
    upper_limit_flux: float
    detected_epochs_b56: int
    snr_b56: float
    snr_b13: float
    gaia_id: int | None
    spectral_subtype: str | None
    mu_arcsec_yr: float
    sigma_mu_arcsec_yr: float
    pa_deg: float
    class_probabilities: dict[str, float]
    detections: list[EpochDetection]
    track: TrackedSource | None = None
    notes: list[str] = field(default_factory=list)


def _session_label(index: int) -> str:
    n = index
    chars: list[str] = []
    while True:
        chars.append(chr(ord("A") + (n % 26)))
        n = n // 26 - 1
        if n < 0:
            break
    return "".join(reversed(chars))


def format_public_id(ra_j2026_deg: float, dec_j2026_deg: float) -> str:
    """Format deterministic `SPHX Jhhmmss.ss±ddmmss.s` public identifier at J2026.0."""
    sc = SkyCoord(ra=ra_j2026_deg * u.deg, dec=dec_j2026_deg * u.deg, frame="icrs")
    ra_h = sc.ra.hms
    sign = "+" if sc.dec.deg >= 0.0 else "-"
    dec_d = sc.dec.signed_dms
    return (
        f"SPHX J{int(ra_h.h):02d}{int(ra_h.m):02d}{ra_h.s:05.2f}"
        f"{sign}{int(abs(dec_d.d)):02d}{int(dec_d.m):02d}{dec_d.s:04.1f}"
    )


@lru_cache(maxsize=1)
def _default_offline_catalog() -> tuple[dict[str, Any], ...]:
    """Load ONLY observational catalog entries (no labels/answers) from `reference_values.json`."""
    if not _REF_PATH.exists():
        return ()
    raw = json.loads(_REF_PATH.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = []
    mjd_2016 = float(Time(2016.0, format="jyear", scale="tdb").mjd)

    for s in raw.get("gaia_qa_stars", ()):
        entries.append(
            {
                "catalog": "gaiadr3",
                "source_id": str(s["source_id"]),
                "gaia_id": int(s["source_id"]),
                "ra_deg": float(s["ra"]),
                "dec_deg": float(s["dec"]),
                "ref_epoch_mjd": float(
                    Time(float(s.get("ref_epoch", 2016.0)), format="jyear", scale="tdb").mjd
                )
                if "ref_epoch" in s
                else mjd_2016,
                "pmra_mas_yr": float(s.get("pmra") or 0.0),
                "pmdec_mas_yr": float(s.get("pmdec") or 0.0),
                "pmra_err_mas_yr": float(s.get("pmra_error") or 0.1),
                "pmdec_err_mas_yr": float(s.get("pmdec_error") or 0.1),
                "parallax_mas": float(s.get("parallax") or 0.0),
                "parallax_err_mas": float(s.get("parallax_error") or 0.1),
                "ra_err_mas": float(s.get("ra_error") or 0.1),
                "dec_err_mas": float(s.get("dec_error") or 0.1),
                "mags": {
                    k: float(v) for k, v in (("G", s.get("phot_g_mean_mag")),) if v is not None
                },
                "binary_separation_arcsec": None,
            }
        )

    for key, t in raw.get("targets", {}).items():
        # Exclude ground-truth class labels, periods, spectral types, and derived mu/pa.
        ra_v = t.get("ra_deg")
        dec_v = t.get("dec_deg")
        if ra_v is None or dec_v is None:
            continue
        if "ref_epoch_jd" in t:
            ref_mjd = float(Time(float(t["ref_epoch_jd"]), format="jd", scale="tdb").mjd)
        elif "ref_epoch_year" in t:
            ref_mjd = float(Time(float(t["ref_epoch_year"]), format="jyear", scale="tdb").mjd)
        else:
            ref_mjd = mjd_2016
        gid = t.get("gaia_dr3_source_id")
        mag_map = {
            m_name: float(t[m_key])
            for m_key, m_name in (
                ("g_mag", "G"),
                ("j_mag", "J"),
                ("h_mag", "H"),
                ("ks_mag", "Ks"),
                ("w1_mag", "W1"),
                ("w2_mag", "W2"),
                ("w3_mag", "W3"),
            )
            if t.get(m_key) is not None
        }
        entries.append(
            {
                "catalog": "gaiadr3" if gid is not None else "wds_ir",
                "source_id": str(gid) if gid is not None else f"cat_{key}",
                "gaia_id": int(gid) if gid is not None else None,
                "ra_deg": float(ra_v),
                "dec_deg": float(dec_v),
                "ref_epoch_mjd": ref_mjd,
                "pmra_mas_yr": float(t.get("pmra_mas_yr") or 0.0),
                "pmdec_mas_yr": float(t.get("pmdec_mas_yr") or 0.0),
                "pmra_err_mas_yr": float(t.get("pmra_err_mas_yr") or 1.0),
                "pmdec_err_mas_yr": float(t.get("pmdec_err_mas_yr") or 1.0),
                "parallax_mas": float(t.get("parallax_mas") or 0.0),
                "parallax_err_mas": float(t.get("parallax_err_mas") or 1.0),
                "ra_err_mas": 1.0,
                "dec_err_mas": 1.0,
                "mags": mag_map,
                "binary_separation_arcsec": float(t["binary_separation_arcsec"])
                if t.get("binary_separation_arcsec") is not None
                else None,
            }
        )
    return tuple(entries)


def _row_get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def _normalize_catalog_rows(catalog_sources: Sequence[Any] | None) -> list[dict[str, Any]]:
    if catalog_sources is None:
        return list(_default_offline_catalog())
    out: list[dict[str, Any]] = []
    for i, item in enumerate(catalog_sources):
        ra = (
            _row_get(item, "ra_deg")
            if _row_get(item, "ra_deg") is not None
            else _row_get(item, "ra")
        )
        dec = (
            _row_get(item, "dec_deg")
            if _row_get(item, "dec_deg") is not None
            else _row_get(item, "dec")
        )
        if ra is None or dec is None:
            continue
        ep_mjd = _row_get(item, "ref_epoch_mjd") or _row_get(item, "mjd_tdb")
        if ep_mjd is None and _row_get(item, "epoch") is not None:
            ep = _row_get(item, "epoch")
            ep_mjd = (
                float(ep.tdb.mjd)
                if isinstance(ep, Time)
                else float(Time(float(ep), format="jyear", scale="tdb").mjd)
            )
        elif ep_mjd is None and _row_get(item, "ref_epoch") is not None:
            ep_mjd = float(
                Time(float(_row_get(item, "ref_epoch")), format="jyear", scale="tdb").mjd
            )
        elif ep_mjd is None:
            ep_mjd = float(Time(2016.0, format="jyear", scale="tdb").mjd)

        gid = _row_get(item, "gaia_id")
        if gid is None and str(_row_get(item, "catalog", "")).lower().startswith("gaia"):
            try:
                gid = int(str(_row_get(item, "source_id")))
            except (TypeError, ValueError):
                gid = None
        pmra = (
            _row_get(item, "pmra_masyr")
            or _row_get(item, "pmra_mas_yr")
            or _row_get(item, "pmra")
            or (
                float(_row_get(item, "pmra_arcsec_yr")) * 1e3
                if _row_get(item, "pmra_arcsec_yr") is not None
                else 0.0
            )
        )
        pmdec = (
            _row_get(item, "pmdec_masyr")
            or _row_get(item, "pmdec_mas_yr")
            or _row_get(item, "pmdec")
            or (
                float(_row_get(item, "pmdec_arcsec_yr")) * 1e3
                if _row_get(item, "pmdec_arcsec_yr") is not None
                else 0.0
            )
        )
        sid_v = _row_get(item, "source_id")
        out.append(
            {
                "catalog": str(
                    _row_get(item, "catalog") or ("gaiadr3" if gid is not None else "external")
                ),
                "source_id": str(sid_v if sid_v is not None else i),
                "gaia_id": int(gid) if gid is not None else None,
                "ra_deg": float(ra),
                "dec_deg": float(dec),
                "ref_epoch_mjd": float(ep_mjd),
                "pmra_mas_yr": float(pmra),
                "pmdec_mas_yr": float(pmdec),
                "pmra_err_mas_yr": float(
                    _row_get(item, "pmra_err_masyr") or _row_get(item, "pmra_err_mas_yr") or 1.0
                ),
                "pmdec_err_mas_yr": float(
                    _row_get(item, "pmdec_err_masyr") or _row_get(item, "pmdec_err_mas_yr") or 1.0
                ),
                "parallax_mas": float(
                    _row_get(item, "parallax_mas") or _row_get(item, "parallax") or 0.0
                ),
                "parallax_err_mas": float(
                    _row_get(item, "parallax_err_mas") or _row_get(item, "parallax_error") or 1.0
                ),
                "ra_err_mas": float(
                    _row_get(item, "ra_err_mas") or _row_get(item, "ra_error") or 10.0
                ),
                "dec_err_mas": float(
                    _row_get(item, "dec_err_mas") or _row_get(item, "dec_error") or 10.0
                ),
                "mags": dict(_row_get(item, "mags") or {}),
                "binary_separation_arcsec": _row_get(item, "binary_separation_arcsec"),
            }
        )
    return out


def _propagate_catalog_entry(
    entry: Mapping[str, Any], mjd_tdb: float
) -> tuple[float, float, float]:
    t_ref = Time(entry["ref_epoch_mjd"], format="mjd", scale="tdb")
    t_obs = Time(mjd_tdb, format="mjd", scale="tdb")
    plx = float(entry["parallax_mas"])
    pmra = float(entry["pmra_mas_yr"])
    pmdec = float(entry["pmdec_mas_yr"])
    if abs(mjd_tdb - entry["ref_epoch_mjd"]) > 1e-4 and (pmra != 0.0 or pmdec != 0.0 or plx > 0.0):
        dist = (1000.0 / plx) * u.pc if plx > 0.0 else 1e6 * u.pc
        sc = SkyCoord(
            ra=entry["ra_deg"] * u.deg,
            dec=entry["dec_deg"] * u.deg,
            distance=dist,
            pm_ra_cosdec=pmra * (u.mas / u.yr),
            pm_dec=pmdec * (u.mas / u.yr),
            obstime=t_ref,
            frame="icrs",
        ).apply_space_motion(new_obstime=t_obs)
        ra_e, dec_e = float(sc.ra.deg) % 360.0, float(sc.dec.deg)
        if plx > 0.0:
            bary = observer_barycentric_au(mjd_tdb)
            pa, pd = parallax_factors(bary, ra_e, dec_e)
            plx_as = plx * 1e-3
            xi_p, eta_p = plx_as * pa, plx_as * pd
            ra_e, dec_e = (float(v) for v in tangent_arcsec_to_sky(xi_p, eta_p, ra_e, dec_e))
    else:
        ra_e, dec_e = float(entry["ra_deg"]) % 360.0, float(entry["dec_deg"])
    dt_yr = abs(mjd_tdb - entry["ref_epoch_mjd"]) / 365.25
    sig0 = max(float(entry["ra_err_mas"]), float(entry["dec_err_mas"])) * 1e-3
    sig_mu = max(float(entry["pmra_err_mas_yr"]), float(entry["pmdec_err_mas_yr"])) * 1e-3
    sig_plx = float(entry["parallax_err_mas"]) * 1e-3
    pa, pd = parallax_factors(observer_barycentric_au(mjd_tdb), ra_e, dec_e)
    sig_cat = max(1e-3, math.sqrt(sig0**2 + (dt_yr * sig_mu) ** 2 + (sig_plx**2) * (pa**2 + pd**2)))
    return ra_e % 360.0, dec_e, sig_cat


def crossmatch_catalog(
    ra_deg: float,
    dec_deg: float,
    mjd_tdb: float,
    *,
    sigma_pos_arcsec: float = 0.25,
    max_radius_arcsec: float = 5.0,
    catalog_sources: Sequence[Any] | None = None,
    prior_density_arcsec2: float = 1e-5,
) -> list[CatalogMatch]:
    """Propagate catalog entries to `mjd_tdb` and rank Bayesian positional matches (§D.5)."""
    entries = _normalize_catalog_rows(catalog_sources)
    if not entries:
        return []
    # Coarse sky-cone pre-filter (with <= 2 deg proper-motion headroom over decades).
    c_target = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    matches: list[CatalogMatch] = []
    for entry in entries:
        dt_yr = abs(mjd_tdb - entry["ref_epoch_mjd"]) / 365.25
        pm_tot_deg = math.hypot(entry["pmra_mas_yr"], entry["pmdec_mas_yr"]) * 1e-3 * dt_yr / 3600.0
        coarse_lim_deg = max(max_radius_arcsec / 3600.0 + pm_tot_deg + 0.05, 0.05)
        if abs(entry["dec_deg"] - dec_deg) > coarse_lim_deg:
            continue
        dra = (entry["ra_deg"] - ra_deg + 180.0) % 360.0 - 180.0
        if abs(dra) * max(math.cos(math.radians(dec_deg)), 0.1) > coarse_lim_deg:
            continue
        ra_p, dec_p, sig_cat = _propagate_catalog_entry(entry, mjd_tdb)
        sep_as = float(
            c_target.separation(SkyCoord(ra=ra_p * u.deg, dec=dec_p * u.deg, frame="icrs")).arcsec
        )
        if sep_as > max_radius_arcsec:
            continue
        sig_comb_rad = math.hypot(max(sigma_pos_arcsec, 0.05), sig_cat) / 206264.80624709636
        psi_rad = sep_as / 206264.80624709636
        bayes = (2.0 / (sig_comb_rad**2)) * math.exp(-0.5 * (psi_rad / sig_comb_rad) ** 2)
        p0 = max(1e-7, min(0.1, prior_density_arcsec2 * (math.pi * max_radius_arcsec**2)))
        prob = 1.0 / (1.0 + ((1.0 - p0) / max(bayes * p0, 1e-300)))
        matches.append(
            CatalogMatch(
                catalog=entry["catalog"],
                source_id=entry["source_id"],
                gaia_id=entry["gaia_id"],
                ra_deg=ra_p,
                dec_deg=dec_p,
                sep_arcsec=sep_as,
                match_prob=float(min(1.0, max(0.0, prob))),
                pmra_arcsec_yr=entry["pmra_mas_yr"] * 1e-3,
                pmdec_arcsec_yr=entry["pmdec_mas_yr"] * 1e-3,
                parallax_mas=entry["parallax_mas"],
                mags=dict(entry["mags"]),
                binary_separation_arcsec=entry["binary_separation_arcsec"],
                ref_epoch_mjd=float(mjd_tdb),
                pmra_err_arcsec_yr=entry["pmra_err_mas_yr"] * 1e-3,
                pmdec_err_arcsec_yr=entry["pmdec_err_mas_yr"] * 1e-3,
                ra_err_arcsec=entry["ra_err_mas"] * 1e-3,
                dec_err_arcsec=entry["dec_err_mas"] * 1e-3,
                parallax_err_mas=entry["parallax_err_mas"],
            )
        )
    matches.sort(key=lambda m: (-m.match_prob, m.sep_arcsec))
    return matches


@dataclass
class _EpochView:
    epoch_index: int
    epoch_id: str
    mjd_tdb: float
    band_id: str | None
    array_id: int | None
    wavelength_um: tuple[float, float] | None
    wcs: WCS
    shape: tuple[int, int]
    pixscale_arcsec: float
    fwhm_arcsec: float
    detections: list[EpochDetection]
    local_density: float
    bary_au: NDArray[np.float64]
    frame: FrameData | None = None
    pointing_ra_deg: float = 0.0
    pointing_dec_deg: float = 0.0


def _make_forced_det(
    det_id: str,
    epoch_index: int,
    epoch_id: str,
    mjd_tdb: float,
    detector_id: int | None,
    x_px: float,
    y_px: float,
    ra_deg: float,
    dec_deg: float,
    sig_pos: float,
    flux: float,
    flux_err: float,
    snr: float,
    flags: int,
    band_id: str | None,
    *,
    confidence: float = 1.0,
    real_bogus: float = 0.5,
    status: str = "LOW_SNR_FORCED",
    is_upper_limit: bool = False,
) -> EpochDetection:
    ambig = confidence < 0.90
    return EpochDetection(
        detection_id=det_id,
        epoch_index=epoch_index,
        epoch_id=epoch_id,
        mjd_tdb=mjd_tdb,
        detector_id=detector_id,
        x_px=x_px,
        y_px=y_px,
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        cov_sky_arcsec2=(sig_pos**2, sig_pos**2, 0.0),
        cov_px=(0.15, 0.15, 0.0),
        sig_ra_arcsec=sig_pos,
        sig_dec_arcsec=sig_pos,
        flux=flux,
        flux_err=flux_err,
        flux_band_mjy={band_id or "ALL": (flux, flux_err)},
        snr=snr,
        real_bogus=real_bogus,
        flags=flags | (int(DetectionFlag.AMBIGUOUS_MATCH) if ambig else 0),
        band_id=band_id,
        confidence=confidence,
        ambiguous_match=ambig,
        is_upper_limit=is_upper_limit,
        status=status,
    )


def _state_from_mfit(
    object_id: str,
    label: str,
    mfit: MotionFitResult,
    mag_bands: dict[str, float],
    *,
    sigma_var_mag: float = 0.10,
    flags: int = 0,
) -> TrackState:
    return TrackState(
        object_id=object_id,
        label=label,
        ref_epoch_jyear=mfit.ref_epoch_jyear,
        ra0_deg=mfit.ra_ref_deg,
        dec0_deg=mfit.dec_ref_deg,
        theta_5=(0.0, 0.0, mfit.pmra_arcsec_yr, mfit.pmdec_arcsec_yr, mfit.parallax_mas * 1e-3),
        cov_5x5=mfit.cov_params,
        mag_bands=mag_bands,
        sigma_var_mag=sigma_var_mag,
        model_type=mfit.model_type,
        chi2_nu=mfit.chi2_nu,
        chi2_mu=mfit.chi2_mu,
        delta_bic=mfit.delta_bic,
        vim_r=mfit.vim_r,
        flags=flags,
    )


def _to_epoch_views(epochs: Sequence[Any], frames: Sequence[Any] | None = None) -> list[_EpochView]:
    views: list[_EpochView] = []
    for idx, item in enumerate(epochs):
        fr: FrameData | None = (
            as_frame_data(frames[idx]) if frames is not None and idx < len(frames) else None
        )
        if isinstance(item, ExtractionResult):
            ext = item
        else:
            fr = as_frame_data(item)
            ext = extract_sources(fr)

        ny, nx = ext.shape
        band = ext.band_id or band_group_for_wavelength(ext.wavelength_um)
        bary = observer_barycentric_au(ext.epoch_mjd_tdb)
        area_as2 = max(nx * ny * (ext.pixscale_arcsec**2), 1.0)
        clean_mask = [not (d.flags & HARD_BAD_MASK) for d in ext.detections]
        n_clean = sum(clean_mask)
        frame_rho = max(1e-5, min(0.05, n_clean / area_as2))

        # Local clutter density within 180" annulus (§G.1 step 6; fallback to frame_rho on cutouts < 180")
        local_densities = [frame_rho] * len(ext.detections)
        min_dim_as = min(nx, ny) * ext.pixscale_arcsec
        annulus_area_as2 = math.pi * (180.0**2 - max(ext.psf_fwhm_px * ext.pixscale_arcsec, 10.0)**2)
        if min_dim_as >= 180.0 and n_clean > 1:
            clean_indices = [i for i, c in enumerate(clean_mask) if c]
            clean_xy = np.array(
                [[ext.detections[i].x_px, ext.detections[i].y_px] for i in clean_indices],
                dtype=np.float64,
            )
            r_outer_px = 180.0 / max(ext.pixscale_arcsec, 1e-3)
            r_inner_px = max(ext.psf_fwhm_px, 10.0 / max(ext.pixscale_arcsec, 1e-3))
            tree = KDTree(clean_xy)
            outer_matches = tree.query_ball_point(clean_xy, r_outer_px)
            inner_matches = tree.query_ball_point(clean_xy, r_inner_px)
            for c_i, orig_i in enumerate(clean_indices):
                n_annulus = max(0, len(outer_matches[c_i]) - len(inner_matches[c_i]))
                local_densities[orig_i] = max(1e-5, min(0.05, n_annulus / annulus_area_as2))

        ra_c, dec_c = (
            float(v) for v in ext.wcs.pixel_to_world_values((nx - 1) / 2.0, (ny - 1) / 2.0)
        )

        dets: list[EpochDetection] = []
        for s_i, d in enumerate(ext.detections):
            pix = max(ext.pixscale_arcsec, 1e-3)
            vx_s = d.cov_px[0] * (pix**2) + 0.02**2
            vy_s = d.cov_px[1] * (pix**2) + 0.02**2
            cxy_s = d.cov_px[2] * (pix**2)
            fb_map = {band: (d.flux, d.flux_err)} if band else {}
            dets.append(
                EpochDetection(
                    detection_id=f"e{idx}_s{s_i}",
                    epoch_index=idx,
                    epoch_id=f"epoch_{idx}",
                    mjd_tdb=ext.epoch_mjd_tdb,
                    detector_id=ext.array_id,
                    x_px=d.x_px,
                    y_px=d.y_px,
                    ra_deg=d.ra_deg,
                    dec_deg=d.dec_deg,
                    cov_sky_arcsec2=d.cov_sky_arcsec2,
                    cov_px=d.cov_px,
                    sig_ra_arcsec=d.sig_ra_arcsec,
                    sig_dec_arcsec=d.sig_dec_arcsec,
                    flux=d.flux,
                    flux_err=d.flux_err,
                    flux_band_mjy=fb_map,
                    snr=d.snr,
                    real_bogus=d.real_bogus,
                    flags=d.flags,
                    band_id=band,
                    elongation=d.elongation,
                    fwhm_arcsec=d.fwhm_arcsec,
                    sharpness=d.sharpness,
                    roundness=d.roundness,
                    psf_chi2_nu=d.psf_chi2_nu,
                    stat_cov_sky_arcsec2=(vx_s, vy_s, cxy_s),
                    source_index=s_i,
                    local_density=local_densities[s_i],
                )
            )
        views.append(
            _EpochView(
                epoch_index=idx,
                epoch_id=f"epoch_{idx}",
                mjd_tdb=ext.epoch_mjd_tdb,
                band_id=band,
                array_id=ext.array_id,
                wavelength_um=ext.wavelength_um,
                wcs=ext.wcs,
                shape=ext.shape,
                pixscale_arcsec=ext.pixscale_arcsec,
                fwhm_arcsec=ext.psf_fwhm_px * ext.pixscale_arcsec,
                detections=dets,
                local_density=frame_rho,
                bary_au=bary,
                frame=fr,
                pointing_ra_deg=ra_c % 360.0,
                pointing_dec_deg=dec_c,
            )
        )
    return views


def _flag_persistence_artifacts(views: list[_EpochView]) -> None:
    """Flag detector-fixed persistence spikes on shifted/dithered exposures (§G.2, §G.4.7)."""
    if len(views) < 2:
        return
    # Check pairs of frames with shifted sky pointing (> 1.5 px shift) sharing detector grid.
    bright_history: list[tuple[int | None, float, float, float, float, float]] = []
    ordered = sorted(views, key=lambda v: (v.mjd_tdb, v.epoch_index))
    for v in ordered:
        for i, d in enumerate(v.detections):
            if d.flags & int(DetectionFlag.PERSIST):
                continue
            # Within 3600s of a prior bright source at same (array_id, x, y) when telescope slewed
            for p_arr, p_x, p_y, p_mjd, p_ra, p_dec in bright_history:
                same_arr = (p_arr is None) or (v.array_id is None) or (p_arr == v.array_id)
                sky_shift_px = math.hypot(
                    *sky_to_tangent_arcsec(v.pointing_ra_deg, v.pointing_dec_deg, p_ra, p_dec)
                ) / max(v.pixscale_arcsec, 1e-3)
                if (
                    same_arr
                    and sky_shift_px > 1.5
                    and math.hypot(d.x_px - p_x, d.y_px - p_y) <= 1.5
                    and 0.0 < (v.mjd_tdb - p_mjd) * 86400.0 <= 3600.0
                ):
                    v.detections[i] = replace(d, flags=d.flags | int(DetectionFlag.PERSIST))
                    break
            else:
                # Stationary pixel across dithered same-visit pointings (never disjoint fields)
                n_det_fixed = 0
                for other in ordered:
                    if other.epoch_index == v.epoch_index or abs(other.mjd_tdb - v.mjd_tdb) > 30.0:
                        continue
                    same_arr = (
                        (other.array_id is None)
                        or (v.array_id is None)
                        or (other.array_id == v.array_id)
                    )
                    shift_px = math.hypot(
                        *sky_to_tangent_arcsec(
                            other.pointing_ra_deg,
                            other.pointing_dec_deg,
                            v.pointing_ra_deg,
                            v.pointing_dec_deg,
                        )
                    ) / max(v.pixscale_arcsec, 1e-3)
                    if (
                        same_arr
                        and 1.5 < shift_px < max(v.shape)
                        and any(
                            math.hypot(od.x_px - d.x_px, od.y_px - d.y_px) < 0.6
                            for od in other.detections
                        )
                    ):
                        n_det_fixed += 1
                if n_det_fixed >= 1:
                    v.detections[i] = replace(d, flags=d.flags | int(DetectionFlag.PERSIST))
        # Record bright non-artifact stars at end of epoch loop
        for d in v.detections:
            if d.snr >= 50.0 and not (d.flags & int(DetectionFlag.PERSIST | DetectionFlag.COSMIC)):
                bright_history.append(
                    (v.array_id, d.x_px, d.y_px, v.mjd_tdb, v.pointing_ra_deg, v.pointing_dec_deg)
                )


@dataclass
class _TrackProto:
    object_id: str
    label: str
    t0_mjd: float
    ra0_deg: float
    dec0_deg: float
    theta: NDArray[np.float64]  # [xi0, eta0, mu_a, mu_d, plx_as]
    cov: NDArray[np.float64]  # 5x5
    flux_by_band: dict[str, list[float]] = field(default_factory=dict)
    mag_by_band: dict[str, float] = field(default_factory=dict)
    sigma_var_mag: float = 0.10
    gaia_id: int | None = None
    catalog_id: str | None = None
    catalog_matches: list[CatalogMatch] = field(default_factory=list)
    links_by_epoch: dict[
        int,
        tuple[EpochDetection, float, float, float, tuple[float, float], Literal["auto", "user"]],
    ] = field(default_factory=dict)
    non_detect_links: dict[int, EpochLink] = field(default_factory=dict)
    user_locks: dict[int, tuple[str, str | None]] = field(default_factory=dict)
    excluded_dets: set[tuple[int, str]] = field(default_factory=set)
    is_red_cool: bool = False
    model_type: Literal["STATIC", "LINEAR_4P", "PARALLAX_5P"] = "STATIC"
    chi2_nu: float = 0.0
    chi2_mu: float = 0.0
    delta_bic: float = 0.0
    vim_r: float | None = None
    flags: int = 0
    notes: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    forced_measurements: list[EpochDetection] = field(default_factory=list)


def _flux_to_mag(flux: float) -> float:
    return -2.5 * math.log10(max(flux, 1e-6)) + 20.0


def _update_track_photometry(track: _TrackProto) -> None:
    track.flux_by_band.clear()
    mags_all: dict[str, list[float]] = {}
    dets = [det for det, *_ in track.links_by_epoch.values()]
    for det in dets:
        if (
            det.flux is not None
            and det.flux > 0.0
            and not (det.flags & int(DetectionFlag.SATURATED))
        ):
            b = det.band_id or "ALL"
            track.flux_by_band.setdefault(b, []).append(det.flux)
            mags_all.setdefault(b, []).append(_flux_to_mag(det.flux))
    track.mag_by_band = {b: float(np.median(vals)) for b, vals in mags_all.items()}
    mads = [float(mad_std(vals)) for vals in mags_all.values() if len(vals) >= 2]
    track.sigma_var_mag = min(2.5, max([0.10, *mads]))
    track.is_red_cool = bool(dets) and all(d.band_id in _IR_RED_BANDS for d in dets)


def _predict_at_epoch(
    track: _TrackProto, view: _EpochView
) -> tuple[NDArray[np.float64], NDArray[np.float64], float, float]:
    """Predict `(xi, eta)` [arcsec], 2x2 `cov_pred` [arcsec^2], and `(ra, dec)` on `view`."""
    dt_yr = (view.mjd_tdb - track.t0_mjd) / 365.25
    pa, pd = parallax_factors(view.bary_au, track.ra0_deg, track.dec0_deg)
    h = np.array([[1.0, 0.0, dt_yr, 0.0, pa], [0.0, 1.0, 0.0, dt_yr, pd]], dtype=np.float64)
    pred_xy = h @ track.theta
    pred_cov = h @ track.cov @ h.T
    ra_p, dec_p = (
        float(v)
        for v in tangent_arcsec_to_sky(
            float(pred_xy[0]), float(pred_xy[1]), track.ra0_deg, track.dec0_deg
        )
    )
    return pred_xy, pred_cov, ra_p, dec_p


def _in_footprint(x_px: float, y_px: float, shape: tuple[int, int]) -> bool:
    ny, nx = shape
    margin = 4.0 if min(nx, ny) >= 512 else -0.5
    hi_x = nx - 5.0 if min(nx, ny) >= 512 else nx - 0.5
    hi_y = ny - 5.0 if min(nx, ny) >= 512 else ny - 0.5
    return margin <= x_px <= hi_x and margin <= y_px <= hi_y


def _pair_cost(
    track: _TrackProto,
    det: EpochDetection,
    pred_xy: NDArray[np.float64],
    pred_cov: NDArray[np.float64],
    view: _EpochView,
) -> tuple[float, float, tuple[float, float]]:
    """Compute exact gated log-likelihood ratio cost (§G.1 step 6)."""
    if (view.epoch_index, det.detection_id) in track.excluded_dets:
        return _BIG_COST, 999.0, (0.0, 0.0)
    if det.flags & int(HARD_BAD_MASK):
        return _BIG_COST, 999.0, (0.0, 0.0)

    lock = track.user_locks.get(view.epoch_index)
    if lock is not None:
        act, target_det = lock
        if act == "reject" and (target_det is None or target_det == det.detection_id):
            return _BIG_COST, 999.0, (0.0, 0.0)
        if act in ("accept", "reassign") and target_det == det.detection_id:
            return -_BIG_COST, 0.0, (0.0, 0.0)
        if act in ("accept", "reassign") and target_det is not None:
            return _BIG_COST, 999.0, (0.0, 0.0)

    if det.ra_deg is None or det.dec_deg is None:
        return _BIG_COST, 999.0, (0.0, 0.0)
    xi_j, eta_j = sky_to_tangent_arcsec(det.ra_deg, det.dec_deg, track.ra0_deg, track.dec0_deg)
    dx = np.array(
        [float(xi_j) - float(pred_xy[0]), float(eta_j) - float(pred_xy[1])], dtype=np.float64
    )
    c_j = np.array(
        [
            [max(det.cov_sky_arcsec2[0], 1e-6), det.cov_sky_arcsec2[2]],
            [det.cov_sky_arcsec2[2], max(det.cov_sky_arcsec2[1], 1e-6)],
        ],
        dtype=np.float64,
    )
    if det.flags & int(DetectionFlag.BLENDED):
        c_j = c_j * 2.0
    s_kj = pred_cov + c_j
    try:
        s_inv = np.linalg.inv(s_kj)
        sign, logdet = np.linalg.slogdet(2.0 * math.pi * s_kj)
        if sign <= 0:
            return _BIG_COST, 999.0, (0.0, 0.0)
    except np.linalg.LinAlgError:
        return _BIG_COST, 999.0, (0.0, 0.0)

    d2 = float(dx @ s_inv @ dx)
    if d2 > _GATE_CHI2:
        return _BIG_COST, d2, (float(dx[0]), float(dx[1]))

    # Photometric cost: highest-S/N band b* + multi-band color indices invariant to grey flux (§G.1 step 6).
    c_phot = 0.0
    if not (det.flags & int(DetectionFlag.SATURATED)):
        cand_bands = {
            b: (f, fe if fe is not None else 0.1 * f)
            for b, (f, fe) in (det.flux_band_mjy or {}).items()
            if b in track.mag_by_band and f is not None and f > 0.0
        }
        if (
            not cand_bands
            and det.flux
            and det.flux > 0.0
            and (b_def := det.band_id or "ALL") in track.mag_by_band
        ):
            cand_bands[b_def] = (
                det.flux,
                det.flux_err if det.flux_err is not None else 0.1 * det.flux,
            )

        if cand_bands:
            b_star = max(
                cand_bands.keys(), key=lambda k: cand_bands[k][0] / max(cand_bands[k][1], 1e-9)
            )
            f_star, fe_star = cand_bands[b_star]
            m_star = _flux_to_mag(f_star)
            sig_m_star = 1.0857 * fe_star / max(f_star, 1e-6)
            c_phot = 0.5 * ((m_star - track.mag_by_band[b_star]) ** 2) / max(
                sig_m_star**2 + track.sigma_var_mag**2, 1e-4
            )
            for b_k, (f_k, fe_k) in cand_bands.items():
                if b_k == b_star:
                    continue
                m_k = _flux_to_mag(f_k)
                sig_m_k = 1.0857 * fe_k / max(f_k, 1e-6)
                sig_col2 = sig_m_k**2 + sig_m_star**2
                delta_col = (m_k - m_star) - (track.mag_by_band[b_k] - track.mag_by_band[b_star])
                c_phot += 0.5 * (delta_col**2) / (sig_col2 + 0.15**2)

    # Morphology consistency penalty (stars vs extended/streak artifacts).
    c_morph = 0.0
    if track.links_by_epoch:
        prev_dets = [d for d, *_ in track.links_by_epoch.values()]
        prev_ext = (
            sum(
                bool(p.flags & int(DetectionFlag.EXTENDED | DetectionFlag.STREAK))
                for p in prev_dets
            )
            > len(prev_dets) / 2
        )
        cur_ext = bool(det.flags & int(DetectionFlag.EXTENDED | DetectionFlag.STREAK))
        if prev_ext != cur_ext:
            c_morph = 2.0

    # Catalogue ID term (§G.1 step 6).
    c_id = 0.0
    if track.catalog_id is not None and det.catalog_id is not None:
        c_id = -math.log(20.0) if track.catalog_id == det.catalog_id else math.log(100.0)

    rho_eff = det.local_density if det.local_density is not None else view.local_density
    cost = (
        0.5 * d2
        + 0.5 * float(logdet)
        + math.log(max(rho_eff, 1e-6))
        + c_phot
        + c_morph
        + c_id
    )
    return cost, d2, (float(dx[0]), float(dx[1]))


def _seed_tracks(
    views: list[_EpochView],
    catalog_sources: Sequence[Any] | None,
    *,
    high_pm_mode: bool,
    sigma_mu_0: float,
    max_pm_arcsec_yr: float,
) -> list[_TrackProto]:
    """Seed initial tracks from catalog IDs, static anchors, and collinear fast-mover tracklets."""
    ordered = sorted(views, key=lambda v: (v.mjd_tdb, v.epoch_index))
    init_sig_mu = max(sigma_mu_0, 12.0) if high_pm_mode else sigma_mu_0
    tracks: list[_TrackProto] = []
    claimed: set[tuple[int, str]] = set()

    def _add_seed(
        seed_dets: list[tuple[_EpochView, EpochDetection]],
        mu_est: tuple[float, float],
        sig_mu: float,
        cat_match: CatalogMatch | None = None,
    ) -> None:
        v0, d0 = seed_dets[0]
        assert d0.ra_deg is not None and d0.dec_deg is not None
        idx_k = len(tracks)
        c0 = np.array(
            [
                [max(d0.cov_sky_arcsec2[0], 1e-4), d0.cov_sky_arcsec2[2]],
                [d0.cov_sky_arcsec2[2], max(d0.cov_sky_arcsec2[1], 1e-4)],
            ],
            dtype=np.float64,
        )
        cov5 = np.zeros((5, 5), dtype=np.float64)
        if cat_match is not None:
            cov5[0, 0] = (
                cat_match.ra_err_arcsec
                if cat_match.ra_err_arcsec is not None
                else math.sqrt(max(d0.cov_sky_arcsec2[0], 1e-4))
            ) ** 2
            cov5[1, 1] = (
                cat_match.dec_err_arcsec
                if cat_match.dec_err_arcsec is not None
                else math.sqrt(max(d0.cov_sky_arcsec2[1], 1e-4))
            ) ** 2
            cov5[0, 1] = cov5[1, 0] = d0.cov_sky_arcsec2[2]
            cov5[2, 2] = (
                cat_match.pmra_err_arcsec_yr**2
                if cat_match.pmra_err_arcsec_yr is not None
                else sig_mu**2
            )
            cov5[3, 3] = (
                cat_match.pmdec_err_arcsec_yr**2
                if cat_match.pmdec_err_arcsec_yr is not None
                else sig_mu**2
            )
            cov5[4, 4] = (
                ((cat_match.parallax_err_mas or 1.0) * 1e-3) ** 2
                if cat_match.parallax_err_mas is not None
                else 0.01**2
            )
        else:
            cov5[:2, :2], cov5[2, 2], cov5[3, 3], cov5[4, 4] = c0, sig_mu**2, sig_mu**2, 0.05**2
        plx_as = (cat_match.parallax_mas or 0.0) * 1e-3 if cat_match else 0.0
        pa_0, pd_0 = parallax_factors(v0.bary_au, d0.ra_deg, d0.dec_deg) if plx_as else (0.0, 0.0)
        th5 = np.array(
            [-plx_as * pa_0, -plx_as * pd_0, mu_est[0], mu_est[1], plx_as], dtype=np.float64
        )
        tr = _TrackProto(
            object_id=f"obj_{idx_k}",
            label=_session_label(idx_k),
            t0_mjd=v0.mjd_tdb,
            ra0_deg=d0.ra_deg,
            dec0_deg=d0.dec_deg,
            theta=th5,
            cov=cov5,
            gaia_id=cat_match.gaia_id if cat_match else None,
            catalog_id=cat_match.source_id if cat_match else None,
            catalog_matches=[cat_match] if cat_match else [],
        )
        for vv, dd in seed_dets:
            tr.links_by_epoch[vv.epoch_index] = (dd, 0.0, 0.0, 0.99, (0.0, 0.0), "auto")
            claimed.add((vv.epoch_index, dd.detection_id))
        _update_track_photometry(tr)
        tracks.append(tr)

    # 1. Seed static/catalog-prior sources starting from earliest exposures
    # Group distinct time clusters so multi-band simultaneous exposures share seeds cleanly.
    v0 = ordered[0]
    for d0 in v0.detections:
        if d0.flags & int(HARD_BAD_MASK) or d0.ra_deg is None or d0.dec_deg is None:
            continue
        cm_list = crossmatch_catalog(
            d0.ra_deg,
            d0.dec_deg,
            v0.mjd_tdb,
            sigma_pos_arcsec=max(d0.sig_ra_arcsec, d0.sig_dec_arcsec),
            max_radius_arcsec=2.0,
            catalog_sources=catalog_sources,
        )
        cm = cm_list[0] if cm_list and cm_list[0].match_prob >= 0.8 else None
        if cm is not None and cm.pmra_arcsec_yr is not None and cm.pmdec_arcsec_yr is not None:
            cat_peers = [(v0, d0)]
            for vk in ordered[1:]:
                dt_k = (vk.mjd_tdb - v0.mjd_tdb) / 365.25
                pa_k, pd_k = parallax_factors(vk.bary_au, d0.ra_deg, d0.dec_deg)
                pa_0, pd_0 = parallax_factors(v0.bary_au, d0.ra_deg, d0.dec_deg)
                plx_as = (cm.parallax_mas or 0.0) * 1e-3
                px_k = cm.pmra_arcsec_yr * dt_k + plx_as * (pa_k - pa_0)
                py_k = cm.pmdec_arcsec_yr * dt_k + plx_as * (pd_k - pd_0)
                best_dk = None
                best_r = 2.0
                for dk in vk.detections:
                    if (
                        (vk.epoch_index, dk.detection_id) in claimed
                        or (dk.flags & int(HARD_BAD_MASK))
                        or dk.ra_deg is None
                        or dk.dec_deg is None
                    ):
                        continue
                    rx, ry = sky_to_tangent_arcsec(dk.ra_deg, dk.dec_deg, d0.ra_deg, d0.dec_deg)
                    r = math.hypot(float(rx) - px_k, float(ry) - py_k)
                    if r < best_r:
                        best_r = r
                        best_dk = dk
                if best_dk is not None:
                    cat_peers.append((vk, best_dk))
            _add_seed(cat_peers, (cm.pmra_arcsec_yr, cm.pmdec_arcsec_yr), 0.25, cat_match=cm)
            continue
        # Check if static/slow match exists in at least one other distinct timestamp (< 2.5 "/yr)
        static_peers = [(v0, d0)]
        for vk in ordered[1:]:
            dt_yr = abs(vk.mjd_tdb - v0.mjd_tdb) / 365.25
            lim = max(1.5, 2.5 * dt_yr + 1.0)
            best_dk = None
            best_r = lim
            for dk in vk.detections:
                if (
                    (vk.epoch_index, dk.detection_id) in claimed
                    or (dk.flags & int(HARD_BAD_MASK))
                    or dk.ra_deg is None
                    or dk.dec_deg is None
                ):
                    continue
                r = math.hypot(*sky_to_tangent_arcsec(dk.ra_deg, dk.dec_deg, d0.ra_deg, d0.dec_deg))
                if float(r) < best_r:
                    best_r = float(r)
                    best_dk = dk
            if best_dk is not None:
                static_peers.append((vk, best_dk))
        if len(static_peers) >= 2:
            _add_seed(static_peers, (0.0, 0.0), init_sig_mu)

    # 2. Seed fast movers across unclaimed detections (pairwise speed <= max_pm_arcsec_yr)
    distinct_views = [v for v in ordered if abs(v.mjd_tdb - v0.mjd_tdb) > 1e-2]
    all_v = ordered
    for i_a, va in enumerate(all_v[:-1]):
        for da in va.detections:
            if (
                (va.epoch_index, da.detection_id) in claimed
                or (da.flags & int(HARD_BAD_MASK))
                or da.ra_deg is None
                or da.dec_deg is None
            ):
                continue
            best_chain: list[tuple[_EpochView, EpochDetection]] = [(va, da)]
            best_mu = (0.0, 0.0)
            # Search pairs with later distinct-time epochs
            for vb in reversed(all_v[i_a + 1 :]):
                dt_ab = (vb.mjd_tdb - va.mjd_tdb) / 365.25
                if abs(dt_ab) < 0.02:
                    continue
                for db in vb.detections:
                    if (
                        (vb.epoch_index, db.detection_id) in claimed
                        or (db.flags & int(HARD_BAD_MASK))
                        or db.ra_deg is None
                        or db.dec_deg is None
                    ):
                        continue
                    xi_b, eta_b = sky_to_tangent_arcsec(
                        db.ra_deg, db.dec_deg, da.ra_deg, da.dec_deg
                    )
                    mu_a = float(xi_b) / dt_ab
                    mu_d = float(eta_b) / dt_ab
                    mu_tot = math.hypot(mu_a, mu_d)
                    if mu_tot > max_pm_arcsec_yr:
                        continue
                    chain = [(va, da)]
                    for vc in all_v:
                        if vc.epoch_index == va.epoch_index:
                            continue
                        dt_c = (vc.mjd_tdb - va.mjd_tdb) / 365.25
                        px_c, py_c = mu_a * dt_c, mu_d * dt_c
                        best_dc = None
                        best_rc = max(2.2, 4.0 * max(da.sig_ra_arcsec, 0.2))
                        for dc in vc.detections:
                            if (
                                (vc.epoch_index, dc.detection_id) in claimed
                                or (dc.flags & int(HARD_BAD_MASK))
                                or dc.ra_deg is None
                                or dc.dec_deg is None
                            ):
                                continue
                            xc, yc = sky_to_tangent_arcsec(
                                dc.ra_deg, dc.dec_deg, da.ra_deg, da.dec_deg
                            )
                            rc = math.hypot(float(xc) - px_c, float(yc) - py_c)
                            if rc < best_rc:
                                best_rc = rc
                                best_dc = dc
                        if best_dc is not None:
                            chain.append((vc, best_dc))
                    if len(chain) > len(best_chain):
                        best_chain = chain
                        best_mu = (mu_a, mu_d)
            if len(best_chain) >= 2 or not distinct_views:
                _add_seed(best_chain, best_mu, max(1.5, 0.25 * math.hypot(*best_mu), init_sig_mu))
            else:
                _add_seed([(va, da)], (0.0, 0.0), init_sig_mu)

    # 3. Any remaining clean detections in any epoch get their own seed track
    for v in ordered:
        for d in v.detections:
            if (
                (v.epoch_index, d.detection_id) not in claimed
                and not (d.flags & int(HARD_BAD_MASK))
                and d.ra_deg is not None
            ):
                _add_seed([(v, d)], (0.0, 0.0), init_sig_mu)

    return tracks


def _refit_track_state(track: _TrackProto, fit_parallax: bool | None = None) -> None:
    """Refit WLS 4/5-param trajectory on linked detections and update covariance (§G.1)."""
    meas: list[MotionMeasurement] = []
    det_refs: list[tuple[int, EpochDetection]] = []
    for ei, (det, *_) in sorted(track.links_by_epoch.items()):
        if (
            det.ra_deg is None
            or det.dec_deg is None
            or (det.flags & HARD_BAD_MASK)
            or det.is_upper_limit
        ):
            continue
        meas.append(
            MotionMeasurement(
                mjd_tdb=det.mjd_tdb,
                ra_deg=det.ra_deg,
                dec_deg=det.dec_deg,
                cov_sky_arcsec2=det.cov_sky_arcsec2,
                stat_cov_sky_arcsec2=det.stat_cov_sky_arcsec2,
                flux=det.flux,
                flux_err=det.flux_err,
                flags=det.flags,
                band_id=det.band_id,
                epoch_index=ei,
            )
        )
        det_refs.append((ei, det))
    if len(meas) < 2:
        _update_track_photometry(track)
        return
    try:
        mfit = fit_motion(meas, fit_parallax=fit_parallax)
    except ValueError as exc:
        msg = f"REFIT_FAILED: {exc}"
        if msg not in track.notes:
            track.notes.append(msg)
        return

    # Propagate outlier flags into excluded set for next EM pass and emit PM_OUTLIER link.
    for out_idx in mfit.outlier_indices:
        if 0 <= out_idx < len(det_refs):
            ei_bad, det_bad = det_refs[out_idx]
            track.excluded_dets.add((ei_bad, det_bad.detection_id))
            if (removed := track.links_by_epoch.pop(ei_bad, None)) is not None:
                _, c_v, dc_2nd, c_p, resid, dec_b = removed
                track.non_detect_links[ei_bad] = EpochLink(
                    track.object_id,
                    ei_bad,
                    det_bad.epoch_id,
                    det_bad.detection_id,
                    "PM_OUTLIER",
                    c_v,
                    dc_2nd,
                    c_p,
                    resid,
                    dec_b,
                )

    xi0, eta0 = sky_to_tangent_arcsec(
        mfit.ra_ref_deg, mfit.dec_ref_deg, mfit.ra_ref_deg, mfit.dec_ref_deg
    )
    track.t0_mjd = mfit.ref_epoch_mjd_tdb
    track.ra0_deg = mfit.ra_ref_deg
    track.dec0_deg = mfit.dec_ref_deg
    track.theta = np.array(
        [
            float(xi0),
            float(eta0),
            mfit.pmra_arcsec_yr,
            mfit.pmdec_arcsec_yr,
            mfit.parallax_mas * 1e-3,
        ],
        dtype=np.float64,
    )
    cov5 = np.diag([0.04, 0.04, 1.0, 1.0, 0.01])
    k = len(mfit.cov_params)
    cov5[:k, :k] = np.asarray(mfit.cov_params, dtype=np.float64)
    track.cov = cov5
    track.model_type = mfit.model_type
    track.chi2_nu = mfit.chi2_nu
    track.chi2_mu = mfit.chi2_mu
    track.delta_bic = mfit.delta_bic
    track.vim_r = mfit.vim_r
    if mfit.vim_flagged:
        track.notes = [n for n in track.notes if not n.startswith("VIM_CANDIDATE")]
        track.notes.append("VIM_CANDIDATE: photocentre wobble correlated with flux")
    _update_track_photometry(track)


def _solve_epoch_hungarian(
    tracks: list[_TrackProto], view: _EpochView
) -> dict[int, tuple[int, float, float, float, tuple[float, float]]]:
    """Global 1-to-1 Hungarian assignment + exact 2nd-best margin on active tracks (§G.1)."""
    k_cnt = len(tracks)
    j_cnt = len(view.detections)
    if k_cnt == 0:
        return {}

    preds: list[tuple[NDArray[np.float64], NDArray[np.float64], bool]] = []
    for tr in tracks:
        p_xy, p_cov, ra_p, dec_p = _predict_at_epoch(tr, view)
        x_p, y_p = (float(v) for v in view.wcs.world_to_pixel_values(ra_p, dec_p))
        in_fov = _in_footprint(x_p, y_p, view.shape)
        band_ok = not (tr.is_red_cool and (view.band_id or "") in _OPTICAL_BANDS)
        masked = False
        if in_fov and view.frame is not None and view.frame.flags is not None:
            ny, nx = view.shape
            ix, iy = int(np.clip(round(x_p), 0, nx - 1)), int(np.clip(round(y_p), 0, ny - 1))
            masked = bool(int(view.frame.flags[iy, ix]) & HARD_BAD_MASK)
        active = in_fov and band_ok and not masked
        nd_status: Literal["OUT_OF_FIELD", "BAND_UNCOVERED", "MASKED"] | None = (
            "OUT_OF_FIELD"
            if not in_fov
            else ("BAND_UNCOVERED" if not band_ok else ("MASKED" if masked else None))
        )
        if nd_status is not None:
            tr.non_detect_links[view.epoch_index] = EpochLink(
                tr.object_id,
                view.epoch_index,
                view.epoch_id,
                None,
                nd_status,
                0.0,
                10.0,
                1.0,
                (0.0, 0.0),
            )
        preds.append((p_xy, p_cov, active))

    active_k = [k for k, (_, _, ok) in enumerate(preds) if ok]
    if not active_k:
        return {}

    m = len(active_k)
    cost = np.full((m, j_cnt + m), _BIG_COST, dtype=np.float64)
    res_map: dict[tuple[int, int], tuple[float, float]] = {}

    for rk, k in enumerate(active_k):
        tr = tracks[k]
        p_xy, p_cov, _ = preds[k]
        snr_est = max(
            [d.snr for d, *_ in tr.links_by_epoch.values() if d.band_id == view.band_id]
            or [d.snr for d, *_ in tr.links_by_epoch.values()]
            or [8.0]
        )
        p_d = float(np.clip(ndtr(snr_est - 5.0), 0.01, 0.99))
        cost[rk, j_cnt + rk] = -math.log(max(1.0 - p_d, 1e-6))
        for j, det in enumerate(view.detections):
            cij, _, r_vec = _pair_cost(tr, det, p_xy, p_cov, view)
            if cij < 0.5 * _BIG_COST:
                cost[rk, j] = cij
                res_map[(rk, j)] = r_vec

    # Solve global optimal assignment and compute exact 2nd-best margin per track.
    row_ind, col_ind = linear_sum_assignment(cost)
    opt_total = float(cost[row_ind, col_ind].sum())
    results: dict[int, tuple[int, float, float, float, tuple[float, float]]] = {}

    for rk, cj in zip(row_ind, col_ind, strict=True):
        k = active_k[int(rk)]
        c_val = float(cost[rk, cj])
        j_sel = int(cj) if int(cj) < j_cnt else -1
        # Exact 2nd-best margin: block chosen assignment (rk, cj) and solve again.
        c_alt = cost.copy()
        c_alt[rk, cj] = _BIG_COST
        r2, c2 = linear_sum_assignment(c_alt)
        alt_total = float(c_alt[r2, c2].sum())
        dc_2nd = max(0.0, alt_total - opt_total)
        # User forced accept/reassign locks confidence to 1.0.
        if (
            tracks[k].user_locks.get(view.epoch_index, ("", None))[0] in ("accept", "reassign")
            and j_sel >= 0
        ):
            dc_2nd = 12.0
        # Penalize blended collisions against silent false errors in crowded fields.
        if j_sel >= 0:
            det = view.detections[j_sel]
            prob = 1.0 / (1.0 + math.exp(-min(dc_2nd, 30.0)))
            n_clean_anchors = sum(
                1
                for ei_p, (prev_d, *_) in tracks[k].links_by_epoch.items()
                if ei_p != view.epoch_index and not (prev_d.flags & int(DetectionFlag.BLENDED))
            )
            if (det.flags & int(DetectionFlag.BLENDED)) and (dc_2nd < 6.0 or n_clean_anchors < 2):
                prob = min(prob, 0.85)
        else:
            prob = 1.0 / (1.0 + math.exp(-min(dc_2nd, 30.0)))
        results[k] = (j_sel, c_val, dc_2nd, prob, res_map.get((int(rk), j_sel), (0.0, 0.0)))
    return results


def link_epochs(
    epochs: Sequence[ExtractionResult | FrameData | bytes | Path | str | fits.HDUList],
    *,
    catalog_sources: Sequence[Any] | None = None,
    high_pm_mode: bool = False,
    frames: Sequence[Any] | None = None,
    overrides: Sequence[UserOverride] | None = None,
    max_pm_arcsec_yr: float = 25.0,
    sigma_mu_0: float = 2.0,
) -> list[TrackedSource]:
    """Link multi-epoch detections into persistent object identities via §G.1 Hungarian EM."""
    if not epochs:
        return []
    views = _to_epoch_views(epochs, frames=frames)
    _flag_persistence_artifacts(views)

    ordered = sorted(views, key=lambda v: (v.mjd_tdb, v.epoch_index))
    tracks = _seed_tracks(
        views,
        catalog_sources,
        high_pm_mode=high_pm_mode,
        sigma_mu_0=sigma_mu_0,
        max_pm_arcsec_yr=max_pm_arcsec_yr,
    )
    if overrides:
        by_id = {t.object_id: t for t in tracks}
        for ov in overrides:
            if ov.object_id in by_id:
                det_id = (
                    f"e{ov.epoch_index}_s{ov.detection_index}"
                    if ov.detection_index is not None
                    else None
                )
                by_id[ov.object_id].user_locks[ov.epoch_index] = (ov.action, det_id)

    # Initial refit on seeds before EM assignment.
    for tr in tracks:
        _refit_track_state(tr)

    prev_sig: dict[tuple[str, int], str | None] = {}
    for _it in range(3):
        cur_sig: dict[tuple[str, int], str | None] = {}
        for tr in tracks:
            tr.links_by_epoch.clear()
            tr.non_detect_links.clear()

        claimed_by_epoch: dict[int, set[str]] = {v.epoch_index: set() for v in ordered}
        for v in ordered:
            assign = _solve_epoch_hungarian(tracks, v)
            for k, (j_sel, cost_v, dc_2nd, conf, resid) in assign.items():
                tr = tracks[k]
                if j_sel >= 0:
                    det = v.detections[j_sel]
                    ambig = conf < 0.90
                    d_flags = det.flags | (int(DetectionFlag.AMBIGUOUS_MATCH) if ambig else 0)
                    dec_by: Literal["auto", "user"] = (
                        "user"
                        if tr.user_locks.get(v.epoch_index, ("", None))[0] in ("accept", "reassign")
                        else "auto"
                    )
                    if dec_by == "user":
                        d_flags |= int(DetectionFlag.USER_OVERRIDE)
                    det_linked = replace(det, confidence=conf, ambiguous_match=ambig, flags=d_flags)
                    tr.links_by_epoch[v.epoch_index] = (
                        det_linked,
                        cost_v,
                        dc_2nd,
                        conf,
                        resid,
                        dec_by,
                    )
                    claimed_by_epoch[v.epoch_index].add(det.detection_id)
                    cur_sig[(tr.object_id, v.epoch_index)] = det.detection_id
                else:
                    cur_sig[(tr.object_id, v.epoch_index)] = None
                    # Check forced PSF photometry if frame available (§G.2).
                    p_xy, _, ra_p, dec_p = _predict_at_epoch(tr, v)
                    status_nd: Literal["LOW_SNR_FORCED", "UPPER_LIMIT", "PM_OUTLIER"] = (
                        "UPPER_LIMIT"
                    )
                    if v.frame is not None:
                        fl, fe, snr_f, flg_f = forced_photometry_at_sky(v.frame, ra_p, dec_p)
                        if 2.5 <= snr_f < 5.0:
                            status_nd = "LOW_SNR_FORCED"
                            x_p, y_p = (
                                float(val) for val in v.wcs.world_to_pixel_values(ra_p, dec_p)
                            )
                            sig_f = max(0.3, v.fwhm_arcsec / (2.355 * max(snr_f, 1.0)))
                            det_forced = _make_forced_det(
                                f"e{v.epoch_index}_forced_{tr.object_id}",
                                v.epoch_index,
                                v.epoch_id,
                                v.mjd_tdb,
                                v.array_id,
                                x_p,
                                y_p,
                                ra_p,
                                dec_p,
                                sig_f,
                                fl,
                                fe,
                                snr_f,
                                flg_f | int(DetectionFlag.LOW_SNR),
                                v.band_id,
                                confidence=conf,
                            )
                            tr.links_by_epoch[v.epoch_index] = (
                                det_forced,
                                cost_v,
                                dc_2nd,
                                conf,
                                resid,
                                "auto",
                            )
                        elif snr_f < 2.5:
                            x_p, y_p = (
                                float(val) for val in v.wcs.world_to_pixel_values(ra_p, dec_p)
                            )
                            sig_f = max(0.3, v.fwhm_arcsec / 2.355)
                            det_ul = _make_forced_det(
                                f"e{v.epoch_index}_ul_{tr.object_id}",
                                v.epoch_index,
                                v.epoch_id,
                                v.mjd_tdb,
                                v.array_id,
                                x_p,
                                y_p,
                                ra_p,
                                dec_p,
                                sig_f,
                                3.0 * fe,
                                fe,
                                snr_f,
                                flg_f,
                                v.band_id,
                                confidence=conf,
                                is_upper_limit=True,
                                status="UPPER_LIMIT",
                            )
                            tr.forced_measurements.append(det_ul)
                    det_excl = next(
                        (did for e, did in tr.excluded_dets if e == v.epoch_index), None
                    )
                    if status_nd == "UPPER_LIMIT" and det_excl is not None:
                        status_nd = "PM_OUTLIER"
                    if status_nd != "LOW_SNR_FORCED":
                        tr.non_detect_links[v.epoch_index] = EpochLink(
                            tr.object_id,
                            v.epoch_index,
                            v.epoch_id,
                            det_excl if status_nd == "PM_OUTLIER" else None,
                            status_nd,
                            cost_v,
                            dc_2nd,
                            conf,
                            resid,
                            "user" if v.epoch_index in tr.user_locks else "auto",
                        )

        # Pick up any clean unclaimed detections not linked by existing tracks
        for v in ordered:
            for d in v.detections:
                if (
                    d.detection_id not in claimed_by_epoch[v.epoch_index]
                    and not (d.flags & HARD_BAD_MASK)
                    and d.ra_deg is not None
                    and d.dec_deg is not None
                ):
                    # Add remaining unlinked clean detections as singleton tracks.
                    idx_new = len(tracks)
                    nt = _TrackProto(
                        object_id=f"obj_{idx_new}",
                        label=_session_label(idx_new),
                        t0_mjd=v.mjd_tdb,
                        ra0_deg=d.ra_deg,
                        dec0_deg=d.dec_deg,
                        theta=np.array([0.0, 0.0, 0.0, 0.0, 0.0]),
                        cov=np.diag([0.09, 0.09, sigma_mu_0**2, sigma_mu_0**2, 0.01]),
                    )
                    nt.links_by_epoch[v.epoch_index] = (d, 0.0, 10.0, 1.0, (0.0, 0.0), "auto")
                    _update_track_photometry(nt)
                    tracks.append(nt)

        for tr in tracks:
            _refit_track_state(tr)

        if cur_sig == prev_sig:
            break
        prev_sig = cur_sig

    # Deduplicate tracks sharing >= 80% of epoch detection IDs (§G.2) and drop empty tracks.
    kept_protos: list[_TrackProto] = []
    tracks_sorted = sorted(tracks, key=lambda t: (-len(t.links_by_epoch), t.chi2_nu))
    sig_sets: list[tuple[_TrackProto, set[tuple[int, str]]]] = []
    for tr in tracks_sorted:
        if not tr.links_by_epoch:
            continue
        cur_set = {
            (ei, det.detection_id)
            for ei, (det, *_) in tr.links_by_epoch.items()
            if "_forced_" not in det.detection_id
        }
        if not cur_set:
            continue
        is_dup = False
        dup_target: _TrackProto | None = None
        for prev_tr, s_prev in sig_sets:
            overlap = len(cur_set & s_prev)
            if overlap >= 0.8 * min(len(cur_set), len(s_prev)) and overlap > 0:
                is_dup = True
                dup_target = prev_tr
                break
        if is_dup and dup_target is not None:
            dup_target.flags |= int(DetectionFlag.DUPLICATE_PROPOSAL)
            dup_target.notes.append(
                f"DUPLICATE_PROPOSAL: {tr.object_id} proposed with >=80% overlap"
            )
            tr.duplicate_of = dup_target.object_id
            tr.flags |= int(DetectionFlag.DUPLICATE_PROPOSAL)
            continue
        sig_sets.append((tr, cur_set))
        kept_protos.append(tr)

    # Assign final deterministic catalog matches, unresolved binary checks, public_id, LOST status.
    out: list[TrackedSource] = []
    view_by_idx = {v.epoch_index: v for v in views}
    for i_tr, tr in enumerate(kept_protos):
        dets_ordered = [tr.links_by_epoch[ei][0] for ei in sorted(tr.links_by_epoch)]
        confs = [d.confidence for d in dets_ordered]
        track_conf = float(min(confs)) if len(confs) == 1 else float(np.median(confs))

        # Crossmatch final trajectory center at t0 against catalog
        matches = crossmatch_catalog(
            tr.ra0_deg,
            tr.dec0_deg,
            tr.t0_mjd,
            sigma_pos_arcsec=0.3,
            max_radius_arcsec=3.0,
            catalog_sources=catalog_sources,
        )
        gaia_m = [m for m in matches if m.gaia_id is not None and m.match_prob >= 0.8]
        gaia_id = gaia_m[0].gaia_id if gaia_m else tr.gaia_id

        # Check unresolved binary metadata (< 0.8 FWHM) -> caveat per §G.2 & §F.3
        notes = list(tr.notes)
        mean_fwhm = float(np.mean([d.fwhm_arcsec for d in dets_ordered]))
        for cm in matches:
            if (
                cm.binary_separation_arcsec is not None
                and cm.binary_separation_arcsec < 0.8 * mean_fwhm
            ):
                msg = (
                    f'UNRESOLVED_PHOTOCENTER ({cm.binary_separation_arcsec:.2f}" < 0.8*FWHM): '
                    "binary unresolved; orbit not measurable"
                )
                if msg not in notes:
                    notes.append(msg)

        # Build EpochLink list and flag LOST (>= 2 consecutive misses at SNR_pred >= 7)
        links_out: list[EpochLink] = []
        consec_miss = 0
        snr_pred = float(np.median([d.snr for d in dets_ordered]))
        lost = False
        for v in ordered:
            ei = v.epoch_index
            if ei in tr.links_by_epoch:
                det, c_v, dc_2nd, c_p, resid, dec_b = tr.links_by_epoch[ei]
                consec_miss = 0
                links_out.append(
                    EpochLink(
                        object_id=tr.object_id,
                        epoch_index=ei,
                        epoch_id=v.epoch_id,
                        detection_id=det.detection_id,
                        status="LOW_SNR_FORCED"
                        if det.status == "LOW_SNR_FORCED"
                        else ("AMBIGUOUS_MATCH" if det.ambiguous_match else "LINKED"),
                        cost_ll=c_v,
                        delta_c_2nd=dc_2nd,
                        match_prob=c_p,
                        residual_arcsec=resid,
                        decided_by=dec_b,
                    )
                )
            elif ei in tr.non_detect_links:
                nd = tr.non_detect_links[ei]
                links_out.append(nd)
                if nd.status == "UPPER_LIMIT" and snr_pred >= 7.0:
                    consec_miss += 1
                    if consec_miss >= 2:
                        lost = True

        all_flags = int(tr.flags)
        for d in dets_ordered:
            all_flags |= d.flags
        if lost:
            all_flags |= int(DetectionFlag.LOST)
            notes.append("LOST: >= 2 consecutive misses inside active FOV at SNR_pred >= 7.")

        # Public ID propagated to J2026.0 for stable multi-epoch tracks (>= 3 epochs, conf >= 0.90)
        pub_id: str | None = None
        if len(dets_ordered) >= 3 and track_conf >= 0.90:
            mjd_2026 = float(Time(2026.0, format="jyear", scale="tdb").mjd)
            dt_26 = (mjd_2026 - tr.t0_mjd) / 365.25
            xi26 = float(tr.theta[0]) + float(tr.theta[2]) * dt_26
            eta26 = float(tr.theta[1]) + float(tr.theta[3]) * dt_26
            ra26, dec26 = (
                float(v) for v in tangent_arcsec_to_sky(xi26, eta26, tr.ra0_deg, tr.dec0_deg)
            )
            pub_id = format_public_id(ra26, dec26)

        t_state = TrackState(
            object_id=tr.object_id,
            label=_session_label(i_tr),
            ref_epoch_jyear=mjd_to_jyear(tr.t0_mjd),
            ra0_deg=tr.ra0_deg,
            dec0_deg=tr.dec0_deg,
            theta_5=tuple(float(v) for v in tr.theta),  # type: ignore[arg-type]
            cov_5x5=tuple(tuple(float(x) for x in r) for r in tr.cov),
            mag_bands=dict(tr.mag_by_band),
            sigma_var_mag=tr.sigma_var_mag,
            model_type=tr.model_type,
            chi2_nu=tr.chi2_nu,
            chi2_mu=tr.chi2_mu,
            delta_bic=tr.delta_bic,
            vim_r=tr.vim_r,
            flags=all_flags,
        )
        # Order linked detections monotonically by epoch_index.
        dets_ordered.sort(key=lambda d: d.epoch_index)
        out.append(
            TrackedSource(
                object_id=tr.object_id,
                label=_session_label(i_tr),
                public_id=pub_id,
                status="lost" if lost else "active",
                detections=dets_ordered,
                links=links_out,
                state=t_state,
                confidence=track_conf,
                gaia_id=gaia_id,
                catalog_matches=matches,
                flags=all_flags,
                notes=notes,
                duplicate_of=tr.duplicate_of,
                forced_measurements=tr.forced_measurements,
                user_locked_pairs={
                    ei: lock for ei, lock in tr.user_locks.items() if ei in view_by_idx
                },
            )
        )
    return out


def apply_manual_override(
    tracks: Sequence[TrackedSource],
    *,
    object_id: str,
    epoch_index: int,
    action: Literal["accept", "reject", "reassign"],
    new_detection: EpochDetection | None = None,
    reason: str = "user override",
) -> list[TrackedSource]:
    """Apply user override (`USER_OVERRIDE`, `decided_by='user'`) and refit motion (§G.2)."""
    updated: list[TrackedSource] = []
    for tr in tracks:
        if tr.object_id != object_id:
            updated.append(tr)
            continue
        new_dets = list(tr.detections)
        new_links = list(tr.links)
        if action == "reject":
            new_dets = [d for d in new_dets if d.epoch_index != epoch_index]
            new_links = [
                EpochLink(
                    tr.object_id,
                    epoch_index,
                    f"epoch_{epoch_index}",
                    None,
                    "MASKED",
                    _BIG_COST,
                    10.0,
                    1.0,
                    (0.0, 0.0),
                    "user",
                )
                if lk.epoch_index == epoch_index
                else lk
                for lk in new_links
            ]
        elif action == "accept":
            new_dets = [
                replace(
                    d,
                    confidence=1.0,
                    ambiguous_match=False,
                    flags=d.flags | int(DetectionFlag.USER_OVERRIDE),
                )
                if d.epoch_index == epoch_index
                else d
                for d in new_dets
            ]
            new_links = [
                replace(lk, match_prob=1.0, decided_by="user", status="LINKED")
                if lk.epoch_index == epoch_index
                else lk
                for lk in new_links
            ]
        elif action == "reassign":
            if new_detection is None:
                raise ValueError("`new_detection` is required for action='reassign'.")
            locked_det = replace(
                new_detection,
                epoch_index=epoch_index,
                confidence=1.0,
                ambiguous_match=False,
                flags=new_detection.flags | int(DetectionFlag.USER_OVERRIDE),
            )
            new_dets = [d for d in new_dets if d.epoch_index != epoch_index] + [locked_det]
            new_dets.sort(key=lambda d: d.epoch_index)
            new_links = [
                EpochLink(
                    tr.object_id,
                    epoch_index,
                    f"epoch_{epoch_index}",
                    locked_det.detection_id,
                    "LINKED",
                    -_BIG_COST,
                    10.0,
                    1.0,
                    (0.0, 0.0),
                    "user",
                )
                if lk.epoch_index == epoch_index
                else lk
                for lk in new_links
            ]

        new_locks = dict(tr.user_locked_pairs)
        new_locks[epoch_index] = (action, new_detection.detection_id if new_detection else None)
        tr_mod = replace(
            tr,
            detections=new_dets,
            links=new_links,
            flags=tr.flags | int(DetectionFlag.USER_OVERRIDE),
            notes=[*tr.notes, f"User override ({action}) at epoch {epoch_index}: {reason}"],
            user_locked_pairs=new_locks,
        )
        # Recompute motion state when >= 2 epochs remain.
        if len(new_dets) >= 2:
            tr_mod.state = _state_from_mfit(
                tr_mod.object_id,
                tr_mod.label,
                fit_motion(tr_mod),
                tr_mod.state.mag_bands,
                sigma_var_mag=tr_mod.state.sigma_var_mag,
                flags=tr_mod.flags,
            )
        updated.append(tr_mod)
    return updated


def merge_tracks(
    tracks: Sequence[TrackedSource], primary_id: str, duplicate_id: str
) -> list[TrackedSource]:
    """Merge duplicate track `duplicate_id` into `primary_id` flagging `MERGED` (§G.2)."""
    by_id = {t.object_id: t for t in tracks}
    if primary_id not in by_id or duplicate_id not in by_id or primary_id == duplicate_id:
        raise ValueError("Invalid primary_id or duplicate_id for merge_tracks.")
    prim, dup = by_id[primary_id], by_id[duplicate_id]
    seen_ep = {d.epoch_index: d for d in dup.detections} | {
        d.epoch_index: d for d in prim.detections
    }
    merged_dets = [seen_ep[k] for k in sorted(seen_ep)]
    seen_links = {l.epoch_index: l for l in dup.links} | {
        l.epoch_index: l for l in prim.links
    }
    merged_links = [seen_links[k] for k in sorted(seen_links)]
    m_flag = int(DetectionFlag.MERGED)
    out: list[TrackedSource] = []
    for t in tracks:
        if t.object_id == primary_id:
            merged_track = replace(
                t,
                detections=merged_dets,
                links=merged_links,
                flags=t.flags | m_flag,
                notes=[*t.notes, f"Merged detections from {duplicate_id}."],
            )
            if len(merged_dets) >= 2:
                merged_track.state = _state_from_mfit(
                    merged_track.object_id,
                    merged_track.label,
                    fit_motion(merged_track),
                    merged_track.state.mag_bands,
                    sigma_var_mag=merged_track.state.sigma_var_mag,
                    flags=merged_track.flags,
                )
            out.append(merged_track)
        elif t.object_id == duplicate_id:
            out.append(
                replace(
                    t,
                    status="merged",
                    merged_into=primary_id,
                    flags=t.flags | m_flag,
                    notes=[*t.notes, f"Marked duplicate; merged into {primary_id}."],
                )
            )
        else:
            out.append(t)
    return out


def _joint_deblend_res(
    p: NDArray[np.float64],
    prf_m: CircularGaussianPRF,
    gx_l: NDArray[np.float64],
    gy_l: NDArray[np.float64],
    stat_unit: NDArray[np.float64],
    obs_l: NDArray[np.float64],
    err_l: NDArray[np.float64],
) -> NDArray[np.float64]:
    fm, xm, ym, fs = (float(v) for v in p)
    prf_m.x_0, prf_m.y_0 = xm, ym
    mod = fm * np.asarray(prf_m(gx_l, gy_l), dtype=np.float64) + fs * stat_unit
    return np.asarray((mod - obs_l) / err_l, dtype=np.float64)


def _simultaneous_deblend_track(
    b56_frames: list[FrameData],
    x0_init: float,
    y0_init: float,
    t0: float,
    x1_init: float,
    y1_init: float,
    t1: float,
    ref_wcs: WCS,
    static_sky_positions: list[tuple[float, float, float]],
) -> list[EpochDetection]:
    """Simultaneous grouped multi-PSF fit along linear dipole seed on native B5/B6 frames (§G.2)."""
    ra_s0, dec_s0 = (float(v) for v in ref_wcs.pixel_to_world_values(x0_init, y0_init))
    ra_s1, dec_s1 = (float(v) for v in ref_wcs.pixel_to_world_values(x1_init, y1_init))
    xi1, eta1 = sky_to_tangent_arcsec(ra_s1, dec_s1, ra_s0, dec_s0)
    dt_pair = (t1 - t0) / 365.25
    if abs(dt_pair) < 1e-4:
        return []
    vx_as, vy_as = float(xi1) / dt_pair, float(eta1) / dt_pair

    dets: list[EpochDetection] = []
    for e_i, fr in enumerate(b56_frames):
        bg = estimate_background_and_noise(fr)
        dt_k = (fr.epoch.mjd_tdb_mid - t0) / 365.25
        ra_g, dec_g = (
            float(v) for v in tangent_arcsec_to_sky(vx_as * dt_k, vy_as * dt_k, ra_s0, dec_s0)
        )
        xg, yg = (float(v) for v in fr.wcs.world_to_pixel_values(ra_g, dec_g))
        ny, nx = fr.shape
        if not (1.0 <= xg <= nx - 2.0 and 1.0 <= yg <= ny - 2.0):
            continue

        fwhm_px = 2.3548200450309493 * fr.psf_sigma_px
        far_neighbors: list[tuple[float, float, float]] = []
        close_neighbors: list[tuple[float, float, float]] = []
        for sra, sdec, sflux in static_sky_positions:
            sx, sy = (float(v) for v in fr.wcs.world_to_pixel_values(sra, sdec))
            d_px = math.hypot(sx - xg, sy - yg)
            if d_px < 10.0 and 0.5 <= sx <= nx - 1.5 and 0.5 <= sy <= ny - 1.5:
                (close_neighbors if d_px <= 3.5 else far_neighbors).append(
                    (sx, sy, max(sflux, 10.0))
                )

        work_img = bg.subtracted.copy()
        if far_neighbors:
            yy, xx = np.mgrid[:ny, :nx]
            for fx_p, fy_p, ff_p in far_neighbors:
                work_img -= ff_p * CircularGaussianPRF(fwhm=fwhm_px, flux=1.0, x_0=fx_p, y_0=fy_p)(
                    xx, yy
                )

        if close_neighbors:
            yy_s, xx_s = np.mgrid[:ny, :nx]
            ixg, iyg = int(round(xg)), int(round(yg))
            x0b, x1b, y0b, y1b = (
                max(0, ixg - 4),
                min(nx, ixg + 5),
                max(0, iyg - 4),
                min(ny, iyg + 5),
            )
            q_loc = bg.quality_mask[y0b:y1b, x0b:x1b]
            gx_l = xx_s[y0b:y1b, x0b:x1b][q_loc].astype(np.float64)
            gy_l = yy_s[y0b:y1b, x0b:x1b][q_loc].astype(np.float64)
            obs_l = work_img[y0b:y1b, x0b:x1b][q_loc].astype(np.float64)
            err_l = np.maximum(bg.rms[y0b:y1b, x0b:x1b][q_loc].astype(np.float64), 1e-9)
            nx_st, ny_st, fs_0 = close_neighbors[0]
            stat_unit = np.asarray(
                CircularGaussianPRF(fwhm=fwhm_px, flux=1.0, x_0=nx_st, y_0=ny_st)(gx_l, gy_l),
                dtype=np.float64,
            )
            prf_m = CircularGaussianPRF(fwhm=fwhm_px, flux=1.0, x_0=0.0, y_0=0.0)
            lsq = least_squares(
                _joint_deblend_res,
                x0=np.array([45.0, xg, yg, fs_0]),
                args=(prf_m, gx_l, gy_l, stat_unit, obs_l, err_l),
                bounds=([0.0, xg - 2.0, yg - 2.0, 0.0], [np.inf, xg + 2.0, yg + 2.0, np.inf]),
                xtol=1e-4,
                ftol=1e-4,
                max_nfev=80,
            )
            flux_m, x_fit, y_fit, _ = (float(v) for v in lsq.x)
            chi2_nu = float(np.sum(lsq.fun**2)) / max(len(obs_l) - 4, 1)
            cov_p = np.linalg.pinv(lsq.jac.T @ lsq.jac) * max(1.0, chi2_nu)
            flux_err = math.sqrt(max(float(cov_p[0, 0]), 1e-6))
            cov_xy, det_flag = cov_p[1:3, 1:3], int(DetectionFlag.BLENDED)
        else:
            prf = CircularGaussianPRF(fwhm=fwhm_px)
            prf.x_0.bounds, prf.y_0.bounds = (xg - 2.5, xg + 2.5), (yg - 2.5, yg + 2.5)
            side = max(7, min(9, ny - (ny % 2 == 0)))
            phot = PSFPhotometry(
                prf, fit_shape=(side, side), aperture_radius=max(2.5, 1.3 * fwhm_px)
            )
            res_t = phot(
                work_img,
                mask=~bg.quality_mask,
                error=bg.rms,
                init_params=Table({"x_0": [xg], "y_0": [yg], "flux_0": [45.0]}),
            )
            flux_m, flux_err = float(res_t["flux_fit"][0]), max(float(res_t["flux_err"][0]), 1e-6)
            x_fit, y_fit = float(res_t["x_fit"][0]), float(res_t["y_fit"][0])
            chi2_nu = (
                float(res_t["reduced_chi2"][0]) if np.isfinite(res_t["reduced_chi2"][0]) else 1.0
            )
            fit_infos = getattr(phot, "fit_info", None) or getattr(phot, "fit_results", None)
            cov_xy = None
            if (
                fit_infos
                and hasattr(fit_infos[0], "get")
                and (pcov := fit_infos[0].get("param_cov")) is not None
            ):
                cov_xy = np.asarray(pcov[1:3, 1:3], dtype=np.float64) * max(1.0, chi2_nu)
            if cov_xy is None:
                xe = (
                    float(res_t["x_err"][0])
                    if "x_err" in res_t.colnames and np.isfinite(res_t["x_err"][0])
                    else max(0.05, fr.psf_sigma_px / max(flux_m / flux_err, 1.0))
                )
                ye = (
                    float(res_t["y_err"][0])
                    if "y_err" in res_t.colnames and np.isfinite(res_t["y_err"][0])
                    else max(0.05, fr.psf_sigma_px / max(flux_m / flux_err, 1.0))
                )
                cov_xy = np.diag([xe**2, ye**2]) * max(1.0, chi2_nu)
            det_flag = 0

        snr_m = flux_m / max(flux_err, 1e-9)
        if flux_m <= 0.0 or not math.isfinite(snr_m):
            continue
        pixscale = fr.wcs_metrics.pixscale_arcsec
        jac = local_wcs_jacobian(fr.wcs, x_fit, y_fit)
        cov_sky = jac @ (cov_xy + (0.05**2 + 0.03**2) / (pixscale**2) * np.eye(2)) @ jac.T + (
            0.05**2 + 0.10**2
        ) * np.eye(2)
        ra_m, dec_m = (float(v) for v in fr.wcs.pixel_to_world_values(x_fit, y_fit))
        band = fr.band_id or band_group_for_wavelength(fr.wavelength_um) or "B6"
        dets.append(
            EpochDetection(
                f"deblend_e{e_i}",
                e_i,
                f"b56_{e_i}",
                fr.epoch.mjd_tdb_mid,
                fr.array_id,
                x_fit,
                y_fit,
                ra_m % 360.0,
                dec_m,
                (float(cov_sky[0, 0]), float(cov_sky[1, 1]), float(cov_sky[0, 1])),
                (float(cov_xy[0, 0]), float(cov_xy[1, 1]), float(cov_xy[0, 1])),
                math.sqrt(max(float(cov_sky[0, 0]), 1e-8)),
                math.sqrt(max(float(cov_sky[1, 1]), 1e-8)),
                flux_m,
                flux_err,
                {band: (flux_m, flux_err)},
                snr_m,
                0.95,
                det_flag,
                band,
                psf_chi2_nu=chi2_nu,
                confidence=0.96,
                ambiguous_match=False,
            )
        )
    return dets


def run_target_pipeline(
    ra_query_deg: float,
    dec_query_deg: float,
    frames: Sequence[Any],
    *,
    min_fov_arcmin: float = 6.0,
    catalog_sources: Sequence[Any] | None = None,
) -> TargetSearchResult:
    """Search `frames` over `>= min_fov_arcmin` for high-PM / faint-red movers (e.g. WISE 0855)."""
    from sphx_core.classify import classify_source

    loaded = [as_frame_data(f) for f in frames]
    if not loaded:
        raise ValueError("run_target_pipeline requires at least one frame.")
    if (
        max(math.sqrt(max(fr.wcs_metrics.area_arcmin2, 0.0)) for fr in loaded)
        < min_fov_arcmin - 0.2
    ):
        raise ValueError(f"Frame FOV below required min_fov_arcmin={min_fov_arcmin:.2f}'.")

    b56_frames = [
        fr
        for fr in loaded
        if (fr.band_id or band_group_for_wavelength(fr.wavelength_um)) in _IR_RED_BANDS
    ] or list(loaded)
    b13_frames = [
        fr
        for fr in loaded
        if (fr.band_id or band_group_for_wavelength(fr.wavelength_um)) in _OPTICAL_BANDS
    ]
    b13_exts, b56_exts = (
        [extract_sources(fr) for fr in b13_frames],
        [extract_sources(fr) for fr in b56_frames],
    )

    static_stars: list[tuple[float, float, float]] = []
    for ext in b13_exts:
        for d in ext.detections:
            if not any(
                math.hypot(*sky_to_tangent_arcsec(d.ra_deg, d.dec_deg, r0, d0)) < 3.0
                for r0, d0, _ in static_stars
            ):
                static_stars.append((d.ra_deg, d.dec_deg, d.flux))

    # 1. Direct unblended mover links in B5/B6 rejecting tracks pinned to static B1-3 stars.
    direct_tracks = link_epochs(b56_exts, catalog_sources=catalog_sources, high_pm_mode=True)
    candidate_track = max(
        (
            tr
            for tr in direct_tracks
            if len(tr.detections) >= 2
            and tr.state.chi2_mu > 25.0
            and tr.gaia_id is None
            and sum(
                any(
                    math.hypot(*sky_to_tangent_arcsec(float(d.ra_deg), float(d.dec_deg), sra, sdec))
                    < 8.0
                    for sra, sdec, _ in static_stars
                )
                for d in tr.detections
                if d.ra_deg is not None and d.dec_deg is not None
            )
            < max(2, len(tr.detections) - 1)
        ),
        key=lambda t: t.state.chi2_mu,
        default=None,
    )

    # 2. If blended near foreground star, seed from ZOGY dipole + grouped PSF fit (§G.2).
    if candidate_track is None and len(b56_frames) >= 2:
        if not static_stars:
            static_stars = [
                (tr.ra_deg, tr.dec_deg, tr.detections[0].flux or 100.0)
                for tr in direct_tracks
                if tr.state.chi2_mu <= 9.0 and tr.detections
            ]
        diff = difference_frames(b56_frames)
        for st in (
            s
            for s in diff.stamp_tests
            if s.dipole_favored or (abs(s.snr_corr) >= 4.8 and s.displacement_px >= 0.4)
        ):
            ia, ib = st.pair_indices
            dets_deb = _simultaneous_deblend_track(
                b56_frames,
                st.x0_dipole_px,
                st.y0_dipole_px,
                b56_frames[ia].epoch.mjd_tdb_mid,
                st.x1_dipole_px,
                st.y1_dipole_px,
                b56_frames[ib].epoch.mjd_tdb_mid,
                b56_frames[ib].wcs,
                static_stars,
            )
            if sum(1 for d in dets_deb if d.snr >= 5.0) >= 2:
                mfit_d = fit_motion(
                    [
                        MotionMeasurement(
                            mjd_tdb=d.mjd_tdb,
                            ra_deg=float(d.ra_deg),
                            dec_deg=float(d.dec_deg),
                            cov_sky_arcsec2=d.cov_sky_arcsec2,
                            stat_cov_sky_arcsec2=d.stat_cov_sky_arcsec2,
                            flux=d.flux,
                            flux_err=d.flux_err,
                            flags=d.flags,
                            band_id=d.band_id,
                            epoch_index=d.epoch_index,
                        )
                        for d in dets_deb
                        if d.ra_deg is not None and d.dec_deg is not None
                    ]
                )
                if mfit_d.chi2_mu > 25.0:
                    mags_d = {
                        d.band_id or "B6": _flux_to_mag(d.flux)
                        for d in dets_deb
                        if d.flux and d.flux > 0
                    }
                    candidate_track = TrackedSource(
                        "target_mover",
                        "A",
                        format_public_id(mfit_d.ra_ref_deg, mfit_d.dec_ref_deg),
                        "active",
                        dets_deb,
                        [
                            EpochLink(
                                "target_mover",
                                d.epoch_index,
                                d.epoch_id,
                                d.detection_id,
                                "LINKED",
                                0.0,
                                8.0,
                                d.confidence,
                                (0.0, 0.0),
                            )
                            for d in dets_deb
                        ],
                        _state_from_mfit("target_mover", "A", mfit_d, mags_d),
                        float(np.median([d.confidence for d in dets_deb])),
                        None,
                    )
                    break

    if candidate_track is None:
        med_rms = float(
            np.median([estimate_background_and_noise(fr).empirical_rms for fr in b56_frames])
        )
        med_sig = float(np.median([fr.psf_sigma_px for fr in b56_frames]))
        ul_3sig = 3.0 * max(
            2.0 * math.sqrt(math.pi) * med_sig * med_rms / math.sqrt(len(b56_frames)), 1e-6
        )
        return TargetSearchResult(
            False,
            ul_3sig,
            0,
            0.0,
            0.0,
            None,
            None,
            0.0,
            0.0,
            0.0,
            {"brown_dwarf": 0.0, "mover": 0.0},
            [],
            notes=[f"NON-DETECTION: 3-sigma upper limit {ul_3sig:.2f}."],
        )

    mfit = fit_motion(candidate_track)
    det_b56 = [d for d in candidate_track.detections if d.snr >= 5.0]
    snr_b56 = float(np.mean([d.snr for d in det_b56])) if det_b56 else 0.0

    # Forced simultaneous photometry in B1-B3 along predicted trajectory coordinates.
    b13_flux_w: list[tuple[float, float]] = []
    b13_forced: list[EpochDetection] = []
    for k_f, fr in enumerate(b13_frames):
        dt_yr = (fr.epoch.mjd_tdb_mid - mfit.ref_epoch_mjd_tdb) / 365.25
        ra_p, dec_p = (
            float(v)
            for v in tangent_arcsec_to_sky(
                mfit.pmra_arcsec_yr * dt_yr,
                mfit.pmdec_arcsec_yr * dt_yr,
                mfit.ra_ref_deg,
                mfit.dec_ref_deg,
            )
        )
        xp, yp = (float(v) for v in fr.wcs.world_to_pixel_values(ra_p, dec_p))
        nbrs = [
            (sx, sy, sfl)
            for sra, sdec, sfl in static_stars
            for sx, sy in ((float(v) for v in fr.wcs.world_to_pixel_values(sra, sdec)),)
            if math.hypot(sx - xp, sy - yp) < 10.0
        ]
        fl, fe, snr_f, flg_f = forced_photometry_at_sky(fr, ra_p, dec_p, neighbors=nbrs)
        b13_flux_w.append((fl, fe))
        b13_forced.append(
            _make_forced_det(
                f"b13_forced_{k_f}",
                1000 + k_f,
                f"b13_{k_f}",
                fr.epoch.mjd_tdb_mid,
                fr.array_id,
                xp,
                yp,
                ra_p,
                dec_p,
                0.5,
                max(fl, 0.0),
                fe,
                max(snr_f, 0.0),
                flg_f | int(DetectionFlag.LOW_SNR),
                fr.band_id or "B2",
                real_bogus=0.0,
                status="UPPER_LIMIT",
                is_upper_limit=True,
            )
        )
    w_sum = sum(1.0 / (max(fe, 1e-6) ** 2) for _, fe in b13_flux_w)
    fl_stack = (
        sum(fl / (max(fe, 1e-6) ** 2) for fl, fe in b13_flux_w) / max(w_sum, 1e-12)
        if b13_flux_w
        else 0.0
    )
    snr_b13 = max(0.0, fl_stack * math.sqrt(w_sum)) if b13_flux_w else 0.0

    gaia_id = next(
        (
            m.gaia_id
            for m in crossmatch_catalog(
                mfit.ra_ref_deg,
                mfit.dec_ref_deg,
                mfit.ref_epoch_mjd_tdb,
                max_radius_arcsec=2.0,
                catalog_sources=catalog_sources,
            )
            if m.gaia_id is not None and m.match_prob >= 0.8
        ),
        None,
    )
    track_for_cls = replace(candidate_track, forced_measurements=b13_forced, gaia_id=gaia_id)
    cls_res = classify_source(track_for_cls)
    return TargetSearchResult(
        True,
        0.0,
        len(det_b56),
        snr_b56,
        snr_b13,
        gaia_id,
        cls_res.spectral_subtype,
        mfit.mu_arcsec_yr,
        mfit.sigma_mu_arcsec_yr,
        mfit.pa_deg,
        cls_res.probabilities,
        candidate_track.detections,
        track_for_cls,
        cls_res.evidence,
    )


__all__ = [
    "CatalogMatch",
    "EpochDetection",
    "EpochLink",
    "TargetSearchResult",
    "TrackState",
    "TrackedSource",
    "UserOverride",
    "apply_manual_override",
    "crossmatch_catalog",
    "format_public_id",
    "link_epochs",
    "merge_tracks",
    "run_target_pipeline",
]
