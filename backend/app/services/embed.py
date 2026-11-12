"""oEmbed metadata, cached multi-epoch GIF rendering, and chrome-less embed HTML."""

from __future__ import annotations

import html
import io
import json
import math
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import astropy.units as u
import numpy as np
from astropy.coordinates import Angle, SkyCoord
from astropy.time import Time
from fastapi import status
from PIL import Image, ImageDraw

from app.api.deps import APIProblemError
from app.providers.base import Provider
from app.schemas.fields import OEmbedResponse
from app.services.cache import CacheBackend
from app.services.fields import (
    asinh_stretch_u8,
    extract_2d_image,
    group_frames_into_epochs,
    parse_bands_query,
    parse_utc_query,
    resolve_target_name,
)
from app.services.jobs import compute_params_hash
from app.services.storage import StorageBackend
from sphx_core.wcs import healpix9_nested

_ALLOWED_VIEWER_PATHS = {"", "/", "/v", "/v/", "/embed", "/embed/"}
_VIEWER_PARAMS = frozenset(
    {
        "ra",
        "dec",
        "target",
        "fov",
        "from",
        "to",
        "release",
        "bands",
        "mode",
        "a",
        "b",
        "op",
        "overlay",
        "track",
    }
)
_SEXAGESIMAL_MARKERS = (":", "h", "d", "m", "s")
_MAX_GIF_EPOCHS = 24
_MAX_FRAMES_PER_EPOCH = 6


def _is_sexagesimal(value: str) -> bool:
    low = value.lower()
    return any(ch in low for ch in _SEXAGESIMAL_MARKERS)


def parse_icrs_coords(ra: str, dec: str) -> tuple[float, float]:
    """Parse decimal degree or sexagesimal RA/Dec strings into `(ra_deg, dec_deg)` ICRS."""
    ra_s, dec_s = ra.strip(), dec.strip()
    if not ra_s or not dec_s:
        raise APIProblemError(422, "Invalid Coordinates", "Both ra and dec must be non-empty.")
    if not _is_sexagesimal(ra_s) and not _is_sexagesimal(dec_s):
        try:
            ra_val, dec_val = float(ra_s), float(dec_s)
        except ValueError as exc:
            raise APIProblemError(
                422, "Invalid Coordinates", f"Could not parse ra={ra!r}, dec={dec!r}."
            ) from exc
        if (
            not math.isfinite(ra_val)
            or not math.isfinite(dec_val)
            or not (0.0 <= ra_val < 360.0)
            or not (-90.0 <= dec_val <= 90.0)
        ):
            raise APIProblemError(
                422,
                "Coordinates Out of Range",
                "Expected 0 <= ra < 360 and -90 <= dec <= 90 deg.",
            )
        return ra_val, dec_val

    try:
        ra_unit = u.hourangle if _is_sexagesimal(ra_s) else u.deg
        coord = SkyCoord(ra_s, dec_s, unit=(ra_unit, u.deg), frame="icrs")
    except Exception as exc:
        raise APIProblemError(
            422,
            "Invalid Coordinates",
            f"Could not parse sexagesimal coordinates ra={ra!r}, dec={dec!r}: {exc}",
        ) from exc
    return float(coord.ra.deg) % 360.0, float(coord.dec.deg)


def resolve_embed_coords(
    *,
    ra: str | None,
    dec: str | None,
    target: str | None,
    simbad: Provider,
    cache: CacheBackend,
) -> tuple[float, float, str | None]:
    """Resolve explicit `ra`/`dec` or `target` name into `(ra_deg, dec_deg, label)`."""
    if ra is not None or dec is not None:
        if ra is None or dec is None:
            raise APIProblemError(
                422,
                "Incomplete Coordinates",
                "Both ra and dec are required when specifying sky coordinates.",
            )
        ra_deg, dec_deg = parse_icrs_coords(ra, dec)
        return ra_deg, dec_deg, target.strip() if target and target.strip() else None
    if target is not None and target.strip():
        resolved = resolve_target_name(target, simbad=simbad, cache=cache)
        if resolved is None:
            raise APIProblemError(
                status.HTTP_404_NOT_FOUND,
                "Target Not Found",
                f"Could not resolve target name: {target}",
                error_type="https://spherex.example/problems/target-not-found",
            )
        return resolved.ra_deg, resolved.dec_deg, resolved.query
    raise APIProblemError(422, "Missing Target", "Provide either ra and dec, or target.")


