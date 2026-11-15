## Current phase: 1 (FITS Display Pipeline & Production Infrastructure)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | Fix missing httpx dependency in Docker image
Done:
- Added `"httpx>=0.27"` into `infra/backend.Dockerfile` pre-cached dependencies layer and removed `--no-deps` flag from editable install.
- Verified Docker build locally.
Files touched: infra/backend.Dockerfile, docs/STATUS.md
Verified by: docker build (passed)   Lines before -> after: 2 lines updated in Dockerfile
## Next task: Verify live end-to-end integration on Vercel and Render
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
