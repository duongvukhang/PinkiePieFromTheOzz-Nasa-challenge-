## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-14 | Enable Live SPHEREx DR1 FITS Cutouts & Field Display
Done: (1) Diagnosed why field display fell back to synthetic demo: `spherex_qr_v1.yaml` queried outdated collection `spherex_qr` (returning 0 frames) instead of live `spherex_dr1` (374 frames), and non-overlapping detector arrays caused HTTP 422 aborts; (2) Updated `collection_id` to `spherex_dr1` and added DR1 aliases; (3) Added per-frame error resilience and epoch candidate sampling in `execute_build_cutouts`; (4) Verified full cutout pipeline successfully downloads live SPHEREx Level-2 FITS frames from IRSA, cuts them out, and generates real display/science frames.
Files touched: `backend/app/providers/release_profiles/spherex_qr_v1.yaml`, `backend/app/workers/tasks_fast.py`, `docs/STATUS.md`
Verified by: `npm run typecheck` (0 errors), `npm test -- --run` (22/22 passed), full cutout pipeline live execution (11 real frames loaded)   Lines before -> after: 501 -> 508 (tasks_fast.py)
## Next task: T6 / Integration — Project workspace (notes, findings, FITS files) + live backend E2E verification
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
