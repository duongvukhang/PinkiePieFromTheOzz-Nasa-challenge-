import { describe, expect, it } from "vitest";
import {
  decodeFlagBits,
  formatSexagesimal,
  parseFitsWcs,
  pixelToSky,
  skyToPixel,
} from "../../src/viewer/aladin/wcs";
import {
  clampFov,
  parseCoordsInput,
  parseViewParams,
  toViewQuery,
} from "../../src/viewer/state/viewStore";

describe("viewer WCS & URL state", () => {
  it("round-trips TAN projection between sky and pixel coordinates", () => {
    const wcs = parseFitsWcs(
      "NAXIS1  = 128\nNAXIS2  = 128\nCRVAL1  = 133.79513\nCRVAL2  = -7.24515\nCRPIX1  = 64.5\nCRPIX2  = 64.5\nCD1_1   = -0.001302\nCD2_2   = 0.001302",
      133.79513,
      -7.24515,
      10,
    );
    const pt = skyToPixel(133.81, -7.23, wcs);
    const back = pixelToSky(pt.x, pt.y, wcs);
    expect(Math.abs(back.ra - 133.81)).toBeLessThan(1e-5);
    expect(Math.abs(back.dec - -7.23)).toBeLessThan(1e-5);
  });

  it("parses decimal and sexagesimal WISE 0855 coordinates", () => {
    const dec = parseCoordsInput("133.79513, -7.24515");
    expect(dec?.ra).toBeCloseTo(133.79513, 5);
    expect(dec?.dec).toBeCloseTo(-7.24515, 5);

    const sexa = parseCoordsInput("08:55:10.831 -07:14:42.53");
    expect(sexa?.ra).toBeCloseTo(133.79513, 4);
    expect(sexa?.dec).toBeCloseTo(-7.24515, 4);
    expect(formatSexagesimal(133.79513, -7.24515)).toContain("08h 55m");
  });

  it("clamps FOV by authentication status and round-trips URL params", () => {
    expect(clampFov(30, false)).toBe(15);
    expect(clampFov(30, true)).toBe(30);
    const parsed = parseViewParams(
      "?target=WISE+0855&fov=8&bands=arr6&mode=compare&op=diff&overlay=gaia,catwise&frame=2",
      false,
    );
    expect(parsed.bands).toBe("arr6");
    expect(parsed.op).toBe("diff");
    expect(parsed.overlay).toEqual(["gaia", "catwise"]);
    expect(parsed.inspectorTab).toBe("spectrum");

    const parsedFlags = parseViewParams("?inspectorTab=flags", false);
    expect(parsedFlags.inspectorTab).toBe("flags");

    const qs = toViewQuery({
      target: "WISE 0855",
      ra: 133.79513,
      dec: -7.24515,
      fov: 8,
      from: "",
      to: "",
      release: "latest",
      bands: "arr6",
      mode: "compare",
      op: "diff",
      frame: 2,
      fps: 2,
      playing: false,
      stretch: "asinh",
      colormap: "gray",
      overlay: ["gaia", "catwise"],
      track: [],
      hips: false,
      swipe: 0.5,
      inspectorTab: "spectrum",
      selectBoxMode: false,
    });
    expect(qs).toContain("bands=arr6");
    expect(qs).toContain("op=diff");
    expect(qs).toContain("overlay=gaia%2Ccatwise");
  });

  it("decodes detector flag bitmasks", () => {
    expect(decodeFlagBits((1 << 1) | (1 << 7))).toEqual(["BLENDED", "LOW_SNR"]);
  });
});
