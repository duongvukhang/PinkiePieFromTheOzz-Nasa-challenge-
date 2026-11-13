import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CatalogOverlaySource,
  CutoutFrame,
  DetectionRead,
  ScienceBuffer,
  SuggestionItem,
  TrackedObjectDetailResponse,
} from "../../api/client";
import { AladinBase } from "../aladin/AladinBase";
import type { WcsParams } from "../aladin/wcs";
import { skyToPixel } from "../aladin/wcs";
import { WebGL2SkyCanvas } from "../gl/WebGL2SkyCanvas";
import { WcsSvgOverlay } from "../overlays/WcsSvgOverlay";
import { useViewStore } from "../state/viewStore";
import { nextTrackLabel } from "../state/useViewerData";
import { SelectedObjectBar } from "./SelectedObjectBar";
import {
  clampZoom,
  clientToSkyCoords,
  computeCenterPan,
  computeZoomAtPoint,
} from "./viewerTransform";

interface ViewerStageProps {
  wcs: WcsParams;
  ra: number;
  dec: number;
  fov: number;
  frameA?: CutoutFrame;
  bufA?: ScienceBuffer;
  bufB?: ScienceBuffer;
  gaiaSources: CatalogOverlaySource[];
  catwiseSources: CatalogOverlaySource[];
  detections: DetectionRead[];
  suggestions: SuggestionItem[];
  selectBoxMode: boolean;
  objectDetail?: TrackedObjectDetailResponse;
  onBoxSelect?: (box: { raMin: number; raMax: number; decMin: number; decMax: number }) => void;
  onVisibilityPause: (paused: boolean) => void;
  loading?: boolean;
  errorMsg?: string;
  emptyWarning?: string;
}

