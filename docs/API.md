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
| Frontend | React 18, TypeScript 5, Vite 5, aladin-lite 3.x, uPlot 1.6, zustand 4, @tanstack/react-query 5, openapi-typescript 7 | |

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

## 3. Own public API (REST, OpenAPI 3.1, `/api/v1`)

Conventions: JSON; angles in deg, rates in mas/yr or arcsec/hr (field name carries unit), times as ISO-UTC **and** `mjd_tdb`; errors RFC 9457; pagination `?cursor=`; async jobs return `202` + `job_id`. Auth: cookie session or `Authorization: Bearer <token>`; anonymous allowed for read-only viewer endpoints (rate-limited).

| Method | Path | Purpose |
|---|---|---|
| GET | `/resolve?name=` | Sesame name → coords (+PM) |
| GET | `/fields/frames?ra=&dec=&fov=&from=&to=&release=&bands=` | list available epochs/frames |
| POST | `/cutouts` | build cutout set (async) |
| GET | `/cutouts/{id}` | metadata + frame URLs (display/science/FITS) |
| POST | `/uploads` (multipart / tus resumable) | upload FITS |
| GET | `/uploads/{id}/report` | compatibility report |
| POST | `/analyses` | run pipeline (register→extract→suggest) on a frame set (async) |
| GET | `/analyses/{id}/sources?bbox=` | extracted sources |
| GET | `/analyses/{id}/suggestions` | suggested objects with reasons |
| POST | `/projects/{pid}/objects` | select objects (box / list / suggestion ids) → persistent labels |
| POST | `/projects/{pid}/tracks` | associate + fit + classify (async) |
| GET | `/objects/{oid}` | full object: measurements, fits, classification, consensus |
| GET | `/objects/{oid}/associations` | per-epoch matches + alternatives |
| PATCH | `/objects/{oid}/associations/{epoch}` | accept/reject/reassign (manual correction) |
| GET | `/objects/{oid}/spectrum?epoch=` | on-the-fly SED |
| GET | `/objects/{oid}/lightcurve` | photometry vs time |
| GET | `/catalogs/overlay?cat=gaia&ra=&dec=&fov=&epoch=` | overlay sources propagated to epoch |
| POST | `/compare` | A vs B job |
| GET | `/compare/{id}` | products, header diff, source changes |
| POST | `/planetx/runs` | start Planet X run (logged-in) |
| GET | `/planetx/runs/{id}` / `/candidates` | status, efficiency, candidates |
| GET | `/jobs/{id}` ; `/jobs/{id}/events` (SSE) | job status/progress |
| CRUD | `/projects`, `/projects/{pid}/notes`, `/findings`, `/views`, `/versions`, `/share` | workspace |
| POST | `/projects/{pid}/exports` | CSV/JSON/FITS/PNG/GIF/MP4/ADES |
| GET | `/releases` | known data releases & status |
| GET | `/oembed?url=` | embed metadata |
| GET | `/examples` | the 5 presets + latest validation results |

### 3.1 Examples

```http
GET /api/v1/fields/frames?ra=133.79513&dec=-7.24515&fov=10&bands=arr5,arr6
200 OK
{
  "field": {"ra": 133.79513, "dec": -7.24515, "fov_arcmin": 10, "healpix9": 1234567},
  "release": "spherex_qr_2026-10",
  "epochs": [
    {"epoch": "S1", "mjd_tdb_mean": 60850.41, "utc_mean": "2025-06-21T09:50:24Z",
     "n_frames": 34, "arrays": [5, 6], "lambda_um": [3.82, 5.00],
     "flagged_fraction": 0.031},
    {"epoch": "S2", "mjd_tdb_mean": 61031.77, "utc_mean": "2025-12-19T18:28:48Z", "n_frames": 36,
     "arrays": [5, 6], "lambda_um": [3.82, 5.00], "flagged_fraction": 0.044}
  ],
  "warnings": ["High-PM target: position propagated per epoch (see SCIENCE §F.3)."]
}
```
*(values illustrative)*

```http
POST /api/v1/projects/7f3c.../objects
{"analysis_id": "an_91", "mode": "box", "epoch": "S1",
 "box": {"ra_min": 133.790, "ra_max": 133.800, "dec_min": -7.250, "dec_max": -7.240}}
201 Created
{"objects": [{"id": "obj_1", "label": "A", "ra": 133.7951, "dec": -7.2452,
              "crossmatch": null, "flux_ujy": {"arr6": 41.2}, "selection_mode": "box"}]}
```

```http
GET /api/v1/objects/obj_1
{
  "label": "A",
  "measurements": [{"epoch": "S1", "mjd_tdb": 60850.41, "ra": 133.79510, "dec": -7.24521,
                    "sig_ra_mas": 310, "sig_dec_mas": 290, "corr": 0.05, "flags": [],
                    "association_confidence": 0.97}],
  "motion_fit": {"model": "linear", "ref_epoch_mjd_tdb": 60941.0,
                 "pmra_masyr": -8090, "pmdec_masyr": 700, "pm_total_masyr": 8120, "pm_err_masyr": 420,
                 "pa_deg": 274.9, "pa_err_deg": 3.0, "chi2_red": 0.9, "dof": 2, "baseline_yr": 1.0},
  "classification": {"top": "brown_dwarf", "p": 0.83,
                     "probabilities": {"brown_dwarf": 0.83, "candidate_new_mover": 0.09, "star_pm": 0.05, "artifact": 0.03},
                     "evidence": ["μ = 8.1 ± 0.4 ″/yr (E3), E1 dipole 7.2σ", "4.5 µm excess; < 3σ at < 3 µm", "no Gaia counterpart"]},
  "consensus": {"verdict": "Consistent", "estimators": {"E1": "agree", "E2": "n/a (no catalog)", "E3": "agree", "E4": "agree"}},
  "caveats": ["Confidence < 0.9 at epoch S3 (possible blend)"]
}
```
*(values illustrative, not results)*

```http
PATCH /api/v1/objects/obj_1/associations/S3
{"action": "reassign", "detection_id": "det_88812", "reason": "visual: correct source is SW blob"}
200 OK  {"refit_job_id": "job_552", "decided_by": "user"}
```

```http
POST /api/v1/compare
{"a": "fits_123", "b": "fits_456", "ops": ["diff","sum","ratio","header_diff","sources"], "psf_match": "zogy"}
202 Accepted {"job_id": "job_77", "compare_id": "cmp_9"}
```

### 3.2 Embed scheme
- `GET /embed?ra=&dec=&fov=&mode=&bands=&from=&to=` → chrome-less viewer page (same params as `/v`, see FRONTEND §4.3), cookie-less, `frame-ancestors *`.
- `GET /api/v1/oembed?url=https://<host>/v?ra=...` → `{"type":"rich","html":"<iframe ...>","width":640,"height":480,"provider_name":"..."}`.
- Image endpoint for static embeds: `GET /api/v1/render.gif?ra=&dec=&fov=&bands=` (cached animated GIF of epochs).

### 3.3 Python client snippet
```python
import requests
r = requests.get("https://<host>/api/v1/fields/frames",
                 params={"ra": "08:55:10.831", "dec": "-07:14:42.53", "fov": 8, "bands": "arr6"})
r.raise_for_status()
for e in r.json()["epochs"]:
    print(e["epoch"], e["utc_mean"], e["n_frames"])
```
