## Current phase: 1 (FITS Display Pipeline & Production Infrastructure)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | Asynchronous Cutouts Execution & Docker Layer Caching
Done:
- Made `create_cutouts` execute `execute_build_cutouts` in background task via `asyncio.create_task` instead of blocking HTTP request for 68 seconds, preventing gateway timeouts.
- Optimized `infra/backend.Dockerfile` layer caching by installing heavy scientific dependencies prior to copying application source.
- Shrunk `create_cutouts` dispatch logic by 12 lines.
- Validated with pytest (passed 2/2).
Files touched: backend/app/api/v1/fields.py, infra/backend.Dockerfile, docs/STATUS.md
Verified by: pytest tests/unit/test_production_config_and_rate_limit.py (passed)   Lines before -> after: 31 lines -> 19 lines in create_cutouts dispatch (-12 lines shrink pass)
## Next task: Verify live end-to-end integration on Vercel and Render
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
