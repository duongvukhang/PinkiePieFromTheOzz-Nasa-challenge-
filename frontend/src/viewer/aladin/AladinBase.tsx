import { useEffect, useRef } from "react";
import type { WcsParams } from "./wcs";

interface AladinBaseProps {
  wcs: WcsParams;
  ra: number;
  dec: number;
  fov: number;
  enabled: boolean;
}

/** Synchronized optional lazy Aladin Lite v3 HiPS sky backdrop. */
export function AladinBase({ ra, dec, fov, enabled }: AladinBaseProps) {
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
        /* Fallback silently when offline or sandboxed */
      }
    };
    void loadAladin();
    return () => {
      active = false;
      el.replaceChildren();
    };
  }, [enabled, ra, dec, fov]);

  if (!enabled) return null;

  return (
    <div className="aladin-base" aria-hidden="true">
      <div ref={hostRef} className="aladin-hips-host" />
    </div>
  );
}
