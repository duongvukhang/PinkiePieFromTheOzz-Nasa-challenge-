# API: Spec Section 6.5 (External data sources + own public API)

> Endpoints and limits change; every entry marked **[verify]** must be confirmed against the provider's current docs at implementation time. All providers are accessed through the plug-in interface (`ARCHITECTURE.md §4`) with Redis/object-store caching and per-provider politeness limits.

## 1. Required libraries (minimum versions)

| Library | ≥ Version | Use |
|---|---|---|
| Python | 3.11 | |
| numpy / scipy | 1.26 / 1.11 | arrays, optimisation, `linear_sum_assignment` |
| astropy | 6.0 | FITS, WCS, coordinates, time, units, `LombScargle`, `fit_wcs_from_points` |
| astroquery | 0.4.7 | Gaia, IRSA, SIMBAD, VizieR, Horizons, IMCCE/SkyBoT, MAST |
| pyvo | 1.5 | TAP/SIA/SSA generic VO access |
| reproject | 0.13 | flux-conserving reprojection |
| photutils | 1.12 | background, PSF photometry, segmentation |
| sep | 1.2 | fast extraction/deblending |
| scikit-image / scikit-learn | 0.22 / 1.4 | Radon/streaks; real–bogus, classifiers |
| emcee | 3.1 | MCMC astrometry/orbits |
| astroscrappy | 1.1 | cosmic rays (single-frame uploads) |
| filterpy | 1.4 | Kalman filtering |
| healpy or astropy-healpix | 1.16 / 1.0 | HEALPix indexing |
| FastAPI / Pydantic / SQLAlchemy / Alembic / Celery | 0.110 / 2.6 / 2.0 / 1.13 / 5.3 | backend |
| Frontend | React 18, React Router 6, TypeScript 5, Vite 5, zustand 5, @tanstack/react-query 5 (planned/optional: aladin-lite 3.x, uPlot 1.6) | |

## 2. External sources

