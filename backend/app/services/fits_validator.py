"""FITS upload validation and multi-epoch compatibility service."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import numpy as np
from astropy.time import Time

from app.db.flags import DetectionFlag
from app.schemas.fields import UploadCompatibilityReport
from app.services.storage import StorageBackend, compute_sha256
from sphx_core.io import (
    CompatibilitySummary,
    FitsInspection,
    evaluate_compatibility,
    inspect_fits,
)

if TYPE_CHECKING:
    from app.providers.base import FrameRef

DEFAULT_UPLOAD_MAX_BYTES = 200 * 1024 * 1024
AUTHENTICATED_UPLOAD_MAX_BYTES = 2 * 1024 * 1024 * 1024


def get_upload_max_bytes(*, authenticated: bool) -> int:
    """Return configured upload size cap in bytes for anonymous or authenticated callers."""
    env_key = "SPHX_UPLOAD_MAX_BYTES_AUTH" if authenticated else "SPHX_UPLOAD_MAX_BYTES_ANON"
    default = AUTHENTICATED_UPLOAD_MAX_BYTES if authenticated else DEFAULT_UPLOAD_MAX_BYTES
    raw = os.environ.get(env_key)
    return int(raw) if raw and raw.isdigit() and int(raw) > 0 else default


@dataclass(frozen=True)
class FitsUploadValidationResult:
    """Validated FITS upload bundle containing API report, science inspection, and DB metadata."""

    report: UploadCompatibilityReport
    inspection: FitsInspection
    compatibility: CompatibilitySummary
    flags: int
    sha256: str | None = None
    storage_path: str | None = None
    fits_file_metadata: dict[str, Any] | None = None


def _normalize_reference(
    reference: FitsInspection | FrameRef | Path | str | bytes | Mapping[str, Any],
) -> FitsInspection | Mapping[str, Any]:
    from app.providers.base import FrameRef

    if isinstance(reference, FitsInspection):
        return reference
    if isinstance(reference, FrameRef):
        return {
            "footprint": reference.footprint,
            "mjd_tdb_mid": reference.mjd_tdb,
            "band_label": reference.band_id,
            "wavelength_um": reference.wavelength_um,
        }
    if isinstance(reference, Mapping):
        return reference
    return inspect_fits(reference)


def validate_fits_upload(
    source: Path | str | bytes,
    *,
    reference: (
        FitsInspection
        | FrameRef
        | Path
        | str
        | bytes
        | Mapping[str, Any]
        | Sequence[FitsInspection | FrameRef | Path | str | bytes | Mapping[str, Any]]
        | None
    ) = None,
    upload_id: UUID | None = None,
    fits_file_id: UUID | None = None,
    max_bytes: int = DEFAULT_UPLOAD_MAX_BYTES,
    user_epoch: Time | str | float | int | None = None,
    storage: StorageBackend | None = None,
    storage_key: str | None = None,
) -> FitsUploadValidationResult:
    """Validate an uploaded FITS file, compute compatibility, and optionally persist to storage."""
    uid = upload_id or uuid4()
    fid = fits_file_id or uuid4()
    inspection = inspect_fits(source, max_bytes=max_bytes, user_epoch=user_epoch)

    norm_ref: (
        FitsInspection | Mapping[str, Any] | Sequence[FitsInspection | Mapping[str, Any]] | None
    ) = None
    if reference is not None:
        if isinstance(reference, Sequence) and not isinstance(reference, (str, bytes)):
            norm_ref = [_normalize_reference(r) for r in reference]
        else:
            norm_ref = _normalize_reference(reference)

    compat = evaluate_compatibility(inspection, reference=norm_ref)

    flags = 0
    if inspection.epoch is not None and inspection.epoch.user_supplied:
        flags |= int(DetectionFlag.USER_EPOCH)
    if compat.reprojection_needed:
        flags |= int(DetectionFlag.REPROJECTED)

    report = UploadCompatibilityReport(
        upload_id=uid,
        fits_file_id=fid,
        valid=compat.valid,
        compatibility_status=compat.status,
        overlap_pct=compat.overlap_pct,
        pixscale_arcsec=inspection.wcs.pixscale_arcsec if inspection.wcs else None,
        rotation_deg=inspection.wcs.rotation_deg if inspection.wcs else None,
        ra_center_deg=inspection.wcs.ra_center_deg if inspection.wcs else None,
        dec_center_deg=inspection.wcs.dec_center_deg if inspection.wcs else None,
        band_label=inspection.band_label,
        mjd_tdb_mid=inspection.epoch.mjd_tdb_mid if inspection.epoch else None,
        reprojection_needed=compat.reprojection_needed,
        warnings=compat.warnings,
        errors=compat.errors,
        accuracy_notes=compat.accuracy_notes,
    )

    sha256: str | None = None
    stored_path: str | None = None
    if inspection.size_bytes <= max_bytes:
        src_path = Path(source) if isinstance(source, (Path, str)) else None
        payload = source if isinstance(source, bytes) else None
        if payload is not None:
            sha256 = compute_sha256(payload)
            default_name = f"{fid}.fits"
        elif src_path is not None and src_path.is_file():
            with src_path.open("rb") as handle:
                sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
            default_name = src_path.name
        else:
            default_name = ""
        if storage is not None and compat.valid and default_name:
            key = storage_key or f"uploads/anonymous/{uid}/{default_name}"
            raw = payload if payload is not None else src_path.read_bytes()  # type: ignore[union-attr]
            stored = storage.put_bytes(key, raw, content_type="application/fits")
            stored_path, sha256 = stored.key, stored.sha256

    wcs = inspection.wcs
    epoch = inspection.epoch
    metadata = {
        "origin": "upload",
        "size_bytes": inspection.size_bytes,
        "band_label": inspection.band_label,
        "array_id": inspection.array_id,
        "wavelength_min_um": inspection.wavelength_um[0] if inspection.wavelength_um else None,
        "wavelength_max_um": inspection.wavelength_um[1] if inspection.wavelength_um else None,
        "wavelength_um": list(inspection.wavelength_um) if inspection.wavelength_um else None,
        "date_obs_utc": epoch.utc_iso if epoch else None,
        "mjd_tdb_mid": epoch.mjd_tdb_mid if epoch else None,
        "exptime_s": epoch.exptime_s if epoch else None,
        "ra_center": wcs.ra_center_deg if wcs else None,
        "dec_center": wcs.dec_center_deg if wcs else None,
        "naxis1": wcs.naxis1 if wcs else None,
        "naxis2": wcs.naxis2 if wcs else None,
        "pixscale_arcsec": wcs.pixscale_arcsec if wcs else None,
        "rotation_deg": wcs.rotation_deg if wcs else None,
        "parity": wcs.parity if wcs else None,
        "wcs_header": wcs.wcs_header if wcs else "",
        "footprint": wcs.footprint if wcs else None,
        "healpix9": wcs.healpix9 if wcs else None,
        "header_summary": inspection.header_summary,
        "validation": report.model_dump(mode="json"),
    }

    try:
        from app.services.fields import extract_2d_image

        raw_b = payload if payload is not None else (src_path.read_bytes() if src_path is not None else None)
        if raw_b is not None:
            arr = extract_2d_image(raw_b)
            fin = arr[np.isfinite(arr)]
            if fin.size > 0:
                p_lo, p_hi = (float(x) for x in np.percentile(fin, [1.0, 99.5]))
                metadata["stretch"] = {
                    "mode": "asinh",
                    "percentile": [1.0, 99.5],
                    "vmin": p_lo,
                    "vmax": p_hi,
                    "shape": list(arr.shape),
                }
    except Exception:
        pass

    return FitsUploadValidationResult(
        report=report,
        inspection=inspection,
        compatibility=compat,
        flags=flags,
        sha256=sha256,
        storage_path=stored_path,
        fits_file_metadata=metadata,
    )


def validate_fits_set(
    sources: Sequence[Path | str | bytes],
    *,
    user_epochs: Sequence[Time | str | float | int | None] | None = None,
    max_bytes: int = DEFAULT_UPLOAD_MAX_BYTES,
) -> tuple[list[FitsUploadValidationResult], CompatibilitySummary]:
    """Validate a set of FITS uploads against each other and compute joint compatibility."""
    if not sources:
        raise ValueError("At least one FITS source is required")
    epochs = list(user_epochs) if user_epochs is not None else [None] * len(sources)
    if len(epochs) != len(sources):
        raise ValueError("user_epochs length must match sources length")

    inspections = [
        inspect_fits(src, max_bytes=max_bytes, user_epoch=ep)
        for src, ep in zip(sources, epochs, strict=True)
    ]
    ref_idx = next((i for i, ins in enumerate(inspections) if ins.wcs is not None), 0)
    ref_ins = inspections[ref_idx]

    results = [
        validate_fits_upload(
            src,
            reference=None if i == ref_idx else ref_ins,
            max_bytes=max_bytes,
            user_epoch=ep,
        )
        for i, (src, ep) in enumerate(zip(sources, epochs, strict=True))
    ]
    others = [ins for i, ins in enumerate(inspections) if i != ref_idx]
    joint = evaluate_compatibility(ref_ins, reference=others or None)
    return results, joint


__all__ = [
    "AUTHENTICATED_UPLOAD_MAX_BYTES",
    "DEFAULT_UPLOAD_MAX_BYTES",
    "FitsUploadValidationResult",
    "get_upload_max_bytes",
    "validate_fits_set",
    "validate_fits_upload",
]
