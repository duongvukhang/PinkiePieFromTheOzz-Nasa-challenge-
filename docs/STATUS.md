## Current phase: 0/1 (Science acceptance tests & reference values pinned; sphx_core implementation pending)
## Last session: Gemini 3.8 Flash High | 2026-11-11 | Configure .gitignore, AGENTS.md commit-and-push rule, and push project to GitHub
Done: Updated `.gitignore` for FITS/large binaries/archives and `.env*` files (untracking `Docker.dmg`), added commit-and-push-after-every-task rule to `AGENTS.md`, and pushed clean repository to `origin/main` (preceded by `tests/science/` acceptance suite & `tests/science/reference_values.json`).
Files touched: `.gitignore`, `AGENTS.md`, `docs/STATUS.md`
Verified by: `git ls-files` (no `Docker.dmg` / `.fits` / `.env`); `git rev-list` (0 blobs >1 MB); `git push -u origin main`   Lines before -> after: 28 -> 53 (`.gitignore`), 86 -> 87 (`AGENTS.md`)
## Next task: Implement `sphx_core` modules (`registration`, `extraction`, `difference`, `identity`, `motion`, `classify`, `consensus`, `comparison`, `orbits`, `planetx`) to turn `tests/science/` green — Gemini 3.8 Flash High
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Run `make setup` / `pip install -e ./backend` on a machine with setuptools/network so `sphx.providers` entry-point metadata includes all providers.
- Run `docker compose config` and full Compose stack on a machine with Docker Compose v2 installed.
## Escalations: None
