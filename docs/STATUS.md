## Current phase: 1 (Backend API: fields/cutouts, jobs, uploads, analyses/suggestions, objects/tracks/associations, compare, auth, and projects implemented; science core verified except orbits/planetx)
## Last session: Gemini 3.8 Flash (High) | 2026-11-12 | T1.10: Backend API projects and auth
Done: Implemented `/api/v1/auth/*` (`register`, `login`, `logout`, `me`, scoped API `tokens` CRUD, Argon2id hashing, `HttpOnly` signed session cookies, cache-backed logout revocation) and `/api/v1/projects/*` (`projects` CRUD, `notes`, `findings` with `SCIENCE.md §D` consensus `Conflict` guard requiring `summary_md`, `views`, `versions` + `/{version}/restore`, revocable `share` links, async `exports` + `GET .../exports/{export_id}` download for `csv`/`json`/`fits`/`png`/`gif`/`ades` with `params_hash` deduplication). Added `WorkspaceStore` mixins in `workspace_store.py`, `auth.py` helpers, `exports.py`, and `export_project` Celery task in `tasks_fast.py`; kept `/embed`, `/oembed`, `/render.gif` as 501 stubs and `science/` untouched.
Files touched: `backend/app/api/deps.py`, `backend/app/api/v1/auth.py`, `backend/app/api/v1/projects.py`, `backend/app/services/auth.py`, `backend/app/services/exports.py`, `backend/app/services/jobs.py`, `backend/app/services/workspace_store.py`, `backend/app/workers/tasks_fast.py`, `tests/unit/test_contracts.py`, `tests/unit/test_api_projects_auth.py`, `docs/STATUS.md`
Verified by: `.venv/bin/pytest tests/unit/ -v --tb=short` (60 passed in 47.30s); `.venv/bin/ruff check` & `ruff format --check` clean; `.venv/bin/mypy backend/app` clean (49 files).
Lines before -> after: backend touched files `3238 -> 3225` (-13 on shrink pass).
## Next task: Implement next Backend API endpoint group (`embed`: `/embed`, `/api/v1/oembed`, `/api/v1/render.gif`), or `sphx_core.orbits` & `sphx_core.planetx` (§E, §I) — Gemini 3.8 Flash (High)
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Run `make setup` / `pip install -e ./backend` on a machine with setuptools/network so `sphx.providers` entry-point metadata includes all providers.
- Run `docker compose config` and full Compose stack on a machine with Docker Compose v2 installed.
## Escalations: None
