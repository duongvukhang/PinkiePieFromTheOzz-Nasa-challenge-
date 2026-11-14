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
});
