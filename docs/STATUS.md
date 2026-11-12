## Current phase: 1 (Backend OpenAPI 3.1 contract + Home/About/Auth + Task 5b Observatory Viewer `/v` & `/embed` complete)
## Last session: Gemini 3.8 Flash (High) | 2026-11-13 | T5b.2-fix: Console warnings, Vite API proxy, HTML-instead-of-JSON guard, CORS
Done: Replaced `fetchPriority` with lowercase `fetchpriority` in `Home.tsx`; enabled React Router `v7_startTransition` and `v7_relativeSplatPath` future flags in `routes.tsx`; added Vite `/api` and `/health` proxy (`VITE_API_PROXY_TARGET` / `localhost:8000`) in `vite.config.ts`; added HTML-response detection & `VITE_API_BASE_URL` fallback in `client.ts` & `AuthPage.tsx`; added dev `CORSMiddleware` in `backend/app/main.py`; stabilized `smoke.spec.ts` unroute ordering and added `apiClient.test.ts`.
Files touched: `frontend/src/{pages/Home.tsx,pages/AuthPage.tsx,app/routes.tsx,api/client.ts}`, `frontend/vite.config.ts`, `frontend/tests/{unit/apiClient.test.ts,e2e/smoke.spec.ts}`, `backend/app/main.py`, `docs/STATUS.md`
Verified by: `npm --prefix frontend run build && lint && format:check && test` (5/5 Vitest passed), `npm --prefix frontend run test:e2e` (4/4 Playwright passed), `.venv/bin/pytest tests/unit/test_api_fields_cutouts.py tests/unit/test_api_projects_auth.py -q` (7 passed)   Lines before -> after: 7 modified files `749 -> 780` (`+31` net for proxy, CORS, and HTML guard)
## Next task: T5c / Integration — Object trajectory creation & persistent A..Z IDs + Compare upload UI + Projects workspace + live backend E2E verification — Gemini 3.8 Flash (High)
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