| Source | Purpose | Protocol / endpoint | Auth | Rate / volume | Citation / licence | Fallback & caching | New-release detection |
|---|---|---|---|---|---|---|---|
| **SPHEREx @ IRSA** | Level-2 spectral images (frames, flags, variance, zodi, PSF, λ-map); future source catalogs/spectra | SIA v2 `https://irsa.ipac.caltech.edu/SIA?COLLECTION=spherex_qr&POS=CIRCLE ra dec r&TIME=...` [verify collection ids, e.g. QR vs DR]; TAP `https://irsa.ipac.caltech.edu/TAP` (`ivoa.obscore`, SPHEREx tables) [verify]; cutouts via IRSA cutout params on access URLs [verify]; IRSA Firefly-based SPHEREx tools as reference | none (public) | No published hard limit; we cap 4 concurrent, ≤ 2 req/s; full L2 frame ~ 2048² × several ext ≈ 50–80 MB [verify], cutouts KB–MB | NASA/IPAC IRSA + SPHEREx acknowledgement text from IRSA page [verify]; public domain (NASA) | cache cutouts 30–90 d; on outage show cached epochs + banner | Celery beat polls ObsCore `obs_release_date`/collection list every 6 h (§ARCHITECTURE 5) |
| **WISE / NEOWISE** (AllWISE, NEOWISE-R single exposures, unWISE, CatWISE2020, unTimely) | Long-baseline astrometric anchors (2010–2024), W1/W2 photometry, colours, time-resolved coadds | IRSA TAP tables `allwise_p3as_psd`, `catwise_2020`, `neowiser_p1bs_psd` [verify names]; IRSA SIA for images; unWISE cutouts `https://unwise.me/cutout_fits?...` [verify]; WiseView-style time-resolved unWISE | none | IRSA polite; NEOWISE single-exposure queries can be large: restrict radius ≤ 30″ | "This publication makes use of data products from WISE…/NEOWISE…" (IRSA text); Meisner et al. (unWISE), Marocco et al. 2021 (CatWISE) | cache cone results 7 d | Static (NEOWISE ended 2024); watch for unTimely/CatWISE updates |
| **Gaia DR3** (DR4 expected Dec 2026 [verify]) | Astrometric reference frame, μ/ϖ priors, XP spectra for ZPs, known-object identity | ESA TAP `https://gea.esac.esa.int/tap-server/tap` (`gaiadr3.gaia_source`); mirrors: CDS VizieR `I/355/gaiadr3`, ARI Heidelberg TAP | anonymous (limits) / registered account for big async jobs | anonymous row/time caps [verify]; we query per field (≤ 10⁴ rows) | ESA/Gaia/DPAC acknowledgement text; Gaia Collaboration 2016, 2023 | ESA → VizieR → cache; cache per HEALPix-9 cell 30 d | Admin-configured `gaia_release` profile; DR4 = new provider config |
| **2MASS** | ~1998–2001 anchor epoch (huge baseline), JHKs | IRSA TAP `fp_psc`; SIA images | none | light | 2MASS acknowledgement (UMass/IPAC, NASA/NSF) | cache | static |
| **Pan-STARRS DR2** | Optical astrometry/photometry (2010–2014) north of δ −30° | MAST Catalogs API `https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/mean?ra=&dec=&radius=` ; images `https://ps1images.stsci.edu/cgi-bin/ps1cutouts` | none | MAST polite (≤ 5 req/s) | PS1 acknowledgement, Chambers+2016 | cache | static |
| **SIMBAD** | Names, types, literature, RV, variability type | TAP `https://simbad.cds.unistra.fr/simbad/sim-tap`; astroquery.simbad | none | CDS: keep ≤ ~5 req/s or risk temporary ban [verify] | "This research has made use of the SIMBAD database, CDS, Strasbourg" (Wenger+2000) | cache 7 d | n/a |
| **VizieR / CDS XMatch** | Any catalog (UltracoolSheet, Backyard Worlds discoveries, variable catalogs VSX/GCVS) | TAP `https://tapvizier.cds.unistra.fr/TAPVizieR/tap`; XMatch `https://cdsxmatch.u-strasbg.fr/xmatch/api/v1/sync` | none | same CDS policy | Ochsenbein+2000; per-catalog citations | cache | n/a |
| **Sesame** | Name → coordinates (+ PM) for the coordinate box | `https://cds.unistra.fr/cgi-bin/nph-sesame/-ox/SNV?<name>` | none | light | CDS | cache 30 d | n/a |
| **Aladin Lite / HiPS / MOCServer / hips2fits** | Sky context imagery, footprints (MOC), quick cutouts of other surveys | HiPS from CDS (`https://alasky.cds.unistra.fr/...`); MOCServer `https://alasky.cds.unistra.fr/MocServer/query`; hips2fits `https://alasky.cds.unistra.fr/hips-image-services/hips2fits` | none | CDN-backed | Bonnarel+2000; Boch & Fernique 2014; HiPS IVOA standard | browser cache | MOCServer lists new HiPS (e.g. a SPHEREx HiPS) |
| **JPL Horizons** | Ephemerides of SSOs and of the SPHEREx spacecraft (observer position) | REST `https://ssd.jpl.nasa.gov/api/horizons.api`; astroquery.jplhorizons | none | JPL asks for **serial** requests (no parallel flooding) | JPL SSD citation | cache ephemerides per (object, span) 7 d | n/a |
| **JPL SBDB & SB Identification** | Orbit elements/uncertainties; "which known small bodies are in this field at time t" | `https://ssd-api.jpl.nasa.gov/sbdb.api`; `https://ssd-api.jpl.nasa.gov/sb_ident.api` | none | serial | JPL SSD | cache | n/a |
| **Minor Planet Center** | Known-object check (MPChecker), observations, orbit catalogs (MPCORB), distant-object lists | MPC API `https://data.minorplanetcenter.net/api/...` [verify]; MPChecker `https://minorplanetcenter.net/cgi-bin/checkmp.cgi`; MPCORB download (daily) | none (submission requires observatory code: **we never auto-submit**; we export ADES-format reports for the user) | polite | MPC | local MPCORB copy refreshed daily (≈ 1.4 M orbits, ~300 MB) | daily file timestamp |
| **IMCCE SkyBoT** | Fast cone search for known SSOs at epoch (incl. observer location) | `https://ssp.imcce.fr/webservices/skybot/api/conesearch.php`; astroquery.imcce.Skybot | none | polite | Berthier+2006 | cache per (frame) | n/a |
| Optional: NED, Euclid Q1, Rubin (future) | galaxies/z; future deep imaging | NED TAP; IRSA Euclid; Rubin TAP (when public) | per service | — | per service | — | provider plug-ins (Stretch) |

**Data volumes per typical request** (10′ FOV, 3 surveys): SPHEREx cutouts ~ 102 channels × 3 × ~100 KB ≈ 30 MB raw float32 (display WebP ≈ 2 MB); catalog cones ~ 1–5 MB. Full-frame SSO/Planet X jobs: ~ GBs (worker-side only).

