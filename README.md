# SPHEREx Moving-Object & Trajectory Platform

Public astronomical web platform for viewing multi-epoch SPHEREx (and multi-mission) sky cutouts as an interactive blink video, detecting moving objects, preserving persistent cross-epoch identities, fitting astrometric motion, and inspecting spectra/light curves.

---

## 1. Quick Start & Production Deployment (Single VPS)

The platform is designed to deploy seamlessly on a single VPS or server using Docker Compose and a multi-stage production Nginx container for static asset delivery and reverse proxying.

### 1.1 Production Launch with Docker Compose

1. Clone repository and create production environment configuration:
```sh
cp .env.example .env
```
2. Generate secure production secrets in `.env`:
```sh
# Set APP_ENV=production, strong SECRET_KEY, and POSTGRES_PASSWORD
SECRET_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
```
3. Launch the complete stack:
```sh
docker compose -f docker-compose.prod.yml up -d --build
```
This launches:
- **Frontend** (`nginx:1.27-alpine`): Multi-stage build serving optimized static SPA with Gzip, security headers, and caching policies on port 80. Proxies `/api/` and `/embed` to the API gateway.
- **API Gateway** (`backend.Dockerfile`): Production FastAPI service running behind Uvicorn with sliding-window rate limiting, ETag 304 caching, and structured JSON logging.
- **Celery Worker**: Asynchronous pipeline worker for FITS extraction, plate solving, and synthetic cutouts.
- **PostgreSQL 16**: Relational database with automated migrations.
- **Redis 7**: Hot-cache and task queue broker.

### 1.2 Local Development

1. Setup virtual environments and dependencies:
```sh
make setup
```
2. Run development stack (with hot reload):
```sh
docker compose up -d
# Or run API and Vite directly:
# Backend: cd backend && uvicorn app.main:app --reload
# Frontend: cd frontend && npm run dev
```

---

## 2. Core Capabilities & User Guide

For detailed controls, keyboard shortcuts, and scientific workflows, see the [**User Guide**](docs/USER_GUIDE.md).

- **Multi-Epoch Blink Video**: Blink through multi-epoch observations at 0.5–10 FPS with instant 60 FPS frame switching and WebP caching.
- **Moving Object Tracking**: Click any green bounding box or drag a selection box to track targets across epochs with persistent identities (**Track A** in cyan, **Track B** in orange).
- **Astrometric & Motion Fitting**: Fits proper motion ($\mu_\alpha \cos\delta, \mu_\delta$) and parallax vectors using Astropy and linear/MCMC estimators.
- **Compare & Difference Modes**: Pixel-level differencing, ratio, and interactive swipe curtains between epochs to isolate moving or variable transients.
- **NASA IRSA Integration**: Direct ObsCore search across SPHEREx spectral bands D1–D6 (0.75–5.0 µm) with automatic cutout retrieval.
- **FITS Upload & Inspection**: Upload custom FITS images (up to 200 MB) with automatic celestial WCS validation and multi-epoch alignment.

### Mandatory Calibration Test Targets

| Target | RA / Dec (J2000) | Astronomical Class | Verification Target |
|---|---|---|---|
| **Barnard's Star** | `17h57m48.5s +04°41′36″` | High Proper Motion | $\mu \approx 10.3\,\text{arcsec/yr}$ positional shift |
| **WISE 0855** | `08h55m10.83s -07°14′42.5″` | Cold Sub-stellar Brown Dwarf | Mid-IR SPHEREx bands D4–D6 benchmark |
| **Epsilon Indi B** | `22h04m10.5s -56°46′58″` | Brown Dwarf Binary | Orbital and proper motion |
| **RW Aurigae** | `05h07m49.5s +30°24′05″` | T Tauri Variable | Brightness variation; static coordinates |
| **R Draconis** | `16h32m40.2s +66°45′18″` | Mira Variable | Fixed position; large flux changes |

---

## 3. Caching, Rate Limits & Observability

- **Rate Limiting**: Sliding-window rate limiter keyed by client IP or auth token with RFC 9457 problem details (HTTP 429). Configurable via `RATE_LIMIT_PER_MINUTE` (default: 120 req/min).
- **HTTP Caching**: Automatic ETag computation and conditional `304 Not Modified` headers for successful GET requests, plus `Cache-Control: public, max-age=31536000, immutable` for static assets.
- **Logging & Monitoring**: Configurable structured JSON logging (`LOG_FORMAT=json` in production) and optional Sentry error tracking via `SENTRY_DSN`.
- **Accessibility & Performance**: WCAG 2.2 AA compliant focus outlines, keyboard navigation, and high-performance SVG rendering optimized for large FOVs and dense fields.

---

## 4. Continuous Integration & Quality Assurance

Automated checks are run on every push and pull request via GitHub Actions (`.github/workflows/ci.yml`):
- **Backend**: Ruff linting, import sorting, and Pytest unit tests across API contracts, plate solving, and rate limiting.
- **Frontend**: ESLint, TypeScript typecheck (`tsc --noEmit`), Vitest unit tests, and production Vite bundle build.
- **Docker**: Syntax and configuration validation for both local and production Docker Compose stacks.

Common local quality commands:
```sh
make test          # Run Python and frontend test suites
make lint          # Run Ruff lint/format checks, ESLint, and TypeScript typechecking
make format        # Auto-format Python and frontend code
make compose-check # Validate docker compose configurations
```

---

## 5. Documentation Map

Consult the technical specifications in `docs/`:
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Service breakdown, deployment, security, and observability.
- [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) — Step-by-step user guide and keyboard shortcuts.
- [`docs/SCIENCE.md`](docs/SCIENCE.md) — Mathematical formulas, astrometry, photutils calibration, and acceptance criteria.
- [`docs/API.md`](docs/API.md) — OpenAPI contract specifications and archive provider APIs.
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — PostgreSQL 16 schema, flags, and tables.
- [`docs/STATUS.md`](docs/STATUS.md) — Project status and handoff log.