export function ViewerStage({
  wcs,
  ra,
  dec,
  fov,
  frameA,
  bufA,
  bufB,
  gaiaSources,
  catwiseSources,
  detections,
  suggestions,
  selectBoxMode,
  objectDetail,
  onBoxSelect,
  onVisibilityPause,
  loading,
  errorMsg,
  emptyWarning,
}: ViewerStageProps) {
  const { op, stretch, colormap, swipe, hips, track, obj, addTrack, setView } = useViewStore();

  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [isPanning, setIsPanning] = useState(false);
  const [cursorInfo, setCursorInfo] = useState<{ ra: number; dec: number } | null>(null);

  const viewportRef = useRef<HTMLDivElement>(null);
  const panStartRef = useRef<{
    startX: number;
    startY: number;
    initPanX: number;
    initPanY: number;
  } | null>(null);

  // Reset view on coordinate target change; preserve zoom/pan across blink epochs
  const prevCoordsRef = useRef({ ra, dec });
  useEffect(() => {
    if (prevCoordsRef.current.ra !== ra || prevCoordsRef.current.dec !== dec) {
      prevCoordsRef.current = { ra, dec };
      setPan({ x: 0, y: 0 });
      setZoom(1);
    }
  }, [ra, dec]);

  const resetView = useCallback(() => {
    setPan({ x: 0, y: 0 });
    setZoom(1);
  }, []);

  const centerTarget = useCallback(
    (targetRa: number, targetDec: number) => {
      const vp = viewportRef.current;
      if (!vp) return;
      const rect = vp.getBoundingClientRect();
      const px = skyToPixel(targetRa, targetDec, wcs);
      const targetZoom = Math.max(zoom, 2.5);
      const nextPan = computeCenterPan(
        px.x,
        px.y,
        wcs.naxis1,
        wcs.naxis2,
        rect.width,
        rect.height,
        targetZoom,
      );
      setZoom(targetZoom);
      setPan(nextPan);
    },
    [wcs, zoom],
  );

  const onWheel = useCallback(
    (e: React.WheelEvent<HTMLDivElement>) => {
      e.preventDefault();
      const vp = viewportRef.current;
      if (!vp) return;
      const rect = vp.getBoundingClientRect();
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      const res = computeZoomAtPoint(
        zoom,
        factor,
        e.clientX,
        e.clientY,
        pan.x,
        pan.y,
        rect.left,
        rect.top,
      );
      setZoom(res.zoom);
      setPan({ x: res.panX, y: res.panY });
    },
    [zoom, pan],
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (selectBoxMode || e.button !== 0) return;
      panStartRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        initPanX: pan.x,
        initPanY: pan.y,
      };
      setIsPanning(true);
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    },
    [selectBoxMode, pan],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const vp = viewportRef.current;
      if (vp) {
        const rect = vp.getBoundingClientRect();
        const sky = clientToSkyCoords(e.clientX, e.clientY, rect, pan, zoom, wcs);
        if (sky) setCursorInfo(sky);
      }
      if (panStartRef.current) {
        const dx = e.clientX - panStartRef.current.startX;
        const dy = e.clientY - panStartRef.current.startY;
        setPan({
          x: panStartRef.current.initPanX + dx,
          y: panStartRef.current.initPanY + dy,
        });
      }
    },
    [pan, zoom, wcs],
  );

  const onPointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (panStartRef.current) {
      panStartRef.current = null;
      setIsPanning(false);
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      } catch {
        // pointer capture release fallback
      }
    }
  }, []);

  const selectedObjInfo = useMemo(() => {
    if (!obj) return null;
    const trackEntry = track.find((t) => t.startsWith(`${obj}:`));
    if (trackEntry) {
      const [lbl, coords] = trackEntry.split(":");
      const [raS, decS] = (coords ?? "").split(",");
      const r = Number(raS);
      const d = Number(decS);
      if (Number.isFinite(r) && Number.isFinite(d)) {
        return {
          label: lbl ?? obj,
          ra: r,
          dec: d,
          isTrack: true,
          snr: undefined,
        };
      }
    }
    const det = detections.find((d) => String(d.id) === obj);
    if (det) {
      const existingTrack = track.find((t) => {
        const [, coords] = t.split(":");
        const coordsArr = (coords ?? "").split(",").map(Number);
        const r = coordsArr[0];
        const d = coordsArr[1];
        if (r === undefined || d === undefined || Number.isNaN(r) || Number.isNaN(d)) return false;
        return Math.abs(r - det.ra) < 0.001 && Math.abs(d - det.dec) < 0.001;
      });
      return {
        label: existingTrack ? existingTrack.split(":")[0]! : `DET #${det.id}`,
        ra: det.ra,
        dec: det.dec,
        isTrack: Boolean(existingTrack),
        snr: det.snr,
      };
    }
    const sug = suggestions.find((s) => String(s.detection_id) === obj);
    if (sug) {
      return {
        label: `SUG #${sug.detection_id}`,
        ra: sug.ra,
        dec: sug.dec,
        isTrack: false,
        snr: sug.score,
      };
    }
    return null;
  }, [obj, track, detections, suggestions]);

  const label = frameA
    ? `SPHEREx sky plate at ICRS ${ra.toFixed(4)}°, ${dec.toFixed(4)}°, ${frameA.epoch} (${frameA.utc_mid.slice(0, 10)})`
    : `Sky canvas centered at ICRS ${ra.toFixed(4)}°, ${dec.toFixed(4)}°`;

  return (
    <div className="viewer-stage viewer-stage-fullscreen">
      <div
        ref={viewportRef}
        className="viewer-viewport viewer-viewport-fullscreen"
        style={{
          cursor: selectBoxMode ? "crosshair" : isPanning ? "grabbing" : "grab",
        }}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onDoubleClick={resetView}
      >
        <AladinBase wcs={wcs} ra={ra} dec={dec} fov={fov} enabled={hips} />
        <div
          className="viewer-transform-layer"
          style={{
            position: "absolute",
            inset: 0,
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: "0 0",
          }}
        >
          <WebGL2SkyCanvas
            frameA={bufA}
            frameB={bufB}
            fallbackUrl={frameA?.display_url}
            stretch={stretch}
            op={op}
            colormap={colormap}
            swipe={swipe}
            label={label}
            onVisibilityPause={onVisibilityPause}
          />
          <WcsSvgOverlay
            wcs={wcs}
            gaiaSources={gaiaSources}
            catwiseSources={catwiseSources}
            detections={detections}
            suggestions={suggestions}
            tracks={track}
            selectBoxMode={selectBoxMode}
            objectDetail={objectDetail}
            onSelectBox={onBoxSelect}
            onSelectCoord={(sRa, sDec) => {
              const nextLabel = nextTrackLabel(track);
              addTrack(nextLabel, sRa, sDec);
              setView({ obj: nextLabel, inspectorTab: "object" }, false);
            }}
            onSelectDetection={(d) => {
              setView({ obj: String(d.id), inspectorTab: "object" });
            }}
            onSelectTrack={(lbl) => {
              setView({ obj: lbl, inspectorTab: "object" });
            }}
          />
        </div>

        {selectedObjInfo && (
          <SelectedObjectBar
            label={selectedObjInfo.label}
            ra={selectedObjInfo.ra}
            dec={selectedObjInfo.dec}
            snr={selectedObjInfo.snr}
            isTrack={selectedObjInfo.isTrack}
            onCenter={() => centerTarget(selectedObjInfo.ra, selectedObjInfo.dec)}
            onInspect={() => setView({ inspectorTab: "object" })}
            onOpenSpectrum={() => setView({ inspectorTab: "spectrum" })}
            onTrack={() => {
              const nextLbl = nextTrackLabel(track);
              addTrack(nextLbl, selectedObjInfo.ra, selectedObjInfo.dec);
              setView({ obj: nextLbl });
            }}
            onDeselect={() => setView({ obj: undefined })}
          />
        )}

        {cursorInfo && (
          <div className="viewer-coord-hud">
            ICRS {cursorInfo.ra.toFixed(5)}°, {cursorInfo.dec.toFixed(5)}°
            {selectBoxMode && <span> · Box Drag active</span>}
            {zoom !== 1 && <span> · {(zoom * 100).toFixed(0)}%</span>}
          </div>
        )}

        <div className="viewer-zoom-controls" role="group" aria-label="Viewport zoom controls">
          <button
            type="button"
            className="viewer-btn-sm"
            onClick={() => setZoom((z) => clampZoom(z * 1.25))}
            title="Zoom in"
          >
            +
          </button>
          <button
            type="button"
            className="viewer-btn-sm"
            onClick={() => setZoom((z) => clampZoom(z / 1.25))}
            title="Zoom out"
          >
            −
          </button>
          <button
            type="button"
            className="viewer-btn-sm"
            onClick={resetView}
            title="Reset pan and zoom (double-click viewport)"
          >
            {(zoom * 100).toFixed(0)}% ⟲
          </button>
        </div>

        {frameA && (
          <div className="viewer-frame-badge">
            <span>{frameA.epoch}</span>
            <span>{frameA.utc_mid.slice(0, 10)}</span>
            <span>{frameA.band_label ?? "SPHEREx"}</span>
            {op !== "none" && <span className="op-tag">{op.toUpperCase()}</span>}
          </div>
        )}
        {loading && (
          <div className="viewer-status-overlay" role="status" aria-live="polite">
            <div className="viewer-status-spinner" />
            <div className="viewer-status-msg">Loading FITS cutouts…</div>
          </div>
        )}
        {!loading && errorMsg && (
          <div className="viewer-status-overlay" role="alert">
            <div className="viewer-status-msg viewer-status-error">{errorMsg}</div>
          </div>
        )}
        {!loading && !errorMsg && !frameA && (
          <div className="viewer-status-overlay" role="status">
            <div className="viewer-status-msg">
              {emptyWarning || "No FITS frames available for coordinates"}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
