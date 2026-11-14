"""Matplotlib-powered FITS image renderer with astronomical stretches and colormaps."""

from __future__ import annotations

import io
import math
from typing import Any, Literal
from collections.abc import Sequence
import numpy as np
from numpy.typing import NDArray
from PIL import Image
import matplotlib.cm as cm

# Supported colormaps
SUPPORTED_COLORMAPS: dict[str, Any] = {
    "inferno": cm.inferno,
    "viridis": cm.viridis,
    "plasma": cm.plasma,
    "magma": cm.magma,
    "cividis": cm.cividis,
    "hot": cm.hot,
    "cool": cm.cool,
    "bone": cm.bone,
    "gray": cm.gray,
    "gist_heat": cm.gist_heat,
}


class ImageRenderer:
    """Renders 2D scientific FITS arrays into scaled RGB PNG images.

    Preserves original scientific calibration in backend; applies visualization
    scaling only for display with astronomical orientation (origin="lower").
    """

    @staticmethod
    def render_to_png_bytes(
        data: NDArray[Any] | Sequence[Sequence[float]],
        stretch: str = "asinh",
        colormap: str = "inferno",
        p_min: float = 0.5,
        p_max: float = 99.5,
        custom_vmin: float | None = None,
        custom_vmax: float | None = None,
        brightness: float = 1.0,
        contrast: float = 1.0,
        max_dim: int | None = 1200,
    ) -> bytes:
        """Transforms raw scientific array to scaled 8-bit RGB PNG image bytes."""
        arr = np.asarray(data, dtype=np.float64)
        finite_mask = np.isfinite(arr)

        if not np.any(finite_mask):
            img = Image.new("RGB", (max(1, arr.shape[1]), max(1, arr.shape[0])), color=(10, 14, 23))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        finite_vals = arr[finite_mask]

        # Determine cutoff values
        if custom_vmin is not None:
            vmin = float(custom_vmin)
        else:
            vmin = float(np.percentile(finite_vals, max(0.0, min(100.0, p_min))))

        if custom_vmax is not None:
            vmax = float(custom_vmax)
        else:
            vmax = float(np.percentile(finite_vals, max(0.0, min(100.0, p_max))))

        if stretch.lower() == "zscale":
            try:
                from astropy.visualization import ZScaleInterval
                zmin, zmax = ZScaleInterval().get_limits(arr)
                if math.isfinite(zmin) and math.isfinite(zmax) and zmax > zmin:
                    vmin, vmax = float(zmin), float(zmax)
            except Exception:
                pass

        if vmax <= vmin:
            vmax = vmin + 1e-6

        # Clip values to [vmin, vmax]
        arr_clipped = np.copy(arr)
        arr_clipped[~finite_mask] = vmin
        np.clip(arr_clipped, vmin, vmax, out=arr_clipped)

        # Normalize to [0.0, 1.0]
        norm = (arr_clipped - vmin) / (vmax - vmin)

        # Apply astronomical stretch function
        stretch_type = (stretch or "asinh").lower()
        if stretch_type == "asinh":
            Q = 8.0
            norm = np.arcsinh(Q * norm) / np.arcsinh(Q)
        elif stretch_type == "log":
            norm = np.log1p(100.0 * norm) / np.log1p(100.0)
        elif stretch_type == "sqrt":
            norm = np.sqrt(norm)

        # Apply contrast & brightness adjustment
        if contrast != 1.0 or brightness != 1.0:
            norm = (norm - 0.5) * contrast + 0.5
            norm = norm * brightness
            np.clip(norm, 0.0, 1.0, out=norm)

        # Invert vertical axis so astronomical Y (origin="lower") matches image display
        norm_flipped = np.flipud(norm)

        # Apply colormap
        cmap_obj = SUPPORTED_COLORMAPS.get(colormap.lower(), cm.inferno)
        rgba = cmap_obj(norm_flipped)
        rgb_bytes = (rgba[:, :, :3] * 255.0).astype(np.uint8)

        # Convert to PIL Image
        pil_img = Image.fromarray(rgb_bytes, mode="RGB")

        if max_dim is not None and (pil_img.width > max_dim or pil_img.height > max_dim):
            pil_img.thumbnail((max_dim, max_dim), Image.Resampling.BILINEAR)

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


image_renderer = ImageRenderer()
render_fits_to_png = image_renderer.render_to_png_bytes
