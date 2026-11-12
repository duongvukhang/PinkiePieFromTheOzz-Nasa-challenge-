"""Project export generators (CSV, JSON, FITS BINTABLE, PNG, animated GIF, IAU ADES)."""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import numpy as np
from astropy.io import fits
from astropy.time import Time
from PIL import Image, ImageDraw

from app.services.jobs import JobStore
from app.services.storage import StorageBackend

_FORMAT_META: dict[str, tuple[str, str]] = {
    "csv": ("csv", "text/csv; charset=utf-8"),
    "json": ("json", "application/json"),
    "fits": ("fits", "application/fits"),
    "png": ("png", "image/png"),
    "gif": ("gif", "image/gif"),
    "ades": ("psv", "text/plain; charset=utf-8"),
}


def has_consensus_conflict(bundle: dict[str, Any]) -> bool:
    """Return True if tracked object's consensus verdict is 'Conflict'."""
    cls = bundle.get("classification")
    consensus = getattr(cls, "consensus", None) if cls is not None else None
    return isinstance(consensus, dict) and str(consensus.get("verdict", "")).lower() == "conflict"


def _preferred_fit(bundle: dict[str, Any]) -> dict[str, Any]:
    fits_list = bundle.get("motion_fits") or []
    return next((f for f in fits_list if f.get("is_preferred")), fits_list[0] if fits_list else {})


def _bundle_rows(bundles: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for b in bundles:
        fit = _preferred_fit(b)
        fparams = fit.get("params") or {}
        cls = b.get("classification")
        verdict = (cls.consensus or {}).get("verdict") if cls and cls.consensus else None
        caveats = "; ".join(b.get("caveats") or [])
        common = {
            "object_id": str(b["id"]),
            "label": b["label"],
            "public_id": b.get("public_id") or "",
            "status": b.get("status") or "active",
            "identity_confidence": b.get("identity_confidence"),
            "pmra_masyr": fparams.get("pmra_masyr"),
            "pmdec_masyr": fparams.get("pmdec_masyr"),
            "pm_total_masyr": fit.get("pm_total_masyr"),
            "pm_err_masyr": fit.get("pm_total_err"),
            "top_class": cls.top_class if cls else "",
            "top_prob": cls.top_prob if cls else None,
            "consensus_verdict": verdict or "",
            "caveats": caveats,
        }
        meas = b.get("measurements") or []
        if not meas:
            rows.append(
                {
                    **common,
                    "epoch": "",
                    "mjd_tdb": b.get("ref_mjd_tdb"),
                    "ra_deg": b.get("ref_ra"),
                    "dec_deg": b.get("ref_dec"),
                    "sig_ra_mas": None,
                    "sig_dec_mas": None,
                    "band_group": "",
                    "flux_ujy": None,
                    "flux_err_ujy": None,
                }
            )
            continue
        for m in meas:
            rows.append(
                {
                    **common,
                    "epoch": m.epoch or "",
                    "mjd_tdb": m.mjd_tdb,
                    "ra_deg": m.ra,
                    "dec_deg": m.dec,
                    "sig_ra_mas": m.sig_ra_mas,
                    "sig_dec_mas": m.sig_dec_mas,
                    "band_group": m.band_group or "",
                    "flux_ujy": m.flux_ujy,
                    "flux_err_ujy": m.flux_err_ujy,
                }
            )
    return rows


def _to_csv(rows: Sequence[dict[str, Any]]) -> bytes:
    cols = [
        "object_id",
        "label",
        "public_id",
        "status",
        "identity_confidence",
        "epoch",
        "mjd_tdb",
        "ra_deg",
        "dec_deg",
        "sig_ra_mas",
        "sig_dec_mas",
        "band_group",
        "flux_ujy",
        "flux_err_ujy",
        "pmra_masyr",
        "pmdec_masyr",
        "pm_total_masyr",
        "pm_err_masyr",
        "top_class",
        "top_prob",
        "consensus_verdict",
        "caveats",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in cols})
    return buf.getvalue().encode("utf-8")


