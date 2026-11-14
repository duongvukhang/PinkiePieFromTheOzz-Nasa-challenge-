import { useState, type KeyboardEvent, type PointerEvent } from "react";
import type {
  CatalogOverlaySource,
  DetectionRead,
  SuggestionItem,
  TrackedObjectDetailResponse,
} from "../../api/client";
import type { WcsParams } from "../aladin/wcs";
import { pixelToSky, skyToPixel } from "../aladin/wcs";
import { useViewStore } from "../state/viewStore";
export interface SelectedSourceData {
  id: string;
  label: string;
  ra: number;
  dec: number;
  pixel_x?: number;
  pixel_y?: number;
  pos_err_arcsec?: number;
  peak_flux?: number;
  flux_unit?: string;
  snr?: number | null;
  fwhm_pixels?: number;
  isTrack: boolean;
}

const OKABE_ITO = ["#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7"];

/** Computes shortest-arc RA bounds across the 0°/360° meridian. */
export function computeShortestArcRaBounds(
  ra1: number,
  ra2: number,
): { raMin: number; raMax: number } {
  const n1 = ((ra1 % 360) + 360) % 360;
  const n2 = ((ra2 % 360) + 360) % 360;
  const diff = n2 - n1;
  if (Math.abs(diff) <= 180) {
    return { raMin: Math.min(n1, n2), raMax: Math.max(n1, n2) };
  }
  return { raMin: Math.max(n1, n2), raMax: Math.min(n1, n2) };
}

interface WcsSvgOverlayProps {
  wcs: WcsParams;
  gaiaSources: CatalogOverlaySource[];
  catwiseSources: CatalogOverlaySource[];
  detections: DetectionRead[];
  suggestions: SuggestionItem[];
  tracks: string[];
  selectBoxMode: boolean;
  objectDetail?: TrackedObjectDetailResponse;
  selectedTarget?: SelectedSourceData | null;
  currentEpoch?: string;
  showGrid?: boolean;
  showSources?: boolean;
  showLabels?: boolean;
  showTrajectories?: boolean;
  showUncertainty?: boolean;
  predictionData?: {
    trajectory_points?: Array<{
      ra_deg: number;
      dec_deg: number;
      uncertainty_semi_major_arcsec?: number;
      uncertainty_semi_minor_arcsec?: number;
    }>;
  };
  onSelectCoord: (ra: number, dec: number) => void;
  onSelectBox?: (box: { raMin: number; raMax: number; decMin: number; decMax: number }) => void;
  onSelectDetection?: (detection: DetectionRead) => void;
  onTrackDetection?: (detection: DetectionRead) => void;
  onSelectTrack?: (trackLabel: string) => void;
  onSelectEpoch?: (epoch: string) => void;
}

