# SPEC_INDEX: Assumptions, Reading Order & Traceability

## Assumptions (≤ 10 lines)
1. **SPHEREx data status:** Mission launched Mar 2025; by Nov 2026 ~3 all-sky 6-month passes exist, with 6.2″ pixels, 6 H2RG arrays, and LVF spectral exposures stepped over ~1–2 weeks per visit (`SCIENCE.md §0`).
2. **Archive specifics:** Level-2 MEF extension names, flag bits, EXPTIME, spacecraft Horizons ID, and IRSA collection names marked **[verify]** will be confirmed against official IRSA/SPHEREx docs before implementation.
3. **Unresolved binary limit:** Epsilon Indi Ba/Bb (~0.7″ separation) is unresolved at 6.2″/px; validation tests photocentre motion and common proper motion with ε Indi A rather than the binary orbit.
4. **Short-baseline degeneracy:** With ~3 SPHEREx passes, long-baseline anchors (Gaia DR3, WISE/CatWISE, 2MASS) are required to break proper-motion vs parallax degeneracy for faint/slow movers.
5. **Stack & MVP staging:** Full target architecture uses FastAPI, PG 16 + Q3C/HEALPix, Celery/Redis, S3/MinIO, React 18 + TS + Aladin Lite v3 + WebGL2; `Agents.md §4` defers Q3C, OTel, ORCID/magic-link, K8s, GPU, and MP4 to Phase 2 (`ROADMAP.md §2`).
6. **Auth & workspace:** Viewer works anonymously with rate/FOV limits; MVP project workspaces use email/password, upgrading to ORCID + magic-link in Beta.
7. **Repo conventions:** Root rules file `Agents.md` keeps its current filename; all spec sections live in `docs/*.md` mapped 1:1 to Master Prompt §6.
8. **Verification policy:** No network fetches occur during spec authoring; reference target parameters will be fetched and pinned in `tests/science/reference_values.json` during Stage 0.

---

## Reading order & Section 6 mapping

Per `Agents.md`, read only the doc needed for your task rather than loading everything into context:

| Master Prompt Section | Target File | Read When |
|---|---|---|
| §6.1 System architecture & §6.2 Backend | [ARCHITECTURE.md](./ARCHITECTURE.md) | Working on infra, ingestion, providers, queues, caching, or backend modules |
| §6.2 Frontend, Design System, Wireframes, Viewer | [FRONTEND.md](./FRONTEND.md) | Working on UI, design tokens, pages, WebGL2/Aladin viewer, or URL embed |
| §6.3 Database & Data Models | [DATA_MODEL.md](./DATA_MODEL.md) | Working on PostgreSQL DDL, Alembic migrations, indexes, or flag bitmasks |
| §6.4 Scientific Methodology (§5.A–F, §4.3–4.6) | [SCIENCE.md](./SCIENCE.md) | Implementing or reviewing `sphx_core/` algorithms, equations, or target tests |
| §6.5 API & External Data Sources | [API.md](./API.md) | Integrating archives (IRSA, Gaia, WISE, JPL, CDS) or FastAPI endpoints |
| §6.6 Development Roadmap & Testing | [ROADMAP.md](./ROADMAP.md) | Planning milestones, MVP vs Phase 2 scope, risks, or test bars |
| Current handoff state | [STATUS.md](./STATUS.md) | Start and end of every coding session |

---

## Requirements-Traceability Checklist

