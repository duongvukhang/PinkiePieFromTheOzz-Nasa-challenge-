## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | Live Backend E2E Playwright Verification Across 5 Mandatory Benchmark Targets
Done:
- Configured Vite preview proxy for `/api` and `/health` to live FastAPI backend on port 8000, and enabled dual webServer in Playwright configuration.
- Resolved 17 TypeScript compile errors across `WcsSvgOverlay.tsx`, `ArchiveSearchModal.tsx`, `InspectorTabs.tsx`, `ViewerStage.tsx`, `useViewerData.ts`, and `boxSelectAndRegimes.test.ts`.
- Fixed array_id numeric vs string type mismatch in `useViewerData.ts` infrared bandpass filtering that was discarding valid multi-epoch cutout frames.
- Defaulted `isTimelineOpen: true` in `viewStore.ts` so the multi-epoch timeline dock and blink playback controls render by default.
- Implemented and verified comprehensive live backend E2E Playwright test suite `benchmark_targets_live.spec.ts` covering `/health`, `/api/v1/examples`, `/api/v1/resolve`, coordinate sexagesimal rendering, live cutout loading, canvas WebP display, and blink/compare playback across all 5 benchmark targets (`WISE 0855`, `Barnard's Star`, `eps Ind B`, `RW Aurigae`, `R Draconis`).
Files touched: frontend/playwright.config.ts, frontend/vite.config.ts, frontend/src/viewer/overlays/WcsSvgOverlay.tsx, frontend/src/viewer/panels/ArchiveSearchModal.tsx, frontend/src/viewer/panels/InspectorTabs.tsx, frontend/src/viewer/panels/ViewerStage.tsx, frontend/src/viewer/state/useViewerData.ts, frontend/src/viewer/state/viewStore.ts, frontend/tests/unit/boxSelectAndRegimes.test.ts, frontend/tests/e2e/benchmark_targets_live.spec.ts, docs/STATUS.md
Verified by: playwright test tests/e2e/benchmark_targets_live.spec.ts (7/7 passed, 14.5s), npm test (8 test files, 40 passed), pytest test_api_fields_cutouts.py & test_motion_and_mandatory_targets.py (12 passed)   Lines before -> after: 564 -> 565 (+1 line in useViewerData.ts)
## Next task: Prepare for science-core review and audit (Phase 1 completion checklist)
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
