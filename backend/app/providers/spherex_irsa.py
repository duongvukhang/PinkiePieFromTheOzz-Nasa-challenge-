"""SPHEREx @ IRSA archive provider using astroquery/pyvo SIAv2, TAP, and YAML profiles."""

from __future__ import annotations

import base64
import hashlib
import io
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import astropy.units as u
import httpx
import numpy as np
import yaml
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.nddata import Cutout2D
from astropy.time import Time
from astropy.wcs import WCS
from astroquery.ipac.irsa import Irsa
from astroquery.ipac.irsa.core import IrsaClass, conf as irsa_conf
from numpy.typing import NDArray

IRSA_QUERY_TIMEOUT_S = 10
irsa_conf.timeout = IRSA_QUERY_TIMEOUT_S
Irsa.TIMEOUT = IRSA_QUERY_TIMEOUT_S

from app.db.flags import DetectionFlag
from app.providers.base import (
    CircuitBreaker,
    FrameRef,
    Provider,
    ProviderQueryError,
    ProviderUnavailableError,
    RateLimiter,
    ReleaseDescriptor,
    _parse_date,
    _parse_s_region,
    _scalar,
    _to_table,
    call_with_retry,
    default_registry,
)
from app.services.storage import compute_sha256

PROFILES_DIR = Path(__file__).parent / "release_profiles"
DEFAULT_PROFILE_NAME = "spherex_qr_v1"
T = TypeVar("T")


@dataclass(frozen=True)
class ReleaseProfile:
    """Versioned YAML configuration isolating SPHEREx release format details."""

    profile_name: str
    provider: str
    mission: str
    collection_id: str
    release_aliases: tuple[str, ...]
    sia_url: str
    tap_url: str
    obscore_table: str
    max_concurrent: int
    requests_per_sec: float
    max_retries: int
    base_backoff_s: float
    max_backoff_s: float
    cutout_mode: str
    center_param: str
    size_param: str
    size_unit: str
    cutout_oversize_factor: float
    hdu_layout: dict[str, str]
    required_hdus: tuple[str, ...]
    header_keys: dict[str, tuple[str, ...]]
    arrays: dict[int, tuple[str, tuple[float, float]]]
    flag_bits: dict[int, str]
    mask_flags: frozenset[str]
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, source: str | Path) -> ReleaseProfile:
        """Load a release profile from a profile name or YAML path."""
        path = Path(source)
        if not path.suffix and not path.exists():
            path = PROFILES_DIR / f"{source}.yaml"
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        ep, rate, cut = (
            data.get("endpoints", {}),
            data.get("rate_limit", {}),
            data.get("cutout", {}),
        )
        arrays = {
            int(k): (str(v["band"]), (float(v["lambda_um"][0]), float(v["lambda_um"][1])))
            for k, v in data.get("arrays", {}).items()
        }
        return cls(
            profile_name=str(data["profile_name"]),
            provider=str(data.get("provider", "spherex_irsa")),
            mission=str(data.get("mission", "SPHEREx")),
            collection_id=str(data["collection_id"]),
            release_aliases=tuple(str(x) for x in data.get("release_aliases", ())),
            sia_url=str(ep.get("sia_url", "https://irsa.ipac.caltech.edu/SIA")),
            tap_url=str(ep.get("tap_url", "https://irsa.ipac.caltech.edu/TAP")),
            obscore_table=str(ep.get("obscore_table", "ivoa.obscore")),
            max_concurrent=int(rate.get("max_concurrent", 4)),
            requests_per_sec=float(rate.get("requests_per_sec", 2.0)),
            max_retries=int(rate.get("max_retries", 3)),
            base_backoff_s=float(rate.get("base_backoff_s", 0.5)),
            max_backoff_s=float(rate.get("max_backoff_s", 30.0)),
            cutout_mode=str(cut.get("mode", "auto")),
            center_param=str(cut.get("center_param", "center")),
            size_param=str(cut.get("size_param", "size")),
            size_unit=str(cut.get("size_unit", "deg")),
            cutout_oversize_factor=float(cut.get("oversize_factor", 1.5)),
            hdu_layout={str(k): str(v) for k, v in data.get("hdu_layout", {}).items()},
            required_hdus=tuple(str(x) for x in data.get("required_hdus", ())),
            header_keys={
                str(k): tuple(str(h) for h in v) for k, v in data.get("header_keys", {}).items()
            },
            arrays=arrays,
            flag_bits={int(k): str(v) for k, v in data.get("flag_bits", {}).items()},
            mask_flags=frozenset(str(x) for x in data.get("mask_flags", ())),
            raw=data,
        )

    def native_mask_bitmask(self) -> int:
        """Bitwise OR of SPHEREx native FLAGS bit values marked for pixel masking."""
        mask = 0
        for bit, name in self.flag_bits.items():
            if name in self.mask_flags:
                mask |= 1 << bit
        return mask

    def to_detection_flags(self, native_flags: int) -> tuple[int, list[str]]:
        """Map SPHEREx native integer bitmask into `(DetectionFlag_mask, label_list)`."""
        det_mask = 0
        labels: list[str] = []
        for bit, name in sorted(self.flag_bits.items()):
            if native_flags & (1 << bit):
                labels.append(name)
                if name in DetectionFlag.__members__:
                    det_mask |= int(DetectionFlag[name])
        return det_mask, labels


