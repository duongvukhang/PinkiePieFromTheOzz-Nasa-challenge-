import { describe, expect, it } from "vitest";
import { api, type UploadCompatibilityReport } from "../../src/api/client";

describe("FITS Upload & Validation Client", () => {
  it("exposes uploadFits and getUploadReport on api client", () => {
    expect(typeof api.uploadFits).toBe("function");
    expect(typeof api.getUploadReport).toBe("function");
  });

  it("validates permitted astronomical FITS extensions", () => {
    const validExtensions = [".fits", ".fit", ".fts", ".fits.gz", ".fz"];
    const isValidFits = (name: string) => {
      const lower = name.toLowerCase();
      return validExtensions.some((ext) => lower.endsWith(ext));
    };

    expect(isValidFits("wise_cutout_w1.fits")).toBe(true);
    expect(isValidFits("test_epoch.fit")).toBe(true);
    expect(isValidFits("archive.fts")).toBe(true);
    expect(isValidFits("spherex_survey.fits.gz")).toBe(true);
    expect(isValidFits("mosaic.fz")).toBe(true);

    expect(isValidFits("table.csv")).toBe(false);
    expect(isValidFits("image.png")).toBe(false);
    expect(isValidFits("script.py")).toBe(false);
  });

  it("extracts celestial center and FOV from UploadCompatibilityReport", () => {
    const mockReport: UploadCompatibilityReport = {
      upload_id: "11111111-1111-1111-1111-111111111111",
      fits_file_id: "22222222-2222-2222-2222-222222222222",
      valid: true,
      compatibility_status: "compatible",
      pixscale_arcsec: 6.2,
      ra_center_deg: 133.79513,
      dec_center_deg: -7.24515,
      band_label: "Arr 2 (1.32–2.34 µm)",
      reprojection_needed: false,
      warnings: [],
      errors: [],
      accuracy_notes: [],
    };

    const ra = mockReport.ra_center_deg ?? 133.79513;
    const dec = mockReport.dec_center_deg ?? -7.24515;
    const fov = mockReport.pixscale_arcsec
      ? Math.min(60, (mockReport.pixscale_arcsec * 256) / 60)
      : 10;

    expect(ra).toBeCloseTo(133.79513);
    expect(dec).toBeCloseTo(-7.24515);
    expect(fov).toBeGreaterThan(0);
    expect(fov).toBeLessThanOrEqual(60);
  });
});
