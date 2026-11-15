## Current phase: 1 (FITS Display Pipeline & Production Infrastructure)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | CORS & Double-URL Prefix Fix for Vercel/Render
Done:
- Fixed CORS blocking on Render by setting default `cors_origins: str = "*"` and passing `allow_origin_regex` in FastAPI CORSMiddleware, supporting all Vercel domains.
- Fixed duplicate `API_BASE` prepending in `frontend/src/api/client.ts` by introducing `toApiUrl` helper and decoupling `buildUrl` parameter construction.
- Cleaned URL resolution across all API calls and `AuthPage.tsx`.
- Validated with Vitest (43 passed), Vite production build (0 errors), and pytest (2 passed).
Files touched: backend/app/core/config.py, backend/app/main.py, frontend/src/api/client.ts, frontend/src/pages/AuthPage.tsx, docs/STATUS.md
Verified by: vitest (43 passed), vite build (0 errors), pytest (passed)   Lines before -> after: 17 lines modified across client.ts and config.py
## Next task: Verify live end-to-end integration on Vercel and Render
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