def build_oembed_response(
    url: str, *, maxwidth: int | None = None, maxheight: int | None = None
) -> OEmbedResponse:
    """Validate viewer URL and return an oEmbed rich iframe descriptor."""
    cleaned = url.strip()
    if any(ch.isspace() or ord(ch) < 32 for ch in cleaned):
        raise APIProblemError(
            422, "Invalid Viewer URL", "URL must not contain whitespace or control characters."
        )
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise APIProblemError(
            422, "Invalid Viewer URL", "URL must be an absolute http(s) viewer URL."
        )
    if parsed.path not in _ALLOWED_VIEWER_PATHS:
        raise APIProblemError(
            422,
            "Unsupported Embed Path",
            f"Path {parsed.path!r} is not a supported viewer or embed route.",
        )

    raw_params = parse_qsl(parsed.query, keep_blank_values=False)
    param_map = dict(raw_params)
    if "ra" in param_map and "dec" in param_map:
        parse_icrs_coords(param_map["ra"], param_map["dec"])
    elif "ra" in param_map:
        try:
            ra_str = param_map["ra"]
            val = (
                float(Angle(ra_str, unit=u.hourangle).deg)
                if _is_sexagesimal(ra_str)
                else float(ra_str)
            )
            if not math.isfinite(val) or not (0.0 <= val < 360.0):
                raise ValueError("ra out of range")
        except Exception as exc:
            raise APIProblemError(
                422, "Invalid Coordinates", f"Invalid ra parameter: {param_map['ra']!r}"
            ) from exc
    if "fov" in param_map:
        try:
            fov_val = float(param_map["fov"])
            if not math.isfinite(fov_val) or not (0.0 < fov_val <= 60.0):
                raise ValueError("fov out of range")
        except ValueError as exc:
            raise APIProblemError(
                422, "Invalid FOV", f"FOV must be in (0, 60] arcmin, got {param_map['fov']!r}."
            ) from exc

    filtered = [(k, v) for k, v in raw_params if k in _VIEWER_PARAMS]
    embed_url = urlunsplit((parsed.scheme, parsed.netloc, "/embed", urlencode(filtered), ""))
    width = max(160, min(maxwidth if maxwidth is not None else 640, 1920))
    height = max(120, min(maxheight if maxheight is not None else 480, 1200))
    iframe_html = (
        f'<iframe src="{html.escape(embed_url, quote=True)}" '
        f'width="{width}" height="{height}" loading="lazy" '
        'title="SPHEREx sky viewer" allowfullscreen></iframe>'
    )
    return OEmbedResponse(html=iframe_html, width=width, height=height)


