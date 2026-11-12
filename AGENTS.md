# AGENTS.md: SPHEREx Moving-Object & Trajectory Platform

Read this file at the start of EVERY session. Keep it short. Detailed specs live in `docs/`; read ONLY the part relevant to your task.

## 1. Project in 5 lines
- Public web tool: user enters RA/Dec (or uploads same-field FITS files) and sees SPHEREx (and other) sky images over time as a "little video" to spot moving objects.
- Core function: detect and verify trajectories of objects within a FITS file and between FITS files (same coordinates, different dates), with maximum astrometric precision and noise rejection.
- Users select objects by click-drag box or accept program suggestions. Chosen objects (A, B, C...) MUST keep a persistent identity across epochs.
- Also: A-vs-B FITS comparison, Planet X search mode, project workspace (notes, findings, FITS files), coordinates-in-URL embedding.
- Mandatory test targets: Barnard's Star, Epsilon Indi B, WISE 0855 (RA 08h55m10.831s, Dec -7d14m42.53s), RW Aurigae, R Draconis. Variables change brightness, NOT position; never confuse the two.

## 2. CODE EFFICIENCY RULE (mandatory, applies to every model)
Write the **smallest correct code**. If 1000 lines can be 10 with identical behaviour, accuracy and precision, write the 10.
1. **Library first:** use Astropy, photutils, SEP, reproject, astroquery, SciPy, NumPy, scikit-learn, FastAPI/Pydantic, React/TanStack features before writing anything custom.
2. **Vectorise:** NumPy/array operations instead of Python loops; comprehensions and built-ins instead of manual iteration.
3. **No bloat:** no dead code, duplicated logic, speculative abstraction, wrapper-only layers, unused parameters, config for things never configured, or comments that restate the code.
4. **DRY:** one generic function or component instead of near-copies (parametrise, don't copy-paste).
5. **Shrink pass (before finishing any task):** re-read your diff and ask "can this be fewer lines with the same behaviour?" Apply it, then re-run the tests. Report the result as `lines before -> after` in one line.
6. **Limits on shrinking** (correctness always wins): never cut accuracy, precision, error/uncertainty propagation, input validation at system boundaries, tests, or the one-line unit/type docstrings. No code golf: shorter must stay readable (no cryptic one-liners).
7. **Proof of equivalence:** the same tests must pass before and after a shrink. If no test covers it, add a small one first.
8. Prefer deleting code over adding it. Before adding a function, check if one already exists in `sphx_core/` or `frontend/src/`.

## 3. Doc map (read only what you need; NEVER read a whole large doc)
| File | Contains |
|---|---|
| `docs/ARCHITECTURE.md` | Components, data flow, providers, ingestion, caching, queues, backend folder tree |
| `docs/DATA_MODEL.md` | DB schema (DDL), ER diagram, flag bits |
| `docs/SCIENCE.md` | Large. Read §0 + only the section you need (index below) |
| `docs/API.md` | Own API + external data sources |
| `docs/FRONTEND.md` | Stack, design system, pages, viewer spec, URL scheme |
| `docs/ROADMAP.md` | Phases, milestones, MVP/Phase 2 tags |
| `docs/STATUS.md` | **Handoff log: current state, last session summary, next task** |

**SCIENCE.md section index:** §0 SPHEREx constraints | A data/WCS/time/reprojection | B noise, artifacts, co-add, differencing, real-bogus, mover-vs-variable | C astrometry, plate solution, proper motion, parallax, limits | D classification/SED/cross-match | E orbits/kinematics | **F validation + acceptance criteria (F.3 = 5-target table)** | G cross-epoch identity pipeline | H trajectory checking | I Planet X | J A-vs-B comparison | K pseudocode | L parameter defaults.

## 4. Stack and MVP scope
- **MVP (build now):** Python 3.11+, FastAPI, Pydantic v2, Astropy, astroquery, reproject, photutils, SEP, SciPy, NumPy, scikit-learn; PostgreSQL 16 with HEALPix index columns; Redis + Celery; local disk or MinIO; React 18 + TypeScript + Vite; Aladin Lite v3; Zustand + TanStack Query; pytest, Vitest, Playwright. Auth: anonymous viewer + email/password for projects (unless ROADMAP says otherwise).
- **Deferred (= ROADMAP Beta / Public / Stretch; do NOT build until ROADMAP says so):** Q3C extension, OpenTelemetry/Prometheus/Grafana, ORCID + magic-link login, Kubernetes/KEDA, own HiPS generation, WebCodecs MP4 export, CuPy/GPU, read replicas, ClamAV.
- If a doc describes a Phase 2 item, implement the simplest MVP substitute and leave a one-line `# PHASE2:` note.

## 5. Model routing (the human switches models manually)
Claude quota is exhausted, so for now:
- **Gemini 3.8 Flash High: BUILDS EVERYTHING** (scaffold, UI, API, DB, providers, science core `sphx_core/`, tests, docs, deploy, shrink passes). Science work is done in the smallest possible steps, tests first.
- **Gemini 4 Argon (small quota): INSPECTION ONLY.** Reviews diffs/files and returns a short issue list `[file:line] problem -> fix`. Never writes whole files, never builds features, never reads the whole repo.
- **Claude (Opus/Sonnet): reserved.** When quota returns (reportedly weekly), use it for the science-core review and final audit; see STAGE_PROMPTS.md.
- If you are not the right model for the task given, say so in one line and stop.
- Escalation: if a task fails twice, STOP, write the failing test + error + relevant file path under "Escalations" in `docs/STATUS.md`, and tell the human to ask Argon for a diagnosis (Flash then applies the patch).

## 6. Token discipline (mandatory)
1. Read AGENTS.md, `docs/STATUS.md`, and only the doc sections and files your task needs. Not the whole repo.
2. Edit files in place with small patches. NEVER reprint whole files.
3. Never load FITS data into context. Use header summaries and the tiny fixtures in `tests/fixtures/`.
4. Truncate logs and test output (last ~40 lines) before reasoning about them.
5. Work in vertical slices. First slice: WISE 0855 end to end.
6. Keep replies short: what changed, files touched, how verified, lines before -> after, next step.
7. One task per session. Finish by updating `docs/STATUS.md`.

## 7. Scientific rules (non-negotiable)
- Science correctness beats speed. Never fake, hardcode or silently fall back to made-up results; test-target reference values come only from `tests/science/reference_values.json`.
- Every measured position/flux carries an uncertainty. Every detection carries a confidence and false-positive note.
- Correct time standards (MJD/TDB) and frames (ICRS); WCS via `astropy.wcs`; astrometry on native frames, resampled data only for display/differencing/stacking.
- Identity matching: predict, gate, global assignment (Hungarian), position + flux/colour/shape + catalogue ID, persistent IDs, flag uncertain matches, allow manual user correction.
- Classification outputs probabilities with evidence; cross-check independent estimators and report disagreements.
- Never present a candidate (including Planet X) as confirmed.
- Science changes need a test with a numeric acceptance criterion from SCIENCE.md §F.
- Do not guess astronomy formulas or library APIs: take them from SCIENCE.md or the official Astropy/photutils/SEP docs, run the code, and let the tests decide.
- Items marked **[verify]** in the docs must be checked against official SPHEREx/IRSA documentation before relying on them.

## 8. Conventions
- Typed Python with one-line docstrings stating units. Strict TypeScript.
- Contract-first: OpenAPI + Pydantic models + DB schema are the source of truth. Change the contract before the code and note it in STATUS.md.
- Provider abstraction: every external archive is a plug-in; SPHEREx release specifics live only in the provider + `release_profiles/*.yaml`.
- Secrets in environment variables only.
- UI: minimalist editorial (see FRONTEND.md §2), dark "sky" viewer, WCAG 2.2 AA, responsive.

## 9. Definition of done (every task)
- Code runs; relevant tests pass (state command and result).
- Shrink pass done (section 2.5), tests still pass.
- No unrelated files changed.
- `docs/STATUS.md` updated; any doc that became wrong is patched.
- Only after the tests pass: commit with the task ID and name as the message (e.g. "T0.2: MVP contracts") and push to origin. Never commit .env, secrets or large FITS files, and never force-push. If the push fails (no remote or no credentials), stop, note it in docs/STATUS.md, and do not retry in a loop.

## 10. STATUS.md template (under 60 lines)
```
## Current phase: <n>
## Last session: <model> | <date> | <task>
Done: ...
Files touched: ...
Verified by: <command/result>   Lines before -> after: ...
## Next task: ...
## Open questions: ...
## Escalations: ...
```