def _to_fits(proj: dict[str, Any], rows: Sequence[dict[str, Any]]) -> bytes:
    primary = fits.PrimaryHDU()
    primary.header["PROJECT"] = str(proj.get("name", ""))[:68]
    primary.header["PROJ_ID"] = str(proj["id"])
    primary.header["VERSION"] = int(proj.get("current_version", 1))
    primary.header["ORIGIN"] = "SPHEREx-Platform"

    def _fcol(key: str) -> np.ndarray:
        return np.array(
            [float(r[key]) if r.get(key) is not None else np.nan for r in rows], dtype=np.float64
        )

    def _scol(key: str, width: int) -> np.ndarray:
        return np.array([str(r.get(key) or "")[:width] for r in rows], dtype=f"S{width}")

    cols = [
        fits.Column(name="OBJECT_ID", format="36A", array=_scol("object_id", 36)),
        fits.Column(name="LABEL", format="8A", array=_scol("label", 8)),
        fits.Column(name="EPOCH", format="16A", array=_scol("epoch", 16)),
        fits.Column(name="MJD_TDB", format="D", unit="d", array=_fcol("mjd_tdb")),
        fits.Column(name="RA_DEG", format="D", unit="deg", array=_fcol("ra_deg")),
        fits.Column(name="DEC_DEG", format="D", unit="deg", array=_fcol("dec_deg")),
        fits.Column(name="SIG_RA_MAS", format="D", unit="mas", array=_fcol("sig_ra_mas")),
        fits.Column(name="SIG_DEC_MAS", format="D", unit="mas", array=_fcol("sig_dec_mas")),
        fits.Column(name="FLUX_UJY", format="D", unit="uJy", array=_fcol("flux_ujy")),
        fits.Column(name="FLUX_ERR_UJY", format="D", unit="uJy", array=_fcol("flux_err_ujy")),
        fits.Column(name="PM_TOTAL", format="D", unit="mas/yr", array=_fcol("pm_total_masyr")),
    ]
    tbl = fits.BinTableHDU.from_columns(cols, name="OBJECTS")
    out = io.BytesIO()
    fits.HDUList([primary, tbl]).writeto(out, overwrite=True)
    return out.getvalue()


