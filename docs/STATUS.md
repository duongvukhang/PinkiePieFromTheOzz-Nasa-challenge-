## Current phase: 1 (FITS Display Pipeline & Production Infrastructure)
## Last session: Gemini 3.8 Flash (High) | 2026-11-15 | Production Images, CI/CD, Caching, Rate Limits & Viewer Optimization
Done:
- Built production-ready container images: multi-stage frontend Dockerfile (`node:22-alpine` builder + `nginx:1.27-alpine` runner) with Gzip, SPA fallback, security headers, and asset caching; non-root backend Dockerfile with Uvicorn proxy headers and health checks.
- Configured single-VPS deployment with `docker-compose.prod.yml` orchestrating PostgreSQL 16, Redis 7, Celery worker, FastAPI API gateway, and Nginx reverse proxy.
- Added centralized production configuration in `app.core.config` and structured logging with Sentry error monitoring in `app.core.logging`.
- Implemented sliding-window rate limiting middleware (`RATE_LIMIT_PER_MINUTE`) with RFC 9457 ProblemDetail envelopes (HTTP 429) and automatic ETag / 304 Not Modified caching in `app.core.rate_limit`.
- Created GitHub Actions CI matrix in `.github/workflows/ci.yml` covering backend lint & pytest, frontend typecheck, vitest & production build, and docker compose validation.
- Performed accessibility & large FOV performance passes in `WcsSvgOverlay.tsx`, `UploadFitsModal.tsx`, `ViewerToolbar.tsx`, and added client-side `ErrorBoundary.tsx`.
- Authored comprehensive `README.md` and detailed `docs/USER_GUIDE.md` covering astronomical benchmark targets and single VPS operations.
Files touched: .env.example, .github/workflows/ci.yml, README.md, backend/app/core/__init__.py, backend/app/core/config.py, backend/app/core/logging.py, backend/app/core/rate_limit.py, backend/app/main.py, docker-compose.prod.yml, docs/STATUS.md, docs/USER_GUIDE.md, frontend/src/app/ErrorBoundary.tsx, frontend/src/main.tsx, frontend/src/viewer/overlays/WcsSvgOverlay.tsx, frontend/src/viewer/panels/UploadFitsModal.tsx, frontend/src/viewer/panels/ViewerToolbar.tsx, infra/backend.Dockerfile, infra/frontend.Dockerfile, infra/nginx/frontend.conf, tests/unit/test_production_config_and_rate_limit.py
Verified by: pytest (95 passed), vitest (43 passed), tsc && vite build (0 errors), docker compose config (0 errors)   Lines before -> after: 86 lines -> 74 lines in rate limiter (-12 lines shrink pass)
## Next task: Prepare for science-core review and audit (Phase 1 completion checklist)
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Phase 2 deferred items tracked in `ROADMAP.md`: `/api/v1/planetx/*` backend shift-and-stack, `/api/v1/auth/orcid`, `2mass`/`panstarrs`/`simbad`/`sso` overlays, `tus` uploads, `mp4` export.
## Escalations: None
