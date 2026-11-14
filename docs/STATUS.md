## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-14 | T1.15: Responsive Black Field, Remove Grid Lines, Align Detection Boxes, Cursor Zoom
Done: (1) Made black viewport responsive to window size (pure #000000 full screen); (2) Removed dashed coordinate grid lines in AladinBase for a plain dark sky field; (3) Fixed detection bounding box misalignment by projecting ra/dec via skyToPixel(d.ra, d.dec, wcs) scaled with pxScale; (4) Fixed cursor-anchored wheel zooming with top-left origin (0 0) and centered pan on reset; (5) Verified 0 TypeScript errors, 22/22 frontend tests passing, 81/81 backend unit tests passing.
Files touched: frontend/src/design/viewer.css, frontend/src/viewer/aladin/AladinBase.tsx, frontend/src/viewer/overlays/WcsSvgOverlay.tsx, frontend/src/viewer/panels/ViewerStage.tsx, frontend/src/viewer/panels/viewerTransform.ts, docs/STATUS.md
Verified by: npm --prefix frontend run typecheck (0 errors), npm --prefix frontend test -- --run (22/22 passed), pytest tests/unit/ (81/81 passed)   Lines before -> after: 101 -> 60 (AladinBase.tsx), 464 -> 530 (ViewerStage.tsx)
## Next task: T6 / Integration — Project workspace (notes, findings, FITS files) + live backend E2E verification
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
