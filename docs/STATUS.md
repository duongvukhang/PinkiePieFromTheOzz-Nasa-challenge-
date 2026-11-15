## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | Remove Initial Loading Cutoff & Display "No Data Available" on Observatory Entry
Done:
- Removed route-level lazy loading flash (`<Suspense fallback="Loading observatory…">`) on `/v` route by importing `ViewerPage` directly in `routes.tsx`.
- Cleared pre-set target and coordinates in `viewStore.ts` (`DEFAULT_STATE` now has `target: ""`, `ra: undefined`, `dec: undefined`), and eliminated automatic fallback to `"WISE 0855"` in `syncFromSearch`.
- Set initial toolbar field label to `"No target selected"` when entering the observatory without parameters.
- Replaced blank viewport with `"no data available"` status overlay in `ViewerStage.tsx` when no FITS frame is loaded and no background loading/error is active.
- Added unit test in `wcsAndUrl.test.ts` verifying empty observatory initialization without pre-set loading or target parameters.
Files touched: frontend/src/app/routes.tsx, frontend/src/viewer/state/viewStore.ts, frontend/src/viewer/panels/ViewerToolbar.tsx, frontend/src/viewer/panels/ViewerStage.tsx, frontend/src/design/viewer.css, frontend/tests/unit/wcsAndUrl.test.ts, docs/STATUS.md
Verified by: pytest test_api_archive_search.py test_providers.py test_motion_and_mandatory_targets.py (28 passed), npm test (8 test files, 41 passed), npm run build (passed)   Lines before -> after: 58 -> 51 in routes.tsx (-7 lines)
## Next task: Prepare for science-core review and audit (Phase 1 completion checklist)
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
