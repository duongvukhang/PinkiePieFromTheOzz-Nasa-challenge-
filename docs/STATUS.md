## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | IRSA SIAv2 Live Query, VOTable Column Masking, and Cutout Optimization
Done:
- Resolved VOTable column ID masking (`col_0..col_50`) in `base._to_table()` by passing `use_names_over_ids=True`, preventing live IRSA SIAv2 fields from being dropped.
- Replaced slow ADQL ivoa.obscore `/TAP/sync` query in `irsa_search.py` with fast native SIAv2 query targeting `spherex_dr1` collection with spatial circle and time parameters.
- Disambiguated detector band products in exposure groups (`obs_id=f"{raw_obs_id}_{det}"`, `observation_group_id=raw_obs_id`) and included `energy_bandpassname` in `spherex_irsa.py`.
- Bumped search cache key to `irsa_search_v2` and restricted caching strictly to `LIVE_ARCHIVE` responses, preventing transient synthetic fallbacks from poisoning cell caches.
- Returning `LIVE_ARCHIVE` with empty records when live SIA queries find 0 matches instead of generating synthetic fallback epochs.
- Implemented native fast IBE cutout query parameter appending (`?center={ra},{dec}&size={fov}deg`) with 503 retry and reuse of client streaming in `load_archive_product()`.
- Normalized comma decimals (`.replace(",", ".")`) across coordinate and radius parsing in `ArchiveSearchModal.tsx`.
Files touched: backend/app/providers/base.py, backend/app/providers/spherex_irsa.py, backend/app/services/irsa_search.py, frontend/src/viewer/panels/ArchiveSearchModal.tsx, frontend/tests/unit/archiveModal.test.ts, tests/unit/test_api_archive_search.py, docs/STATUS.md
Verified by: pytest test_api_archive_search.py test_providers.py test_motion_and_mandatory_targets.py (28 passed), npm test (8 test files, 40 passed)   Lines before -> after: 623 -> 649 (+26 lines in irsa_search.py)
## Next task: Prepare for science-core review and audit (Phase 1 completion checklist)
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