def render_epochs_gif(
    *,
    ra: float,
    dec: float,
    fov: float,
    bands: str,
    release: str,
    from_utc: str | None,
    to_utc: str | None,
    fps: float,
    provider: Provider,
    cache: CacheBackend,
    storage: StorageBackend,
) -> tuple[bytes, bool]:
    """Render and cache an animated multi-epoch GIF `(gif_bytes, cache_hit)`."""
    t0 = parse_utc_query(from_utc)
    t1 = parse_utc_query(to_utc)
    if t0 is not None and t1 is not None and t0 > t1:
        raise APIProblemError(
            status.HTTP_400_BAD_REQUEST,
            "Invalid Time Range",
            "`from` timestamp must be <= `to` timestamp.",
        )

    band_list = parse_bands_query(bands)
    band_key = ",".join(sorted(band_list)) if band_list else "all"
    hpx = healpix9_nested(ra, dec)
    key_params = {
        "ra": round(ra, 6),
        "dec": round(dec, 6),
        "fov": round(fov, 3),
        "bands": band_key,
        "from_utc": from_utc or "",
        "to_utc": to_utc or "",
        "fps": round(fps, 2),
    }
    key_hash = compute_params_hash("render_gif", key_params, release).hex()[:24]
    cache_key = f"render_gif:{key_hash}"
    storage_key = f"products/embed/{hpx}/{key_hash}.gif"

    if (cached := cache.get_json(cache_key)) is not None:
        sk = str(cached.get("storage_key", storage_key))
        if storage.exists(sk):
            return storage.get_bytes(sk, verify_checksum=False), True

    center = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")
    frames = provider.search_frames(
        center,
        (fov / 2.0) * u.arcmin,
        t0=t0,
        t1=t1,
        bands=band_list,
        release=release,
    )
    epoch_groups = group_frames_into_epochs(frames)
    if not epoch_groups:
        raise APIProblemError(
            status.HTTP_404_NOT_FOUND,
            "No Frames Found",
            "No sky frames available for the requested coordinates and filters.",
            error_type="https://spherex.example/problems/no-frames",
        )

    epoch_arrays: list[tuple[str, np.ndarray]] = []
    for ep_label, grp in epoch_groups[:_MAX_GIF_EPOCHS]:
        arrays = [
            extract_2d_image(
                storage.get_bytes(
                    provider.fetch_to_storage(
                        ref, storage, cutout=(center, fov * u.arcmin)
                    ).storage_path,
                    verify_checksum=False,
                )
            )
            for ref in grp[:_MAX_FRAMES_PER_EPOCH]
        ]
        if not arrays:
            continue
        same_shape = [a for a in arrays if a.shape == arrays[0].shape]
        stacked = (
            np.nanmedian(np.stack(same_shape, axis=0), axis=0) if len(same_shape) > 1 else arrays[0]
        )
        mjd_mean = sum(r.mjd_tdb for r in grp) / len(grp)
        utc_date = Time(mjd_mean, format="mjd", scale="tdb").utc.iso[:10]
        band_tag = grp[0].band_id or (f"arr{grp[0].array_id}" if grp[0].array_id else "")
        epoch_arrays.append((f"{ep_label} {utc_date} {band_tag}".strip(), stacked))

    if not epoch_arrays:
        raise APIProblemError(
            status.HTTP_404_NOT_FOUND,
            "No Renderable Frames",
            "Matching frames did not contain renderable 2D image data.",
        )

    non_empty = [p for _, arr in epoch_arrays if (p := arr[np.isfinite(arr)]).size > 0]
    finite_all = np.concatenate(non_empty) if non_empty else np.empty(0, dtype=np.float32)
    vmin, vmax = (
        (float(x) for x in np.percentile(finite_all, [1.0, 99.5]))
        if finite_all.size
        else (0.0, 1.0)
    )

    pil_frames: list[Image.Image] = []
    for label_text, arr in epoch_arrays:
        u8, _, _ = asinh_stretch_u8(arr, vmin=vmin, vmax=vmax)
        img = (
            Image.fromarray(u8, mode="L")
            .convert("RGB")
            .resize((256, 256), Image.Resampling.BILINEAR)
        )
        draw = ImageDraw.Draw(img)
        draw.rectangle([(0, 0), (256, 20)], fill=(5, 7, 10))
        draw.text((6, 4), label_text[:36], fill=(230, 234, 240))
        pil_frames.append(img)

    buf = io.BytesIO()
    duration_ms = max(50, int(round(1000.0 / fps)))
    pil_frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=pil_frames[1:],
        duration=duration_ms,
        loop=0,
    )
    gif_bytes = buf.getvalue()
    storage.put_bytes(storage_key, gif_bytes, content_type="image/gif")
    resolved_release = frames[0].release if frames else release
    cache.set_json(
        cache_key,
        {"storage_key": storage_key, "n_epochs": len(pil_frames), "release": resolved_release},
        ttl_s=86400,
    )
    return gif_bytes, False


