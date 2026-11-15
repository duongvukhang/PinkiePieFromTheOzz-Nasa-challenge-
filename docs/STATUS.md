## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | Fix IRSA SIAv2 Timeouts, Synthetic Demo Loading & State Preservation
Done:
- Removed unindexed `TIME` parameter from IRSA SIAv2 query in `irsa_search.py` and `spherex_irsa.py`, relying on local MJD filtering to prevent >12s latency.
- Reduced `max_retries` from 3 to 1 in `spherex_qr_v1.yaml` to ensure cutout fallback occurs within <10s.
- Updated `load_archive_product` in `irsa_search.py` to synthesize SPHEREx demo products in-memory via `generate_synthetic_sequence` instead of failing HTTP downloads with 502.
- Preserved `fitsIds` and `fitsId` across `onApplyInterval` in `ViewerToolbar.tsx` and `syncFromSearch` in `viewStore.ts`.
- Removed redundant `fieldsQ` call in `useViewerData.ts` to prevent duplicate concurrent uncached SIA queries on coordinate updates.
Files touched: backend/app/services/irsa_search.py, backend/app/providers/spherex_irsa.py, backend/app/providers/release_profiles/spherex_qr_v1.yaml, frontend/src/viewer/panels/ViewerToolbar.tsx, frontend/src/viewer/state/viewStore.ts, frontend/src/viewer/state/useViewerData.ts, frontend/tests/unit/archiveModal.test.ts, tests/unit/test_api_archive_search.py, tests/unit/test_providers.py, docs/STATUS.md
Verified by: pytest test_api_archive_search.py test_providers.py test_motion_and_mandatory_targets.py (29 passed), npm test (8 test files, 42 passed), npm run build (passed)   Lines before -> after: 565 -> 546 in useViewerData.ts (-19 lines)
## Next task: Prepare for science-core review and audit (Phase 1 completion checklist)
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
