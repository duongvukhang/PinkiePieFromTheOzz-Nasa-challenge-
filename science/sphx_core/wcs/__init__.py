"""WCS metrics, HEALPix indexing, and spherical footprint polygon operations (ICRS deg)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import pixel_to_skycoord, proj_plane_pixel_scales
from astropy_healpix import HEALPix
from numpy.typing import NDArray

_HPX9 = HEALPix(nside=2**9, order="nested", frame="icrs")
ARCSEC_PER_RAD = 206264.80624709636


@dataclass(frozen=True)
class WCSMetrics:
    """Celestial WCS summary (ICRS deg, pixel scale arcsec/px, PA deg E of N, arcmin^2)."""

    ra_center_deg: float
    dec_center_deg: float
    pixscale_x_arcsec: float
    pixscale_y_arcsec: float
    pixscale_arcsec: float
    rotation_deg: float
    parity: int
    naxis1: int
    naxis2: int
    has_sip: bool
    ctype: tuple[str, str]
    footprint: list[tuple[float, float]]
    area_arcmin2: float
    healpix9: int
    wcs_header: str


def wrap_angle_180(angle_deg: float) -> float:
    """Wrap angle in degrees onto `(-180.0, 180.0]`."""
    wrapped = (angle_deg + 180.0) % 360.0 - 180.0
    return 180.0 if wrapped == -180.0 else wrapped


def healpix9_nested(ra_deg: float, dec_deg: float) -> int:
    """Return nested HEALPix order-9 pixel index for ICRS `(ra_deg, dec_deg)`."""
    return int(_HPX9.lonlat_to_healpix(ra_deg * u.deg, dec_deg * u.deg))


def circle_footprint(
    ra_deg: float, dec_deg: float, radius_deg: float, n_vertices: int = 16
) -> list[tuple[float, float]]:
    """Build an ICRS `(ra_deg, dec_deg)` polygon approximating a spherical circle."""
    angles = np.linspace(0.0, 360.0, n_vertices, endpoint=False) * u.deg
    ring = SkyCoord(ra_deg * u.deg, dec_deg * u.deg, frame="icrs").directional_offset_by(
        angles, radius_deg * u.deg
    )
    return [(float(r), float(d)) for r, d in zip(ring.ra.deg, ring.dec.deg, strict=True)]


def box_footprint(ra_deg: float, dec_deg: float, fov_arcmin: float) -> list[tuple[float, float]]:
    """Build a 4-vertex tangent-plane square footprint `(ra_deg, dec_deg)` of width `fov_arcmin`."""
    half_deg = 0.5 * fov_arcmin / 60.0
    w = WCS(naxis=2)
    w.wcs.crpix = [1.5, 1.5]
    w.wcs.cdelt = [-half_deg, half_deg]
    w.wcs.crval = [ra_deg, dec_deg]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    sc = pixel_to_skycoord([0.5, 0.5, 2.5, 2.5], [0.5, 2.5, 2.5, 0.5], w, origin=1).icrs
    return [(float(r), float(d)) for r, d in zip(sc.ra.deg, sc.dec.deg, strict=True)]


def _poly_to_unit_vecs(poly: Sequence[tuple[float, float]]) -> NDArray[np.float64]:
    arr = np.asarray(poly, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] < 3 or arr.shape[1] != 2:
        return np.empty((0, 3), dtype=np.float64)
    ra = np.deg2rad(arr[:, 0])
    dec = np.deg2rad(arr[:, 1])
    cos_d = np.cos(dec)
    return np.column_stack((cos_d * np.cos(ra), cos_d * np.sin(ra), np.sin(dec)))


def _unit_vecs_area_sr(vecs: NDArray[np.float64]) -> float:
    """Exact spherical polygon solid angle in steradians via Oosterom & Strackee."""
    if len(vecs) < 3:
        return 0.0
    center = vecs.mean(axis=0)
    norm = float(np.linalg.norm(center))
    if norm < 1e-12:
        return 0.0
    c = center / norm
    nxt = np.roll(vecs, -1, axis=0)
    num = np.cross(vecs, nxt) @ c
    den = 1.0 + (vecs @ c) + np.sum(vecs * nxt, axis=1) + (nxt @ c)
    return float(abs(np.sum(2.0 * np.arctan2(num, den))))


def spherical_polygon_area_sr(poly: Sequence[tuple[float, float]]) -> float:
    """Compute spherical area (sr) of an ICRS `(ra_deg, dec_deg)` polygon."""
    return _unit_vecs_area_sr(_poly_to_unit_vecs(poly))


def _tangent_basis(center: NDArray[np.float64]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    up = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    east = np.cross(up, center)
    norm_e = float(np.linalg.norm(east))
    if norm_e < 1e-10:
        east = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    else:
        east /= norm_e
    north = np.cross(center, east)
    north /= float(np.linalg.norm(north))
    return east, north


def _ensure_ccw(pts: NDArray[np.float64]) -> NDArray[np.float64]:
    if len(pts) < 3:
        return pts
    x, y = pts[:, 0], pts[:, 1]
    area2 = float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    return pts if area2 >= 0.0 else pts[::-1]


def _sutherland_hodgman(
    subject: list[tuple[float, float]], clip: NDArray[np.float64]
) -> list[tuple[float, float]]:
    """Clip 2D `subject` polygon against convex CCW `clip` polygon."""
    out = list(subject)
    n_clip = len(clip)
    for i in range(n_clip):
        if len(out) < 3:
            return []
        px, py = float(clip[i, 0]), float(clip[i, 1])
        qx, qy = float(clip[(i + 1) % n_clip, 0]), float(clip[(i + 1) % n_clip, 1])
        ex, ey = qx - px, qy - py
        if ex * ex + ey * ey < 1e-28:
            continue

        def _inside(
            pt: tuple[float, float], px: float = px, py: float = py, ex: float = ex, ey: float = ey
        ) -> bool:
            return ex * (pt[1] - py) - ey * (pt[0] - px) >= -1e-13

        def _intersect(
            a: tuple[float, float],
            b: tuple[float, float],
            px: float = px,
            py: float = py,
            ex: float = ex,
            ey: float = ey,
        ) -> tuple[float, float]:
            dx, dy = b[0] - a[0], b[1] - a[1]
            denom = ex * dy - ey * dx
            if abs(denom) < 1e-20:
                return b
            t = (ex * (py - a[1]) - ey * (px - a[0])) / denom
            t = min(1.0, max(0.0, t))
            return (a[0] + t * dx, a[1] + t * dy)

        inp = out
        out = []
        prev = inp[-1]
        prev_in = _inside(prev)
        for cur in inp:
            cur_in = _inside(cur)
            if cur_in:
                if not prev_in:
                    out.append(_intersect(prev, cur))
                out.append(cur)
            elif prev_in:
                out.append(_intersect(prev, cur))
            prev, prev_in = cur, cur_in
    return out


def spherical_polygon_overlap(
    ref_poly: Sequence[tuple[float, float]],
    other_polys: Sequence[tuple[float, float]] | Sequence[Sequence[tuple[float, float]]],
) -> tuple[float, float]:
    """Compute `(intersection_area_sr, overlap_fraction_of_ref)` for convex sky polygons."""
    if not other_polys:
        return 0.0, 0.0
    first_item = other_polys[0]
    if isinstance(first_item[0], (int, float)):
        poly_list: list[Sequence[tuple[float, float]]] = [other_polys]  # type: ignore[list-item]
    else:
        poly_list = list(other_polys)  # type: ignore[arg-type]

    ref_vecs = _poly_to_unit_vecs(ref_poly)
    ref_area = _unit_vecs_area_sr(ref_vecs)
    if ref_area <= 0.0:
        return 0.0, 0.0

    center = ref_vecs.mean(axis=0)
    center /= float(np.linalg.norm(center))
    east, north = _tangent_basis(center)

    def _project(vecs: NDArray[np.float64]) -> NDArray[np.float64] | None:
        dots = vecs @ center
        if np.any(dots <= 1e-6):
            return None
        pts = np.column_stack(((vecs @ east) / dots, (vecs @ north) / dots))
        return _ensure_ccw(pts)

    ref_2d = _project(ref_vecs)
    if ref_2d is None:
        return 0.0, 0.0
    running: list[tuple[float, float]] = [(float(p[0]), float(p[1])) for p in ref_2d]

    for poly in poly_list:
        vecs = _poly_to_unit_vecs(poly)
        if len(vecs) < 3:
            return 0.0, 0.0
        proj = _project(vecs)
        if proj is None:
            return 0.0, 0.0
        running = _sutherland_hodgman(running, proj)
        if len(running) < 3:
            return 0.0, 0.0

    pts_arr = np.asarray(running, dtype=np.float64)
    vecs_out = center[None, :] + pts_arr[:, 0:1] * east[None, :] + pts_arr[:, 1:2] * north[None, :]
    vecs_out /= np.linalg.norm(vecs_out, axis=1, keepdims=True)
    inter_area = _unit_vecs_area_sr(vecs_out)
    frac = min(1.0, max(0.0, inter_area / ref_area))
    return inter_area, frac


def extract_wcs_metrics(
    header: fits.Header, shape: tuple[int, int] | None = None
) -> WCSMetrics | None:
    """Parse celestial WCS into pixel scale (arcsec/px), rotation (deg E of N), and footprint."""
    try:
        wcs = WCS(header)
    except Exception:
        return None
    if not wcs.has_celestial:
        return None
    cw = wcs.celestial

    if shape is not None and len(shape) >= 2:
        ny, nx = int(shape[-2]), int(shape[-1])
    else:
        nx = int(header.get("NAXIS1", 0) or 0)
        ny = int(header.get("NAXIS2", 0) or 0)
    if nx <= 0 or ny <= 0:
        return None

    scales = np.asarray(proj_plane_pixel_scales(cw), dtype=np.float64) * 3600.0
    sx, sy = float(scales[0]), float(scales[1])
    pixscale = math.sqrt(max(sx, 0.0) * max(sy, 0.0))

    mat = np.asarray(cw.pixel_scale_matrix, dtype=np.float64)
    # PA of detector +y axis (East of North); works across CD, PC+CDELT, and parity flips.
    rotation_deg = wrap_angle_180(math.degrees(math.atan2(float(mat[0, 1]), float(mat[1, 1]))))
    det = float(np.linalg.det(mat))
    parity = -1 if det < 0.0 else 1

    center_sc = cw.pixel_to_world((nx - 1) / 2.0, (ny - 1) / 2.0).icrs
    ra_c = float(center_sc.ra.deg) % 360.0
    dec_c = float(center_sc.dec.deg)

    corners = pixel_to_skycoord(
        [-0.5, -0.5, nx - 0.5, nx - 0.5], [-0.5, ny - 0.5, ny - 0.5, -0.5], cw, origin=0
    ).icrs
    footprint = [
        (float(r) % 360.0, float(d)) for r, d in zip(corners.ra.deg, corners.dec.deg, strict=True)
    ]
    if cw.sip is not None:
        mx, my = (nx - 1.0) / 2.0, (ny - 1.0) / 2.0
        px8 = [0.0, 0.0, 0.0, mx, nx - 1.0, nx - 1.0, nx - 1.0, mx]
        py8 = [0.0, my, ny - 1.0, ny - 1.0, ny - 1.0, my, 0.0, 0.0]
        sc8 = pixel_to_skycoord(px8, py8, cw, origin=0).icrs
        poly_for_area = [
            (float(r) % 360.0, float(d)) for r, d in zip(sc8.ra.deg, sc8.dec.deg, strict=True)
        ]
    else:
        poly_for_area = footprint
    area_arcmin2 = spherical_polygon_area_sr(poly_for_area) * (180.0 / math.pi * 60.0) ** 2

    return WCSMetrics(
        ra_center_deg=ra_c,
        dec_center_deg=dec_c,
        pixscale_x_arcsec=sx,
        pixscale_y_arcsec=sy,
        pixscale_arcsec=pixscale,
        rotation_deg=rotation_deg,
        parity=parity,
        naxis1=nx,
        naxis2=ny,
        has_sip=cw.sip is not None,
        ctype=(str(cw.wcs.ctype[0]), str(cw.wcs.ctype[1])),
        footprint=footprint,
        area_arcmin2=area_arcmin2,
        healpix9=healpix9_nested(ra_c, dec_c),
        wcs_header=cw.to_header(relax=True).tostring(sep="\n"),
    )


def sky_to_tangent_arcsec(
    ra_deg: NDArray[np.float64] | float,
    dec_deg: NDArray[np.float64] | float,
    ra_ref_deg: float,
    dec_ref_deg: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Project ICRS `(ra_deg, dec_deg)` onto gnomonic tangent plane `(xi, eta)` [arcsec] (§A.2)."""
    ra = np.deg2rad(np.asarray(ra_deg, dtype=np.float64))
    dec = np.deg2rad(np.asarray(dec_deg, dtype=np.float64))
    ra0 = math.radians(ra_ref_deg)
    dec0 = math.radians(dec_ref_deg)
    dra = ra - ra0
    sin_d, cos_d = np.sin(dec), np.cos(dec)
    sin_d0, cos_d0 = math.sin(dec0), math.cos(dec0)
    cos_dra, sin_dra = np.cos(dra), np.sin(dra)
    denom = np.maximum(sin_d * sin_d0 + cos_d * cos_d0 * cos_dra, 1e-12)
    xi = ARCSEC_PER_RAD * (cos_d * sin_dra) / denom
    eta = ARCSEC_PER_RAD * (sin_d * cos_d0 - cos_d * sin_d0 * cos_dra) / denom
    return xi, eta


