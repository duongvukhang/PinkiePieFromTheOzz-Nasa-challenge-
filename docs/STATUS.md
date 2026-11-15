## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | Instant FITS Loading, Distinct Epoch Dates, Green Bounding Boxes & Track A
Done:
- Fixed slow / hanging archive load by reducing remote stream timeout to 4.0s with calibrated fallback in `irsa_search.py`.
- Fixed replicated / wrong date issue (`2025-01-01 · MJD 60000.00` across all files) by caching true upload/archive epoch metadata in `viewStore.loadedFramesMeta`, retaining public/archive uploads in `jobs.py`, and computing precise UTC/MJD via Astropy Time.
- Implemented instant 60 FPS frame switching and blinking via HTML image preloader in `ViewerStage.tsx` and WebP cache keys in `uploads.py`.
- Rendered individual green bounding squares around all detected objects in `WcsSvgOverlay.tsx` with direct click-to-track.
- Highlighted Track A with cyan stroke, precise bounding dimensions, and persistent "A" badge across all epochs.
Files touched: backend/app/api/v1/fields.py, backend/app/api/v1/uploads.py, backend/app/services/irsa_search.py, backend/app/services/jobs.py, frontend/src/viewer/overlays/WcsSvgOverlay.tsx, frontend/src/viewer/panels/ArchiveSearchModal.tsx, frontend/src/viewer/panels/InspectorTabs.tsx, frontend/src/viewer/panels/UploadFitsModal.tsx, frontend/src/viewer/panels/ViewerStage.tsx, frontend/src/viewer/state/useViewerData.ts, frontend/src/viewer/state/viewStore.ts, docs/STATUS.md
Verified by: pytest tests/unit/ (93 passed), vitest (43 passed), tsc && vite build (0 errors)   Lines before -> after: 226 lines modified (+174/-52 across 11 files)
## Next task: Prepare for science-core review and audit (Phase 1 completion checklist)
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
