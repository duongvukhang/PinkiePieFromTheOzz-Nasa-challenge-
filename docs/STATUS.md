## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-14 | T1.17: Fix uploads display & science endpoints and handle missing storage artifacts
Done: (1) Replaced nonexistent `sphx_core.fits` import with `from app.services.fields import extract_2d_image` in uploads display & science endpoints; (2) Serialized science array with little-endian `np.asarray(arr, dtype="<f4").tobytes()` in `GET /api/v1/uploads/{id}/science.bin` (matching `tasks_fast.py`); (3) Consolidated `_resolve_fits_raw` to catch `(FileNotFoundError, StorageError)` when an in-memory/DB upload record points to a missing storage artifact and raise `APIProblemError(404, "FITS File Not Found", ...)`; (4) Added `test_upload_display_and_science_bin` covering display.webp, science.bin byte order and shape, missing storage 404, and unknown ID 404.
Files touched: backend/app/api/v1/uploads.py, tests/unit/test_api_uploads.py, docs/STATUS.md
Verified by: ./.venv/bin/pytest tests/unit/ (83/83 passed in ~1m14s), npm --prefix frontend test -- --run (22/22 passed)   Lines before -> after: 311 -> 311 (backend/app/api/v1/uploads.py)
## Next task: T6 / Integration — Project workspace (notes, findings, FITS files) + live backend E2E verification
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
