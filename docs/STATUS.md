## Current phase: 1 (Backend API: fields/cutouts + async jobs implemented; science core verified except orbits/planetx)
## Last session: Gemini 3.8 Flash (High) | 2026-11-12 | Backend API fields/cutouts & jobs
Done: Implemented `fields/cutouts` and `jobs` endpoint group: RFC 9457 error envelopes (`deps.py`, `main.py`), Redis/memory TTL cache (`services/cache.py`), DB/in-memory `JobStore` + provenance (`services/jobs.py`, `db/session.py`), Celery `fast` worker task with WebP/float32 cutout rendering (`workers/celery_app.py`, `workers/tasks_fast.py`), `/resolve`, `/fields/frames`, `/cutouts`, `/cutouts/{id}` + artifact endpoints, `/catalogs/overlay`, `/releases`, `/examples`, `/jobs/{id}`, and `/jobs/{id}/events` SSE. Added `TestClient` API tests; `science/` untouched.
Files touched: `backend/app/main.py`, `backend/app/api/deps.py`, `backend/app/api/v1/fields.py`, `backend/app/api/v1/jobs.py`, `backend/app/db/session.py`, `backend/app/services/__init__.py`, `backend/app/services/cache.py`, `backend/app/services/fields.py`, `backend/app/services/jobs.py`, `backend/app/workers/celery_app.py`, `backend/app/workers/tasks_fast.py`, `pyproject.toml`, `tests/unit/test_contracts.py`, `tests/unit/test_api_fields_cutouts.py`, `docs/STATUS.md`
Verified by: `.venv/bin/pytest tests/unit/test_contracts.py tests/unit/test_api_fields_cutouts.py tests/unit/test_providers.py tests/unit/test_providers_catalogs_sso.py tests/unit/test_fits_validator.py tests/unit/test_scaffold.py -v --tb=short` (28 passed in 11.03s); `.venv/bin/ruff check` & `ruff format --check` clean; `.venv/bin/mypy backend/app` clean (41 files).
Lines before -> after: backend API/jobs/services `1927 -> 1906` (-21 on shrink pass).
## Next task: Implement next Backend API endpoint group (`uploads` | `analyses/suggestions` | `objects/tracks/associations` | `compare` | `projects/auth`), or `sphx_core.orbits` & `sphx_core.planetx` (§E, §I) — Gemini 3.8 Flash High
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Run `make setup` / `pip install -e ./backend` on a machine with setuptools/network so `sphx.providers` entry-point metadata includes all providers.
- Run `docker compose config` and full Compose stack on a machine with Docker Compose v2 installed.
## Escalations: None