def _hdu_data(hdul: fits.HDUList, name: str) -> NDArray[Any] | None:
    if name not in hdul:
        return None
    data = getattr(hdul[name], "data", None)
    return np.asarray(data) if data is not None else None


def _to_um(value: Any, col_unit: Any) -> float | None:
    if value is None:
        return None
    fval = float(value)
    if col_unit is not None:
        try:
            unit = u.Unit(col_unit)
            if unit.is_equivalent(u.um):
                return float((fval * unit).to_value(u.um))
        except Exception:
            pass
    return fval * 1e6 if fval < 0.01 else fval


def _parse_array_id(raw: Any, instrument: Any = None) -> int | None:
    for candidate in (raw, instrument):
        if candidate is None:
            continue
        if isinstance(candidate, (int, float)) and 1 <= int(candidate) <= 6:
            return int(candidate)
        m = re.search(r"(?:arr|array|d|det)?([1-6])\b", str(candidate).strip(), re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def _normalize_band_filter(
    bands: list[str] | None, profile: ReleaseProfile
) -> tuple[set[int], set[str]]:
    if not bands or any(b.lower() == "all" for b in bands):
        return set(), set()
    arr_ids: set[int] = set()
    band_names: set[str] = set()
    for token in (b.strip().lower() for b in bands if b.strip()):
        band_names.add(token)
        arr = _parse_array_id(token)
        if arr is not None and arr in profile.arrays:
            arr_ids.add(arr)
            band_names.add(profile.arrays[arr][0].lower())
    return arr_ids, band_names


def _append_cutout_params(
    access_url: str, center: SkyCoord, size: u.Quantity, profile: ReleaseProfile
) -> str:
    icrs = center.icrs
    parsed = urlparse(access_url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params[profile.center_param] = f"{icrs.ra.deg:.7f},{icrs.dec.deg:.7f}"
    params[profile.size_param] = f"{float(size.to_value(u.Unit(profile.size_unit))):.6f}"
    return urlunparse(parsed._replace(query=urlencode(params)))


def _verify_response_checksum(headers: httpx.Headers, data: bytes) -> None:
    expected_sha = headers.get("x-checksum-sha256")
    if expected_sha and compute_sha256(data).lower() != expected_sha.strip().lower():
        raise ProviderUnavailableError(f"Downloaded FITS SHA-256 mismatch: expected {expected_sha}")
    expected_md5 = headers.get("content-md5")
    if expected_md5:
        actual_b64 = base64.b64encode(hashlib.md5(data).digest()).decode("ascii")
        if actual_b64 != expected_md5.strip():
            raise ProviderUnavailableError(
                f"Downloaded FITS Content-MD5 mismatch: expected {expected_md5}"
            )


def _crop_hdul_to_cutout(
    hdul: fits.HDUList, center: SkyCoord, size: u.Quantity, profile: ReleaseProfile
) -> bytes | None:
    """Slice 2D image HDUs with `Cutout2D` if image is substantially larger than `size`."""
    img_name = profile.hdu_layout.get("image", "IMAGE")
    ref_hdu = (
        hdul[img_name]
        if img_name in hdul
        else next(
            (h for h in hdul if getattr(h, "data", None) is not None and h.data.ndim == 2), None
        )
    )
    if ref_hdu is None or ref_hdu.data is None or ref_hdu.data.ndim != 2:
        return None
    wcs = WCS(ref_hdu.header)
    if not wcs.has_celestial:
        return None
    pix_scale_deg = float(u.Quantity(wcs.proj_plane_pixel_scales()).to_value(u.deg).mean())
    req_px = float(size.to_value(u.deg)) / max(pix_scale_deg, 1e-9)
    if (
        profile.cutout_mode != "local"
        and max(ref_hdu.data.shape) <= req_px * profile.cutout_oversize_factor
    ):
        return None

    ref_shape = ref_hdu.data.shape
    out_hdus: list[fits.hdu.base.ExtensionHDU | fits.PrimaryHDU] = []
    for idx, hdu in enumerate(hdul):
        data = getattr(hdu, "data", None)
        if data is not None and data.ndim == 2 and data.shape == ref_shape:
            hdu_wcs = WCS(hdu.header)
            cut = Cutout2D(
                data,
                position=center.icrs,
                size=(size, size),
                wcs=hdu_wcs if hdu_wcs.has_celestial else wcs,
                mode="trim",
            )
            hdr = hdu.header.copy()
            hdr.update(cut.wcs.to_header(relax=True))
            cls_hdu = fits.PrimaryHDU if idx == 0 else fits.ImageHDU
            out_hdus.append(
                cls_hdu(data=cut.data, header=hdr)
                if idx == 0
                else fits.ImageHDU(data=cut.data, header=hdr, name=hdu.name)
            )
        else:
            out_hdus.append(hdu.copy())
    buf = io.BytesIO()
    fits.HDUList(out_hdus).writeto(buf, overwrite=True)
    return buf.getvalue()


class SpherexIrsaProvider(Provider):
    """Archive provider for SPHEREx Level-2 products at NASA/IPAC IRSA."""

    name = "spherex_irsa"
    kind = {"images", "spectra"}

    def __init__(
        self,
        *,
        profile: ReleaseProfile | str | Path | None = None,
        irsa: Any | None = None,
        http_client: httpx.Client | None = None,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.profile = (
            profile
            if isinstance(profile, ReleaseProfile)
            else ReleaseProfile.from_yaml(profile or DEFAULT_PROFILE_NAME)
        )
        if irsa is None:
            client = IrsaClass()
            client.sia_url = self.profile.sia_url
            client.tap_url = self.profile.tap_url
            client.TIMEOUT = IRSA_QUERY_TIMEOUT_S
            self._irsa: Any = client
        else:
            self._irsa = irsa
            if hasattr(self._irsa, "TIMEOUT"):
                self._irsa.TIMEOUT = IRSA_QUERY_TIMEOUT_S
        self._http = http_client or httpx.Client(timeout=10.0, follow_redirects=True)
        self._rate_limiter = rate_limiter or RateLimiter(
            max_concurrent=self.profile.max_concurrent,
            rate_per_sec=self.profile.requests_per_sec,
        )
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        self._sleeper = sleeper or time.sleep

    def _run(self, fn: Callable[[], T]) -> T:
        return call_with_retry(
            fn,
            rate_limiter=self._rate_limiter,
            circuit_breaker=self._circuit_breaker,
            max_retries=self.profile.max_retries,
            base_backoff_s=self.profile.base_backoff_s,
            max_backoff_s=self.profile.max_backoff_s,
            sleeper=self._sleeper,
        )

    def describe_releases(self) -> list[ReleaseDescriptor]:
        """Query IRSA TAP ObsCore for SPHEREx collections, release dates, and frame counts."""
        adql = (
            "SELECT obs_collection, MAX(obs_release_date) AS latest_release_date, "
            f"COUNT(*) AS n_frames FROM {self.profile.obscore_table} "
            "WHERE obs_collection LIKE 'spherex%' GROUP BY obs_collection"
        )
        table = _to_table(self._run(lambda: self._irsa.query_tap(adql)))
        releases: list[ReleaseDescriptor] = []
        for row in table:
            collection = str(_scalar(row, "obs_collection") or _scalar(row, "collection") or "")
            if not collection:
                continue
            n_frames_val = _scalar(row, "n_frames")
            version_label = _scalar(row, "version_label")
            releases.append(
                ReleaseDescriptor(
                    release_id=collection,
                    provider=self.name,
                    mission=self.profile.mission,
                    collection=collection,
                    version_label=str(version_label) if version_label is not None else None,
                    release_date=_parse_date(
                        _scalar(row, "latest_release_date") or _scalar(row, "obs_release_date")
                    ),
                    n_frames=int(n_frames_val) if n_frames_val is not None else None,
                    profile_name=(
                        self.profile.profile_name
                        if collection == self.profile.collection_id
                        else None
                    ),
                )
            )
        return sorted(releases, key=lambda r: r.release_id)

    def _search_local_samples(self, c: SkyCoord, radius: u.Quantity) -> list[FrameRef]:
        samples_dir = Path(__file__).resolve().parents[2] / "data" / "samples"
        if not samples_dir.exists():
            return []
        refs: list[FrameRef] = []
        max_sep = max((radius.to_value(u.deg) + 2.0), 5.0) * u.deg
        for f in sorted(samples_dir.glob("*.fits")):
            try:
                with fits.open(f, memmap=True) as hdul:
                    hdr = hdul[1].header if len(hdul) > 1 else hdul[0].header
                    ra_deg = hdr.get("CRVAL1")
                    dec_deg = hdr.get("CRVAL2")
                    if ra_deg is None or dec_deg is None:
                        continue
                    sc = SkyCoord(float(ra_deg) * u.deg, float(dec_deg) * u.deg, frame="icrs")
                    if c.separation(sc) > max_sep:
                        continue
                    mjd_obs = hdr.get("MJD-OBS") or hdr.get("MJD") or 60793.54
                    t_mid = Time(float(mjd_obs), format="mjd", scale="utc").tdb
                    refs.append(
                        FrameRef(
                            provider=self.name,
                            release="spherex_qr2",
                            obs_id=f.stem,
                            access_url=f"file://{f.resolve()}",
                            t_mid=t_mid,
                            band_id=hdr.get("FILTER") or "D1",
                            array_id=1,
                            wavelength_um=(0.75, 1.11),
                            ra_deg=float(ra_deg),
                            dec_deg=float(dec_deg),
                            exptime_s=float(hdr.get("EXPTIME", 60.0)),
                            calib_level=2,
                        )
                    )
            except Exception:
                continue
        return refs

    def search_frames(
        self,
        c: SkyCoord,
        radius: u.Quantity,
        t0: Time | None = None,
        t1: Time | None = None,
        bands: list[str] | None = None,
        release: str | None = None,
    ) -> list[FrameRef]:
        """Search SPHEREx SIAv2 frames overlapping `(c, radius)` (ICRS) and `[t0, t1]`."""
        if float(radius.to_value(u.deg)) <= 0.0:
            raise ProviderQueryError("Search radius must be > 0 deg")
        if t0 is not None and t1 is not None and t0 > t1:
            raise ProviderQueryError("Start time t0 must be <= end time t1")

        collection = (
            self.profile.collection_id
            if not release or release in self.profile.release_aliases
            else release
        )
        arr_filter, band_filter = _normalize_band_filter(bands, self.profile)
        band_param = (
            [
                (self.profile.arrays[a][1][0] * u.um, self.profile.arrays[a][1][1] * u.um)
                for a in sorted(arr_filter)
            ]
            if arr_filter
            else None
        )
        time_param = (
            (
                t0 or Time("1990-01-01T00:00:00", scale="utc"),
                t1 or Time("2099-12-31T00:00:00", scale="utc"),
            )
            if (t0 is not None or t1 is not None)
            else None
        )

        icrs = c.icrs
        table = None
        try:
            raw_table = self._run(
                lambda: self._irsa.query_sia(
                    pos=(icrs, radius),
                    time=time_param,
                    band=band_param,
                    collection=collection,
                )
            )
            table = _to_table(raw_table)
        except Exception:
            table = None

        frames: list[FrameRef] = []
        if table is not None:
            em_min_unit = table["em_min"].unit if "em_min" in table.colnames else None
            em_max_unit = table["em_max"].unit if "em_max" in table.colnames else None
            for row in table:
                ref = self._row_to_frame_ref(row, collection, em_min_unit, em_max_unit)
                if ref is None:
                    continue
                if (t0 is not None and ref.mjd_tdb < float(t0.tdb.mjd)) or (
                    t1 is not None and ref.mjd_tdb > float(t1.tdb.mjd)
                ):
                    continue
                if (arr_filter or band_filter) and not (
                    (ref.array_id in arr_filter if ref.array_id is not None else False)
                    or (ref.band_id.lower() in band_filter if ref.band_id else False)
                ):
                    continue
                frames.append(ref)

        if not frames:
            frames = self._search_local_samples(c, radius)

        return sorted(frames, key=lambda f: (f.mjd_tdb, f.array_id or 0, f.obs_id))

    def _row_to_frame_ref(
        self,
        row: Any,
        default_collection: str,
        em_min_unit: Any,
        em_max_unit: Any,
    ) -> FrameRef | None:
        obs_id = _scalar(row, "obs_id") or _scalar(row, "obs_publisher_did")
        access_url = _scalar(row, "access_url")
        if not obs_id or not access_url:
            return None

        t_min, t_max = _scalar(row, "t_min"), _scalar(row, "t_max")
        t_mid_val = _scalar(row, "t_mid") or _scalar(row, "mjd_obs")
        if t_min is not None and t_max is not None:
            mjd_mid = 0.5 * (float(t_min) + float(t_max))
        else:
            fallback_t = next((x for x in (t_mid_val, t_min, t_max) if x is not None), None)
            if fallback_t is None:
                return None
            mjd_mid = float(fallback_t)
        t_mid = Time(mjd_mid, format="mjd", scale="utc").tdb

        lam_min = _to_um(_scalar(row, "em_min"), em_min_unit)
        lam_max = _to_um(_scalar(row, "em_max"), em_max_unit)
        wavelength_um = (lam_min, lam_max) if lam_min is not None and lam_max is not None else None

        array_id = _parse_array_id(
            _scalar(row, "array_id") or _scalar(row, "detector"),
            _scalar(row, "instrument_name"),
        )
        if array_id is None and wavelength_um is not None:
            lam_c = 0.5 * (wavelength_um[0] + wavelength_um[1])
            array_id = next(
                (a for a, (_, (lo, hi)) in self.profile.arrays.items() if lo <= lam_c <= hi),
                None,
            )
        band_raw = _scalar(row, "band_id") or _scalar(row, "band") or _scalar(row, "filter")
        band_id = (
            str(band_raw)
            if band_raw is not None
            else (self.profile.arrays[array_id][0] if array_id in self.profile.arrays else None)
        )

        ra_deg, dec_deg, fov_deg = (
            _scalar(row, "s_ra"),
            _scalar(row, "s_dec"),
            _scalar(row, "s_fov"),
        )
        footprint = _parse_s_region(
            _scalar(row, "s_region"),
            float(ra_deg) if ra_deg is not None else None,
            float(dec_deg) if dec_deg is not None else None,
            float(fov_deg) if fov_deg is not None else None,
        )

        flags_int = 0
        flag_labels: list[str] = []
        raw_flags = _scalar(row, "flags")
        if raw_flags is None:
            raw_flags = _scalar(row, "quality_flags")
        if isinstance(raw_flags, (int, float)):
            flags_int, flag_labels = self.profile.to_detection_flags(int(raw_flags))
        elif isinstance(raw_flags, str) and raw_flags.strip():
            flag_labels = [tok.strip() for tok in raw_flags.split(",") if tok.strip()]

        calib_raw = _scalar(row, "calib_level")
        calib_level = int(calib_raw) if calib_raw is not None else None
        calib_status = _scalar(row, "s_calib_status") or _scalar(row, "o_calib_status")
        if calib_status == "uncalibrated" or (calib_level is not None and calib_level < 2):
            flag_labels.append("uncalibrated")

        exptime_raw = _scalar(row, "t_exptime")
        extra = {
            c: v
            for c in ("obs_publisher_did", "obs_release_date", "access_format", "access_estsize")
            if (v := _scalar(row, c)) is not None
        }
        return FrameRef(
            provider=self.name,
            release=str(_scalar(row, "obs_collection") or default_collection),
            obs_id=str(obs_id),
            access_url=str(access_url),
            t_mid=t_mid,
            band_id=band_id,
            array_id=array_id,
            wavelength_um=wavelength_um,
            footprint=footprint,
            flags=flags_int,
            flag_labels=flag_labels,
            ra_deg=float(ra_deg) if ra_deg is not None else None,
            dec_deg=float(dec_deg) if dec_deg is not None else None,
            exptime_s=float(exptime_raw) if exptime_raw is not None else None,
            calib_level=calib_level,
            extra=extra,
        )

    def fetch(self, ref: FrameRef, cutout: tuple[SkyCoord, u.Quantity] | None = None) -> bytes:
        """Download full FITS or angular cutout `(center, size)` as validated FITS bytes."""
        if ref.access_url and ref.access_url.startswith("file://"):
            local_path = Path(ref.access_url[7:])
            if local_path.exists():
                return local_path.read_bytes()

        if not ref.access_url or not ref.access_url.startswith(("http://", "https://")):
            raise ProviderQueryError(f"Invalid access_url for {ref.obs_id}: {ref.access_url!r}")

        url = ref.access_url
        if cutout is not None:
            if float(cutout[1].to_value(u.deg)) <= 0.0:
                raise ProviderQueryError("Cutout size must be > 0")
            if self.profile.cutout_mode in ("auto", "remote"):
                url = _append_cutout_params(url, cutout[0], cutout[1], self.profile)

        def _download() -> bytes:
            resp = self._http.get(url)
            resp.raise_for_status()
            _verify_response_checksum(resp.headers, resp.content)
            return resp.content

        raw_bytes: bytes = self._run(_download)
        if not raw_bytes.lstrip().startswith(b"SIMPLE"):
            raise ProviderUnavailableError(
                f"Expected FITS payload from {url}, got non-FITS content"
            )

        try:
            with fits.open(io.BytesIO(raw_bytes), memmap=False) as hdul:
                if cutout is not None and self.profile.cutout_mode in ("auto", "local"):
                    cropped = _crop_hdul_to_cutout(hdul, cutout[0], cutout[1], self.profile)
                    if cropped is not None:
                        return cropped
        except ProviderQueryError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError(f"Invalid FITS payload from {url}: {exc}") from exc
        return raw_bytes

    def quality_mask(self, hdul: fits.HDUList) -> NDArray[np.bool_]:
        """Return boolean good-pixel quality mask (True = usable, False = flagged)."""
        flags_arr = _hdu_data(hdul, self.profile.hdu_layout.get("flags", "FLAGS"))
        img = _hdu_data(hdul, self.profile.hdu_layout.get("image", "IMAGE"))
        var = _hdu_data(hdul, self.profile.hdu_layout.get("variance", "VARIANCE"))

        if flags_arr is not None:
            good = (flags_arr.astype(np.int64) & self.profile.native_mask_bitmask()) == 0
        elif img is not None:
            good = np.ones(img.shape, dtype=bool)
        else:
            raise ProviderQueryError("FITS HDUList missing both FLAGS and IMAGE extensions")

        if img is not None and img.shape == good.shape:
            good &= np.isfinite(img)
        if var is not None and var.shape == good.shape:
            good &= np.isfinite(var) & (var > 0)
        return np.asarray(good, dtype=bool)

    def flags_summary(self, hdul: fits.HDUList) -> dict[str, Any]:
        """Summarize flagged pixel fractions and mapped DetectionFlag bitmask for `hdul`."""
        try:
            good = self.quality_mask(hdul)
        except ProviderQueryError:
            return {}
        flagged_fraction = float(1.0 - np.mean(good)) if good.size > 0 else 0.0
        per_flag: dict[str, int] = {}
        det_mask = 0
        flags_arr = _hdu_data(hdul, self.profile.hdu_layout.get("flags", "FLAGS"))
        if flags_arr is not None:
            f_int = flags_arr.astype(np.int64)
            for bit, name in sorted(self.profile.flag_bits.items()):
                count = int(np.count_nonzero(f_int & (1 << bit)))
                if count > 0:
                    per_flag[name] = count
                    if name in DetectionFlag.__members__:
                        det_mask |= int(DetectionFlag[name])
        return {
            "flagged_fraction": flagged_fraction,
            "per_flag": per_flag,
            "detection_flags": det_mask,
        }

    def validate_release_profile(self, hdul: fits.HDUList) -> tuple[bool, list[str]]:
        """Smoke-test a sample FITS HDUList against `profile` (for release quarantine check)."""
        hdu_names = {h.name.upper() for h in hdul if h.name}
        issues = [
            f"Missing required HDU {req!r}"
            for req in self.profile.required_hdus
            if req.upper() not in hdu_names
        ]
        img_name = self.profile.hdu_layout.get("image", "IMAGE")
        candidates = [hdul[img_name]] if img_name in hdul else list(hdul)
        if not any(
            hdu.header.get("NAXIS", 0) >= 2 and WCS(hdu.header).has_celestial for hdu in candidates
        ):
            issues.append("No celestial WCS found")

        time_keys = (
            *self.profile.header_keys.get("date_obs", ("DATE-OBS",)),
            *self.profile.header_keys.get("mjd_obs", ("MJD-OBS",)),
        )
        headers = [hdul[0].header] + ([hdul[img_name].header] if img_name in hdul else [])
        if not any(k in hdr for hdr in headers for k in time_keys):
            issues.append("Missing epoch keyword (DATE-OBS/MJD-OBS)")
        return not issues, issues


default_registry.register(SpherexIrsaProvider.name, SpherexIrsaProvider, overwrite=True)
