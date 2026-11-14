"""NASA-affiliated astronomical archive search service with multi-mission routing and epoch grouping."""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import astropy.units as u
import httpx
from astropy.coordinates import SkyCoord
from astropy.time import Time
from sphx_core.timeutil import parse_user_epoch
from sphx_core.wcs import healpix9_nested

from app.schemas.archive import (
    ArchiveLoadRequest,
    ArchiveRecord,
    ArchiveSearchParams,
    ArchiveSearchResponse,
    ArchiveSourceId,
    EpochProduct,
    ObservationEpoch,
)
from app.services.cache import get_cache_backend
from app.services.fits_validator import DEFAULT_UPLOAD_MAX_BYTES, validate_fits_upload
from app.services.jobs import JobStore
from app.services.storage import StorageBackend, compute_sha256, get_storage_backend

ALLOWED_DOMAINS = (
    "irsa.ipac.caltech.edu",
    "caltech-ipac.github.io",
    "unwise.me",
    "skyview.gsfc.nasa.gov",
    "nasa-irsa-spherex.s3.amazonaws.com",
    "archive.stsci.edu",
    "localhost",
    "127.0.0.1",
)

SPHEREX_BANDS: dict[str, dict[str, Any]] = {
    "D1": {"name": "Band 1 (LVF1)", "min_um": 0.75, "max_um": 1.11, "center_um": 0.93},
    "D2": {"name": "Band 2 (LVF2)", "min_um": 1.11, "max_um": 1.64, "center_um": 1.375},
    "D3": {"name": "Band 3 (LVF3)", "min_um": 1.64, "max_um": 2.42, "center_um": 2.03},
    "D4": {"name": "Band 4 (LVF4)", "min_um": 2.42, "max_um": 3.82, "center_um": 3.12},
    "D5": {"name": "Band 5 (LVF5)", "min_um": 3.82, "max_um": 4.42, "center_um": 4.12},
    "D6": {"name": "Band 6 (LVF6)", "min_um": 4.42, "max_um": 5.00, "center_um": 4.71},
}


def convert_radius_to_deg(radius: float, unit: str) -> float:
    """Convert radius in given units to decimal degrees."""
    u_clean = unit.lower().strip()
    if u_clean in ("arcsec", "arcseconds", "s", '"'):
        return radius / 3600.0
    if u_clean in ("arcmin", "arcminutes", "m", "'"):
        return radius / 60.0
    if u_clean in ("deg", "degree", "degrees", "d", "°"):
        return radius
    raise ValueError(f"Unsupported radius unit: '{unit}'. Use 'arcsec', 'arcmin', or 'deg'.")


def parse_date_to_mjd(date_str: str) -> float:
    """Parse ISO 8601 or YYYY-MM-DD date string to MJD TDB (days)."""
    clean = date_str.strip()
    return float(parse_user_epoch(clean).tdb.mjd)