## 3. Own public API (REST, OpenAPI 3.1, `/api/v1`, schema at `/api/v1/openapi.json`)

Conventions: JSON; angles in decimal deg (`RaDeg` in `[0, 360)`, `DecDeg` in `[-90, 90]`), rates in mas/yr or arcsec/hr (field name carries unit), times as ISO-UTC **and** `mjd_tdb`; errors RFC 9457 (`application/problem+json`); pagination `?cursor=&limit=`; async jobs return `202` + `job_id`. Auth: `HttpOnly` cookie session or `Authorization: Bearer <token>`; anonymous allowed for read-only viewer endpoints (FOV ≤ 15′; authenticated up to 60′).

### Implemented endpoints (MVP)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` *(root)* | Service liveness (`{"status":"ok","version":"0.1.0"}`) |
| POST | `/auth/register` | Register email/password account (`display_name` optional) → `201 UserRead` |
| POST | `/auth/login` | Login with email/password (sets session cookie + returns Bearer token) |
| POST | `/auth/logout` | Revoke session and clear cookie → `204` |
| GET | `/auth/me` | Authenticated user profile (`UserRead`) |
| POST | `/auth/tokens` | Mint scoped API token (`ApiTokenCreated`) |
| GET | `/auth/tokens` | List user API tokens (`list[ApiTokenRead]`) |
| DELETE | `/auth/tokens/{token_id}` | Revoke API token → `204` |
| GET | `/resolve?name=` | SIMBAD / Sesame target name → ICRS decimal coords (+PM) |
| GET | `/fields/frames?ra=&dec=&fov=&from=&to=&release=&bands=` | List available epochs/frames (`ra`, `dec` in decimal deg) |
| POST | `/cutouts` | Build multi-epoch cutout set (async `202` → `cutout_id`, `job_id`) |
| GET | `/cutouts/{cutout_id}` | Cutout metadata + per-frame URLs (`display_url`, `science_url`, `fits_url`, WCS, stretch) |
| GET | `/cutouts/{cutout_id}/frames/{frame_id}/display.webp` | 8-bit asinh-stretched WebP display tile |
| GET | `/cutouts/{cutout_id}/frames/{frame_id}/science.bin` | Little-endian `float32` science array (`X-Dtype`, `X-Shape` headers) |
| GET | `/cutouts/{cutout_id}/frames/{frame_id}/frame.fits` | Downloadable FITS cutout |
| POST | `/uploads` (`multipart/form-data`) | Upload FITS (`file` + optional `user_epoch`, reference field/upload, `project_id`) → `202` |
| GET | `/uploads/{upload_id}/report` | FITS validation & multi-epoch compatibility report |
| POST | `/analyses` | Run registration → extraction → suggestion on `fits_file_ids`, `cutout_id`, or `ra`/`dec` (+ optional `fov_arcmin`, `release`) (`202`) |
| GET | `/analyses/{analysis_id}/sources?bbox=&cursor=&limit=` | Extracted detections (`bbox=ra_min,dec_min,ra_max,dec_max`) |
| GET | `/analyses/{analysis_id}/suggestions` | Ranked moving/anomalous object suggestions with reasons |
| POST | `/projects/{project_id}/objects` | Select objects (`box` / `suggestion` / `catalog` / `manual`) → persistent labels (`201`) |
| POST | `/projects/{project_id}/tracks` | Cross-epoch associate + fit motion + classify (async `202`) |
| GET | `/objects/{object_id}` | Full object: measurements, preferred motion fit, classification + consensus, caveats |
| GET | `/objects/{object_id}/associations` | Per-epoch match hypotheses + alternatives |
| PATCH | `/objects/{object_id}/associations/{epoch}` | Accept/reject/reassign detection (`detection_id: int`) + trigger refit |
| GET | `/objects/{object_id}/spectrum?epoch=` | On-the-fly forced-PSF SED (`wavelength_um`, `flux_ujy`, `mjd_tdb`) |
| GET | `/objects/{object_id}/lightcurve` | Multi-epoch photometry + variability indices |
| GET | `/catalogs/overlay?cat=gaia\|catwise&ra=&dec=&fov=&epoch=` | Reference catalog sources propagated to epoch |
| POST | `/compare` | Launch A-vs-B FITS comparison (`a`, `b` `fits_file_id` UUIDs; async `202`) |
| GET | `/compare/{compare_id}` | Product URLs, header diff, source changes |
| GET | `/compare/{compare_id}/products/{product_name}` | Download `diff\|sum\|ratio.{fits,webp,bin}` or `score_corr.fits` |
| GET | `/jobs/{job_id}` ; `/jobs/{job_id}/events` (SSE) | Job status/progress (`0..1`) and SSE `progress` stream |
| GET / POST | `/projects` (`?cursor=&limit=` on `GET`) ; `GET / PATCH / DELETE /projects/{project_id}` | Project workspace CRUD (`?share_token=` / `X-Share-Token` supported on `GET /projects/{project_id}` and all read-only `/projects/{project_id}/*` endpoints) |
| GET / POST | `/projects/{project_id}/notes` ; `PATCH / DELETE /projects/{project_id}/notes/{note_id}` | Markdown notes |
| GET / POST | `/projects/{project_id}/findings` ; `PATCH / DELETE /projects/{project_id}/findings/{finding_id}` | Scientific findings |
| GET / POST | `/projects/{project_id}/views` ; `DELETE /projects/{project_id}/views/{view_id}` | Saved viewer URL bookmarks |
| GET / POST | `/projects/{project_id}/versions` ; `POST /projects/{project_id}/versions/{version}/restore` | Immutable project snapshots |
| POST / DELETE | `/projects/{project_id}/share` | Create/rotate or revoke revocable share link |
| POST | `/projects/{project_id}/exports` | Export job (`csv`, `json`, `fits`, `png`, `gif`, `ades`; async `202`) |
| GET | `/projects/{project_id}/exports/{export_id}` | Download completed export artifact (`export_id` = `job_id`) |
| GET | `/releases` | Known data releases & indexing status |
| GET | `/examples` | The 5 mandatory benchmark presets (`targets` list) |
| GET | `/oembed?url=&maxwidth=&maxheight=` | oEmbed iframe metadata |
| GET | `/render.gif?ra=&dec=&fov=&bands=&release=&from=&to=&fps=` | Cached animated multi-epoch GIF |
| GET | `/embed` *(root)* | Chrome-less cookie-less viewer (`?ra=&dec=&target=&fov=&mode=&bands=&release=&from=&to=&overlay=&track=`) |

