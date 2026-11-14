## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | Persistent Identity Canvas Click-to-Track & Timeline Trajectory
Done:
- Scaled bounding box squares dynamically to source profile FWHM (`d.fwhm_arcsec`) clamped between 7px and 26px, tightly fitting in-box objects.
- Wired direct canvas click on green bounded objects to track as Target A or Target B (`MAX_TRACKED_OBJECTS = 2`), rendering persistent badges (`A` in Cyan, `B` in Orange).
- Added an SVG HUD cap alert when attempting to track more than 2 objects and added 1-click Untrack in the box candidate list.
- Implemented progressive trajectory vector drawing and active waypoint halo illumination synchronized with timeline playback.
Files touched: frontend/src/viewer/overlays/WcsSvgOverlay.tsx, frontend/src/viewer/panels/ViewerStage.tsx, frontend/src/pages/ViewerPage.tsx, frontend/src/viewer/state/useViewerData.ts, frontend/src/viewer/panels/InspectorTabs.tsx, frontend/tests/unit/boxSelectAndRegimes.test.ts, docs/STATUS.md
Verified by: vitest (8 test files, 38 passed), pytest tests/science/test_identity_regimes_and_box.py (5 passed)   Lines before -> after: WcsSvgOverlay.tsx 941 -> 1053 (+112 lines)
## Next task: Live backend E2E Playwright verification across the 5 mandatory benchmark targets (WISE 0855, Barnard's Star, eps Ind B, RW Aurigae, R Draconis)
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
