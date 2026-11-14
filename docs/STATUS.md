## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-14 | Fix Duplicate Frame Key Warning & UUID Differentiation
Done: (1) Fixed duplicate key warning in React `TimeController` and `FrameStrip`: differentiated `frame_uuid` generation in `tasks_fast.py` by incorporating `array_id`, `band_id`, and index into the UUID5 seed string; (2) Guaranteed unique React element keys in `TimeController.tsx`, `FrameStrip.tsx`, and `CompareView.tsx` with `${f.frame_id}-${i}`; (3) Verified zero warnings, clean typecheck, and passing unit tests.
Files touched: `backend/app/workers/tasks_fast.py`, `frontend/src/viewer/panels/TimeController.tsx`, `frontend/src/viewer/panels/FrameStrip.tsx`, `frontend/src/viewer/compare/CompareView.tsx`, `docs/STATUS.md`
Verified by: `npm run typecheck` (0 errors), `npm test -- --run` (22/22 passed)   Lines before -> after: 153 -> 153 (TimeController.tsx)
## Next task: T6 / Integration — Project workspace (notes, findings, FITS files) + live backend E2E verification
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
