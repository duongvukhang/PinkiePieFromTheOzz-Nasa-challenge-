import type { WcsParams } from "../aladin/wcs";
import { pixelToSky } from "../aladin/wcs";

/** Clamps zoom level between min and max bounds. */
export function clampZoom(zoom: number, min = 0.5, max = 16): number {
  return Number.isFinite(zoom) ? Math.min(max, Math.max(min, zoom)) : 1;
}

/** Computes next zoom and adjusted pan offsets centered around pointer coordinates. */
export function computeZoomAtPoint(
  prevZoom: number,
  factor: number,
  clientX: number,
  clientY: number,
  panX: number,
  panY: number,
  rectLeft: number,
  rectTop: number,
  min = 0.5,
  max = 16,
): { zoom: number; panX: number; panY: number } {
  const nextZoom = clampZoom(prevZoom * factor, min, max);
  const mx = clientX - rectLeft;
  const my = clientY - rectTop;
  const scaleRatio = nextZoom / prevZoom;
  return {
    zoom: nextZoom,
    panX: mx - (mx - panX) * scaleRatio,
    panY: my - (my - panY) * scaleRatio,
  };
}

/** Computes pan offsets to center a specific pixel coordinate within viewport dimensions. */
export function computeCenterPan(
  targetPxX: number,
  targetPxY: number,
  naxis1: number,
  naxis2: number,
  width: number,
  height: number,
  zoom: number,
): { x: number; y: number } {
  const normX = targetPxX / Math.max(1, naxis1);
  const normY = targetPxY / Math.max(1, naxis2);
  return {
    x: width * 0.5 - normX * width * zoom,
    y: height * 0.5 - normY * height * zoom,
  };
}

/** Converts viewport client coordinates to sky coordinates (ICRS, degrees). */
export function clientToSkyCoords(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  pan: { x: number; y: number },
  zoom: number,
  wcs: WcsParams,
  imgWidth?: number,
  imgHeight?: number,
): { ra: number; dec: number } | null {
  const w = imgWidth ?? rect.width;
  const h = imgHeight ?? rect.height;
  const canvasX = (clientX - rect.left - pan.x) / zoom;
  const canvasY = (clientY - rect.top - pan.y) / zoom;
  if (canvasX < 0 || canvasX > w || canvasY < 0 || canvasY > h) {
    return null;
  }
  const px = (canvasX / w) * wcs.naxis1;
  const py = wcs.naxis2 - (canvasY / h) * wcs.naxis2;
  return pixelToSky(px, py, wcs);
}
