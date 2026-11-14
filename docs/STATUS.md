## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-14 | T1.14: Clean Canvas & Remove Redundant In-Canvas Floating Widgets
Done: (1) Removed redundant in-canvas floating widgets (`BottomDock`, `TimeController`, `SelectedObjectBar`, `SourceInfoCard`) that obstructed the celestial FITS field; (2) Preserved all controls in their canonical layout locations per FRONTEND.md §4.1 (playback in ViewerToolbar & FrameStrip, layer toggles in LayerSidebar, object inspection in InspectorTabs); (3) Inlined `SelectedSourceData` in `WcsSvgOverlay.tsx` and removed ~530 lines of dead CSS; (4) Verified 0 TypeScript errors, 22/22 frontend tests passing, and 81/81 backend unit tests passing.
Files touched: frontend/src/viewer/panels/ViewerStage.tsx, frontend/src/viewer/overlays/WcsSvgOverlay.tsx, frontend/src/design/viewer.css, docs/STATUS.md (deleted: BottomDock.tsx, TimeController.tsx, SelectedObjectBar.tsx, SourceInfoCard.tsx)
Verified by: npm run typecheck (0 errors), npm test -- --run (22/22 passed), pytest tests/unit/ (81/81 passed)   Lines before -> after: 587 -> 464 (ViewerStage.tsx), 2025 -> 1497 (viewer.css)
## Next task: T6 / Integration — Project workspace (notes, findings, FITS files) + live backend E2E verification
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
