# SPHEREx Moving-Object & Trajectory Platform

Public astronomical web platform for viewing multi-epoch SPHEREx (and multi-mission) sky cutouts as an interactive blink video, detecting moving objects, preserving persistent cross-epoch identities, fitting astrometric motion, and inspecting spectra/light curves.

## Repository Layout

- `backend/` — FastAPI gateway, Pydantic schemas, SQLAlchemy/Alembic DB layer, Celery workers, archive providers (`app/`).
- `science/` — Pure Python astronomical core library (`sphx_core/`) with zero web/DB imports.
- `frontend/` — React 18 + strict TypeScript + Vite client, WebGL2 frame renderer, Aladin Lite sky context, Vitest/Playwright tests.
- `tests/` — Shared test suites (`unit/`, `integration/`, `science/`) and tiny synthetic FITS fixtures (`tests/fixtures/`).
- `infra/` — Container build definitions (`backend.Dockerfile`, `frontend.Dockerfile`) and infrastructure support files.
- `docs/` — Architecture, science algorithms, API contract, data model, frontend spec, roadmap, and status log.

## Prerequisites

- Python 3.11+
- Node.js 20+ and npm
- Docker and Docker Compose v2

## Setup

1. Configure environment variables:

```sh
cp .env.example .env
```

2. Run the full stack with Docker Compose (PostgreSQL 16, Redis 7, MinIO, API, Celery worker, frontend):

```sh
make run
```

3. Or set up a local development environment:

```sh
make setup
```

## Common Commands

- `make test` — Run Python (`pytest`) and frontend (`vitest`) test suites.
- `make lint` — Run Ruff lint/format checks, ESLint, and TypeScript typechecking.
- `make format` — Auto-format Python and frontend code.
- `make compose-check` — Validate `docker-compose.yml` (`docker compose config -q`).

## Documentation

Start with [`Agents.md`](Agents.md) and [`docs/STATUS.md`](docs/STATUS.md), then consult [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/SCIENCE.md`](docs/SCIENCE.md), [`docs/API.md`](docs/API.md), [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md), [`docs/FRONTEND.md`](docs/FRONTEND.md), and [`docs/ROADMAP.md`](docs/ROADMAP.md).
