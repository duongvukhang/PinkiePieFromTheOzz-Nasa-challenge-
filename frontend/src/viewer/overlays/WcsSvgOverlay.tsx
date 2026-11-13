import { useState, type KeyboardEvent, type PointerEvent } from "react";
import type {
  CatalogOverlaySource,
  DetectionRead,
  SuggestionItem,
  TrackedObjectDetailResponse,
} from "../../api/client";
import type { WcsParams } from "../aladin/wcs";
import { pixelToSky, skyToPixel } from "../aladin/wcs";

const OKABE_ITO = [
  "#E69F00",
  "#56B4E9",
  "#009E73",
  "#F0E442",
  "#0072B2",
  "#D55E00",
  "#CC79A7",
];

interface WcsSvgOverlayProps {
  wcs: WcsParams;
  gaiaSources: CatalogOverlaySource[];
  catwiseSources: CatalogOverlaySource[];
  detections: DetectionRead[];
  suggestions: SuggestionItem[];
  tracks: string[];
  selectBoxMode: boolean;
  objectDetail?: TrackedObjectDetailResponse;
  onSelectCoord: (ra: number, dec: number) => void;
  onSelectBox?: (box: { raMin: number; raMax: number; decMin: number; decMax: number }) => void;
}

export function WcsSvgOverlay({
  wcs,
  gaiaSources,
  catwiseSources,
  detections,
  suggestions,
  tracks,
  selectBoxMode,
  objectDetail,
  onSelectCoord,
  onSelectBox,
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

  // Keyboard navigation for box selection & reticle (FRONTEND.md §4.2)
  const onKeyDown = (e: KeyboardEvent<SVGSVGElement>) => {
    if (!selectBoxMode) return;
    const step = e.shiftKey ? 8 : 2;
    const cur = dragCurr ?? { x: wcs.naxis1 / 2, y: wcs.naxis2 / 2 };
    let { x, y } = cur;
    if (e.key === "ArrowRight") x = Math.min(wcs.naxis1, x + step);
    else if (e.key === "ArrowLeft") x = Math.max(0, x - step);
    else if (e.key === "ArrowDown") y = Math.min(wcs.naxis2, y + step);
    else if (e.key === "ArrowUp") y = Math.max(0, y - step);
    else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!dragStart) {
        setDragStart({ x: Math.max(0, x - 10), y: Math.max(0, y - 10) });
        setDragCurr({ x: Math.min(wcs.naxis1, x + 10), y: Math.min(wcs.naxis2, y + 10) });
      } else {
        const p1 = pixelToSky(dragStart.x, dragStart.y, wcs);
        const p2 = pixelToSky(x, y, wcs);
        onSelectBox?.({
          raMin: Math.min(p1.ra, p2.ra),
          raMax: Math.max(p1.ra, p2.ra),
          decMin: Math.min(p1.dec, p2.dec),
          decMax: Math.max(p1.dec, p2.dec),
        });
        setDragStart(null);
        setDragCurr(null);
      }
      return;
    } else return;
    e.preventDefault();
    if (!dragStart && e.shiftKey) setDragStart(cur);
    setDragCurr({ x, y });
    setCursorInfo(pixelToSky(x, y, wcs));
  };

  // Trajectory points & motion vectors for active object
  const measurements = (objectDetail?.measurements ?? [])
    .filter((m) => m.ra != null && m.dec != null)
    .sort((a, b) => a.mjd_tdb - b.mjd_tdb);

  const trajPoints = measurements.map((m) => {
    const p = skyToPixel(m.ra!, m.dec!, wcs);
    const degPerPx = Math.abs(wcs.cd11) || 0.0026;
    const rx = Math.max(1.8 * pxScale, (((m.sig_ra_mas ?? 50) / 3.6e6) / degPerPx) * pxScale);
    const ry = Math.max(1.8 * pxScale, (((m.sig_dec_mas ?? 50) / 3.6e6) / degPerPx) * pxScale);
    return { ...p, epoch: m.epoch, flags: m.flags, rx, ry };
  });

  const motion = objectDetail?.motion_fit;
  const pmVector =
    trajPoints.length > 0 && motion?.pm_total_masyr && motion.pa_deg != null
      ? (() => {
          const last = trajPoints[trajPoints.length - 1]!;
          const degPerPx = Math.abs(wcs.cd11) || 0.0026;
          // Length in pixels proportional to annual motion
          const len = Math.max(8 * pxScale, ((motion.pm_total_masyr / 3.6e6) / degPerPx) * 0.8);
          const rad = (motion.pa_deg * Math.PI) / 180;
          return {
            x1: last.x,
            y1: last.y,
            x2: last.x - Math.sin(rad) * len,
            y2: last.y - Math.cos(rad) * len,
          };
        })()
      : null;

  return (
    <>
      <svg
        className={`viewer-layer-svg${selectBoxMode ? " box-select-active" : ""}`}
        viewBox={`0 0 ${wcs.naxis1} ${wcs.naxis2}`}
        preserveAspectRatio="none"
        tabIndex={selectBoxMode ? 0 : undefined}
        role={selectBoxMode ? "application" : undefined}
        aria-label={selectBoxMode ? "Sky selection box tool (use arrows + Shift to select, Enter to confirm)" : undefined}
        style={{ pointerEvents: "auto", cursor: selectBoxMode ? "crosshair" : "default" }}
        onKeyDown={onKeyDown}
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
          const p1 = pixelToSky(dragStart.x, dragStart.y, wcs);
          const p2 = pixelToSky(pt.x, pt.y, wcs);
          const raMin = Math.min(p1.ra, p2.ra);
          const raMax = Math.max(p1.ra, p2.ra);
          const decMin = Math.min(p1.dec, p2.dec);
          const decMax = Math.max(p1.dec, p2.dec);
          if (onSelectBox) {
            onSelectBox({ raMin, raMax, decMin, decMax });
          } else {
            onSelectCoord(0.5 * (raMin + raMax), 0.5 * (decMin + decMax));
          }
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

        {/* Trajectory polyline across epochs with error ellipses (Feature C) */}
        {trajPoints.length >= 2 && (
          <polyline
            className="trajectory-polyline"
            points={trajPoints.map((p) => `${p.x},${p.y}`).join(" ")}
            fill="none"
            stroke="var(--oi-orange)"
            strokeWidth={1.4 * pxScale}
          />
        )}

        {trajPoints.map((p, i) => {
          const isUncertain = (p.flags ?? 0) > 0;
          return (
            <g key={`meas-${i}`} transform={`translate(${p.x},${p.y})`}>
              <ellipse
                className="error-ellipse"
                rx={p.rx}
                ry={p.ry}
                fill="rgba(245, 158, 11, 0.12)"
                stroke={isUncertain ? "#e88b7d" : "var(--oi-orange)"}
                strokeWidth={1 * pxScale}
                strokeDasharray={isUncertain ? `${2 * pxScale} ${2 * pxScale}` : undefined}
              />
              <circle r={1.6 * pxScale} fill="var(--oi-orange)" />
              {isUncertain && (
                <text
                  x={p.rx + 2 * pxScale}
                  y={3 * pxScale}
                  fill="#e88b7d"
                  fontSize={5 * pxScale}
                  fontWeight="bold"
                >
                  ⚠
                </text>
              )}
            </g>
          );
        })}

        {/* Proper motion vector arrow (Feature C) */}
        {pmVector && (
          <g className="pm-arrow">
            <line
              x1={pmVector.x1}
              y1={pmVector.y1}
              x2={pmVector.x2}
              y2={pmVector.y2}
              stroke="var(--oi-sky)"
              strokeWidth={1.5 * pxScale}
            />
            <circle cx={pmVector.x2} cy={pmVector.y2} r={2 * pxScale} fill="var(--oi-sky)" />
          </g>
        )}

        {tracks.map((t, idx) => {
          const [label, coords] = t.split(":");
          const [raS, decS] = (coords ?? "").split(",");
          const ra = Number(raS);
          const dec = Number(decS);
          if (!Number.isFinite(ra) || !Number.isFinite(dec)) return null;
          const p = skyToPixel(ra, dec, wcs);
          const col = OKABE_ITO[idx % OKABE_ITO.length] || "#faf9f5";
          return (
            <g key={t} transform={`translate(${p.x},${p.y})`}>
              <circle r={5 * pxScale} fill="none" stroke={col} strokeWidth={1.2 * pxScale} />
              <rect
                x={5.5 * pxScale}
                y={-9.5 * pxScale}
                width={8 * pxScale}
                height={7 * pxScale}
                fill="rgba(10, 10, 9, 0.85)"
                rx={1 * pxScale}
              />
              <text
                x={7 * pxScale}
                y={-4 * pxScale}
                fill={col}
                fontSize={5.5 * pxScale}
                fontFamily="IBM Plex Mono, monospace"
                fontWeight="bold"
              >
                {label}
              </text>
            </g>
          );
        })}

        {dragStart && dragCurr && (
          <rect
            className="selection-box"
            x={Math.min(dragStart.x, dragCurr.x)}
            y={Math.min(dragStart.y, dragCurr.y)}
            width={Math.abs(dragCurr.x - dragStart.x)}
            height={Math.abs(dragCurr.y - dragStart.y)}
            fill="rgba(46, 125, 166, 0.25)"
            stroke="var(--oi-sky)"
            strokeWidth={1.2 * pxScale}
            strokeDasharray={`${3 * pxScale} ${2 * pxScale}`}
          />
        )}
      </svg>

      {cursorInfo && (
        <div className="viewer-coord-hud">
          ICRS {cursorInfo.ra.toFixed(5)}°, {cursorInfo.dec.toFixed(5)}°
          {selectBoxMode && <span> · Box Drag active</span>}
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
