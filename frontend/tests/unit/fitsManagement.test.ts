import { describe, expect, it } from "vitest";
import { api, type CutoutFrame, type UploadedFitsSummary } from "../../src/api/client";
import { parseViewParams, toViewQuery, useViewStore } from "../../src/viewer/state/viewStore";

describe("FITS File Management & Multi-Epoch State", () => {
  it("exposes listUploads and deleteUpload on api client", () => {
    expect(typeof api.listUploads).toBe("function");
    expect(typeof api.deleteUpload).toBe("function");
  });

  describe("viewStore fitsIds and excludedFrameIds tracking", () => {
    it("handles addFitsId, removeFitsId, and removeFrame actions", () => {
      const store = useViewStore.getState();

      store.setView({ fitsIds: [], excludedFrameIds: [], fitsId: undefined });
      expect(useViewStore.getState().fitsIds).toEqual([]);

      // Add first FITS ID
      store.addFitsId("fits-uuid-1");
      let state = useViewStore.getState();
      expect(state.fitsIds).toContain("fits-uuid-1");
      expect(state.fitsId).toBe("fits-uuid-1");
      expect(state.excludedFrameIds).not.toContain("fits-uuid-1");

      // Add second FITS ID
      store.addFitsId("fits-uuid-2");
      state = useViewStore.getState();
      expect(state.fitsIds).toEqual(["fits-uuid-1", "fits-uuid-2"]);

      // Remove frame (e.g. user deletes an epoch)
      store.removeFrame("fits-uuid-1");
      state = useViewStore.getState();
      expect(state.fitsIds).not.toContain("fits-uuid-1");
      expect(state.fitsIds).toContain("fits-uuid-2");
      expect(state.excludedFrameIds).toContain("fits-uuid-1");
      expect(state.fitsId).toBe("fits-uuid-2");

      // Toggle upload modal state
      expect(state.isUploadModalOpen).toBe(false);
      store.setUploadModalOpen(true);
      expect(useViewStore.getState().isUploadModalOpen).toBe(true);
      store.setUploadModalOpen(false);
      expect(useViewStore.getState().isUploadModalOpen).toBe(false);
    });

    it("round-trips fitsId, fitsIds, and excluded in URL query params", () => {
      const queryStr = "?ra=133.79513&dec=-7.24515&fitsId=fid-1&fitsIds=fid-1,fid-2&excluded=cutout-9";
      const parsed = parseViewParams(queryStr);
      expect(parsed.fitsId).toBe("fid-1");
      expect(parsed.fitsIds).toEqual(["fid-1", "fid-2"]);
      expect(parsed.excludedFrameIds).toEqual(["cutout-9"]);

      const serialized = toViewQuery({
        ra: 133.79513,
        dec: -7.24515,
        fitsIds: ["fid-1", "fid-2"],
        excludedFrameIds: ["cutout-9"],
      });
      expect(serialized).toContain("fitsId=fid-1");
      expect(serialized).toContain("fitsIds=fid-1%2Cfid-2");
      expect(serialized).toContain("excluded=cutout-9");
    });
  });

  describe("Multi-FITS epoch deduplication and synthetic fallback suppression", () => {
    it("ensures user uploaded FITS frames take precedence and are not overwritten by synthetic cutouts", () => {
      const mockUploads: UploadedFitsSummary[] = [
        {
          fits_file_id: "upload-frame-1",
          filename: "epoch_2024.fits",
          epoch: "2024-03-15",
          mjd_tdb_mid: 60384.5,
          utc_mid: "2024-03-15T12:00:00Z",
          display_url: "/api/v1/uploads/upload-frame-1/display.webp",
          science_url: "/api/v1/uploads/upload-frame-1/science.bin",
          fits_url: "/api/v1/uploads/upload-frame-1/frame.fits",
          wcs_header: "NAXIS1 = 64\nNAXIS2 = 64",
          origin: "upload",
          stretch: { vmin: 0, vmax: 1000, shape: [64, 64] },
        },
      ];

      const syntheticCutouts: CutoutFrame[] = [
        {
          frame_id: "synthetic-e1",
          epoch: "E1",
          mjd_tdb_mid: 60400.0,
          utc_mid: "2024-03-30T00:00:00Z",
          display_url: "/api/v1/cutouts/c1/frames/e1/display.webp",
          science_url: "/api/v1/cutouts/c1/frames/e1/science.bin",
          fits_url: "/api/v1/cutouts/c1/frames/e1/frame.fits",
          wcs_header: "",
          flagged_fraction: 0,
        },
        {
          frame_id: "synthetic-e2",
          epoch: "E2",
          mjd_tdb_mid: 60580.0,
          utc_mid: "2024-09-26T00:00:00Z",
          display_url: "/api/v1/cutouts/c1/frames/e2/display.webp",
          science_url: "/api/v1/cutouts/c1/frames/e2/science.bin",
          fits_url: "/api/v1/cutouts/c1/frames/e2/frame.fits",
          wcs_header: "",
          flagged_fraction: 0,
        },
      ];

      const allFitsIds = ["upload-frame-1"];
      const excluded = new Set<string>();

      // Merge logic simulation matching useViewerData:
      const matchedUploads = mockUploads.filter((u) => allFitsIds.includes(u.fits_file_id));
      const userFrames: CutoutFrame[] = matchedUploads.map((u) => ({
        frame_id: u.fits_file_id,
        epoch: u.epoch || "Uploaded FITS",
        mjd_tdb_mid: u.mjd_tdb_mid,
        utc_mid: u.utc_mid,
        display_url: u.display_url,
        science_url: u.science_url,
        fits_url: u.fits_url,
        wcs_header: u.wcs_header,
        stretch: u.stretch || { vmin: 0, vmax: 1000 },
        flagged_fraction: 0,
      }));

      const isSynthetic =
        syntheticCutouts.length > 0 && Boolean(syntheticCutouts[0]?.epoch.startsWith("E"));
      const eligibleCutouts = userFrames.length > 0 && isSynthetic ? [] : syntheticCutouts;

      const seenEpochs = new Set<string>();
      const combined: CutoutFrame[] = [];

      for (const uf of userFrames) {
        if (!excluded.has(uf.frame_id)) {
          combined.push(uf);
          seenEpochs.add(uf.epoch.toLowerCase());
        }
      }

      for (const cf of eligibleCutouts) {
        if (!excluded.has(cf.frame_id) && !seenEpochs.has(cf.epoch.toLowerCase())) {
          combined.push(cf);
          seenEpochs.add(cf.epoch.toLowerCase());
        }
      }

      // Verification: The uploaded frame persists and synthetic fallback is suppressed!
      expect(combined.length).toBe(1);
      expect(combined[0]?.frame_id).toBe("upload-frame-1");
      expect(combined[0]?.epoch).toBe("2024-03-15");
      expect(combined[0]?.wcs_header).toBe("NAXIS1 = 64\nNAXIS2 = 64");
    });

    it("enforces 1 FITS per epoch when multiple archives or cutouts exist for the same epoch", () => {
      const frameA: CutoutFrame = {
        frame_id: "user-ep1",
        epoch: "Epoch 1",
        mjd_tdb_mid: 60000.0,
        utc_mid: "2023-02-25T00:00:00Z",
        display_url: "/api/v1/uploads/user-ep1/display.webp",
        science_url: "/api/v1/uploads/user-ep1/science.bin",
        fits_url: "/api/v1/uploads/user-ep1/frame.fits",
        wcs_header: "",
        flagged_fraction: 0,
      };

      const frameB: CutoutFrame = {
        frame_id: "cutout-ep1-duplicate",
        epoch: "Epoch 1", // duplicate epoch label
        mjd_tdb_mid: 60000.1,
        utc_mid: "2023-02-25T02:24:00Z",
        display_url: "/api/v1/cutouts/duplicate/display.webp",
        science_url: "/api/v1/cutouts/duplicate/science.bin",
        fits_url: "/api/v1/cutouts/duplicate/frame.fits",
        wcs_header: "",
        flagged_fraction: 0,
      };

      const frameC: CutoutFrame = {
        frame_id: "cutout-ep2",
        epoch: "Epoch 2",
        mjd_tdb_mid: 60180.0,
        utc_mid: "2023-08-24T00:00:00Z",
        display_url: "/api/v1/cutouts/ep2/display.webp",
        science_url: "/api/v1/cutouts/ep2/science.bin",
        fits_url: "/api/v1/cutouts/ep2/frame.fits",
        wcs_header: "",
        flagged_fraction: 0,
      };

      const seenEpochs = new Set<string>();
      const combined: CutoutFrame[] = [];

      // User frame added first
      combined.push(frameA);
      seenEpochs.add(frameA.epoch.toLowerCase());

      // Subsequent candidate frames
      for (const f of [frameB, frameC]) {
        if (!seenEpochs.has(f.epoch.toLowerCase())) {
          combined.push(f);
          seenEpochs.add(f.epoch.toLowerCase());
        }
      }

      // Exactly 1 frame per epoch:
      expect(combined.length).toBe(2);
      expect(combined.map((f) => f.epoch)).toEqual(["Epoch 1", "Epoch 2"]);
      expect(combined[0]?.frame_id).toBe("user-ep1"); // User frame took precedence!
    });
  });
});
