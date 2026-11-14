## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-14 | T1.18: Fix passive wheel listener, analysis job polling, and eager deduplicated job execution
Done: (1) Removed React onWheel synthetic listener on ViewerStage and attached native non-passive wheel listener with `{ passive: false }` to eliminate preventDefault passive warning; (2) Added polling loop on `api.getJob(acc.job_id)` in `analysisQ.queryFn` before fetching sources/suggestions to resolve 409 Conflict; (3) Added eager mode execution for orphaned/deduplicated queued jobs in `create_analysis`, `select_project_objects_task`, and `build_cutouts`, with error handling marking jobs failed instead of leaving them queued; (4) Added `broker_connection_retry_on_startup=False` and `retry=False` on `apply_async` in `dispatch_task`; (5) Added tests for eager execution of deduplicated analysis and job failure status marking.
Files touched: frontend/src/viewer/panels/ViewerStage.tsx, frontend/src/viewer/state/useViewerData.ts, backend/app/api/v1/objects.py, backend/app/api/v1/fields.py, backend/app/workers/celery_app.py, tests/unit/test_api_analyses.py, docs/STATUS.md
Verified by: ./.venv/bin/pytest tests/unit/ (85/85 passed in ~1m12s), npm --prefix frontend test -- --run (22/22 passed), npm --prefix frontend run build (tsc + vite passed)   Lines before -> after: 990 -> 947 (backend/app/api/v1/objects.py)
## Next task: T6 / Integration — Project workspace (notes, findings, FITS files) + live backend E2E verification
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
