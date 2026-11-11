# FRONTEND: Spec Section 6.2 (Frontend, design system, pages, viewer)

> Related: endpoints → `API.md`; science behind each panel → `SCIENCE.md`.

## 1. Stack

| Concern | Choice | Why |
|---|---|---|
| Framework | React 18 + TypeScript (strict) + Vite; React Router v6 (data routers) | Ecosystem, Aladin Lite integration, team familiarity |
| Static/editorial pages | Same app, pre-rendered (vite-plugin-ssg) for SEO and speed | One codebase; viewer stays client-side |
| State | Zustand (viewer state, URL-synced) + TanStack Query (server cache) | Small, explicit; URL is the single source for shareable state |
| Sky context | **Aladin Lite v3** (WebGL, HiPS: DSS, 2MASS, AllWISE, unWISE, Gaia, SPHEREx HiPS when available [verify]) | Standard CDS tool, catalog overlays, MOC footprints |
| Science frames | Custom **WebGL2** layer: float32 textures, client-side stretch (linear/asinh/log/zscale), A−B, A+B, ratio, blink at 60 fps | Arithmetic on true data without server round-trip |
| Overlays | SVG layer synced to WCS (trajectories, ellipses, labels, box selection) | Crisp, accessible, hit-testable |
| Charts | uPlot (spectra, light curves; fast, small) + Observable Plot for static figures | Performance at thousands of points |
| Video export | WebCodecs → MP4; gif.js fallback; server fallback (ffmpeg) | Client-side when possible |
| Tests | Vitest + Testing Library; Playwright e2e (5 target presets); axe-core accessibility | — |

## 2. Design system (derived from the six references)

What we take from each: **x.company**: generous whitespace, single strong idea per screen; **anthropic.com**: warm neutrals, serif display + clean sans, calm hierarchy; **outrider.org**: long-form editorial layout, big imagery with restrained type; **newyorker.com**: typographic authority, column rhythm, small caps labels; **harvard.edu**: institutional clarity, accessible contrast; **nasa.gov**: dark imagery-led hero, data credibility, mission metadata.

### 2.1 Typography
- Display / headings: **Source Serif 4** (open licence), weights 400/600; optical sizes.
- UI / body: **Inter** (tabular numerals enabled `font-feature-settings: "tnum"` in tables, coordinates).
- Mono (coordinates, FITS headers, code): **JetBrains Mono**.
- Scale (1.25 modular, rem): 0.75 / 0.875 / 1 / 1.25 / 1.563 / 1.953 / 2.441 / 3.052 / 3.815. Body 1rem/1.6; measure 60–72 ch.
- Labels: small caps, letter-spacing 0.06em (New Yorker style section kickers).

### 2.2 Grid & spacing
- 12-column grid, max content width 1200 px, gutters 24 px (16 px mobile); editorial text column spans 7/12 centred-left.
- 8-px spacing scale: 4, 8, 12, 16, 24, 32, 48, 64, 96, 128.
- Viewer is full-bleed (no max width), with collapsible side panels 320–400 px.

### 2.3 Colour
Light (site): `--paper #F7F5F0`, `--ink #141414`, `--ink-2 #4A4A48`, `--rule #E2DED6`, `--accent #C2410C` (burnt orange, used sparingly), `--link #1D4ED8`.
Dark "sky" (viewer default): `--sky-0 #05070A`, `--sky-1 #0C1117`, `--sky-2 #151C25`, `--text #E6EAF0`, `--muted #8A96A8`, `--accent #F59E0B`.
**Science overlay palette** (colour-blind safe, Okabe–Ito): object identities A, B, C… cycle `#E69F00 #56B4E9 #009E73 #F0E442 #0072B2 #D55E00 #CC79A7`; uncertain match = dashed stroke + ⚠ glyph (never colour alone). Difference image diverging map: blue–black–orange (`cmocean.balance`-like), negative/positive symmetric about 0.
All text ≥ 4.5:1 contrast (WCAG 2.2 AA); large text ≥ 3:1.

### 2.4 Motion
- 150–250 ms ease-out for UI; no decorative parallax. Blink speed user-controlled (0.5–10 fps). `prefers-reduced-motion`: blink defaults to manual step, no auto-play.

