## Current phase: 1 (WCS, calibration, extraction, registration, differencing implemented)
## Last session: Gemini 3.8 Flash High | 2026-11-11 | Implement registration, detection, differencing
Done: Implemented `sphx_core.wcs` tangent/Jacobian helpers, `sphx_core.calib` (FITS loader, ZODI/LVF/2D background/noise, scatter flag, flux-conserving reprojection + `F_corr`, band co-add), `sphx_core.extraction` (saturated-first PSF photometry, matched-filter SNR, SEP/CR vetting, sky covariances, deterministic real-bogus), `sphx_core.registration` (Gaia DR3 filter/epoch+topocentric parallax, adaptive shift/similarity/affine/TAN-SIP deg-3 + MAD clip + pixel-phase LUT), `sphx_core.difference` (ZOGY subtraction, empirical FAR threshold, WLS monopole/dipole BIC), plus 5 unit tests.
Files touched: `pyproject.toml`, `science/sphx_core/{wcs,calib,extraction,registration,difference}/__init__.py`, `tests/unit/test_calib_registration_extraction_difference.py`, `docs/STATUS.md`
Verified by: `.venv/bin/pytest tests/unit/ tests/science/test_astrometric_qa_and_registration.py` (31 passed in 16.54s); `.venv/bin/pytest tests/science/` (2 passed, 11 failed at missing `identity`/`motion`/`classify`/`orbits`/`planetx`); `.venv/bin/mypy backend/app science/sphx_core` (50 files clean); `.venv/bin/ruff format --check` & `ruff check` (clean)   Lines before -> after: `wcs` 312 -> 384, `calib` 768 -> 752 (shrink), `extraction` 595 -> 572 (shrink), `registration` 587 (new), `difference` 823 (new)
## Next task: Implement remaining `sphx_core` modules (`identity`, `motion`, `classify`, `consensus`, `comparison`, `orbits`, `planetx`) — Gemini 3.8 Flash High
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Run `make setup` / `pip install -e ./backend` on a machine with setuptools/network so `sphx.providers` entry-point metadata includes all providers.
- Run `docker compose config` and full Compose stack on a machine with Docker Compose v2 installed.
## Escalations: None
