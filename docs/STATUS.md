## Current phase: 1 (Motion & Trajectory Review & Hardening)
## Last session: Gemini 3.8 Flash (High) | 2026-11-13 | T4.4: Motion Module Review Fixes (SCIENCE.md §C/§D/§H)
Done: Applied all 8 Argon motion review findings: added ridge prior penalty theta^T R theta to _wls_solve chi2 and BIC (§C.4); clamped degenerate leave-one-out studentized residual covariance eigenvalues to 1e-4 * np.diag(ci); pinned active-set 0-mas parallax solution (th5 = np.append(th4, 0.0)) on free fit < -2 sigma; preserved consistent STATIC model properties (k=2, dof=2n-2, delta_bic=0) when multi-epoch PM is insignificant; set DetectionFlag.VIM_CANDIDATE (1<<22) and DetectionFlag.PM_OUTLIER (1<<21) on MotionFitResult.flags; restricted Stetson J pairing to quasi-simultaneous (|dt| <= 1.0 d) multi-band observations (§D.4); propagated directional Jacobian scale v_norm to streak sig_rate; propagated dm.flags into intra-visit tracklet MotionMeasurement.
Files touched: `science/sphx_core/motion/__init__.py`, `science/sphx_core/calib/__init__.py`, `backend/app/db/flags.py`, `docs/DATA_MODEL.md`, `tests/unit/test_motion_identity_classify_consensus_comparison.py`, `docs/STATUS.md`
Verified by: `pytest tests/science/test_motion_and_mandatory_targets.py` (5/5 passed), `pytest tests/unit` (67/67 passed), `npm --prefix frontend run typecheck && test` (5/5 passed)   Lines before -> after: 1289 -> 1310
## Next task: T6 / Integration — Project workspace (notes, findings, FITS files) + live backend E2E verification
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
