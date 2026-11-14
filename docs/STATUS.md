## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | IRSA ObsCore TAP Optimization, Single Provider & Selection Box Fixes
Done:
- Switched IRSA query in `irsa_search.py` to direct indexed ADQL TAP query on `ivoa.obscore` with server-side time/band gating, a 12s timeout shared httpx connection pool, and healpix9 TTL cache.
- Restricted archive source exclusively to `spherex_irsa` (ObsCore), removing `wise_irsa`, `twomass_irsa`, and `skyview` routing and synthetic stubs across backend schemas, frontend UI, and openapi contracts.
- Fixed selection box rendering: destructured `setActiveBox` in `useViewerData.ts`, prevented URL query sync wipes on box creation, passed `allFitsIds` to analysis, projected native pixel coordinates, and styled active box dashed border.
- Enforced consistent infrared bandpass across epochs: `group_records_into_epochs` locks `selected_product_id` to dominant band, `handleLoadAllInterval` loads sequentially to prevent queue clogging, and blinking video frames filter to the reference bandpass.
Files touched: backend/app/schemas/archive.py, backend/app/services/irsa_search.py, backend/app/workers/tasks_fast.py, frontend/src/api/openapi.json, frontend/src/api/schema.d.ts, frontend/src/viewer/overlays/WcsSvgOverlay.tsx, frontend/src/viewer/panels/ArchiveSearchModal.tsx, frontend/src/viewer/state/useViewerData.ts, frontend/src/viewer/state/viewStore.ts, frontend/tests/unit/archiveModal.test.ts, frontend/tests/unit/boxSelectAndRegimes.test.ts, tests/unit/test_api_archive_search.py, docs/STATUS.md
Verified by: vitest (8 test files, 40 passed), pytest tests/unit/test_api_archive_search.py (14 passed), test_identity_regimes_and_box.py (5 passed)   Lines before -> after: irsa_search.py 740 -> 623 (-117 lines)
## Next task: Live backend E2E Playwright verification across the 5 mandatory benchmark targets (WISE 0855, Barnard's Star, eps Ind B, RW Aurigae, R Draconis)
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