### 2.5 Components
Header (wordmark + 6 links + account), Footer (credits, licences), Button (primary/secondary/ghost), IconButton, Input (with unit suffix), CoordinateInput (sexagesimal/decimal + name resolver), RangeSlider (dual, dates), Select/Combobox, Tabs, Segmented control, Tooltip, Popover, Dialog (native `<dialog>`), Toast, Badge (confidence/flags), Table (sortable, virtualised), Card (gallery), Kicker label, Callout (science caveat), EmptyState, Skeleton, ProgressBar (job), Stepper (upload wizard), FrameStrip (epoch thumbnails), ColorBar, Legend, EllipseGlyph, SpectrumChart, LightCurveChart, HeaderDiff, MetadataList, FlagMatrix.

## 3. Page wireframes (all fundamental pages)

Notation: `[ ]` block, `{}` interactive.

**Home / Landing (`/`)**: dark hero, full-bleed SPHEREx co-add of a field with a 3-frame auto-blink of WISE 0855 (respecting reduced motion). Headline (serif, 3.8rem): "Watch the sky move." Subline: one sentence on SPHEREx. `{CoordinateInput + Go}` front and centre. Below on paper: three columns "Enter coordinates → Watch the epochs → Track what moves". Gallery strip of 5 test targets (cards). "How the science works" teaser linking to Methodology. Footer.

**About (`/about`)**: editorial long-form: challenge, SPHEREx mission (6 arrays, 102 bands, 6-month cadence), citizen-science lineage (Pluto, Backyard Worlds), team, partners.

**How it works / Methodology (`/methodology`)**: chaptered (sticky TOC left): Data → Calibration → Registration → Detection → Identity → Motion → Classification → Cross-checks → Limits. Each chapter has plain-language summary + expandable "For scientists" equations (KaTeX) mirroring SCIENCE.md.

**Tool / Viewer (`/v`)**: see §4.