### Deferred endpoints / capabilities (Phase 2 — not implemented in MVP)

- **Planet X search mode:** `POST /api/v1/planetx/runs`, `GET /api/v1/planetx/runs/{id}`, and `/candidates` are deferred to Phase 2 (`# PHASE2` in `backend/app/api/v1/__init__.py`).
- **OAuth / magic-link auth:** `/api/v1/auth/orcid` and email magic-link login are deferred to Phase 2.
- **Resumable uploads:** `tus` chunked upload protocol on `POST /api/v1/uploads` is deferred to Phase 2 (multipart only in MVP).
- **Additional catalog overlays:** `GET /api/v1/catalogs/overlay` currently supports `cat=gaia` and `cat=catwise` only (`2mass`, `panstarrs`, `simbad`, `sso` deferred).
- **Video export:** `mp4` format on `POST /api/v1/projects/{project_id}/exports` is deferred to Phase 2.
- **Note for Viewer integration:** `POST /api/v1/compare` requires registered `fits_file_id` UUIDs (e.g., from `POST /api/v1/uploads`), whereas `CutoutFrame` returns `frame_id`s; client-side epoch A-vs-B differencing uses `GET /api/v1/cutouts/{cutout_id}/frames/{frame_id}/science.bin`. Selecting/tracking objects (`POST /api/v1/projects/{project_id}/objects`) requires an existing `project_id`.

### 3.1 Examples

```http
GET /api/v1/fields/frames?ra=133.79513&dec=-7.24515&fov=10&bands=arr5,arr6
200 OK
{
  "field": {"ra": 133.79513, "dec": -7.24515, "fov_arcmin": 10.0, "healpix9": 1234567},
  "release": "spherex_qr_2026-10",
  "epochs": [
    {"epoch": "S1", "mjd_tdb_mean": 60850.41, "utc_mean": "2025-06-21T09:50:24Z",
     "n_frames": 34, "arrays": [5, 6], "lambda_um": [3.82, 5.00],
     "flagged_fraction": 0.031},
    {"epoch": "S2", "mjd_tdb_mean": 61031.77, "utc_mean": "2025-12-19T18:28:48Z",
     "n_frames": 36, "arrays": [5, 6], "lambda_um": [3.82, 5.00],
     "flagged_fraction": 0.044}
  ],
  "warnings": []
}
```
*(values illustrative)*

