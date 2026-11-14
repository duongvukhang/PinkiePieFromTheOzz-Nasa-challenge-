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
import { WcsSvgOverlay } from "../overlays/WcsSvgOverlay";
import { useViewStore } from "../state/viewStore";
import { nextTrackLabel } from "../state/useViewerData";
import { BottomDock } from "./BottomDock";
import { SelectedObjectBar } from "./SelectedObjectBar";
import { SourceInfoCard, type SelectedSourceData } from "./SourceInfoCard";
import { TimeController } from "./TimeController";
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
  frames?: CutoutFrame[];
  gaiaSources: CatalogOverlaySource[];
  catwiseSources: CatalogOverlaySource[];
  detections: DetectionRead[];
  suggestions: SuggestionItem[];
  selectBoxMode: boolean;
  objectDetail?: TrackedObjectDetailResponse;
  onBoxSelect?: (box: { raMin: number; raMax: number; decMin: number; decMax: number }) => void;
  onVisibilityPause?: (paused: boolean) => void;
  onSelectEpoch?: (epoch: string) => void;
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
  frames,
  gaiaSources,
  catwiseSources,
  detections,
  suggestions,
  selectBoxMode,
  objectDetail,
  onBoxSelect,
  onSelectEpoch,
  loading,
  errorMsg,
}: ViewerStageProps) {
  const { hips, track, obj, fps, playing, addTrack, setView, stretch, colormap } = useViewStore();

  const displayUrl = useMemo(() => {
    if (!frameA?.display_url) return "";
    const sep = frameA.display_url.includes("?") ? "&" : "?";
    return `${frameA.display_url}${sep}stretch=${stretch}&colormap=${colormap}`;
  }, [frameA?.display_url, stretch, colormap]);

  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [isPanning, setIsPanning] = useState(false);
  const [showSources, setShowSources] = useState(true);
  const [showLabels, setShowLabels] = useState(true);
  const [showGrid, setShowGrid] = useState(false);
  const [showTrajectories, setShowTrajectories] = useState(true);
  const [showUncertainty, setShowUncertainty] = useState(false);
  const [isTimelineVisible, setIsTimelineVisible] = useState(true);

  const [cursorInfo, setCursorInfo] = useState<{
    ra: number;
    dec: number;
    px: number;
    py: number;
  } | null>(null);

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
        if (sky) {
          const canvasX = (e.clientX - rect.left - pan.x) / zoom;
          const canvasY = (e.clientY - rect.top - pan.y) / zoom;
          const px = (canvasX / rect.width) * wcs.naxis1;
          const py = wcs.naxis2 - (canvasY / rect.height) * wcs.naxis2;
          setCursorInfo({ ra: sky.ra, dec: sky.dec, px, py });
        } else {
          setCursorInfo(null);
        }
      }
      if (isPanning && panStartRef.current) {
        setPan({
          x: panStartRef.current.initPanX + (e.clientX - panStartRef.current.startX),
          y: panStartRef.current.initPanY + (e.clientY - panStartRef.current.startY),
        });
      }
    },
    [isPanning, pan, zoom, wcs],
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

  const selectedObjInfo: SelectedSourceData | null = useMemo(() => {
    if (!obj) return null;
    const trackEntry = track.find((t) => t.startsWith(`${obj}:`));
    if (trackEntry) {
      const [lbl, coords] = trackEntry.split(":");
      const [raS, decS] = (coords ?? "").split(",");
      const r = Number(raS);
      const d = Number(decS);
      if (Number.isFinite(r) && Number.isFinite(d)) {
        return {
          id: lbl ?? obj,
          label: lbl ?? obj,
          ra: r,
          dec: d,
          isTrack: true,
          snr: undefined,
          pos_err_arcsec: undefined,
          peak_flux: undefined,
          flux_unit: undefined,
          fwhm_pixels: undefined,
        };
      }
    }
    const det = detections.find((d) => {
      const dLabel =
        d.x_pix != null && d.y_pix != null
          ? `src_${d.id.toString(16).padStart(8, "0")}_${Math.round(d.x_pix)}_${Math.round(d.y_pix)}`
          : `src_${d.id}`;
      return String(d.id) === obj || dLabel === obj || `DET #${d.id}` === obj;
    });
    if (det) {
      const dLabel =
        det.x_pix != null && det.y_pix != null
          ? `src_${det.id.toString(16).padStart(8, "0")}_${Math.round(det.x_pix)}_${Math.round(det.y_pix)}`
          : `src_${det.id}`;
      const existingTrack = track.find((t) => {
        const [, coords] = t.split(":");
        const coordsArr = (coords ?? "").split(",").map(Number);
        const r = coordsArr[0];
        const d = coordsArr[1];
        if (r === undefined || d === undefined || Number.isNaN(r) || Number.isNaN(d)) return false;
        return Math.abs(r - det.ra) < 0.001 && Math.abs(d - det.dec) < 0.001;
      });
      const posErr =
        det.sig_ra_mas && det.sig_dec_mas
          ? Math.hypot(det.sig_ra_mas, det.sig_dec_mas) / 1000.0
          : undefined;
      const peakFlux = det.flux_ujy != null ? det.flux_ujy : undefined;
      return {
        id: String(det.id),
        label: existingTrack ? existingTrack.split(":")[0]! : dLabel,
        ra: det.ra,
        dec: det.dec,
        pixel_x: det.x_pix ?? undefined,
        pixel_y: det.y_pix ?? undefined,
        isTrack: Boolean(existingTrack),
        snr: det.snr ?? undefined,
        peak_flux: peakFlux,
        flux_unit: "μJy",
        pos_err_arcsec: posErr,
        fwhm_pixels: det.fwhm_arcsec != null ? det.fwhm_arcsec / 1.5 : undefined,
      };
    }
    const sug = suggestions.find((s) => String(s.detection_id) === obj);
    if (sug) {
      return {
        id: String(sug.detection_id),
        label: `SUG #${sug.detection_id}`,
        ra: sug.ra,
        dec: sug.dec,
        isTrack: false,
        snr: sug.score ?? undefined,
        pos_err_arcsec: undefined,
        peak_flux: undefined,
        flux_unit: undefined,
        fwhm_pixels: undefined,
      };
    }
    return null;
  }, [obj, track, detections, suggestions]);

  return (
    <div className="viewer-stage viewer-stage-fullscreen">
      <div
        ref={viewportRef}
        className="viewer-viewport viewer-viewport-fullscreen"
        style={{
          cursor: selectBoxMode ? "crosshair" : isPanning ? "grabbing" : "crosshair",
        }}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onDoubleClick={resetView}
      >
        <AladinBase wcs={wcs} ra={ra} dec={dec} fov={fov} enabled={hips} />

        <div
          className="viewer-transform-layer relative flex items-center justify-center w-full h-full"
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: "center center",
            transition: isPanning ? "none" : "transform 0.05s ease-out",
          }}
        >
          {frameA && (
            <div className="relative inline-block select-none shadow-2xl">
              <img
                src={displayUrl}
                alt={frameA.epoch}
                draggable={false}
                className="block max-w-[85vw] max-h-[85vh] object-contain select-none pointer-events-none"
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
                selectedTarget={selectedObjInfo}
                currentEpoch={frameA?.epoch}
                showGrid={showGrid}
                showSources={showSources}
                showLabels={showLabels}
                showTrajectories={showTrajectories}
                showUncertainty={showUncertainty}
                onSelectBox={onBoxSelect}
                onSelectEpoch={(ep) => {
                  if (onSelectEpoch) {
                    onSelectEpoch(ep);
                  } else if (frames) {
                    const idx = frames.findIndex((f) => f.epoch === ep);
                    if (idx >= 0) setView({ frame: idx });
                  }
                }}
                onSelectCoord={(sRa, sDec) => {
                  const nextLabel = nextTrackLabel(track);
                  addTrack(nextLabel, sRa, sDec);
                  setView({ obj: nextLabel, inspectorTab: "object" }, false);
                }}
                onSelectDetection={(d) => {
                  const dLabel =
                    d.x_pix != null && d.y_pix != null
                      ? `src_${d.id.toString(16).padStart(8, "0")}_${Math.round(d.x_pix)}_${Math.round(d.y_pix)}`
                      : `src_${d.id}`;
                  setView({ obj: dLabel, inspectorTab: "object" });
                }}
                onSelectTrack={(lbl) => {
                  setView({ obj: lbl, inspectorTab: "object" });
                }}
              />
            </div>
          )}
        </div>

        {/* Top-Right Floating Source Info HUD */}
        {selectedObjInfo && (
          <SourceInfoCard
            source={selectedObjInfo}
            wcs={wcs}
            onClose={() => setView({ obj: undefined })}
            onInspect={() => setView({ inspectorTab: "object" })}
            onCenter={() => centerTarget(selectedObjInfo.ra, selectedObjInfo.dec)}
            onOpenSpectrum={() => setView({ inspectorTab: "spectrum" })}
            onPredictMotion={() => setView({ inspectorTab: "object" })}
            onTrack={() => {
              const nextLbl = nextTrackLabel(track);
              addTrack(nextLbl, selectedObjInfo.ra, selectedObjInfo.dec);
              setView({ obj: nextLbl });
            }}
          />
        )}

        {/* Bottom-Right Floating Observation Epoch Controller */}
        {isTimelineVisible && frames && frames.length > 0 && frameA && (
          <TimeController
            frames={frames}
            currentFrame={frameA}
            currentIndex={frames.findIndex((f) => f.frame_id === frameA.frame_id)}
            playing={playing}
            fps={fps}
            onSelectFrame={(idx) => setView({ frame: idx })}
            onTogglePlay={() => setView({ playing: !playing })}
            onChangeSpeed={(newFps) => setView({ fps: newFps })}
          />
        )}

        {/* Bottom-Center Floating Action Bar */}
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

        {/* Bottom-Center Pill Dock for Viewing Toggles */}
        <BottomDock
          showSources={showSources}
          onToggleSources={() => setShowSources((s) => !s)}
          showLabels={showLabels}
          onToggleLabels={() => setShowLabels((s) => !s)}
          showGrid={showGrid}
          onToggleGrid={() => setShowGrid((s) => !s)}
          showTrajectories={showTrajectories}
          onToggleTrajectories={() => setShowTrajectories((s) => !s)}
          showUncertainty={showUncertainty}
          onToggleUncertainty={() => setShowUncertainty((s) => !s)}
          tracksCount={track.length}
          onToggleTracks={() => setView({ inspectorTab: "tracks" })}
          isSpectrumOpen={false}
          onToggleSpectrum={() => setView({ inspectorTab: "spectrum" })}
          isTimelineOpen={isTimelineVisible}
          onToggleTimeline={() => setIsTimelineVisible((v) => !v)}
          isSettingsOpen={false}
          onToggleSettings={() => setView({ inspectorTab: "layers" })}
        />

        {/* Bottom-Left Focal Plane Coordinate HUD */}
        <div className="viewer-coord-hud stellarium-glass" role="status" aria-live="off">
          {cursorInfo ? (
            <>
              <div className="hud-item">
                <span className="hud-label">Pix: </span>
                <span className="hud-val">
                  X:{cursorInfo.px.toFixed(1)} Y:{cursorInfo.py.toFixed(1)}
                </span>
              </div>
              <div className="hud-item hud-border-left">
                <span className="hud-label">ICRS: </span>
                <span className="hud-sky">
                  RA {cursorInfo.ra.toFixed(4)}°, Dec {cursorInfo.dec.toFixed(4)}°
                </span>
              </div>
              {(() => {
                const arrId = frameA?.array_id ?? 1;
                const ranges: Record<number, [number, number]> = {
                  1: [0.75, 1.1],
                  2: [1.1, 1.6],
                  3: [1.6, 2.42],
                  4: [2.42, 3.82],
                  5: [3.82, 4.42],
                  6: [4.42, 5.0],
                };
                const [wMin, wMax] = ranges[arrId] ?? [0.75, 5.0];
                const frac = Math.max(0, Math.min(1, cursorInfo.py / (wcs.naxis2 || 128)));
                const wave = wMin + frac * (wMax - wMin);
                return (
                  <div className="hud-item hud-border-left">
                    <span className="hud-label">λ: </span>
                    <span className="hud-cyan">{wave.toFixed(3)} μm</span>
                  </div>
                );
              })()}
              {selectBoxMode && <span className="hud-mode">· Box Drag active</span>}
              {zoom !== 1 && <span className="hud-zoom">· {(zoom * 100).toFixed(0)}%</span>}
            </>
          ) : (
            <span className="hud-idle">Pointer in focal plane</span>
          )}
        </div>

        {/* Viewport Zoom Controls */}
        <div
          className="viewer-zoom-controls stellarium-glass"
          role="group"
          aria-label="Viewport zoom controls"
        >
          <button
            type="button"
            className="viewer-zoom-btn"
            onClick={() => setZoom((z) => clampZoom(z * 1.25))}
            title="Zoom In (+)"
            aria-label="Zoom In"
          >
            +
          </button>
          <button
            type="button"
            className="viewer-zoom-btn"
            onClick={() => setZoom((z) => clampZoom(z / 1.25))}
            title="Zoom Out (−)"
            aria-label="Zoom Out"
          >
            −
          </button>
          <button
            type="button"
            className="viewer-zoom-btn"
            onClick={resetView}
            title="Fit to Screen / Reset view (Double-click field)"
            aria-label="Fit to Screen"
          >
            ⟲
          </button>
        </div>

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
        {/* If no FITS file is loaded, render blank per observatory specification */}
        {!loading && !errorMsg && !frameA && null}
      </div>
    </div>
  );
}
