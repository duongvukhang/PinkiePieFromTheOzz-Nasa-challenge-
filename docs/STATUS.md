## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-14 | T1.16: Fix SPHEREx SIAv2 Search Timeout & Update Release Profile to spherex_dr1
Done: (1) Updated release profile spherex_qr_v1.yaml collection_id to spherex_dr1 and added spherex_dr1, spherex_qr3, spherex_qr to release_aliases; (2) Replaced unindexed TAP query in IRSASearchService._search_spherex with fast indexed SIAv2 endpoint query (POS=CIRCLE, COLLECTION=spherex_dr1 & spherex_qr3), parsing VOTable rows (obs_id, s_ra, s_dec, energy_bandpassname, em_min, em_max, t_min, t_max, access_url), filtering by MJD and D1-D6 bands, and returning LIVE_ARCHIVE records with graceful fallback to sample/synthetic; (3) Added test_search_spherex_siv2_parsing_and_fallback; (4) Verified all 82 backend unit tests pass and 22 frontend tests pass.
Files touched: backend/app/providers/release_profiles/spherex_qr_v1.yaml, backend/app/services/irsa_search.py, tests/unit/test_api_archive_search.py, tests/unit/test_providers.py, docs/STATUS.md
Verified by: pytest tests/unit/ (82/82 passed in ~1m15s), npm --prefix frontend test -- --run (22/22 passed), npm --prefix frontend run typecheck (0 errors)   Lines before -> after: 627 -> 675 (backend/app/services/irsa_search.py)
## Next task: T6 / Integration — Project workspace (notes, findings, FITS files) + live backend E2E verification
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