**Examples / Gallery (`/examples`)**: 5 cards (Barnard's Star, ε Indi B, WISE 0855, RW Aur, R Dra) each: thumbnail blink, "what to expect" (e.g. "moves ~5″ per 6 months ≈ 0.8 px"), one-click open in viewer with preset URL, link to the automated validation result (pass/fail, date, measured vs reference values).

**Documentation / API (`/docs`)**: user guide, embed guide (URL params table, iframe snippet), REST API reference (rendered OpenAPI via Scalar/Redoc), Python snippet examples, changelog, data-release notes.

**Data sources & Credits (`/credits`)**: table of each archive with required acknowledgement text (API.md §2), software credits (Astropy, Aladin, etc.), licences.

**FAQ (`/faq`)**: accordion: "Why is the image so pixelated?" (6.2″ pixels), "Why did this star change colour/brightness?" (variables vs movers), "Is this a new planet?" (never confirmed; how to report), etc.

**Contact (`/contact`)**: form (name, email, topic incl. "Report a candidate"), plus links to MPC reporting and Backyard Worlds.

**Privacy / Terms (`/privacy`, `/terms`)**: plain text; data retention for uploads; licences of derived data.

**404**: dark sky field with "This patch of sky is empty." + coordinate input + home link.

**Login / Sign-up (`/login`)**: email magic link, "Continue with ORCID"; no passwords.

**Account (`/account`)**: profile, ORCID link, API tokens (create/revoke), storage usage, delete account.

**Settings (`/settings`)**: default stretch, colour map, overlay palette, units (deg/sexagesimal, mas/yr vs ″/yr), blink speed, reduced motion override, default FOV, theme.

**Projects list (`/projects`)**: table/cards: name, field (RA/Dec), #epochs, #tracked objects, last modified, visibility; `{New project}`; filter/search.

**Project detail (`/projects/:id`)**: header (name, field, share). Tabs: *Overview* (sky thumbnail + key findings), *Files* (uploaded/fetched FITS with metadata, compatibility status), *Objects* (tracked A/B/C… with motion fit summary + classification), *Trajectories* (plots, residuals), *Notes* (Markdown editor with autosave, embedded viewer snapshots), *Findings* (structured: title, object, evidence, status draft/submitted), *Saved views* (URL states), *History* (version timeline, diff, restore), *Export* (CSV, JSON, FITS BINTABLE, PNG/GIF/MP4, shareable URL, citation text).

## 4. Viewer (`/v`)

### 4.1 Layout (desktop ≥ 1280 px)
```
┌──────────────────────────────────────────────────────────────────────────────┐
│ {RA/Dec or name} {FOV ▾} {Dates ⟷} {Release ▾} {Mode: Blink|Compare|PlanetX} │
├────────────┬─────────────────────────────────────────────────┬───────────────┤
│ LEFT       │                 MAIN CANVAS                     │ RIGHT         │
│ Layers     │  WebGL frames over Aladin context               │ Inspector     │
│ ─ Bands/   │  SVG overlays: tracks, ellipses, labels, box    │ ─ Selected obj│
│   arrays   │                                                 │   A: μ, PA,   │
│ ─ Ops      │                                                 │   χ²ν, class  │
│  A+B, A−B  │                                                 │ ─ Spectrum    │
│ ─ Catalogs │                                                 │ ─ Light curve │
│  Gaia/WISE │                                                 │ ─ Matches ⚠   │
│ ─ Flags    │                                                 │ ─ Flags/meta  │
│ Suggestions│                                                 │               │
├────────────┴─────────────────────────────────────────────────┴───────────────┤
│ FrameStrip: ▶ ❚❚ ◀ ▶  fps {slider}  [E1 2025-06][E2 2025-12][E3 2026-06] ...  │
└──────────────────────────────────────────────────────────────────────────────┘
```
Tablet: right panel becomes bottom sheet. Mobile (< 768 px): canvas full width, panels as tabs below; box-drag via two-finger long-press or "Select" tool toggle.

### 4.2 Features (each → requirement)
- **Coordinate input**: sexagesimal or decimal, name resolution via Sesame (SIMBAD/NED/VizieR); shows ICRS + Galactic + ecliptic; epoch note ("coordinates at J2016.0 propagated to each frame epoch for high-PM objects").
- **FOV control**: 2′–60′ (logged-in), presets 5′/15′/30′; shows estimated download size and the max trackable rate for that FOV and cadence.
- **"Little video" blink**: per-epoch frames (co-added per array or per chosen band group), common stretch (zscale over union) to avoid fake brightness changes, fixed or auto fps, loop/bounce, frame labels (UTC date, MJD, band, array).
- **Band/array selection**: 6 arrays × 17 channels; options: single channel, array co-add, wavelength range co-add, "colour composite" (3 user-chosen ranges → RGB), "methane/CO₂ mode" presets (e.g. 3.2–3.4 µm vs 3.9–4.1 µm ratio for T/Y dwarfs) [see SCIENCE D].
- **Add/subtract operations**: A+B (stack), A−B (difference, PSF-matched server product or quick client subtract), A/B ratio, median of all, "each minus median of others" (mover finder).
- **Box-drag selection**: drag rectangle → all extracted sources inside become selected objects labelled A, B, C… (persistent IDs); keyboard alternative (arrow keys move a reticle, Shift+arrows resize, Enter confirm).
- **Suggested-object panel**: ranked list with reason badges (High PM candidate, Red+moving, Mover dipole in diff, Known SSO, Variable, Anomalous SED), confidence, `{Track}`.
- **Trajectory overlay**: per object, polyline through measured positions, 1σ/2σ error ellipses, predicted past/future positions (dashed, ±N yr), parallax loop when fitted, arrow of μ.
- **Spectra panel**: on-the-fly SED from the 102 channels (flux density vs λ, errors, flags greyed), template overlays (best-fit + alternatives), toggle per epoch.
- **Light curve**: flux vs time per band group, variability indices.
- **Metadata / contamination flags**: per-frame header essentials, flag-bit legend with fraction of flagged pixels in FOV, per-source flags matrix (saturation, blend, edge, persistence…).
- **Catalog overlays**: Gaia DR3 (propagated to frame epoch with μ, ϖ), CatWISE2020, AllWISE, 2MASS, SIMBAD, known SSOs (SkyBoT/Horizons at frame epoch).
- **A-vs-B compare** (mode): side-by-side, blink, swipe slider, A−B, A+B, A/B, header diff, photometric diff table, source change lists (appeared / disappeared / moved / changed brightness).
- **Planet X mode**: workflow wizard (SCIENCE E.3): field selection → rate/distance grid → run → candidate gallery with stacked stamps, rejection reasons, MPC/Horizons check, "not confirmed" banner.
- **Identity inspector**: for each object per epoch: chosen match, alternatives with costs, confidence; `{Accept}{Reject}{Reassign…}` (click another source) → creates a manual override recorded in provenance.

### 4.3 URL / embed scheme (state is always in the URL)

```
https://<host>/v?ra=133.79513&dec=-7.24515&fov=10&from=2025-05-01&to=2026-11-01
   &release=latest&bands=arr5-6&mode=blink&fps=2&stretch=asinh&overlay=gaia,catwise
   &track=A:133.7951,-7.2451&frame=2
```
| Param | Type | Default | Notes |
|---|---|---|---|
| `ra`,`dec` | deg or sexagesimal (`08:55:10.831`, `-07:14:42.53`) | — | ICRS |
| `target` | string | — | resolved via Sesame if no ra/dec |
| `fov` | arcmin | 10 | clamped per role |
| `from`,`to` | ISO date | full range | UTC |
| `release` | id or `latest` | latest | reproducibility: share pins resolved release |
| `bands` | `arr1`…`arr6`, `ch:<id>`, `um:3.2-3.4`, list | `all` | |
| `mode` | `blink`/`compare`/`planetx` | blink | |
| `a`,`b` | frame ids | — | compare mode |
| `op` | `diff`/`sum`/`ratio`/`swipe` | — | |
| `overlay` | list | none | gaia,catwise,allwise,2mass,simbad,sso |
| `track` | list `id:ra,dec` | — | |
| `embed` | `1` | 0 | chrome-less UI |

Embed: `<iframe src="https://<host>/embed?ra=..&dec=..&fov=5" width="640" height="480" loading="lazy" title="SPHEREx sky viewer"></iframe>`; oEmbed `GET /oembed?url=...`.

## 5. Accessibility, performance, responsive
- WCAG 2.2 AA; all controls keyboard reachable; ARIA live region announces frame changes ("Epoch 2 of 4, 2025-12-03"); object list mirrors canvas so screen-reader users can inspect sources and tracks as a table; no information by colour only.
- Performance budgets: landing LCP < 2.0 s (4G), JS < 200 KB gz on static pages; viewer first frame < 3 s for 10′ FOV cached; blink ≥ 30 fps at 1024² textures; INP < 200 ms.
- Code-split viewer, Aladin, charts; frames streamed progressively (display WebP first, float32 on demand); service-worker cache for frames.
- Responsive breakpoints: 480 / 768 / 1024 / 1280 / 1600.

## 6. Folder structure
```
/frontend
  src/
    app/ routes.tsx  layout/
    pages/ Home About Methodology Examples Docs Credits Faq Contact Privacy Terms NotFound
           Login Account Settings Projects ProjectDetail Viewer Embed
    viewer/ state/ (zustand, url-sync)  gl/ (shaders: stretch, diff, ratio)
            aladin/  overlays/ (svg)  panels/ (Spectrum, LightCurve, Flags, Inspector, Suggestions)
            compare/  planetx/
    api/ (generated TS client from OpenAPI via openapi-typescript)
    design/ tokens.css components/
  tests/ unit/ e2e/ (playwright: one spec per mandatory target)
```

Example: URL-synced store (TypeScript)
```ts
import { create } from "zustand";
export interface ViewState { ra: number; dec: number; fovArcmin: number; mode: "blink"|"compare"|"planetx"; frame: number; bands: string[]; }
export const useView = create<ViewState & { set: (p: Partial<ViewState>) => void }>((set) => ({
  ra: 0, dec: 0, fovArcmin: 10, mode: "blink", frame: 0, bands: ["all"],
  set: (p) => set((s) => { const n = { ...s, ...p }; history.replaceState(null, "", toQuery(n)); return n; }),
}));
```
