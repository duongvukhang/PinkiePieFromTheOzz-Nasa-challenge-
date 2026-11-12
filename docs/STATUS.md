## Current phase: 1 (Backend API: fields/cutouts, jobs, and uploads implemented; science core verified except orbits/planetx)
## Last session: Gemini 3.8 Flash (High) | 2026-11-12 | Backend API uploads
Done: Implemented `uploads` endpoint group (`POST /api/v1/uploads` returning `202` + `GET /api/v1/uploads/{id}/report`): streaming upload size caps (200 MB anon / 2 GB auth), gzip-bomb pre-check, filename sanitization, `user_epoch` & reference (`reference_upload_id` / sky field) support, async `fast` worker task (`validate_upload` / `execute_validate_upload`) with `FitsFile` + `ProjectFile` + `Provenance` persistence, and idempotent job deduplication (`get_job_by_param`, `get_job_params`). Added `TestClient` API tests; `science/` untouched.
Files touched: `backend/app/api/v1/__init__.py`, `backend/app/api/v1/fields.py`, `backend/app/api/v1/uploads.py`, `backend/app/services/__init__.py`, `backend/app/services/fits_validator.py`, `backend/app/services/jobs.py`, `backend/app/workers/tasks_fast.py`, `tests/unit/test_contracts.py`, `tests/unit/test_api_uploads.py`, `docs/STATUS.md`
Verified by: `.venv/bin/pytest tests/unit/test_contracts.py tests/unit/test_api_uploads.py tests/unit/test_api_fields_cutouts.py tests/unit/test_fits_validator.py tests/unit/test_providers.py tests/unit/test_providers_catalogs_sso.py tests/unit/test_scaffold.py -v --tb=short` (32 passed in 9.54s); `.venv/bin/ruff check` & `ruff format --check` clean; `.venv/bin/mypy backend/app` clean (42 files).
Lines before -> after: backend upload/jobs/worker/fields `1752 -> 1724` (-28 on shrink pass).
## Next task: Implement next Backend API endpoint group (`analyses/suggestions` | `objects/tracks/associations` | `compare` | `projects/auth`), or `sphx_core.orbits` & `sphx_core.planetx` (§E, §I) — Gemini 3.8 Flash High
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Run `make setup` / `pip install -e ./backend` on a machine with setuptools/network so `sphx.providers` entry-point metadata includes all providers.
- Run `docker compose config` and full Compose stack on a machine with Docker Compose v2 installed.
## Escalations: None