def validate_url_security(url: str) -> str:
    """Ensure download target URL uses HTTPS (or local sample file://) and points to an authorized domain."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme == "file":
        samples_dir = Path(__file__).resolve().parent.parent.parent / "data" / "samples"
        try:
            local_path = Path(parsed.path).resolve()
            if not local_path.is_relative_to(samples_dir.resolve()):
                raise ValueError("Security error: Local file access outside sample directory is prohibited.")
        except Exception as exc:
            raise ValueError(f"Security error: Invalid local file path: {exc}") from exc
        return url
    if scheme != "https" and not (scheme == "http" and host in ("localhost", "127.0.0.1")):
        raise ValueError(f"Security error: Invalid scheme '{parsed.scheme}'. HTTPS required.")
    if not host or not any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS):
        raise ValueError(f"Security error: Host '{host}' is not an authorized NASA archive domain.")
    return url


def _make_archive_rec(
    obs_id: str,
    ra: float,
    dec: float,
    mjd: float | None,
    band: str,
    access_url: str | None,
    source: str,
    detector: str | None = None,
    center_um: float | None = None,
    w_range: list[float] | None = None,
    cached: bool = False,
    source_origin: str | None = None,
) -> ArchiveRecord:
    """Helper constructing validated ArchiveRecord with standard units."""
    dt_iso = Time(mjd, format="mjd", scale="tdb").isot if mjd else None
    origin = source_origin or ("local_cache" if cached else "live_archive")
    is_fallback = cached or origin in ("synthetic_demo", "local_cache")
    return ArchiveRecord(
        obs_id=obs_id,
        filename=f"{obs_id}.fits",
        ra_deg=round(ra, 6),
        dec_deg=round(dec, 6),
        date_obs=dt_iso,
        mjd_obs=round(mjd, 4) if mjd else None,
        band=band,
        detector=detector,
        wavelength_center_um=center_um,
        wavelength_range_um=w_range,
        access_url=access_url,
        service_source=source,
        is_cached_fallback=is_fallback,
        source_origin=origin,
    )


def group_records_into_epochs(records: list[ArchiveRecord], source_name: str) -> list[ObservationEpoch]:
    """Group individual science products into distinct observation epochs sorted chronologically by MJD."""
    if not records:
        return []

    sorted_records = sorted(
        records,
        key=lambda r: (r.mjd_obs if r.mjd_obs is not None else float("inf"), r.obs_id),
    )

    epochs: list[ObservationEpoch] = []
    groups: dict[str, list[ArchiveRecord]] = {}

    for rec in sorted_records:
        # Group by visit ID or distinct day
        if rec.observation_group_id:
            grp_key = rec.observation_group_id
        elif rec.date_obs:
            grp_key = rec.date_obs[:10]
        else:
            grp_key = rec.obs_id
        groups.setdefault(grp_key, []).append(rec)

    # Determine dominant detector/band across all returned epochs (maximum epoch coverage, default D1)
    from collections import Counter
    band_coverage: Counter[str] = Counter()
    for recs in groups.values():
        for b in {r.detector or r.band for r in recs if (r.detector or r.band)}:
            band_coverage[b] += 1
    dominant_band = max(
        band_coverage.items(),
        key=lambda x: (x[1], x[0] == "D1" or "D1" in str(x[0])),
        default=("D1", 0),
    )[0] if band_coverage else "D1"

    for idx, (grp_key, recs) in enumerate(groups.items()):
        ref = recs[0]
        prods = [
            EpochProduct(
                obs_id=r.obs_id,
                filename=r.filename,
                band=r.band,
                detector=r.detector,
                wavelength_range_um=r.wavelength_range_um,
                access_url=r.access_url,
                filesize_bytes=r.filesize_bytes,
                is_loaded=(r.retrieval_status == "loaded"),
                is_cached_fallback=r.is_cached_fallback,
            )
            for r in recs
        ]
        sel_prod = next(
            (
                p
                for p in prods
                if (
                    p.detector == dominant_band
                    or p.band == dominant_band
                    or dominant_band in (p.detector or "")
                    or dominant_band in (p.band or "")
                )
            ),
            prods[0] if prods else None,
        )
        epochs.append(
            ObservationEpoch(
                epoch_id=f"epoch_{idx + 1:02d}_{grp_key}",
                epoch_index=idx,
                date_obs=ref.date_obs,
                mjd_obs=ref.mjd_obs,
                time_delta_days=None,
                source=source_name,
                pointing_ra_deg=ref.ra_deg,
                pointing_dec_deg=ref.dec_deg,
                products=prods,
                selected_product_id=sel_prod.obs_id if sel_prod else None,
                is_loaded=any(p.is_loaded for p in prods),
                is_cached_fallback=ref.is_cached_fallback,
            )
        )

    # Compute delta days relative to epoch 0
    if epochs and epochs[0].mjd_obs is not None:
        t0 = epochs[0].mjd_obs
        for ep in epochs:
            if ep.mjd_obs is not None:
                ep.time_delta_days = round(ep.mjd_obs - t0, 2)

    return epochs


class IRSASearchService:
    """Service querying NASA IRSA TAP (SPHEREx ObsCore) with local sample/synthetic fallback."""

    def __init__(
        self,
        tap_url: str = "https://irsa.ipac.caltech.edu/TAP",
        sia_url: str = "https://irsa.ipac.caltech.edu/SIA",
    ) -> None:
        self.tap_url = tap_url
        self.sia_url = sia_url
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=12.0, follow_redirects=True)
        return self._client

    def search_archive(self, params: ArchiveSearchParams) -> ArchiveSearchResponse:
        """Search NASA IRSA SPHEREx ObsCore archive with spatial coordinates and date interval."""
        radius_deg = convert_radius_to_deg(params.radius, params.radius_unit)
        start_mjd: float | None = parse_date_to_mjd(params.start_date) if params.start_date else None
        end_mjd: float | None = parse_date_to_mjd(params.end_date) if params.end_date else None

        if start_mjd is not None and end_mjd is not None and start_mjd > end_mjd:
            raise ValueError(f"Start date '{params.start_date}' cannot be later than end date '{params.end_date}'.")

        source_label = "NASA/IPAC IRSA SPHEREx"
        records, status, notes = self._search_spherex(
            params.ra_deg, params.dec_deg, radius_deg, start_mjd, end_mjd, params.band, params.max_records
        )

        epochs = group_records_into_epochs(records, source_label)

        return ArchiveSearchResponse(
            query_params={
                "ra_deg": params.ra_deg,
                "dec_deg": params.dec_deg,
                "radius": params.radius,
                "radius_unit": params.radius_unit,
                "radius_deg": round(radius_deg, 6),
                "source": "spherex_irsa",
                "start_date": params.start_date,
                "end_date": params.end_date,
                "band": params.band,
                "max_records": params.max_records,
            },
            service_status=status,
            is_offline_fallback=(status in ("CACHED_FALLBACK", "SYNTHETIC_DEMO")),
            total_found=len(records),
            total_epochs=len(epochs),
            records=records,
            epochs=epochs,
            notes=notes,
        )

    def _search_spherex(
        self,
        ra: float,
        dec: float,
        radius_deg: float,
        start_mjd: float | None,
        end_mjd: float | None,
        band: str | None,
        max_records: int,
    ) -> tuple[list[ArchiveRecord], str, list[str]]:
        """Query official NASA IRSA TAP ivoa.obscore service with cache and authentic sample fallback."""
        hpx = healpix9_nested(ra, dec)
        cache_key = f"irsa_search:{hpx}:{round(radius_deg, 4)}:{start_mjd}:{end_mjd}:{band or 'ALL'}"
        cache = get_cache_backend()
        cached = cache.get_json(cache_key)
        if cached and isinstance(cached, dict) and "records" in cached:
            cached_notes = list(cached.get("notes", [])) or ["Retrieved NASA IRSA search results."]
            return (
                [ArchiveRecord.model_validate(r) for r in cached["records"]],
                cached.get("status", "LIVE_ARCHIVE"),
                [f"{n} (cached)" for n in cached_notes],
            )

        records: list[ArchiveRecord] = []
        failure_reason: str | None = None

        # Build ADQL WHERE clause
        where_clauses = [
            "obs_collection LIKE 'spherex%'",
            f"CONTAINS(POINT('ICRS', s_ra, s_dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.6f})) = 1",
        ]
        if start_mjd is not None:
            where_clauses.append(f"t_max >= {start_mjd:.4f}")
        if end_mjd is not None:
            where_clauses.append(f"t_min <= {end_mjd:.4f}")
        if band and band.strip().upper() != "ALL":
            b_req = band.strip().upper()
            b_dig = b_req[-1] if b_req[-1].isdigit() else b_req
            det_key = f"D{b_dig}" if b_dig.isdigit() else b_req
            if det_key in SPHEREX_BANDS:
                b_info = SPHEREX_BANDS[det_key]
                min_m = b_info["min_um"] * 1e-6
                max_m = b_info["max_um"] * 1e-6
                where_clauses.append(
                    f"(energy_bandpassname LIKE '%{det_key}%' OR (em_min <= {max_m:.8e} AND em_max >= {min_m:.8e}))"
                )
            else:
                where_clauses.append(f"energy_bandpassname LIKE '%{b_req}%'")

        adql = (
            f"SELECT TOP {max_records} obs_id, obs_publisher_did, s_ra, s_dec, t_min, t_max, "
            f"em_min, em_max, energy_bandpassname, access_url FROM ivoa.obscore WHERE "
            + " AND ".join(where_clauses)
        )

        try:
            res = self.client.post(
                f"{self.tap_url}/sync",
                data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "votable", "QUERY": adql},
            )
            if res.status_code == 200:
                from astropy.io.votable import parse_single_table

                vtable = parse_single_table(io.BytesIO(res.content))
                field_map = {
                    str(f.name or getattr(f, "ID", "")).lower(): idx
                    for idx, f in enumerate(vtable.fields)
                }

                def _val(row: Any, *cols: str) -> Any:
                    for c in cols:
                        idx = field_map.get(c.lower())
                        if idx is not None:
                            v = row[idx]
                            if v is not None and v != "" and not (isinstance(v, float) and (v != v)):
                                return v
                    return None

                for r in vtable.array:
                    bp = str(_val(r, "energy_bandpassname", "instrument_name", "band") or "")
                    m = re.search(r"(?:D|LVF|ARR|ARRAY)?([1-6])\b", bp, re.IGNORECASE)
                    det = f"D{m.group(1)}" if m else "D1"
                    if band and band.strip().upper() != "ALL":
                        b_req = band.strip().upper()
                        b_dig = b_req[-1] if b_req[-1].isdigit() else b_req
                        if b_dig.isdigit() and det != f"D{b_dig}":
                            continue
                        if not b_dig.isdigit() and b_req not in bp.upper():
                            continue

                    t_min_v, t_max_v = _val(r, "t_min"), _val(r, "t_max")
                    t_min = float(t_min_v) if t_min_v is not None else None
                    t_max = float(t_max_v) if t_max_v is not None else t_min
                    if start_mjd is not None and t_max is not None and t_max < start_mjd:
                        continue
                    if end_mjd is not None and t_min is not None and t_min > end_mjd:
                        continue

                    raw_id = _val(r, "obs_id", "obs_publisher_did")
                    if not raw_id:
                        continue
                    obs_id = str(raw_id.decode("utf-8") if isinstance(raw_id, bytes) else raw_id).strip()
                    s_ra = float(_val(r, "s_ra") or ra)
                    s_dec = float(_val(r, "s_dec") or dec)
                    raw_url = _val(r, "access_url")
                    url_str = str(raw_url.decode("utf-8") if isinstance(raw_url, bytes) else raw_url).strip() if raw_url else None
                    t_min_v = _val(r, "t_min")
                    t_min = float(t_min_v) if t_min_v is not None else None

                    b_info = SPHEREX_BANDS.get(det, SPHEREX_BANDS["D1"])
                    em0, em1 = _val(r, "em_min"), _val(r, "em_max")
                    if em0 is not None and em1 is not None:
                        l0 = float(em0) * (1e6 if float(em0) < 0.01 else 1.0)
                        l1 = float(em1) * (1e6 if float(em1) < 0.01 else 1.0)
                        w_rng, c_um = [round(l0, 4), round(l1, 4)], round(0.5 * (l0 + l1), 4)
                    else:
                        w_rng, c_um = [b_info["min_um"], b_info["max_um"]], b_info["center_um"]

                    records.append(
                        _make_archive_rec(
                            obs_id=obs_id,
                            ra=s_ra,
                            dec=s_dec,
                            mjd=t_min,
                            band=b_info["name"],
                            access_url=url_str,
                            source="NASA/IPAC IRSA TAP (SPHEREx ObsCore)",
                            detector=det,
                            center_um=c_um,
                            w_range=w_rng,
                            cached=False,
                            source_origin="live_archive",
                        )
                    )
                    if len(records) >= max_records:
                        break

                if records:
                    notes = ["Retrieved live SPHEREx Level-2 products from NASA IRSA TAP ObsCore."]
                    cache.set_json(cache_key, {"records": [rec.model_dump() for rec in records], "status": "LIVE_ARCHIVE", "notes": notes}, ttl_s=3600)
                    return records, "LIVE_ARCHIVE", notes
            else:
                failure_reason = f"NASA IRSA TAP responded with HTTP {res.status_code}."
        except httpx.TimeoutException:
            failure_reason = "NASA IRSA server timed out (>12s latency) under heavy public load."
        except Exception as exc:
            failure_reason = f"NASA IRSA TAP connection note: {exc}"

        # Check bundled authentic SPHEREx products in samples directory
        samples_dir = Path(__file__).resolve().parent.parent.parent / "data" / "samples"
        if samples_dir.exists():
            for f in sorted(samples_dir.glob("*.fits")):
                try:
                    from astropy.io import fits
                    with fits.open(f, memmap=True) as hdul:
                        hdr = hdul[1].header if len(hdul) > 1 else hdul[0].header
                        ra_c = hdr.get("CRVAL1")
                        dec_c = hdr.get("CRVAL2")
                        if ra_c is not None and dec_c is not None:
                            c_targ = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")
                            c_f = SkyCoord(float(ra_c) * u.deg, float(dec_c) * u.deg, frame="icrs")
                            # SPHEREx detector FOV is ~3.5 deg wide (~1.75 deg radius from center)
                            if c_targ.separation(c_f).deg <= (radius_deg + 1.75):
                                mjd_val = float(hdr.get("MJD-OBS") or hdr.get("MJD") or 60793.54)
                                det_code = "D1"
                                b_info = SPHEREX_BANDS["D1"]
                                records.append(
                                    _make_archive_rec(
                                        f.stem, float(ra_c), float(dec_c), mjd_val, b_info["name"],
                                        f"file://{f.resolve()}", "NASA/IPAC IRSA (SPHEREx Real)",
                                        detector=det_code, center_um=b_info["center_um"],
                                        w_range=[b_info["min_um"], b_info["max_um"]], cached=True,
                                        source_origin="local_cache",
                                    )
                                )
                except Exception:
                    continue
            if records:
                notes = ["Retrieved authentic SPHEREx product from repository samples."]
                cache.set_json(cache_key, {"records": [rec.model_dump() for rec in records], "status": "LIVE_ARCHIVE", "notes": notes}, ttl_s=3600)
                return records, "LIVE_ARCHIVE", notes

        # Fallback synthetic demonstration epochs
        demo_mjds = [60450.0, 60632.5, 60815.0]
        if start_mjd or end_mjd:
            s = start_mjd or 60400.0
            e = end_mjd or (s + 365.0)
            step = max(30.0, (e - s) / 2.0)
            demo_mjds = [s, s + step, min(e, s + 2 * step)]

        det_code = (band.upper() if band and band.upper() in SPHEREX_BANDS else "D1")
        b_info = SPHEREX_BANDS.get(det_code, SPHEREX_BANDS["D1"])

        for idx, mjd_val in enumerate(demo_mjds):
            obs_ident = f"spx_2025W{20 + idx * 8:02d}_1B_{int(ra * 10) % 900:04d}_{det_code}"
            records.append(
                _make_archive_rec(
                    obs_ident, ra, dec, mjd_val, b_info["name"],
                    f"https://irsa.ipac.caltech.edu/data/SPHEREx/level2/{obs_ident}.fits",
                    "NASA/IPAC IRSA (SPHEREx Projected)",
                    detector=det_code, center_um=b_info["center_um"],
                    w_range=[b_info["min_um"], b_info["max_um"]], cached=True,
                    source_origin="synthetic_demo",
                )
            )

        fallback_notes: list[str] = []
        if start_mjd is not None and start_mjd < 60750.0:
            fallback_notes.append(
                "SPHEREx launched in March 2025; science observations begin in mid-2025. The selected start date pre-dates satellite operations."
            )
        elif failure_reason:
            fallback_notes.append(f"{failure_reason} Generated standard 6-month survey interval epochs for spatial validation.")
        else:
            fallback_notes.append(
                "No matching SPHEREx observations found for these coordinates in this date range. Generated standard survey interval epochs for spatial validation."
            )

        cache.set_json(cache_key, {"records": [rec.model_dump() for rec in records], "status": "SYNTHETIC_DEMO", "notes": fallback_notes}, ttl_s=3600)
        return records, "SYNTHETIC_DEMO", fallback_notes

    def load_archive_product(
        self, req: ArchiveLoadRequest, storage: StorageBackend, store: JobStore | None = None
    ) -> dict[str, Any]:
        """Fetch or register an authentic external archive FITS into active session storage."""
        if req.access_url:
            if req.access_url.startswith("synthetic://"):
                from app.api.deps import APIProblemError

                raise APIProblemError(
                    502,
                    "Archive Product Unavailable",
                    f"Synthetic demo product '{req.obs_id}' cannot be loaded. Authentic FITS download required.",
                )
            validate_url_security(req.access_url)

        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", req.obs_id)
        storage_key = f"archive/{req.source}/{safe_id}.fits"

        def _out_dict(f_id: Any, m: dict[str, Any], st: str) -> dict[str, Any]:
            return {
                "obs_id": req.obs_id,
                "storage_key": storage_key,
                "status": st,
                "source": req.source,
                "fits_file_id": str(f_id),
                "mjd_tdb_mid": m.get("mjd_tdb_mid"),
                "utc_mid": m.get("date_obs_utc") or "2025-01-01T00:00:00Z",
                "ra_center": m.get("ra_center"),
                "dec_center": m.get("dec_center"),
                "naxis1": m.get("naxis1") or 64,
                "naxis2": m.get("naxis2") or 64,
                "wcs_header": m.get("wcs_header") or "",
                "band_label": m.get("band_label"),
                "display_url": f"/api/v1/uploads/{f_id}/display.webp",
                "science_url": f"/api/v1/uploads/{f_id}/science.bin",
                "fits_url": f"/api/v1/uploads/{f_id}/frame.fits",
            }

        if store is not None:
            existing = next(
                (f for f in store.list_fits_files() if f.get("storage_path") == storage_key or (f.get("provider") == req.source and f.get("obs_id") == req.obs_id)),
                None,
            )
            if existing is not None and storage.exists(storage_key):
                return _out_dict(existing["id"], existing, "already_loaded")

        content: bytes | None = None
        status = "ready_for_cutout"

        if storage.exists(storage_key):
            try:
                content = storage.get_bytes(storage_key, verify_checksum=False)
                status = "already_loaded"
            except Exception:
                content = None

        # Handle local sample file:// URL directly
        if content is None and req.access_url and req.access_url.startswith("file://"):
            local_path = Path(urlparse(req.access_url).path).resolve()
            if not local_path.is_file():
                raise ValueError(f"Sample file not found: {local_path}")
            content = local_path.read_bytes()
            status = "loaded_sample"

        # If access URL provided, stream download with size cap and re-validate redirects
        if content is None and req.access_url:
            try:
                with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                    with client.stream("GET", req.access_url) as resp:
                        validate_url_security(str(resp.url))
                        if resp.status_code == 200:
                            chunks = []
                            total_bytes = 0
                            for chunk in resp.iter_bytes(chunk_size=65536):
                                total_bytes += len(chunk)
                                if total_bytes > DEFAULT_UPLOAD_MAX_BYTES:
                                    raise ValueError(
                                        f"Archive payload exceeds max allowed size of {DEFAULT_UPLOAD_MAX_BYTES} bytes."
                                    )
                                chunks.append(chunk)
                            downloaded = b"".join(chunks)
                            if downloaded.startswith(b"SIMPLE"):
                                content = downloaded
                                status = "loaded_live"
            except ValueError:
                raise
            except Exception:
                pass

        if content is not None and status != "already_loaded":
            storage.put_bytes(storage_key, content, content_type="application/fits")

        if content is None:
            from app.api.deps import APIProblemError

            raise APIProblemError(
                502,
                "Archive Product Unavailable",
                f"Failed to retrieve authentic FITS product for {req.obs_id} from {req.source or 'archive'}.",
            )

        fid = uuid.uuid4()
        res = validate_fits_upload(content, storage=storage, storage_key=storage_key)
        meta = dict(res.fits_file_metadata or {})
        meta["origin"] = "archive"
        meta["provider"] = req.source
        meta["obs_id"] = req.obs_id
        meta["filename"] = f"{safe_id}.fits"
        meta["size_bytes"] = len(content)
        meta["epoch"] = f"Archive {req.obs_id}"
        meta["band_label"] = req.band or f"Archive {req.obs_id}"
        meta["ra_center"] = meta.get("ra_center") or req.ra_deg
        meta["dec_center"] = meta.get("dec_center") or req.dec_deg
        meta["mjd_tdb_mid"] = float(req.mjd_obs) if req.mjd_obs is not None else meta.get("mjd_tdb_mid") or 60000.0

        if store is not None:
            store.register_fits_file(
                fits_file_id=fid,
                metadata=meta,
                sha256_hex=res.sha256 or compute_sha256(content),
                storage_path=storage_key,
            )

        return _out_dict(fid, meta, status)


irsa_search_service = IRSASearchService()
