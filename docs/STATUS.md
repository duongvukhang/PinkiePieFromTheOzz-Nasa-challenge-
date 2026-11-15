## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | Fix FITS Loading Performance, IRSA SIA Radius (>5') & Box Selection Direct Tracking
Done:
- Clamped IRSA SIA discovery query radius to <= 4.8' (0.080 deg) in `irsa_search.py` and `spherex_irsa.py`, avoiding Caltech unindexed partition joins while covering full SPHEREx frame footprints.
- Added server-side caching and pre-rendering for WebP display and float32 science binary files in `uploads.py` and `irsa_search.py`.
- Limited source extraction on unbinned large frames (>256x256) to 120 sources in `tasks_heavy.py` to keep PSF photometry fits under 4s.
- Converted archive multi-epoch product loading to bounded concurrency (batches of 3) in `ArchiveSearchModal.tsx`.
- Extended analysis polling timeout in `useViewerData.ts` and added fallback integration for Gaia, CatWISE, and candidate suggestions.
- Implemented direct peak flux centroid and box center object tracking in `InspectorTabs.tsx` along with Gaia star fallbacks, eliminating the "No detections inside selected box area" dead-end.
Files touched: backend/app/api/v1/uploads.py, backend/app/providers/spherex_irsa.py, backend/app/services/irsa_search.py, backend/app/workers/tasks_heavy.py, frontend/src/pages/ViewerPage.tsx, frontend/src/viewer/panels/ArchiveSearchModal.tsx, frontend/src/viewer/panels/InspectorTabs.tsx, frontend/src/viewer/state/useViewerData.ts, frontend/tests/unit/boxSelectAndRegimes.test.ts, tests/unit/test_api_archive_search.py, docs/STATUS.md
Verified by: pytest test_api_archive_search.py test_providers.py (25 passed), pytest test_identity_regimes_and_box.py test_motion_and_mandatory_targets.py (10 passed), npm test (8 test files, 43 passed)   Lines before -> after: 288 insertions / 32 deletions -> 282 insertions / 53 deletions (-27 lines shrunk)
## Next task: Prepare for science-core review and audit (Phase 1 completion checklist)
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
