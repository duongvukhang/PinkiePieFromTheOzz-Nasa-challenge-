## Current phase: 1 (FITS Display Pipeline & Observatory Features)
## Last session: Gemini 3.8 Flash (High) | 2026-11-14 | Fix Live IRSA SPHEREx TAP Query & JSON Extraction
Done: (1) Diagnosed why queries fell back to "Synthetic Survey Demo": IRSA TAP rejected invalid ADQL spatial syntax (`INTERSECTS(CIRCLE(...))`), rows were nested in `TABLEDATA` rather than `data`, and 10s timeout was exceeded; (2) Replaced ADQL with valid spatial footprint matching `1=CONTAINS(POINT('ICRS', ra, dec), s_region)`, added robust VOTable-in-JSON row extraction, and increased timeout to 30s; (3) Verified live querying against Caltech/IPAC IRSA TAP returns real SPHEREx DR1 observation epochs (50 products, 5 epochs for Epsilon Indi B) with `LIVE_ARCHIVE` status.
Files touched: `backend/app/services/irsa_search.py`, `docs/STATUS.md`
Verified by: `.venv/bin/pytest tests/unit/test_api_archive_search.py` (12/12 passed), live TAP extraction test (status: LIVE_ARCHIVE, 50 rows)   Lines before -> after: 582 -> 589 (irsa_search.py)
## Next task: T6 / Integration — Project workspace (notes, findings, FITS files) + live backend E2E verification
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
