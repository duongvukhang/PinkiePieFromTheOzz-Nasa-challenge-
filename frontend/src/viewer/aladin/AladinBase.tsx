import { useEffect, useRef } from "react";
import type { WcsParams } from "./wcs";
import { skyToPixel } from "./wcs";

interface AladinBaseProps {
  wcs: WcsParams;
  ra: number;
  dec: number;
  fov: number;
  enabled: boolean;
}

/** Synchronized ICRS TAN graticule + optional lazy Aladin Lite v3 HiPS sky backdrop. */
export function AladinBase({ wcs, ra, dec, fov, enabled }: AladinBaseProps) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!enabled || !hostRef.current) return;
    let active = true;
    const el = hostRef.current;
    // Lazy-load Aladin Lite v3 only when user enables live HiPS sky context online.
    const loadAladin = async () => {
      try {
        const aladinUrl = "https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js";
        const mod = (await import(/* @vite-ignore */ aladinUrl)) as {
          default?: {
            init?: Promise<void>;
            aladin: (node: HTMLElement, opts: Record<string, unknown>) => void;
          };
        };
        if (!active || !mod.default) return;
        await mod.default.init;
        if (!active) return;
        mod.default.aladin(el, {
          target: `${ra} ${dec}`,
          fov: fov / 60,
          survey: "P/DSS2/color",
          showCooGridControl: false,
          showFullscreenControl: false,
          showLayersControl: false,
          showGotoControl: false,
        });
      } catch {
        /* Fallback silently to TAN coordinate grid when offline or sandboxed */
      }
    };
    void loadAladin();
    return () => {
      active = false;
      el.replaceChildren();
    };
  }, [enabled, ra, dec, fov]);

  const stepDeg = fov <= 6 ? 1 / 60 : fov <= 20 ? 3 / 60 : 10 / 60;
  const raLines = [-2, -1, 0, 1, 2].map((k) => ra + k * stepDeg);
  const decLines = [-2, -1, 0, 1, 2].map((k) => dec + k * stepDeg);

  return (
    <div className="aladin-base" aria-hidden="true">
      {enabled && <div ref={hostRef} className="aladin-hips-host" />}
      <svg
        className="viewer-layer-svg"
        viewBox={`0 0 ${wcs.naxis1} ${wcs.naxis2}`}
        preserveAspectRatio="none"
      >
        {raLines.map((r, idx) => {
          const p1 = skyToPixel(r, dec - fov / 90, wcs);
          const p2 = skyToPixel(r, dec + fov / 90, wcs);
          return (
            <line
              key={`r-${idx}`}
              x1={p1.x}
              y1={p1.y}
              x2={p2.x}
              y2={p2.y}
              stroke="rgba(250,249,245,0.12)"
              strokeWidth={wcs.naxis1 / 256}
              strokeDasharray="2 3"
            />
          );
        })}
        {decLines.map((d, idx) => {
          const p1 = skyToPixel(ra - fov / 90, d, wcs);
          const p2 = skyToPixel(ra + fov / 90, d, wcs);
          return (
            <line
              key={`d-${idx}`}
              x1={p1.x}
              y1={p1.y}
              x2={p2.x}
              y2={p2.y}
              stroke="rgba(250,249,245,0.12)"
              strokeWidth={wcs.naxis1 / 256}
              strokeDasharray="2 3"
            />
          );
        })}
      </svg>
    </div>
  );
}