def _to_ades(rows: Sequence[dict[str, Any]]) -> bytes:
    lines = [
        "# version=2022",
        "# obsContext",
        "! observatory",
        "! stn 500",
        "! note Preliminary unsubmitted SPHEREx platform export (not auto-submitted)",
        (
            "permID|provID|trkSub|mode|stn|obsTime|ra|dec|rmsRA|rmsDec|rmsCorr|"
            "astCat|mag|band|photCat|notes|remarks"
        ),
    ]
    for r in rows:
        ra, dec, mjd = r.get("ra_deg"), r.get("dec_deg"), r.get("mjd_tdb")
        if ra is None or dec is None or mjd is None:
            continue
        obs_t = Time(float(mjd), format="mjd", scale="tdb").utc.isot + "Z"
        rms_ra = f"{float(r['sig_ra_mas']) / 1000.0:.4f}" if r.get("sig_ra_mas") else ""
        rms_dec = f"{float(r['sig_dec_mas']) / 1000.0:.4f}" if r.get("sig_dec_mas") else ""
        flux = r.get("flux_ujy")
        mag = f"{-2.5 * math.log10(float(flux) * 1e-6 / 3631.0):.3f}" if flux and flux > 0 else ""
        trk = (str(r.get("label") or "OBJ")[:7]).replace("|", "_")
        lines.append(
            f"||{trk}|CCD|500|{obs_t}|{float(ra):.7f}|{float(dec):.7f}|"
            f"{rms_ra}|{rms_dec}||GaiaDR3|{mag}|w||preliminary|{r.get('consensus_verdict') or ''}"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _sky_frame_image(
    proj: dict[str, Any],
    bundles: Sequence[dict[str, Any]],
    *,
    epoch_mjd: float | None = None,
    size: int = 512,
) -> Image.Image:
    img = Image.new("RGB", (size, size), (11, 14, 23))
    draw = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    draw.line([(cx, 24), (cx, size - 24)], fill=(38, 46, 68), width=1)
    draw.line([(24, cy), (size - 24, cy)], fill=(38, 46, 68), width=1)

    pts = [
        (float(b["ref_ra"]), float(b["ref_dec"]))
        for b in bundles
        if b.get("ref_ra") is not None and b.get("ref_dec") is not None
    ]
    ra0 = (
        float(proj["center_ra"])
        if proj.get("center_ra") is not None
        else (pts[0][0] if pts else 0.0)
    )
    dec0 = (
        float(proj["center_dec"])
        if proj.get("center_dec") is not None
        else (pts[0][1] if pts else 0.0)
    )
    fov_deg = max(float(proj.get("fov_arcmin") or 10.0) / 60.0, 0.02)
    cos_d = max(math.cos(math.radians(dec0)), 1e-3)
    scale = (size * 0.8) / fov_deg

    for b in bundles:
        ra, dec = b.get("ref_ra"), b.get("ref_dec")
        if epoch_mjd is not None:
            for m in b.get("measurements") or []:
                if (
                    abs(float(m.mjd_tdb) - epoch_mjd) < 1e-3
                    and m.ra is not None
                    and m.dec is not None
                ):
                    ra, dec = m.ra, m.dec
                    break
        if ra is None or dec is None:
            continue
        dra = ((float(ra) - ra0 + 180.0) % 360.0 - 180.0) * cos_d
        px = int(round(cx - dra * scale))
        py = int(round(cy - (float(dec) - dec0) * scale))
        if 8 <= px < size - 8 and 8 <= py < size - 8:
            draw.ellipse([(px - 6, py - 6), (px + 6, py + 6)], outline=(86, 204, 242), width=2)
            draw.text((px + 9, py - 6), str(b["label"]), fill=(240, 244, 248))
    title = f"{proj.get('name', 'Project')} ({ra0:.4f}, {dec0:+.4f})"
    if epoch_mjd is not None:
        title += f" MJD {epoch_mjd:.2f}"
    draw.text((12, 10), title, fill=(190, 204, 224))
    return img


def _to_png(proj: dict[str, Any], bundles: Sequence[dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    _sky_frame_image(proj, bundles).save(buf, format="PNG")
    return buf.getvalue()


def _to_gif(
    proj: dict[str, Any],
    bundles: Sequence[dict[str, Any]],
    file_ids: Sequence[UUID],
    store: JobStore,
    storage: StorageBackend,
) -> bytes:
    frames: list[Image.Image] = []
    for fid in file_ids[:16]:
        rec = store.get_fits_file(fid)
        if not rec:
            continue
        try:
            raw = storage.get_bytes(str(rec["storage_path"]), verify_checksum=False)
            with fits.open(io.BytesIO(raw), memmap=False) as hdul:
                arr = next(
                    (
                        np.asarray(h.data, dtype=np.float32)
                        for h in hdul
                        if getattr(h, "data", None) is not None and h.data.ndim >= 2
                    ),
                    None,
                )
            if arr is not None:
                if arr.ndim > 2:
                    arr = arr[0]
                fin = arr[np.isfinite(arr)]
                vmin, vmax = (
                    (float(x) for x in np.percentile(fin, [1.0, 99.5])) if fin.size else (0.0, 1.0)
                )
                scaled = np.clip(
                    (np.nan_to_num(arr, nan=vmin) - vmin) / max(vmax - vmin, 1e-12), 0.0, 1.0
                )
                u8 = np.clip(
                    np.round(np.arcsinh(10.0 * scaled) / np.arcsinh(10.0) * 255.0), 0, 255
                ).astype(np.uint8)
                frames.append(Image.fromarray(u8, mode="L").convert("RGB").resize((256, 256)))
        except Exception:
            continue
    if not frames:
        mjds: Sequence[float | None] = sorted(
            {
                float(m.mjd_tdb)
                for b in bundles
                for m in (b.get("measurements") or [])
                if m.ra is not None and m.dec is not None
            }
        ) or [None]
        frames = [_sky_frame_image(proj, bundles, epoch_mjd=mjd, size=256) for mjd in mjds]
    if len(frames) == 1:
        frames.append(frames[0].copy())
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=500,
        loop=0,
    )
    return buf.getvalue()


def build_project_export(
    *,
    project_id: UUID,
    fmt: str,
    object_ids: Sequence[UUID] | None,
    store: JobStore,
    storage: StorageBackend,
) -> tuple[bytes, str, str]:
    """Build export payload `(raw_bytes, ext, content_type)` for a project."""
    proj = store.get_project(project_id)
    if proj is None:
        raise KeyError(f"Project not found: {project_id}")

    obj_summaries = store.list_project_objects(project_id, object_ids)
    bundles = [
        b
        for s in obj_summaries
        if (b := store.get_tracked_object_bundle(UUID(str(s["id"])))) is not None
    ]
    rows = _bundle_rows(bundles)
    ext, ctype = _FORMAT_META[fmt]

    if fmt == "csv":
        data = _to_csv(rows)
    elif fmt == "json":
        payload = {
            "project": {k: v for k, v in proj.items() if k != "share_token_hash"},
            "objects": [
                {
                    "id": str(b["id"]),
                    "label": b["label"],
                    "public_id": b.get("public_id"),
                    "status": b.get("status"),
                    "identity_confidence": b.get("identity_confidence"),
                    "measurements": [m.model_dump(mode="json") for m in b["measurements"]],
                    "motion_fits": b["motion_fits"],
                    "classification": (
                        b["classification"].model_dump(mode="json")
                        if b["classification"] is not None
                        else None
                    ),
                    "caveats": b.get("caveats") or [],
                }
                for b in bundles
            ],
            "notes": [n.model_dump(mode="json") for n in store.list_notes(project_id)],
            "findings": [f.model_dump(mode="json") for f in store.list_findings(project_id)],
            "saved_views": [v.model_dump(mode="json") for v in store.list_saved_views(project_id)],
            "exported_at": datetime.now(UTC).isoformat(),
        }
        data = json.dumps(payload, indent=2, default=str).encode("utf-8")
    elif fmt == "fits":
        data = _to_fits(proj, rows)
    elif fmt == "png":
        data = _to_png(proj, bundles)
    elif fmt == "gif":
        data = _to_gif(proj, bundles, store.list_project_file_ids(project_id), store, storage)
    elif fmt == "ades":
        data = _to_ades(rows)
    else:
        raise ValueError(f"Unsupported export format: {fmt!r}")

    return data, ext, ctype