export function WcsSvgOverlay({
  wcs,
  gaiaSources,
  catwiseSources,
  detections,
  suggestions = [],
  tracks,
  selectBoxMode,
  objectDetail,
  selectedTarget,
  currentEpoch,
  showGrid = false,
  showSources = true,
  showLabels = true,
  showTrajectories = true,
  showUncertainty = false,
  predictionData,
  onSelectCoord,
  onSelectBox,
  onSelectDetection,
  onTrackDetection,
  onSelectTrack,
  onSelectEpoch,
}: WcsSvgOverlayProps) {
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [dragCurr, setDragCurr] = useState<{ x: number; y: number } | null>(null);
  const [capNotice, setCapNotice] = useState<string | null>(null);
  const { activeBox, setActiveBox, playing, frame } = useViewStore();

  const isInActiveBox = (ra: number, dec: number) => {
    if (!activeBox) return false;
    const inRa =
      activeBox.raMin <= activeBox.raMax
        ? ra >= activeBox.raMin && ra <= activeBox.raMax
        : ra >= activeBox.raMin || ra <= activeBox.raMax;
    const inDec =
      activeBox.decMin <= activeBox.decMax
        ? dec >= activeBox.decMin && dec <= activeBox.decMax
        : dec >= activeBox.decMax && dec <= activeBox.decMin;
    return inRa && inDec;
  };

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
    let { x, y } = dragCurr ?? { x: wcs.naxis1 / 2, y: wcs.naxis2 / 2 };
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
        const [p1, p2] = [pixelToSky(dragStart.x, dragStart.y, wcs), pixelToSky(x, y, wcs)];
        const { raMin, raMax } = computeShortestArcRaBounds(p1.ra, p2.ra);
        onSelectBox?.({
          raMin,
          raMax,
          decMin: Math.min(p1.dec, p2.dec),
          decMax: Math.max(p1.dec, p2.dec),
        });
        setDragStart(null);
        setDragCurr(null);
      }
      return;
    } else return;
    e.preventDefault();
    if (!dragStart && e.shiftKey) setDragStart({ x, y });
    setDragCurr({ x, y });
  };

  // Trajectory points & motion vectors for active object
  const measurements = (objectDetail?.measurements ?? [])
    .filter((m) => m.ra != null && m.dec != null)
    .sort((a, b) => a.mjd_tdb - b.mjd_tdb);

  const trajPoints = measurements.map((m, mIdx) => {
    const p = skyToPixel(m.ra!, m.dec!, wcs);
    const degPerPx = Math.abs(wcs.cd11) || 0.0026;
    const rx = Math.max(1.8 * pxScale, ((m.sig_ra_mas ?? 50) / 3.6e6 / degPerPx) * pxScale);
    const ry = Math.max(1.8 * pxScale, ((m.sig_dec_mas ?? 50) / 3.6e6 / degPerPx) * pxScale);
    return { ...p, epoch: m.epoch ?? `E${mIdx + 1}`, flags: m.flags, rx, ry, mjd: m.mjd_tdb, idx: mIdx };
  });

  const activeEpochIndex = (() => {
    if (!currentEpoch || trajPoints.length === 0) return trajPoints.length - 1;
    const found = trajPoints.findIndex((p) => p.epoch === currentEpoch);
    if (found >= 0) return found;
    return Math.min(Math.max(0, frame), trajPoints.length - 1);
  })();

  const motion = objectDetail?.motion_fit;
  const pmVector =
    trajPoints.length > 0 && motion?.pm_total_masyr && motion.pa_deg != null
      ? (() => {
          const last = trajPoints[trajPoints.length - 1]!;
          const degPerPx = Math.abs(wcs.cd11) || 0.0026;
          // Length in pixels proportional to annual motion
          const len = Math.max(8 * pxScale, (motion.pm_total_masyr / 3.6e6 / degPerPx) * 0.8);
          const rad = (motion.pa_deg * Math.PI) / 180;
          return {
            x1: last.x,
            y1: last.y,
            x2: last.x - Math.sin(rad) * len,
            y2: last.y - Math.cos(rad) * len,
          };
        })()
      : null;

  // Precision target reticle pixel position
  const selectedPos = selectedTarget
    ? skyToPixel(selectedTarget.ra, selectedTarget.dec, wcs)
    : null;

  // Dropout / non-detection predicted position in current epoch
  const dropoutPrediction = (() => {
    if (!objectDetail || measurements.length === 0 || !currentEpoch) return null;
    const hasCurrent = measurements.some(
      (m) => m.epoch === currentEpoch && m.ra != null && m.dec != null,
    );
    if (hasCurrent) return null;
    const sorted = [...measurements].sort((a, b) => a.mjd_tdb - b.mjd_tdb);
    const mLast = sorted[sorted.length - 1]!;
    let predRa = mLast.ra!;
    let predDec = mLast.dec!;
    if (motion?.pm_total_masyr && motion.pa_deg != null && motion.ref_epoch_mjd_tdb) {
      const dtYears = (mLast.mjd_tdb - motion.ref_epoch_mjd_tdb) / 365.25;
      const rad = (motion.pa_deg * Math.PI) / 180;
      predRa += (motion.pm_total_masyr * Math.sin(rad) * dtYears) / 3.6e6;
      predDec += (motion.pm_total_masyr * Math.cos(rad) * dtYears) / 3.6e6;
    }
    const pos = skyToPixel(predRa, predDec, wcs);
    return { ...pos, label: objectDetail.label || "TARGET" };
  })();

  return (
    <>
      <svg
        className={`viewer-layer-svg${selectBoxMode ? " box-select-active" : ""}`}
        viewBox={`0 0 ${wcs.naxis1} ${wcs.naxis2}`}
        preserveAspectRatio="none"
        tabIndex={selectBoxMode ? 0 : undefined}
        role={selectBoxMode ? "application" : undefined}
        aria-label={
          selectBoxMode
            ? "Sky selection box tool (use arrows + Shift to select, Enter to confirm)"
            : undefined
        }
        style={{
          pointerEvents: selectBoxMode ? "auto" : "none",
          cursor: selectBoxMode ? "crosshair" : "default",
        }}
        onKeyDown={onKeyDown}
        onPointerDown={(e) => {
          const pt = toSvg(e);
          if (selectBoxMode) {
            setDragStart(pt);
            setDragCurr(pt);
          }
        }}
        onPointerMove={(e) => {
          if (dragStart) {
            const pt = toSvg(e);
            setDragCurr(pt);
          }
        }}
        onPointerUp={(e) => {
          if (!dragStart) return;
          const pt = toSvg(e);
          const dx = Math.abs(pt.x - dragStart.x);
          const dy = Math.abs(pt.y - dragStart.y);
          if (dx >= 2 || dy >= 2) {
            const p1 = pixelToSky(dragStart.x, dragStart.y, wcs);
            const p2 = pixelToSky(pt.x, pt.y, wcs);
            const { raMin, raMax } = computeShortestArcRaBounds(p1.ra, p2.ra);
            const decMin = Math.min(p1.dec, p2.dec);
            const decMax = Math.max(p1.dec, p2.dec);
            if (onSelectBox) {
              onSelectBox({ raMin, raMax, decMin, decMax });
            } else {
              const midRa =
                raMin <= raMax ? 0.5 * (raMin + raMax) : (0.5 * (raMin + raMax + 360.0)) % 360.0;
              onSelectCoord(midRa, 0.5 * (decMin + decMax));
            }
            setActiveBox({ raMin, raMax, decMin, decMax });
          }
          setDragStart(null);
          setDragCurr(null);
        }}
      >
        <defs>
          <marker
            id="trajArrow"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="5"
            markerHeight="5"
            orient="auto"
          >
            <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="var(--oi-orange)" />
          </marker>
          <marker
            id="predArrow"
            viewBox="0 0 10 10"
            refX="6"
            refY="5"
            markerWidth="4"
            markerHeight="4"
            orient="auto-start-reverse"
          >
            <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#f59e0b" />
          </marker>
        </defs>

        {/* 1. Coordinate Grid Lines */}
        {showGrid && (
          <g className="pointer-events-none opacity-40">
            {[0.2, 0.4, 0.6, 0.8].map((frac, idx) => {
              const ptX = pixelToSky(wcs.naxis1 * frac, wcs.naxis2 / 2, wcs);
              const ptY = pixelToSky(wcs.naxis1 / 2, wcs.naxis2 * frac, wcs);
              return (
                <g key={`grid_${idx}`}>
                  <line
                    x1={wcs.naxis1 * frac}
                    y1={0}
                    x2={wcs.naxis1 * frac}
                    y2={wcs.naxis2}
                    stroke="rgba(34, 211, 238, 0.4)"
                    strokeWidth={1}
                    strokeDasharray="4,4"
                  />
                  <text
                    x={wcs.naxis1 * frac + 3}
                    y={12}
                    fill="rgba(34, 211, 238, 0.7)"
                    fontSize="9px"
                    fontFamily="monospace"
                  >
                    {ptX.ra.toFixed(2)}°
                  </text>
                  <line
                    x1={0}
                    y1={wcs.naxis2 * frac}
                    x2={wcs.naxis1}
                    y2={wcs.naxis2 * frac}
                    stroke="rgba(34, 211, 238, 0.4)"
                    strokeWidth={1}
                    strokeDasharray="4,4"
                  />
                  <text
                    x={4}
                    y={wcs.naxis2 * frac - 3}
                    fill="rgba(34, 211, 238, 0.7)"
                    fontSize="9px"
                    fontFamily="monospace"
                  >
                    {ptY.dec.toFixed(2)}°
                  </text>
                </g>
              );
            })}
          </g>
        )}
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

        {/* 4. Detected Sources Overlay — aligned green bounding squares with persistent track labels */}
        {showSources &&
          detections.map((d) => {
            const hasPix = d.x_pix != null && d.y_pix != null;
            const p = hasPix
              ? { x: d.x_pix!, y: wcs.naxis2 - d.y_pix! }
              : skyToPixel(d.ra, d.dec, wcs);
            if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) return null;
            if (p.x < -10 || p.x > wcs.naxis1 + 10 || p.y < -10 || p.y > wcs.naxis2 + 10) {
              return null;
            }
            const screenX = p.x;
            const screenY = p.y;
            const dLabel =
              d.x_pix != null && d.y_pix != null
                ? `src_${d.id.toString(16).padStart(8, "0")}_${Math.round(d.x_pix)}_${Math.round(d.y_pix)}`
                : `src_${d.id}`;
            const isSelected =
              selectedTarget?.label === dLabel ||
              String(d.id) === selectedTarget?.label ||
              selectedTarget?.label === `DET #${d.id}`;
            const degPerPx = Math.abs(wcs.cd11) || 0.0026;
            const fwhmArcsec = d.fwhm_arcsec != null && d.fwhm_arcsec > 0 ? d.fwhm_arcsec : degPerPx * 3600 * 2.2;
            const fwhmPx = fwhmArcsec / (3600 * degPerPx);
            const baseFitPx = Math.max(7, Math.min(26, fwhmPx * 1.4));
            const size = (isSelected ? Math.max(baseFitPx * 1.25, 12) : baseFitPx) * pxScale;
            const half = size / 2;

            const matchedTrack = tracks.find((t) => {
              const [, coords] = t.split(":");
              const coordsArr = (coords ?? "").split(",").map(Number);
              const r = coordsArr[0];
              const dec = coordsArr[1];
              return (
                r != null &&
                dec != null &&
                Math.abs(r - d.ra) < 0.001 &&
                Math.abs(dec - d.dec) < 0.001
              );
            });
            const trackLabel = matchedTrack ? matchedTrack.split(":")[0] : null;
            const isConfirmed = Boolean(trackLabel);
            const inBox = isInActiveBox(d.ra, d.dec);
            if (!isSelected && !isConfirmed && !inBox) {
              return null;
            }
            const isConflict = Boolean(trackLabel && matchedTrack?.includes("CONFLICT"));
            const conf = (d as { confidence?: number }).confidence;
            const isAmbiguous = Boolean((d.flags && d.flags & 4) || (conf != null && conf < 0.90));
            const targetColor = trackLabel === "B" ? "#f97316" : trackLabel === "A" ? "#38bdf8" : "#22c55e";
            const detStroke = isConflict
              ? "#f59e0b"
              : isAmbiguous
                ? "#fbbf24"
                : isConfirmed
                  ? targetColor
                  : isSelected
                    ? "#4ade80"
                    : "#22c55e";
            const displayLabel = trackLabel
              ? `${trackLabel} (${dLabel})${isConflict ? " [CONFLICT]" : isAmbiguous ? " [REVIEW]" : ""}`
              : dLabel;

            return (
              <g
                key={`det-${d.id}`}
                style={{ pointerEvents: selectBoxMode ? "none" : "auto", cursor: "pointer" }}
                role="button"
                tabIndex={0}
                aria-label={`Detection ${displayLabel}`}
                onClick={(e) => {
                  e.stopPropagation();
                  if (isConfirmed) {
                    onSelectTrack?.(trackLabel!);
                  } else if (inBox && onTrackDetection) {
                    if (tracks.length >= 2) {
                      setCapNotice("Tracking cap reached: Max 2 targets (Target A, Target B). Untrack one first.");
                      setTimeout(() => setCapNotice(null), 3500);
                      onSelectDetection?.(d);
                    } else {
                      onTrackDetection(d);
                    }
                  } else {
                    onSelectDetection?.(d);
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.stopPropagation();
                    if (isConfirmed) {
                      onSelectTrack?.(trackLabel!);
                    } else if (inBox && onTrackDetection && tracks.length < 2) {
                      onTrackDetection(d);
                    } else {
                      onSelectDetection?.(d);
                    }
                  }
                }}
              >
                <rect
                  x={screenX - half}
                  y={screenY - half}
                  width={size}
                  height={size}
                  fill={isSelected ? "rgba(34, 197, 94, 0.15)" : isConfirmed ? (trackLabel === "B" ? "rgba(249, 115, 22, 0.15)" : "rgba(56, 189, 248, 0.15)") : inBox ? "rgba(34, 197, 94, 0.06)" : "none"}
                  stroke={detStroke}
                  strokeWidth={isSelected ? 1.5 : 1}
                  strokeDasharray={isAmbiguous ? "3,2" : undefined}
                  vectorEffect="non-scaling-stroke"
                />
                {trackLabel && (
                  <g transform={`translate(${screenX - half}, ${screenY - half - 12 * pxScale})`}>
                    <rect
                      x={0}
                      y={0}
                      width={18 * pxScale}
                      height={10 * pxScale}
                      rx={2}
                      fill={trackLabel === "B" ? "#ea580c" : "#0284c7"}
                    />
                    <text
                      x={9 * pxScale}
                      y={7.5 * pxScale}
                      fill="#ffffff"
                      fontSize={7.5 * pxScale}
                      fontWeight="bold"
                      textAnchor="middle"
                      fontFamily="monospace"
                    >
                      {trackLabel}
                    </text>
                  </g>
                )}
                {isAmbiguous && (
                  <text
                    x={screenX + half + 2 * pxScale}
                    y={screenY + 2.5 * pxScale}
                    fill="#fbbf24"
                    fontSize={6 * pxScale}
                    fontWeight="bold"
                    aria-label="Uncertain match"
                  >
                    ⚠
                  </text>
                )}
                {showUncertainty && (
                  <circle
                    cx={screenX}
                    cy={screenY}
                    r={Math.max(4, d.fwhm_arcsec ? d.fwhm_arcsec / 1.5 : 6)}
                    fill="none"
                    stroke={detStroke}
                    strokeWidth={1}
                    strokeDasharray="2,2"
                    opacity={0.6}
                    vectorEffect="non-scaling-stroke"
                  />
                )}
                {(showLabels || isSelected) && (
                  <text
                    x={screenX + half + 3}
                    y={screenY - half + 2}
                    fill={isSelected ? "#4ade80" : detStroke}
                    fontSize={isSelected ? 11 : 10}
                    fontFamily="monospace"
                    fontWeight={isSelected ? "bold" : "normal"}
                    className="pointer-events-none select-none drop-shadow"
                  >
                    {displayLabel}
                  </text>
                )}
              </g>
            );
          })}

        {/* Suggestions Overlay — dashed purple reticles */}
        {showSources &&
          suggestions?.map((s) => {
            const pt = skyToPixel(s.ra, s.dec, wcs);
            return (
              <g key={`sugg-${s.detection_id}`} className="pointer-events-none">
                <circle
                  cx={pt.x}
                  cy={pt.y}
                  r={12}
                  fill="none"
                  stroke="#c084fc"
                  strokeWidth={1.5}
                  strokeDasharray="3 2"
                />
                {showLabels && (
                  <text
                    x={pt.x + 14}
                    y={pt.y - 4}
                    fill="#c084fc"
                    fontSize="10"
                    fontFamily="monospace"
                  >
                    {s.summary ?? `SUGG (${Math.round(s.score * 100)}%)`}
                  </text>
                )}
              </g>
            );
          })}

        {/* 2. Directional Trajectory Vectors with Arrowheads and Glow (Feature C) */}
        {showTrajectories &&
          trajPoints.length >= 2 &&
          trajPoints.map((pt, idx) => {
            if (idx === 0) return null;
            const prev = trajPoints[idx - 1]!;
            const isDrawn = !playing || idx <= activeEpochIndex;
            const isLeading = playing && idx === activeEpochIndex;

            return (
              <g key={`traj-seg-${idx}`} className="pointer-events-none">
                {/* Atmospheric glow beneath active trajectory */}
                {isDrawn && (
                  <line
                    x1={prev.x}
                    y1={prev.y}
                    x2={pt.x}
                    y2={pt.y}
                    stroke="var(--oi-orange)"
                    strokeWidth={(isLeading ? 4.5 : 3.5) * pxScale}
                    strokeOpacity={isLeading ? 0.45 : 0.25}
                  />
                )}
                {/* Directional trajectory vector line with arrowhead */}
                <line
                  x1={prev.x}
                  y1={prev.y}
                  x2={pt.x}
                  y2={pt.y}
                  stroke="var(--oi-orange)"
                  strokeWidth={(isLeading ? 1.8 : 1.4) * pxScale}
                  strokeOpacity={isDrawn ? 0.95 : 0.15}
                  strokeDasharray={isDrawn ? undefined : "3,3"}
                  markerEnd={isDrawn ? "url(#trajArrow)" : undefined}
                />
              </g>
            );
          })}

        {/* 3. Clickable Waypoint Badges along Trajectory (E1, E2, E3...) */}
        {trajPoints.map((pt, idx) => {
          const isReached = !playing || idx <= activeEpochIndex;
          const isCurrent = idx === activeEpochIndex;
          return (
            <g
              key={`traj-wpt-${idx}`}
              transform={`translate(${pt.x},${pt.y})`}
              style={{ pointerEvents: selectBoxMode ? "none" : "auto", cursor: "pointer" }}
              role="button"
              tabIndex={0}
              opacity={isReached ? 1 : 0.35}
              onClick={(e) => {
                e.stopPropagation();
                if (pt.epoch) onSelectEpoch?.(pt.epoch);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.stopPropagation();
                  if (pt.epoch) onSelectEpoch?.(pt.epoch);
                }
              }}
              aria-label={`Jump to Epoch ${pt.epoch ?? idx + 1}`}
            >
              <circle
                r={(isCurrent ? 6.5 : 4) * pxScale}
                fill={isCurrent ? "rgba(245, 158, 11, 0.35)" : "rgba(245, 158, 11, 0.15)"}
                stroke={isCurrent ? "#ffffff" : "var(--oi-orange)"}
                strokeWidth={(isCurrent ? 1.4 : 0.9) * pxScale}
                strokeDasharray={isCurrent ? undefined : `${2 * pxScale},${2 * pxScale}`}
              />
              <circle
                r={(isCurrent ? 3.5 : 2) * pxScale}
                fill={isCurrent ? "#ffffff" : "var(--oi-orange)"}
                stroke="var(--oi-orange)"
                strokeWidth={0.8 * pxScale}
              />
              <text
                x={6 * pxScale}
                y={-4 * pxScale}
                fill={isCurrent ? "#ffffff" : "var(--oi-orange)"}
                fontSize={5 * pxScale}
                fontFamily="IBM Plex Mono, monospace"
                fontWeight="bold"
                className="select-none pointer-events-none"
                style={{ textShadow: "0 1px 3px rgba(0,0,0,0.95)" }}
              >
                E{idx + 1}
              </text>
            </g>
          );
        })}

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

        {/* Prediction dashed trajectory vector in amber with uncertainty ellipses */}
        {predictionData?.trajectory_points && predictionData.trajectory_points.length > 1 && (
          <g className="pointer-events-none">
            {predictionData.trajectory_points.map((pt, idx) => {
              if (idx === 0) return null;
              const prev = predictionData.trajectory_points![idx - 1]!;
              const p1 = skyToPixel(prev.ra_deg, prev.dec_deg, wcs);
              const p2 = skyToPixel(pt.ra_deg, pt.dec_deg, wcs);
              const isLast = idx === predictionData.trajectory_points!.length - 1;

              return (
                <g key={`pred_seg_${idx}`}>
                  <line
                    x1={p1.x}
                    y1={p1.y}
                    x2={p2.x}
                    y2={p2.y}
                    stroke="rgba(245, 158, 11, 0.25)"
                    strokeWidth={2.5 * pxScale}
                    vectorEffect="non-scaling-stroke"
                  />
                  <line
                    x1={p1.x}
                    y1={p1.y}
                    x2={p2.x}
                    y2={p2.y}
                    stroke="#f59e0b"
                    strokeWidth={1.5 * pxScale}
                    strokeDasharray="4,3"
                    vectorEffect="non-scaling-stroke"
                    markerEnd={isLast ? "url(#predArrow)" : undefined}
                  />
                  <circle
                    cx={p2.x}
                    cy={p2.y}
                    r={2.5 * pxScale}
                    fill="#f59e0b"
                    stroke="#ffffff"
                    strokeWidth={1 * pxScale}
                    vectorEffect="non-scaling-stroke"
                  />
                  {showUncertainty && pt.uncertainty_semi_major_arcsec != null && (
                    <ellipse
                      cx={p2.x}
                      cy={p2.y}
                      rx={Math.max(3 * pxScale, pt.uncertainty_semi_major_arcsec / (pxScale * 6.2))}
                      ry={Math.max(
                        2 * pxScale,
                        (pt.uncertainty_semi_minor_arcsec ?? pt.uncertainty_semi_major_arcsec) /
                          (pxScale * 6.2),
                      )}
                      fill="rgba(245, 158, 11, 0.12)"
                      stroke="#f59e0b"
                      strokeWidth={1 * pxScale}
                      strokeDasharray="2,2"
                      opacity={0.8}
                      vectorEffect="non-scaling-stroke"
                    />
                  )}
                </g>
              );
            })}
          </g>
        )}

        {/* 4. Precision Telescope Reticle & Corner Brackets on Selected Object */}
        {selectedPos && (
          <g className="selected-target-reticle" style={{ pointerEvents: "none" }}>
            {/* Pulsing outer dashed ring */}
            <circle
              cx={selectedPos.x}
              cy={selectedPos.y}
              r={14 * pxScale}
              fill="none"
              stroke="var(--oi-sky)"
              strokeWidth={1.2 * pxScale}
              strokeDasharray={`${3.5 * pxScale},${2.5 * pxScale}`}
            />
            {/* 4 crosshair lines extending outward */}
            <line
              x1={selectedPos.x - 18 * pxScale}
              y1={selectedPos.y}
              x2={selectedPos.x - 7 * pxScale}
              y2={selectedPos.y}
              stroke="var(--oi-sky)"
              strokeWidth={1.2 * pxScale}
            />
            <line
              x1={selectedPos.x + 7 * pxScale}
              y1={selectedPos.y}
              x2={selectedPos.x + 18 * pxScale}
              y2={selectedPos.y}
              stroke="var(--oi-sky)"
              strokeWidth={1.2 * pxScale}
            />
            <line
              x1={selectedPos.x}
              y1={selectedPos.y - 18 * pxScale}
              x2={selectedPos.x}
              y2={selectedPos.y - 7 * pxScale}
              stroke="var(--oi-sky)"
              strokeWidth={1.2 * pxScale}
            />
            <line
              x1={selectedPos.x}
              y1={selectedPos.y + 7 * pxScale}
              x2={selectedPos.x}
              y2={selectedPos.y + 18 * pxScale}
              stroke="var(--oi-sky)"
              strokeWidth={1.2 * pxScale}
            />
            {/* Corner brackets */}
            <path
              d={`M ${selectedPos.x - 10 * pxScale} ${selectedPos.y - 6 * pxScale} L ${selectedPos.x - 10 * pxScale} ${selectedPos.y - 10 * pxScale} L ${selectedPos.x - 6 * pxScale} ${selectedPos.y - 10 * pxScale}
                  M ${selectedPos.x + 6 * pxScale} ${selectedPos.y - 10 * pxScale} L ${selectedPos.x + 10 * pxScale} ${selectedPos.y - 10 * pxScale} L ${selectedPos.x + 10 * pxScale} ${selectedPos.y - 6 * pxScale}
                  M ${selectedPos.x - 10 * pxScale} ${selectedPos.y + 6 * pxScale} L ${selectedPos.x - 10 * pxScale} ${selectedPos.y + 10 * pxScale} L ${selectedPos.x - 6 * pxScale} ${selectedPos.y + 10 * pxScale}
                  M ${selectedPos.x + 6 * pxScale} ${selectedPos.y + 10 * pxScale} L ${selectedPos.x + 10 * pxScale} ${selectedPos.y + 10 * pxScale} L ${selectedPos.x + 10 * pxScale} ${selectedPos.y + 6 * pxScale}`}
              fill="none"
              stroke="var(--oi-sky)"
              strokeWidth={1.2 * pxScale}
            />
            <circle cx={selectedPos.x} cy={selectedPos.y} r={1.6 * pxScale} fill="#ffffff" />
            {selectedTarget && (
              <text
                x={selectedPos.x + 13 * pxScale}
                y={selectedPos.y - 6 * pxScale}
                fill="var(--oi-sky)"
                fontSize={5.5 * pxScale}
                fontFamily="IBM Plex Mono, monospace"
                fontWeight="bold"
                style={{ textShadow: "0 1px 4px rgba(0,0,0,0.95)" }}
              >
                {selectedTarget.label}
              </text>
            )}
          </g>
        )}

        {/* 5. Dropout / Non-detection Target Lock in Current Epoch */}
        {dropoutPrediction && (
          <g className="dropout-target-lock" style={{ pointerEvents: "none" }}>
            <circle
              cx={dropoutPrediction.x}
              cy={dropoutPrediction.y}
              r={16 * pxScale}
              fill="rgba(245, 158, 11, 0.08)"
              stroke="#f59e0b"
              strokeWidth={1.3 * pxScale}
              strokeDasharray={`${3.5 * pxScale},${2.5 * pxScale}`}
            />
            <line
              x1={dropoutPrediction.x - 13 * pxScale}
              y1={dropoutPrediction.y}
              x2={dropoutPrediction.x + 13 * pxScale}
              y2={dropoutPrediction.y}
              stroke="#f59e0b"
              strokeWidth={1 * pxScale}
            />
            <line
              x1={dropoutPrediction.x}
              y1={dropoutPrediction.y - 13 * pxScale}
              x2={dropoutPrediction.x}
              y2={dropoutPrediction.y + 13 * pxScale}
              stroke="#f59e0b"
              strokeWidth={1 * pxScale}
            />
            <text
              x={dropoutPrediction.x + 15 * pxScale}
              y={dropoutPrediction.y - 5 * pxScale}
              fill="#f59e0b"
              fontSize={5 * pxScale}
              fontFamily="IBM Plex Mono, monospace"
              fontWeight="bold"
              style={{ textShadow: "0 1px 3px rgba(0,0,0,0.95)" }}
            >
              TARGET (Non-detection in this epoch)
            </text>
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
            <g
              key={t}
              transform={`translate(${p.x},${p.y})`}
              style={{ pointerEvents: selectBoxMode ? "none" : "auto", cursor: "pointer" }}
              role="button"
              tabIndex={0}
              aria-label={`Track ${label}`}
              onClick={(e) => {
                e.stopPropagation();
                if (label) onSelectTrack?.(label);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.stopPropagation();
                  if (label) onSelectTrack?.(label);
                }
              }}
            >
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
            stroke="none"
          />
        )}

        {!dragStart && activeBox && (() => {
          const p1 = skyToPixel(activeBox.raMin, activeBox.decMin, wcs);
          const p2 = skyToPixel(activeBox.raMax, activeBox.decMax, wcs);
          const bx = Math.min(p1.x, p2.x);
          const by = Math.min(p1.y, p2.y);
          const bw = Math.abs(p2.x - p1.x);
          const bh = Math.abs(p2.y - p1.y);
          if (bw < 1 || bh < 1) return null;
          return (
            <rect
              className="selection-box active-selection-box"
              x={bx}
              y={by}
              width={bw}
              height={bh}
              fill="rgba(46, 125, 166, 0.25)"
              stroke="#38bdf8"
              strokeWidth={1.2}
              strokeDasharray="4 2"
              vectorEffect="non-scaling-stroke"
            />
          );
        })()}

        {capNotice && (
          <g transform={`translate(${wcs.naxis1 / 2}, ${24 * pxScale})`} className="pointer-events-none">
            <rect
              x={-140 * pxScale}
              y={-12 * pxScale}
              width={280 * pxScale}
              height={24 * pxScale}
              rx={6 * pxScale}
              fill="rgba(15, 23, 42, 0.92)"
              stroke="#f59e0b"
              strokeWidth={1 * pxScale}
            />
            <text
              x={0}
              y={4 * pxScale}
              fill="#fcd34d"
              fontSize={8.5 * pxScale}
              fontFamily="sans-serif"
              textAnchor="middle"
              fontWeight="bold"
            >
              {capNotice}
            </text>
          </g>
        )}
      </svg>

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
