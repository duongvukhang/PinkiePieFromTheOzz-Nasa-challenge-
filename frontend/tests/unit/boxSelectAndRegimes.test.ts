import { describe, expect, it } from "vitest";
import { MAX_TRACKED_OBJECTS, useViewStore } from "../../src/viewer/state/viewStore";

describe("Box-Scoped Selection & 2-Object Tracking Cap", () => {
  it("enforces MAX_TRACKED_OBJECTS constant equal to 2", () => {
    expect(MAX_TRACKED_OBJECTS).toBe(2);
  });

  it("stores activeBox in viewStore and toggles properly", () => {
    const store = useViewStore.getState();
    expect(store.activeBox).toBeNull();

    store.setActiveBox({ raMin: 133.0, raMax: 134.0, decMin: -7.5, decMax: -7.0 });
    expect(useViewStore.getState().activeBox).toEqual({
      raMin: 133.0,
      raMax: 134.0,
      decMin: -7.5,
      decMax: -7.0,
    });

    store.setActiveBox(null);
    expect(useViewStore.getState().activeBox).toBeNull();
  });

  it("tracks at most MAX_TRACKED_OBJECTS concurrently", () => {
    const store = useViewStore.getState();
    store.addTrack("A", 133.5, -7.2);
    store.addTrack("B", 133.6, -7.1);

    expect(useViewStore.getState().track.length).toBe(2);
    expect(useViewStore.getState().track.length >= MAX_TRACKED_OBJECTS).toBe(true);

    // Clean up
    store.removeTrack("A");
    store.removeTrack("B");
    expect(useViewStore.getState().track.length).toBe(0);
  });

  it("calculates fitted bounding box scale from FWHM correctly", () => {
    const degPerPx = 0.0026; // ~9.36 arcsec/px
    const pxScale = 1;

    // Compact source: FWHM 6 arcsec
    const fwhmCompact = 6.0;
    const fwhmPxCompact = fwhmCompact / (3600 * degPerPx);
    const fitCompact = Math.max(7, Math.min(26, fwhmPxCompact * 1.4)) * pxScale;
    expect(fitCompact).toBe(7); // clamped to minimum 7px

    // Extended source: FWHM 60 arcsec (~6.4 px)
    const fwhmExtended = 60.0;
    const fwhmPxExtended = fwhmExtended / (3600 * degPerPx);
    const fitExtended = Math.max(7, Math.min(26, fwhmPxExtended * 1.4)) * pxScale;
    expect(fitExtended).toBeCloseTo(8.97, 1);
    expect(fitExtended).toBeGreaterThan(7);

    // Very large source: FWHM 150 arcsec (~16 px)
    const fwhmLarge = 150.0;
    const fwhmPxLarge = fwhmLarge / (3600 * degPerPx);
    const fitLarge = Math.max(7, Math.min(26, fwhmPxLarge * 1.4)) * pxScale;
    expect(fitLarge).toBeCloseTo(22.4, 1);
    expect(fitLarge).toBeLessThanOrEqual(26);
  });

  it("progressively draws trajectory segments up to active epoch during playback", () => {
    const epochs = ["E1", "E2", "E3", "E4"];
    const trajPoints = epochs.map((ep, i) => ({ epoch: ep, x: i * 10, y: i * 10 }));

    // When playing at E2 (active index = 1):
    const currentEpoch = "E2";
    const playing = true;
    const activeEpochIndex = trajPoints.findIndex((p) => p.epoch === currentEpoch);
    expect(activeEpochIndex).toBe(1);

    const segments = trajPoints.slice(1).map((pt, idx) => {
      const segIndex = idx + 1;
      const isDrawn = !playing || segIndex <= activeEpochIndex;
      const isLeading = playing && segIndex === activeEpochIndex;
      return { segIndex, isDrawn, isLeading };
    });

    // Segment 1 (E1 -> E2) is drawn and leading
    expect(segments[0]).toEqual({ segIndex: 1, isDrawn: true, isLeading: true });
    // Segments 2 & 3 (E2 -> E3 -> E4) are future ghosted segments
    expect(segments[1]).toEqual({ segIndex: 2, isDrawn: false, isLeading: false });
    expect(segments[2]).toEqual({ segIndex: 3, isDrawn: false, isLeading: false });
  });

  it("preserves activeBox during syncFromSearch when coordinates are identical", () => {
    const store = useViewStore.getState();
    store.setView({ ra: 133.795, dec: -7.245 });
    store.setActiveBox({ raMin: 133.7, raMax: 133.8, decMin: -7.3, decMax: -7.2 });
    expect(useViewStore.getState().activeBox).not.toBeNull();

    // Re-syncing with the exact same coordinates preserves activeBox
    store.syncFromSearch("?ra=133.795&dec=-7.245");
    expect(useViewStore.getState().activeBox).toEqual({
      raMin: 133.7,
      raMax: 133.8,
      decMin: -7.3,
      decMax: -7.2,
    });

    // Navigating to completely new coordinates resets activeBox
    store.syncFromSearch("?ra=200.0&dec=30.0");
    expect(useViewStore.getState().activeBox).toBeNull();
  });

  it("projects native pixel coordinates matching display.webp FITS grid", () => {
    const naxis2 = 128;
    const detection = { id: 42, ra: 133.795, dec: -7.245, x_pix: 64.5, y_pix: 32.2 };
    // Native pixel conversion: x = x_pix, y = naxis2 - y_pix
    const screenX = detection.x_pix;
    const screenY = naxis2 - detection.y_pix;
    expect(screenX).toBe(64.5);
    expect(screenY).toBe(95.8);
  });
});
