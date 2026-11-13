import { useEffect, useRef } from "react";
import type { CutoutFrame } from "../../api/client";
import { useViewStore } from "../state/viewStore";

interface FrameStripProps {
  frames: CutoutFrame[];
  compact?: boolean;
}

/** Formats time offset from baseline epoch t0 into human-readable hours or days. */
export function formatTimeDelta(mjd: number, mjd0: number): string {
  const diffDays = mjd - mjd0;
  if (Math.abs(diffDays) < 1e-4) return "t₀";
  const absDays = Math.abs(diffDays);
  const sign = diffDays >= 0 ? "+" : "−";
  if (absDays < 1.0) {
    const hours = absDays * 24.0;
    return hours >= 0.1 ? `${sign}${hours.toFixed(1)}h` : `${sign}${(hours * 60).toFixed(0)}m`;
  }
  return `${sign}${absDays.toFixed(1)}d`;
}

export function FrameStrip({ frames, compact = false }: FrameStripProps) {
  const { frame, fps, playing, op, swipe, setView } = useViewStore();
  const barRef = useRef<HTMLDivElement>(null);
  const sliderRef = useRef<HTMLInputElement>(null);

  const count = frames.length;
  const safeIdx = count > 0 ? ((frame % count) + count) % count : 0;
  const active = frames[safeIdx];

  // Roving tabindex fallback only when native focusgroup is unsupported (toolbar guide)
  useEffect(() => {
    const bar = barRef.current;
    if (!bar || "focusGroup" in HTMLElement.prototype) return;
    const items = Array.from(bar.querySelectorAll<HTMLButtonElement>("button"));
    items.forEach((b, i) => (b.tabIndex = i === 0 ? 0 : -1));
    const onFocusIn = (e: FocusEvent) => {
      const target = e.target as HTMLButtonElement;
      if (items.includes(target)) items.forEach((b) => (b.tabIndex = b === target ? 0 : -1));
    };
    const onKeyDown = (e: KeyboardEvent) => {
      const i = items.indexOf(document.activeElement as HTMLButtonElement);
      if (i < 0) return;
      const rtl = getComputedStyle(bar).direction === "rtl";
      const delta: Record<string, number> = {
        ArrowRight: rtl ? -1 : 1,
        ArrowLeft: rtl ? 1 : -1,
      };
      const dir = delta[e.key];
      let nextIdx = -1;
      if (e.key === "Home") nextIdx = 0;
      else if (e.key === "End") nextIdx = items.length - 1;
      else if (dir !== undefined) nextIdx = (i + dir + items.length) % items.length;
      if (nextIdx >= 0) {
        e.preventDefault();
        items[nextIdx]?.focus();
      }
    };
    bar.addEventListener("focusin", onFocusIn);
    bar.addEventListener("keydown", onKeyDown);
    return () => {
      bar.removeEventListener("focusin", onFocusIn);
      bar.removeEventListener("keydown", onKeyDown);
    };
  }, [count]);

  // Sync CSS custom properties for cross-browser slider fill (slider guide fallback)
  useEffect(() => {
    const el = sliderRef.current;
    if (!el) return;
    el.style.setProperty("--attr-min", el.min || "0.5");
    el.style.setProperty("--attr-max", el.max || "10");
    el.style.setProperty("--control-value", String(fps));
  }, [fps]);

  const mjd0 = frames[0]?.mjd_tdb_mid ?? active?.mjd_tdb_mid ?? 0;
  const activeDt = active ? formatTimeDelta(active.mjd_tdb_mid, mjd0) : "t₀";

  const statusText = active
    ? `Epoch ${safeIdx + 1} of ${count}: ${active.epoch}, ${active.utc_mid.slice(0, 10)}, Δt ${activeDt}, MJD ${active.mjd_tdb_mid.toFixed(2)}`
    : "No sky frames loaded";

  return (
    <footer className="viewer-strip" aria-label="Epoch timeline and playback">
      {active && (
        <div className="viewer-epoch-status" aria-label="Active epoch details">
          <span className="viewer-epoch-num">
            Epoch {safeIdx + 1}/{count}
          </span>
          <span className="viewer-epoch-date">{active.utc_mid.slice(0, 10)}</span>
          <span className="viewer-epoch-dt" title="Time delta from baseline epoch t₀">
            Δt: {activeDt}
          </span>
          <span className="viewer-epoch-mjd">MJD {active.mjd_tdb_mid.toFixed(2)}</span>
        </div>
      )}
      <div
        ref={barRef}
        role="toolbar"
        aria-label="Blink playback controls"
        {...({ focusgroup: "toolbar wrap" } as Record<string, string>)}
        className="viewer-toolbar"
      >
        <button
          type="button"
          className="viewer-btn"
          aria-pressed={playing}
          aria-disabled={count < 2 ? "true" : undefined}
          onClick={() => count >= 2 && setView({ playing: !playing })}
        >
          {playing ? "❚❚ Pause" : "▶ Play"}
        </button>
        <button
          type="button"
          className="viewer-btn"
          aria-label="Previous frame"
          aria-disabled={count < 2 ? "true" : undefined}
          onClick={() =>
            count >= 2 && setView({ playing: false, frame: (safeIdx + count - 1) % count })
          }
        >
          ◀
        </button>
        <button
          type="button"
          className="viewer-btn"
          aria-label="Next frame"
          aria-disabled={count < 2 ? "true" : undefined}
          onClick={() => count >= 2 && setView({ playing: false, frame: (safeIdx + 1) % count })}
        >
          ▶
        </button>
      </div>

      {!compact && (
        <div className="viewer-slider-wrap">
          <label htmlFor="fps-slider" className="viewer-label">
            FPS {fps.toFixed(1)}
          </label>
          <input
            ref={sliderRef}
            id="fps-slider"
            className="viewer-range"
            type="range"
            min={0.5}
            max={10}
            step={0.5}
            value={fps}
            aria-valuetext={`${fps} frames per second`}
            onChange={(e) => setView({ fps: Number(e.target.value) })}
          />
        </div>
      )}

      {op === "swipe" && (
        <div className="viewer-slider-wrap">
          <label htmlFor="swipe-slider" className="viewer-label">
            Swipe {Math.round(swipe * 100)}%
          </label>
          <input
            id="swipe-slider"
            className="viewer-range"
            type="range"
            min={0.05}
            max={0.95}
            step={0.05}
            value={swipe}
            onChange={(e) => setView({ swipe: Number(e.target.value) }, false)}
          />
        </div>
      )}

      <div className="viewer-epoch-pills" role="group" aria-label="Loaded epochs">
        {frames.map((f, i) => {
          const dt = formatTimeDelta(f.mjd_tdb_mid, mjd0);

          return (
            <button
              key={f.frame_id}
              type="button"
              className="viewer-epoch-btn"
              aria-pressed={i === safeIdx}
              title={`Epoch ${f.epoch}: ${f.utc_mid.slice(0, 10)}, MJD ${f.mjd_tdb_mid.toFixed(2)} (${dt})`}
              onClick={() => setView({ frame: i, playing: false })}
            >
              <span>{f.epoch}</span>
              <span className="viewer-epoch-meta">{f.utc_mid.slice(0, 7)}</span>
              <span className="viewer-epoch-delta" style={{ opacity: 0.8, fontSize: "0.68rem" }}>
                {dt}
              </span>
            </button>
          );
        })}
      </div>

      <p role="status" className="visually-hidden" aria-live="polite">
        {statusText}
      </p>
    </footer>
  );
}
