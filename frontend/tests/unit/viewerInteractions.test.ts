import { describe, expect, it } from "vitest";
import { parseFitsWcs } from "../../src/viewer/aladin/wcs";
import { computeShortestArcRaBounds } from "../../src/viewer/overlays/WcsSvgOverlay";
import { formatTimeDelta } from "../../src/viewer/panels/FrameStrip";
import {
  clampZoom,
  clientToSkyCoords,
  computeCenterPan,
  computeZoomAtPoint,
} from "../../src/viewer/panels/viewerTransform";

describe("Viewer interactions & navigation", () => {
  describe("computeShortestArcRaBounds and box membership", () => {
    it("handles standard non-wrapping RA intervals", () => {
      const b1 = computeShortestArcRaBounds(15, 25);
      expect(b1).toEqual({ raMin: 15, raMax: 25 });

      const b2 = computeShortestArcRaBounds(25, 15);
      expect(b2).toEqual({ raMin: 15, raMax: 25 });
    });

    it("handles meridian wrap-around across RA 0°/360°", () => {
      const b1 = computeShortestArcRaBounds(358, 2);
      expect(b1).toEqual({ raMin: 358, raMax: 2 });

      const b2 = computeShortestArcRaBounds(2, 358);
      expect(b2).toEqual({ raMin: 358, raMax: 2 });
    });

    it("correctly identifies inside/outside coordinates across meridian", () => {
      const { raMin, raMax } = computeShortestArcRaBounds(358, 3);
      const isInside = (ra: number) =>
        raMin <= raMax ? ra >= raMin && ra <= raMax : ra >= raMin || ra <= raMax;

      expect(isInside(359)).toBe(true);
      expect(isInside(1.5)).toBe(true);
      expect(isInside(358)).toBe(true);
      expect(isInside(3)).toBe(true);
      expect(isInside(180)).toBe(false);
      expect(isInside(45)).toBe(false);
    });
  });

  describe("formatTimeDelta", () => {
    it("formats baseline as t₀", () => {
      expect(formatTimeDelta(60000.0, 60000.0)).toBe("t₀");
      expect(formatTimeDelta(60000.00001, 60000.0)).toBe("t₀");
    });

    it("formats sub-day intervals in hours or minutes", () => {
      expect(formatTimeDelta(60000.25, 60000.0)).toBe("+6.0h");
      expect(formatTimeDelta(60000.5, 60000.0)).toBe("+12.0h");
      expect(formatTimeDelta(60000.02, 60000.0)).toBe("+0.5h");
      expect(formatTimeDelta(60000.002, 60000.0)).toBe("+3m");
    });

    it("formats multi-day intervals in days", () => {
      expect(formatTimeDelta(60015.4, 60000.0)).toBe("+15.4d");
      expect(formatTimeDelta(59980.0, 60000.0)).toBe("−20.0d");
    });
  });

  describe("viewerTransform pan and zoom math", () => {
    it("clamps zoom within allowed bounds [0.5, 16.0]", () => {
      expect(clampZoom(0.1)).toBe(0.5);
      expect(clampZoom(25)).toBe(16);
      expect(clampZoom(2.5)).toBe(2.5);
    });

    it("computes center pan so target pixel aligns with viewport midpoint", () => {
      const width = 500;
      const height = 500;
      const naxis1 = 128;
      const naxis2 = 128;
      const zoom = 2.0;

      // Target at pixel (64, 64) -> exact center of 128x128 image
      const pan = computeCenterPan(64, 64, naxis1, naxis2, width, height, zoom);
      // Screen coordinate for (64, 64): pan.x + (64 / 128) * width * zoom
      const screenX = pan.x + (64 / naxis1) * width * zoom;
      const screenY = pan.y + (64 / naxis2) * height * zoom;

      expect(screenX).toBeCloseTo(width / 2, 5);
      expect(screenY).toBeCloseTo(height / 2, 5);
    });

    it("preserves cursor focus point during scroll-wheel zoom", () => {
      const prevZoom = 1.0;
      const factor = 1.5;
      const clientX = 300;
      const clientY = 200;
      const panX = -50;
      const panY = -30;
      const rectLeft = 100;
      const rectTop = 50;

      const res = computeZoomAtPoint(
        prevZoom,
        factor,
        clientX,
        clientY,
        panX,
        panY,
        rectLeft,
        rectTop,
      );
      expect(res.zoom).toBe(1.5);

      // Point under cursor before zoom in transformed space
      const mx = clientX - rectLeft;
      const pBefore = (mx - panX) / prevZoom;
      // Point under cursor after zoom in transformed space
      const pAfter = (mx - res.panX) / res.zoom;
      expect(pBefore).toBeCloseTo(pAfter, 5);
    });

    it("converts viewport client coordinates to sky coordinates", () => {
      const wcs = parseFitsWcs(
        "NAXIS1  = 128\nNAXIS2  = 128\nCRVAL1  = 133.79513\nCRVAL2  = -7.24515\nCRPIX1  = 64.5\nCRPIX2  = 64.5\nCD1_1   = -0.001302\nCD2_2   = 0.001302",
        133.79513,
        -7.24515,
        10,
      );
      const rect = { left: 0, top: 0, width: 256, height: 256 };
      // Center of viewport (128, 128) corresponds to center pixel (64, 64) -> near CRVAL
      const sky = clientToSkyCoords(128, 128, rect, { x: 0, y: 0 }, 1.0, wcs);
      expect(sky).not.toBeNull();
      expect(sky?.ra).toBeCloseTo(133.795, 2);
      expect(sky?.dec).toBeCloseTo(-7.245, 2);

      // Outside viewport returns null
      expect(clientToSkyCoords(300, 300, rect, { x: 0, y: 0 }, 1.0, wcs)).toBeNull();
    });
  });
});