def render_embed_html(
    *,
    ra_deg: float,
    dec_deg: float,
    fov: float,
    mode: str,
    bands: str,
    release: str,
    from_utc: str | None,
    to_utc: str | None,
    label: str | None = None,
    overlay: str | None = None,
    track: str | None = None,
) -> str:
    """Render chrome-less dark-sky HTML5 embed viewer with progressive metadata fetch."""
    gif_params: dict[str, str] = {
        "ra": f"{ra_deg:.6f}",
        "dec": f"{dec_deg:.6f}",
        "fov": f"{fov:.2f}",
        "bands": bands,
        "release": release,
    }
    if from_utc:
        gif_params["from"] = from_utc
    if to_utc:
        gif_params["to"] = to_utc

    viewer_params = {**gif_params, "mode": mode}
    if overlay:
        viewer_params["overlay"] = overlay
    if track:
        viewer_params["track"] = track

    gif_url = f"/api/v1/render.gif?{urlencode(gif_params)}"
    frames_url = f"/api/v1/fields/frames?{urlencode(gif_params)}"
    viewer_url = f"/v?{urlencode(viewer_params)}"

    coord_text = f"RA {ra_deg:.5f}°  Dec {dec_deg:+.5f}° · FOV {fov:.1f}′"
    title_text = f"{label} ({coord_text})" if label else coord_text
    state_json = json.dumps(
        {
            "ra": ra_deg,
            "dec": dec_deg,
            "fov": fov,
            "mode": mode,
            "bands": bands,
            "release": release,
            "framesUrl": frames_url,
        }
    ).replace("<", "\\u003c")

    esc = html.escape
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title_text)} — SPHEREx Embed</title>
<style>
  :root {{
    color-scheme: dark;
    --sky0: #05070A; --sky1: #0C1117; --text: #E6EAF0; --muted: #8A96A8; --accent: #F59E0B;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0; height: 100%; background: var(--sky0); color: var(--text);
    font: 12px/1.4 ui-sans-serif, system-ui, sans-serif; overflow: hidden;
  }}
  main {{ position: relative; width: 100%; height: 100%; display: grid; place-items: center; }}
  img.sky {{ width: 100%; height: 100%; object-fit: contain; image-rendering: pixelated; }}
  .hud {{
    position: absolute; left: 10px; right: 10px; bottom: 10px;
    display: flex; justify-content: space-between; align-items: center; gap: 8px;
    padding: 6px 10px; background: rgba(12,17,23,0.82);
    border: 1px solid rgba(255,255,255,0.12); border-radius: 6px; backdrop-filter: blur(4px);
  }}
  .meta {{
    font-family: ui-monospace, monospace; font-size: 11px; color: var(--text);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .sub {{ color: var(--muted); margin-left: 6px; }}
  a.open {{ color: var(--accent); text-decoration: none; font-weight: 600; white-space: nowrap; }}
  a.open:hover {{ text-decoration: underline; }}
  @media (prefers-reduced-motion: reduce) {{
    .motion-note::after {{ content: " (reduced motion)"; color: var(--muted); }}
  }}
</style>
</head>
<body>
<main
  data-ra="{ra_deg:.6f}" data-dec="{dec_deg:.6f}" data-fov="{fov:.2f}"
  data-mode="{esc(mode, quote=True)}" data-bands="{esc(bands, quote=True)}"
  data-release="{esc(release, quote=True)}"
>
  <img
    class="sky" src="{esc(gif_url, quote=True)}"
    alt="SPHEREx multi-epoch sky view at {esc(coord_text, quote=True)}"
  >
  <div class="hud">
    <div class="meta">
      <span>{esc(title_text)}</span>
      <span
        class="sub motion-note" id="epoch-status" role="status" aria-live="polite"
      >{esc(mode)} · {esc(bands)}</span>
    </div>
    <a
      class="open" href="{esc(viewer_url, quote=True)}" target="_blank" rel="noopener noreferrer"
    >Open viewer</a>
  </div>
</main>
<script>
(() => {{
  const cfg = {state_json};
  const statusEl = document.getElementById("epoch-status");
  fetch(cfg.framesUrl, {{ credentials: "omit" }})
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {{
      if (!data || !Array.isArray(data.epochs) || !statusEl) return;
      const n = data.epochs.length;
      const unit = n === 1 ? "" : "s";
      statusEl.textContent = n
        ? `${{cfg.mode}} · ${{n}} epoch${{unit}} · ${{data.release}}`
        : "no matching epochs";
    }})
    .catch(() => {{}});
}})();
</script>
</body>
</html>
"""