| ID | Prompt Requirement | Document | Exact Section |
|---|---|---|---|
| R-3.1 | Public web tool for RA/Dec sky query & "little video" epoch playback | `FRONTEND.md` | `## 4. Viewer (\`/v\`)` |
| R-3.2 | "May consider": easy updates for new SPHEREx data releases | `ARCHITECTURE.md` | `## 5. Ingestion of new SPHEREx releases ("easy to update")` |
| R-3.3 | "May consider": creative 102-band display & 6-array co-adds | `SCIENCE.md` | `### B.4 Per-array calibration and co-addition [A]` |
| R-3.4 | "May consider": generate on-the-fly spectra | `SCIENCE.md` | `### D.2 SED from 102 channels (on-the-fly spectra) [A]` |
| R-3.5 | "May consider": add (`A+B`) and subtract (`A−B`) multi-epoch images | `FRONTEND.md` | `### 4.2 Features (each → requirement)` |
| R-3.6 | "May consider": user-selectable FOV vs transfer-time/rate tradeoff | `SCIENCE.md` | `### C.6 Max trackable rate vs FOV [A]` |
| R-3.7 | "May consider": overlay Gaia, WISE & other telescope data | `API.md` | `## 2. External sources` |
| R-3.8 | "May consider": coordinates-in-URL & website embedding | `FRONTEND.md` | `### 4.3 URL / embed scheme (state is always in the URL)` |
| R-3.9 | "May consider": display metadata & contamination flags | `DATA_MODEL.md` | `## 3. Flag bitmasks (\`detections.flags\`, \`epoch_measurements.flags\`, \`associations.flags\`)` |
| R-3.10 | "May consider": use Astropy & CDS Aladin Lite tools | `API.md` | `## 1. Required libraries (minimum versions)` |
| R-3.11 | "May consider" backlog tagged MVP / Phase 2 / Stretch with justification | `ROADMAP.md` | `## 2. Feature backlog ("may consider" items + \`Agents.md\` §4 staging)` |
| R-3.12 | Mandatory test targets (Barnard's Star, ε Indi B, WISE 0855, RW Aur, R Dra) | `SCIENCE.md` | `### F.3 Validation plan: mandatory targets` |
| R-4.1a | Data input mode 1: user FITS upload & WCS/overlap compatibility check | `SCIENCE.md` | `### A.1 FITS structure & validation [E]` |
| R-4.1b | Data input mode 2: pluggable provider abstraction (IRSA, WISE, Gaia, etc.) | `ARCHITECTURE.md` | `## 4. Provider abstraction (new missions / releases without core changes)` |
| R-4.2 | Object selection: interactive box-drag + program-suggested targets | `FRONTEND.md` | `### 4.2 Features (each → requirement)` |
| R-4.3 | Cross-epoch identity consistency (registration, prediction, Hungarian/MHT, multi-feature, override) | `SCIENCE.md` | `## G. Cross-epoch identity pipeline (Requirement 4.3)` |
| R-4.4 | Trajectory checking (single FITS & multi-epoch, models, uncertainties, drawing) | `SCIENCE.md` | `## H. Trajectory checking (Requirement 4.4)` |
| R-4.5 | A-vs-B FITS comparison (blink, swipe, diff/sum/ratio, header & source diffs) | `SCIENCE.md` | `## J. FITS comparison A vs B (Requirement 4.5)` |
| R-4.6 | Planet X search mode (parallax signature, shift-and-stack, orbit degeneracy, MPC, limits) | `SCIENCE.md` | `## I. Planet X search mode (Requirement 4.6)` |
| R-5.A | Image/WCS/time/barycentric observer/flux-conserving reprojection & correlated noise | `SCIENCE.md` | `## A. Image and data handling` |
| R-5.B | Noise model, background, artifacts, array co-adds, ZOGY/Alard-Lupton, real-bogus, mover-vs-variable | `SCIENCE.md` | `## B. Noise and artifact rejection` |
| R-5.C | PSF centroiding, Gaia plate refinement, WLS/MCMC proper motion & parallax, baseline limits | `SCIENCE.md` | `## C. Astrometry` |
| R-5.D | IR SEDs, motion/reduced PM, variability, catalog cross-match, Bayesian classifier + evidence | `SCIENCE.md` | `## D. Object classification` |
| R-5.E | Solar-system ephemerides, short-arc OD (B&K/Väisälä/Gauss), Kepler solver, Galactic UVW | `SCIENCE.md` | `## E. Orbital / dynamical methods` |
| R-5.F | Independent multi-method consensus, disagreement flags, 5-target validation | `SCIENCE.md` | `## F. Multi-method cross-validation` |
| R-6.1 | System architecture (diagrams, caching, queues, billion-object scaling, cutouts, security, obs) | `ARCHITECTURE.md` | `## 2. Component overview` |
| R-6.2a | Frontend structure, all pages, Project workspace, editorial design system, WCAG/perf | `FRONTEND.md` | `## 3. Page wireframes (all fundamental pages)` |
| R-6.2b | Backend services, folder tree, storage layout, task contracts | `ARCHITECTURE.md` | `## 13. Backend structure (Section 6.2, backend part)` |
| R-6.3 | Full PostgreSQL schema, ER diagram, spatial indexing (Q3C/HEALPix), provenance/audit | `DATA_MODEL.md` | `## 2. DDL` |
| R-6.4 | Full mathematical spec + pseudocode + parameter defaults | `SCIENCE.md` | `## K. Pseudocode (key algorithms)` |
| R-6.5 | External source specs (protocols, auth, limits, citations, release detection) & own REST API | `API.md` | `## 3. Own public API (REST, OpenAPI 3.1, \`/api/v1\`)` |
| R-6.6 | Phased roadmap, task breakdown, testing strategy, risks, quantitative metrics | `ROADMAP.md` | `## 3. Milestones & task breakdown` |
| R-7.1 | Scientific honesty: limits, `[verify]` tags, `[E]`/`[A]`/`[S]` labels, never confirmed candidates | `SCIENCE.md` | `## 0. SPHEREx facts that constrain everything` |
