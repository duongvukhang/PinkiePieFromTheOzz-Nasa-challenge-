import { useState, type PointerEvent } from "react";
import type { CatalogOverlaySource, DetectionRead, SuggestionItem } from "../../api/client";
import type { WcsParams } from "../aladin/wcs";
import { pixelToSky, skyToPixel } from "../aladin/wcs";

interface WcsSvgOverlayProps {
  wcs: WcsParams;
  gaiaSources: CatalogOverlaySource[];
  catwiseSources: CatalogOverlaySource[];
  detections: DetectionRead[];
  suggestions: SuggestionItem[];
  tracks: string[];
  selectBoxMode: boolean;
  onSelectCoord: (ra: number, dec: number) => void;
}

export function WcsSvgOverlay({
  wcs,
  gaiaSources,
  catwiseSources,
  detections,
  suggestions,
  tracks,
  selectBoxMode,
  onSelectCoord,
}: WcsSvgOverlayProps) {
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [dragCurr, setDragCurr] = useState<{ x: number; y: number } | null>(null);
  const [cursorInfo, setCursorInfo] = useState<{ ra: number; dec: number } | null>(null);

  const pxScale = wcs.naxis1 / 128;
  const toSvg = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return {
      x: ((e.clientX - r.left) / r.width) * wcs.naxis1,
      y: ((e.clientY - r.top) / r.height) * wcs.naxis2,
    };
  };

  return (
    <>
      <svg
        className="viewer-layer-svg"
        viewBox={`0 0 ${wcs.naxis1} ${wcs.naxis2}`}
        preserveAspectRatio="none"
        style={{ pointerEvents: "auto", cursor: selectBoxMode ? "crosshair" : "default" }}
        onPointerDown={(e) => {
          const pt = toSvg(e);
          if (selectBoxMode) {
            setDragStart(pt);
            setDragCurr(pt);
          }
        }}
        onPointerMove={(e) => {
          const pt = toSvg(e);
          setCursorInfo(pixelToSky(pt.x, pt.y, wcs));
          if (dragStart) setDragCurr(pt);
        }}
        onPointerUp={(e) => {
          if (!dragStart) return;
          const pt = toSvg(e);
          const mid = pixelToSky(0.5 * (dragStart.x + pt.x), 0.5 * (dragStart.y + pt.y), wcs);
          onSelectCoord(mid.ra, mid.dec);
          setDragStart(null);
          setDragCurr(null);
        }}
      >
        {gaiaSources.map((s) => {
          const p = skyToPixel(s.ra, s.dec, wcs);
          const r = 2.6 * pxScale;
          return (
            <g key={`gaia-${s.catalog_id}`} transform={`translate(${p.x},${p.y})`}>
              <polygon
                points={`0,${-r} ${r},0 0,${r} ${-r},0`}
                fill="none"
                stroke="var(--oi-sky)"
                strokeWidth={0.9 * pxScale}
              />
            </g>
          );
        })}

        {catwiseSources.map((s) => {
          const p = skyToPixel(s.ra, s.dec, wcs);
          return (
            <circle
              key={`wise-${s.catalog_id}`}
              cx={p.x}
              cy={p.y}
              r={3.2 * pxScale}
              fill="none"
              stroke="var(--oi-orange)"
              strokeWidth={0.9 * pxScale}
            />
          );
        })}

        {detections.map((d) => {
          const p = skyToPixel(d.ra, d.dec, wcs);
          const isSug = suggestions.some((s) => s.detection_id === d.id);
          if (!isSug && detections.length > 40) return null;
          return (
            <g key={`det-${d.id}`} transform={`translate(${p.x},${p.y})`}>
              <rect
                x={-3.5 * pxScale}
                y={-3.5 * pxScale}
                width={7 * pxScale}
                height={7 * pxScale}
                fill="none"
                stroke="var(--oi-green)"
                strokeWidth={0.85 * pxScale}
              />
            </g>
          );
        })}

        {tracks.map((t) => {
          const [label, coords] = t.split(":");
          const [raS, decS] = (coords ?? "").split(",");
          const ra = Number(raS);
          const dec = Number(decS);
          if (!Number.isFinite(ra) || !Number.isFinite(dec)) return null;
          const p = skyToPixel(ra, dec, wcs);
          return (
            <g key={t} transform={`translate(${p.x},${p.y})`}>
              <circle r={5 * pxScale} fill="none" stroke="#faf9f5" strokeWidth={1 * pxScale} />
              <text
                x={6 * pxScale}
                y={-4 * pxScale}
                fill="#faf9f5"
                fontSize={5 * pxScale}
                fontFamily="IBM Plex Mono, monospace"
              >
                {label}
              </text>
            </g>
          );
        })}

        {dragStart && dragCurr && (
          <rect
            x={Math.min(dragStart.x, dragCurr.x)}
            y={Math.min(dragStart.y, dragCurr.y)}
            width={Math.abs(dragCurr.x - dragStart.x)}
            height={Math.abs(dragCurr.y - dragStart.y)}
            fill="rgba(46, 125, 166, 0.2)"
            stroke="var(--oi-sky)"
            strokeWidth={pxScale}
          />
        )}
      </svg>

      {cursorInfo && (
        <div className="viewer-coord-hud">
          ICRS {cursorInfo.ra.toFixed(5)}°, {cursorInfo.dec.toFixed(5)}°
        </div>
      )}

      {/* Accessible mirror table for screen readers (FRONTEND.md §5) */}
      <table className="visually-hidden" aria-label="Visible sky sources in field">
        <thead>
          <tr>
            <th>Catalog</th>
            <th>ID</th>
            <th>RA (deg)</th>
            <th>Dec (deg)</th>
          </tr>
        </thead>
        <tbody>
          {[...gaiaSources, ...catwiseSources].map((s) => (
            <tr key={`${s.catalog}-${s.catalog_id}`}>
              <td>{s.catalog}</td>
              <td>{s.catalog_id}</td>
              <td>{s.ra.toFixed(5)}</td>
              <td>{s.dec.toFixed(5)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