```http
POST /api/v1/projects/00000000-0000-0000-0000-000000000001/objects
{"analysis_id": "00000000-0000-0000-0000-000000000091", "mode": "box", "epoch": "S1",
 "box": {"ra_min": 133.790, "ra_max": 133.800, "dec_min": -7.250, "dec_max": -7.240}}
201 Created
{"objects": [{"id": "00000000-0000-0000-0000-000000000101", "label": "A",
              "ra": 133.7951, "dec": -7.2452, "crossmatch": null,
              "flux_ujy": {"arr6": 41.2}, "selection_mode": "box"}]}
```

```http
GET /api/v1/objects/00000000-0000-0000-0000-000000000101
{
  "id": "00000000-0000-0000-0000-000000000101",
  "label": "A",
  "public_id": "J085510.8-071442",
  "status": "active",
  "identity_confidence": 0.97,
  "measurements": [{"epoch": "S1", "source": "detection", "mjd_tdb": 60850.41,
                    "ra": 133.79510, "dec": -7.24521, "sig_ra_mas": 310.0,
                    "sig_dec_mas": 290.0, "corr_radec": 0.05, "band_group": "arr6",
                    "flux_ujy": 41.2, "flux_err_ujy": 2.1, "is_upper_limit": false,
                    "flags": 0, "association_confidence": 0.97}],
  "motion_fit": {"model": "linear", "ref_epoch_mjd_tdb": 60941.0,
                 "params": {"ra_deg": 133.7951, "dec_deg": -7.2452,
                            "pmra_masyr": -8090.0, "pmdec_masyr": 700.0},
                 "pm_total_masyr": 8120.0, "pm_total_err": 420.0,
                 "pa_deg": 274.9, "pa_err": 3.0, "plx_mas": null, "plx_err": null,
                 "chi2_red": 0.9, "dof": 2, "baseline_yr": 1.0, "is_preferred": true},
  "classification": {"top_class": "brown_dwarf", "top_prob": 0.83,
                     "probabilities": {"brown_dwarf": 0.83, "candidate_new_mover": 0.09, "star_pm": 0.05, "artifact": 0.03},
                     "evidence": ["mu = 8.1 +/- 0.4 arcsec/yr", "4.5 um excess; no Gaia counterpart"],
                     "consensus": {"verdict": "Consistent", "agreement_score": 1.0,
                                   "estimators": {"E1": "agree", "E2": "n/a (no catalog)", "E3": "mover", "E6": "brown_dwarf"},
                                   "disagreements": []}},
  "caveats": ["Confidence < 0.9 at epoch S3 (possible blend)"]
}
```
*(values illustrative, not results)*

```http
PATCH /api/v1/objects/00000000-0000-0000-0000-000000000101/associations/S3
{"action": "reassign", "detection_id": 88812, "reason": "visual: correct source is SW blob"}
200 OK  {"refit_job_id": "00000000-0000-0000-0000-000000000552", "decided_by": "user"}
```

```http
POST /api/v1/compare
{"a": "00000000-0000-0000-0000-000000000123",
 "b": "00000000-0000-0000-0000-000000000456",
 "ops": ["diff","sum","ratio","header_diff","sources"], "psf_match": "zogy"}
202 Accepted {"job_id": "00000000-0000-0000-0000-000000000077",
              "status": "queued",
              "compare_id": "00000000-0000-0000-0000-000000000009"}
```

### 3.2 Embed scheme
- `GET /embed?ra=&dec=&fov=&mode=&bands=&from=&to=` → chrome-less viewer page (same params as `/v`, see FRONTEND §4.3; accepts decimal deg or sexagesimal `ra`/`dec` or `target=`), cookie-less, `frame-ancestors *`.
- `GET /api/v1/oembed?url=https://<host>/v?ra=...` → `{"type":"rich","version":"1.0","html":"<iframe ...>","width":640,"height":480,"provider_name":"..."}`.
- Image endpoint for static embeds: `GET /api/v1/render.gif?ra=&dec=&fov=&bands=` (cached animated GIF of epochs; `ra`, `dec` in decimal deg).

### 3.3 Python client snippet
```python
import requests

resolved = requests.get("https://<host>/api/v1/resolve", params={"name": "WISE 0855"})
resolved.raise_for_status()
coords = resolved.json()

r = requests.get(
    "https://<host>/api/v1/fields/frames",
    params={"ra": coords["ra_deg"], "dec": coords["dec_deg"], "fov": 8, "bands": "arr6"},
)
r.raise_for_status()
for e in r.json()["epochs"]:
    print(e["epoch"], e["utc_mean"], e["n_frames"])
```
