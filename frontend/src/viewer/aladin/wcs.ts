export interface WcsParams {
  crval1: number;
  crval2: number;
  crpix1: number;
  crpix2: number;
  cd11: number;
  cd12: number;
  cd21: number;
  cd22: number;
  naxis1: number;
  naxis2: number;
}

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

/** Parse essential FITS WCS cards or synthesize tangent WCS from field center/FOV. */
export function parseFitsWcs(
  header: string | undefined,
  fallbackRa: number,
  fallbackDec: number,
  fovArcmin: number,
  width = 128,
  height = 128,
): WcsParams {
  const num = (key: string): number | undefined => {
    const m = header?.match(
      new RegExp(`^${key}\\s*=\\s*([+-]?\\d+(?:\\.\\d+)?(?:[Ee][+-]?\\d+)?)`, "m"),
    );
    return m ? Number(m[1]) : undefined;
  };
  const scale = fovArcmin / 60 / Math.max(1, width);
  const naxis1 = num("NAXIS1") ?? width;
  const naxis2 = num("NAXIS2") ?? height;
  const cdelt1 = num("CDELT1") ?? -scale;
  const cdelt2 = num("CDELT2") ?? scale;
  return {
    crval1: num("CRVAL1") ?? fallbackRa,
    crval2: num("CRVAL2") ?? fallbackDec,
    crpix1: num("CRPIX1") ?? (naxis1 + 1) / 2,
    crpix2: num("CRPIX2") ?? (naxis2 + 1) / 2,
    cd11: num("CD1_1") ?? cdelt1,
    cd12: num("CD1_2") ?? 0,
    cd21: num("CD2_1") ?? 0,
    cd22: num("CD2_2") ?? cdelt2,
    naxis1,
    naxis2,
  };
}

/** Gnomonic (TAN) projection from ICRS degrees to 0-based pixel coordinates. */
export function skyToPixel(
  raDeg: number,
  decDeg: number,
  wcs: WcsParams,
): { x: number; y: number } {
  const a = raDeg * D2R;
  const d = decDeg * D2R;
  const a0 = wcs.crval1 * D2R;
  const d0 = wcs.crval2 * D2R;
  const cosD = Math.cos(d);
  const sinD = Math.sin(d);
  const cosD0 = Math.cos(d0);
  const sinD0 = Math.sin(d0);
  const cosDa = Math.cos(a - a0);
  const denom = Math.max(1e-7, sinD * sinD0 + cosD * cosD0 * cosDa);
  const xi = ((cosD * Math.sin(a - a0)) / denom) * R2D;
  const eta = ((sinD * cosD0 - cosD * sinD0 * cosDa) / denom) * R2D;
  const det = wcs.cd11 * wcs.cd22 - wcs.cd12 * wcs.cd21 || 1e-12;
  const dx = (wcs.cd22 * xi - wcs.cd12 * eta) / det;
  const dy = (-wcs.cd21 * xi + wcs.cd11 * eta) / det;
  return { x: wcs.crpix1 - 1 + dx, y: wcs.naxis2 - (wcs.crpix2 + dy) };
}

/** Inverse TAN projection from 0-based canvas pixel coordinates to ICRS degrees. */
export function pixelToSky(x: number, y: number, wcs: WcsParams): { ra: number; dec: number } {
  const dx = x - (wcs.crpix1 - 1);
  const dy = wcs.naxis2 - y - wcs.crpix2;
  const xi = (wcs.cd11 * dx + wcs.cd12 * dy) * D2R;
  const eta = (wcs.cd21 * dx + wcs.cd22 * dy) * D2R;
  const a0 = wcs.crval1 * D2R;
  const d0 = wcs.crval2 * D2R;
  const denom = Math.cos(d0) - eta * Math.sin(d0);
  const ra = ((a0 + Math.atan2(xi, denom)) * R2D + 360) % 360;
  const dec = Math.atan2(Math.sin(d0) + eta * Math.cos(d0), Math.hypot(xi, denom)) * R2D;
  return { ra, dec };
}

export function formatSexagesimal(raDeg: number, decDeg: number): string {
  const norm = ((raDeg % 360) + 360) % 360;
  const h = Math.floor(norm / 15);
  const m = Math.floor((norm / 15 - h) * 60);
  const s = ((norm / 15 - h) * 60 - m) * 60;
  const sign = decDeg < 0 ? "−" : "+";
  const ad = Math.abs(decDeg);
  const dd = Math.floor(ad);
  const dm = Math.floor((ad - dd) * 60);
  const ds = ((ad - dd) * 60 - dm) * 60;
  const p2 = (n: number) => String(n).padStart(2, "0");
  return `${p2(h)}h ${p2(m)}m ${s.toFixed(2).padStart(5, "0")}s · ${sign}${p2(dd)}° ${p2(dm)}′ ${ds.toFixed(1).padStart(4, "0")}″`;
}

/** Approximate ICRS J2000 -> Galactic (l, b) and Ecliptic (lambda, beta) degrees. */
export function toGalacticAndEcliptic(raDeg: number, decDeg: number) {
  const a = raDeg * D2R;
  const d = decDeg * D2R;
  const eps = 23.4392911 * D2R;
  const beta =
    Math.asin(Math.sin(d) * Math.cos(eps) - Math.cos(d) * Math.sin(eps) * Math.sin(a)) * R2D;
  const lambda =
    (Math.atan2(Math.sin(a) * Math.cos(eps) + Math.tan(d) * Math.sin(eps), Math.cos(a)) * R2D +
      360) %
    360;
  const ag = 192.85948 * D2R;
  const dg = 27.12825 * D2R;
  const b =
    Math.asin(Math.sin(d) * Math.sin(dg) + Math.cos(d) * Math.cos(dg) * Math.cos(a - ag)) * R2D;
  const l =
    (122.93192 -
      Math.atan2(
        Math.cos(d) * Math.sin(a - ag),
        Math.sin(d) * Math.cos(dg) - Math.cos(d) * Math.sin(dg) * Math.cos(a - ag),
      ) *
        R2D +
      360) %
    360;
  return { l, b, lambda, beta };
}

export const FLAG_BITS = [
  [0, "SATURATED", "Core pixel saturated or nonlinear"],
  [1, "BLENDED", "Deblended from ≥ 2 components"],
  [2, "EDGE", "Within 2 FWHM of array edge or mask"],
  [3, "BAD_PIX", "Bad/hot detector pixel inside aperture"],
  [4, "COSMIC", "Cosmic-ray hit in core"],
  [5, "PERSIST", "Latent persistence suspected"],
  [6, "GHOST_SPIKE", "Optical ghost or diffraction spike"],
  [7, "LOW_SNR", "Detection S/N < 5"],
  [8, "EXTENDED", "Resolved / extended source"],
  [9, "STREAK", "Elongated trail along motion"],
  [10, "USER_EPOCH", "User-supplied epoch (no DATE-OBS)"],
  [11, "REPROJECTED", "Measured on resampled frame"],
] as const;

export function decodeFlagBits(mask: number): string[] {
  return FLAG_BITS.filter(([bit]) => (mask & (1 << bit)) !== 0).map(([, name]) => name);
}
