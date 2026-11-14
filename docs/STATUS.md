## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-14 | Box Selection, Tracking Regimes & Capacity Cap
Done:
- Integrated box-scoped object selection in viewer SVG overlay: replaced thick blue border with high-contrast dual-tone 1px stroke (white outer #ffffff + black inner #000000) visible against `--sky-0` (`#05070A`) and bright stars; restricted bounding squares to detections inside `activeBox` (or selected/tracked objects); applied dashed strokes + `⚠` glyphs to ambiguous matches ($p < 0.90$ or flag).
- Added "Objects in Box" panel with sorting dropdown (Flux, SNR, Distance to center; disabled Proper-Motion Significance prior to trajectory fit) and per-object tracking buttons.
- Enforced constant `MAX_TRACKED_OBJECTS = 2` across backend API (`backend/app/api/v1/objects.py`), frontend store/actions (`viewStore.ts`, `useViewerData.ts`, `InspectorTabs.tsx`), and science identity core.
- Solved Problem 1: Prior confusion $\Lambda_0 = \rho_{\mathrm{annulus}}\pi(\mu_{\max}\Delta t)^2$ for regime routing ($\Lambda_0 < 0.05 \implies$ Regime A; $\ge 0.05 \implies$ Regime B) using annulus clutter density $\rho$ ($15'' \le r \le 180''$); post-initiation $\Lambda_1 = \rho_{\mathrm{annulus}}\pi(\sigma_\mu \Delta t)^2$ for reporting confidence.
- Solved Problem 2: Preserved global kinematic gate ($\mu_{\max}\Delta t$) for tracker; box scoping restricts candidate inspection only.
- Solved Problem 3: Added null/miss hypothesis rival $e^{-(C_{\mathrm{miss}} - C_1)}$ to marginal posterior confidence with consistent likelihood costs ($-\ln P_D$ vs $-\ln(1 - P_D)$).
- Solved Problem 4: Specified differential astrometry $\boldsymbol{\delta}_{\mathrm{reg}} = \mathrm{median}(\mathbf{r}_{\mathrm{curr}, i} - \mathbf{r}_{\mathrm{ref}, i})$ over local static reference stars.
- Solved Problem 5: Regime B velocity histogram requires pairwise flux/color consistency and $\ge 3\sigma$ Poisson test over random background pair pedestal.
- Solved Problem 6: $N=2$ tracks require quantitative escapes: catalogue PM within $3\sigma$ ($d_{\mathrm{cat}} < 3.0$) or SED consistency $\chi^2_{\mathrm{SED}} \le 9.21$; disjoint bands lack SED corroboration and remain `"unvalidated"`.
Files touched: science/sphx_core/identity/__init__.py, science/sphx_core/motion/__init__.py, backend/app/api/v1/objects.py, frontend/src/viewer/state/viewStore.ts, frontend/src/viewer/state/useViewerData.ts, frontend/src/viewer/overlays/WcsSvgOverlay.tsx, frontend/src/viewer/panels/InspectorTabs.tsx, frontend/src/pages/ViewerPage.tsx, frontend/tests/unit/boxSelectAndRegimes.test.ts, tests/science/test_identity_regimes_and_box.py, docs/STATUS.md
Verified by: pytest tests/science/test_identity_regimes_and_box.py (5/5 passed), pytest tests/unit/ tests/science/ (109 passed), vitest (8 test files, 36 passed)   Lines before -> after: useViewerData.ts 534 -> 489 | identity/__init__.py 2490 -> 2480 | objects.py 972 -> 972
## Next task: Live backend E2E Playwright verification across the 5 mandatory benchmark targets (WISE 0855, Barnard's Star, eps Ind B, RW Aurigae, R Draconis)
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
