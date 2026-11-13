## Current phase: 1 (FITS Display Pipeline & Viewport Reliability)
## Last session: Gemini 3.8 Flash (High) | 2026-11-14 | Fullscreen Unbounded Observatory Viewport & Drawer Panels
Done: (1) Transformed the observatory page (/v) into an edge-to-edge fullscreen viewport matching legacy observatory behavior: omitted site-header and site-footer, eliminated page scrollbars with 100vh/100dvh overflow:hidden, and removed bounded 560px/aspect-ratio card clamps; (2) Sky field is unbounded across the entire screen under the toolbar with square celestial geometry strictly preserved; (3) Converted LayerSidebar and InspectorTabs into floating slide-over glass drawers with header close buttons, global hotkeys (L, T, I, Esc), and auto-opening inspector when an object is clicked; (4) Docked FrameStrip timeline as a floating glass dock centered at the bottom of the field with toggle control in topbar; (5) Enhanced ViewerToolbar with SkyTrace masthead brand link to / and drawer toggle buttons (Layers, Timeline, Inspector).
Files touched: `frontend/src/app/layout/AppShell.tsx`, `frontend/src/design/global.css`, `frontend/src/design/viewer.css`, `frontend/src/pages/ViewerPage.tsx`, `frontend/src/viewer/panels/ViewerStage.tsx`, `frontend/src/viewer/panels/ViewerToolbar.tsx`, `docs/STATUS.md`
Verified by: `pytest tests/unit/` (81/81 passed), `vitest run` (19/19 passed), `tsc --noEmit` (0 errors), `eslint .` (0 errors), `prettier --check .` (passed)   Lines before -> after: 111 -> 198 (ViewerPage)
## Next task: T6 / Integration — Project workspace (notes, findings, FITS files) + live backend E2E verification
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
