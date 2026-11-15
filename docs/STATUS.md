## Current phase: 1 (FITS Display Pipeline & Production Infrastructure)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | Replace Tracking Circle with Dynamic Green Square
Done:
- Replaced solid/dashed circles on current tracking objects and tracks in `WcsSvgOverlay.tsx` with dynamic green bounding squares (`stroke="#22c55e"`).
- Set square fill to nearly invisible (`rgba(34, 197, 94, 0.04)`), ensuring underlying astronomical sources and FITS pixel structures are completely visible.
- Implemented `getDynamicObjectSize` to dynamically scale the square based on object FWHM arcsec, WCS plate scale, and detection SNR.
- Repositioned track label badges outside the dynamic square to prevent obscuring objects.
- Added comprehensive unit tests in `frontend/tests/unit/viewerInteractions.test.ts`.
Files touched: frontend/src/viewer/overlays/WcsSvgOverlay.tsx, frontend/src/viewer/state/useViewerData.ts, frontend/tests/unit/viewerInteractions.test.ts, docs/STATUS.md
Verified by: pytest tests/unit/ -q (95 passed), npm --prefix frontend test (48 passed), npm --prefix frontend run build (passed)   Lines before -> after: 1327 -> 1367 (+40 lines)
## Next task: Verify live end-to-end deployment on Vercel and Render
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