def tangent_arcsec_to_sky(
    xi_arcsec: NDArray[np.float64] | float,
    eta_arcsec: NDArray[np.float64] | float,
    ra_ref_deg: float,
    dec_ref_deg: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Deproject gnomonic tangent-plane `(xi, eta)` [arcsec] back to ICRS `(ra_deg, dec_deg)`."""
    xi = np.asarray(xi_arcsec, dtype=np.float64) / ARCSEC_PER_RAD
    eta = np.asarray(eta_arcsec, dtype=np.float64) / ARCSEC_PER_RAD
    ra0 = math.radians(ra_ref_deg)
    dec0 = math.radians(dec_ref_deg)
    rho = np.hypot(xi, eta)
    c = np.arctan(rho)
    sin_c, cos_c = np.sin(c), np.cos(c)
    sin_d0, cos_d0 = math.sin(dec0), math.cos(dec0)
    safe_rho = np.where(rho > 1e-15, rho, 1.0)
    dec = np.where(
        rho > 1e-15,
        np.arcsin(np.clip(cos_c * sin_d0 + (eta * sin_c * cos_d0) / safe_rho, -1.0, 1.0)),
        dec0,
    )
    ra = np.where(
        rho > 1e-15,
        ra0 + np.arctan2(xi * sin_c, safe_rho * cos_d0 * cos_c - eta * sin_d0 * sin_c),
        ra0,
    )
    return np.rad2deg(ra) % 360.0, np.rad2deg(dec)


def local_wcs_jacobian(
    wcs: WCS, x_px: float, y_px: float, *, eps_px: float = 1e-3
) -> NDArray[np.float64]:
    """Compute local tangent Jacobian `d(xi, eta)/d(x, y)` [arcsec/px] at 0-based `(x, y)`."""
    ra_v, dec_v = wcs.pixel_to_world_values(
        [x_px, x_px + eps_px, x_px], [y_px, y_px, y_px + eps_px]
    )
    xi_d, eta_d = sky_to_tangent_arcsec(ra_v[1:], dec_v[1:], float(ra_v[0]), float(dec_v[0]))
    return np.array(
        [
            [float(xi_d[0]) / eps_px, float(xi_d[1]) / eps_px],
            [float(eta_d[0]) / eps_px, float(eta_d[1]) / eps_px],
        ],
        dtype=np.float64,
    )


__all__ = [
    "ARCSEC_PER_RAD",
    "WCSMetrics",
    "box_footprint",
    "circle_footprint",
    "extract_wcs_metrics",
    "healpix9_nested",
    "local_wcs_jacobian",
    "sky_to_tangent_arcsec",
    "spherical_polygon_area_sr",
    "spherical_polygon_overlap",
    "tangent_arcsec_to_sky",
    "wrap_angle_180",
]
