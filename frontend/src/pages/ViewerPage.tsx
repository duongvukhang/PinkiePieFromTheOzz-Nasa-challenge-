import "../design/viewer.css";
import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { useAuthStore } from "../app/authStore";
import { CompareView } from "../viewer/compare/CompareView";
import { FrameStrip } from "../viewer/panels/FrameStrip";
import { InspectorTabs } from "../viewer/panels/InspectorTabs";
import { LayerSidebar } from "../viewer/panels/LayerSidebar";
import { ViewerStage } from "../viewer/panels/ViewerStage";
import { ViewerToolbar } from "../viewer/panels/ViewerToolbar";
import { PlanetXWizard } from "../viewer/planetx/PlanetXWizard";
import { useViewerData } from "../viewer/state/useViewerData";
import { useViewStore } from "../viewer/state/viewStore";

export function ViewerPage() {
  const { search } = useLocation();
  const user = useAuthStore((s) => s.user);
  const {
    mode,
    fov,
    release,
    op,
    swipe,
    obj,
    isLayersOpen,
    isInspectorOpen,
    isTimelineOpen,
    toggleLayers,
    toggleInspector,
    toggleTimeline,
    setView,
  } = useViewStore();

  const v = useViewerData(search);

  // Auto-open inspector when an object is selected/inspected
  useEffect(() => {
    if (obj || v.objectDetail) {
      setView({ isInspectorOpen: true });
    }
  }, [obj, v.objectDetail, setView]);

  // Global hotkeys: L (Layers), T (Timeline), I (Inspector), Esc (Close drawers)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (
        el &&
        (el.tagName === "INPUT" ||
          el.tagName === "TEXTAREA" ||
          el.tagName === "SELECT" ||
          el.isContentEditable)
      ) {
        return;
      }
      const k = e.key.toLowerCase();
      if (k === "l") {
        e.preventDefault();
        toggleLayers();
      } else if (k === "t") {
        e.preventDefault();
        toggleTimeline();
      } else if (k === "i") {
        e.preventDefault();
        toggleInspector();
      } else if (k === "escape") {
        setView({ isLayersOpen: false, isInspectorOpen: false });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [toggleLayers, toggleTimeline, toggleInspector, setView]);

  return (
    <div className="viewer-shell is-observatory" data-theme="dark">
      <h1 id="main-heading" className="visually-hidden">
        SkyTrace Multi-Epoch Observatory Viewer
      </h1>

      <ViewerToolbar
        examples={v.examples}
        releases={v.releases}
        authenticated={Boolean(user)}
        errorMsg={v.errorMsg ?? v.resolveError}
        isLayersOpen={isLayersOpen}
        onToggleLayers={toggleLayers}
        isTimelineOpen={isTimelineOpen}
        onToggleTimeline={toggleTimeline}
        isInspectorOpen={isInspectorOpen}
        onToggleInspector={toggleInspector}
      />

      {mode === "planetx" ? (
        <main className="viewer-workspace-single" id="main-content">
          <PlanetXWizard
            ra={v.activeRa}
            dec={v.activeDec}
            fov={fov}
            releases={v.releases}
            onOpenViewer={() => setView({ mode: "blink" })}
          />
        </main>
      ) : mode === "compare" ? (
        <main className="viewer-workspace-single" id="main-content">
          <CompareView
            frames={v.frames}
            frameA={v.frameA}
            frameB={v.frameB}
            bufA={v.bufA}
            bufB={v.bufB}
            detections={v.detections}
            op={op}
            swipe={swipe}
            onSelectFrameA={(id) => setView({ a: id })}
            onSelectFrameB={(id) => setView({ b: id })}
            onChangeOp={(newOp) => setView({ op: newOp })}
            onChangeSwipe={(newSwipe) => setView({ swipe: newSwipe }, false)}
          />
        </main>
      ) : (
        <div className="viewer-workspace-fullscreen">
          <ViewerStage
            wcs={v.wcs}
            ra={v.activeRa}
            dec={v.activeDec}
            fov={fov}
            frameA={v.frameA}
            bufA={v.bufA}
            bufB={v.bufB}
            frames={v.frames}
            gaiaSources={v.gaiaSources}
            catwiseSources={v.catwiseSources}
            detections={v.detections}
            suggestions={v.suggestions}
            selectBoxMode={v.selectBoxMode}
            objectDetail={v.objectDetail}
            predictionData={v.ephemerisResult ? { trajectory_points: v.ephemerisResult.trajectory } : undefined}
            onBoxSelect={v.onBoxSelect}
            onTrackDetection={v.onTrackDetection}
            onRemoveTrack={v.removeTrack}
            onVisibilityPause={v.setPausedByVis}
            loading={v.loadingCutouts}
            loadingText={v.loadingText}
            errorMsg={v.errorMsg}
            emptyWarning={v.warnings[0]}
          />

          {isTimelineOpen && (
            <div className="viewer-timeline-dock">
              <FrameStrip frames={v.frames} />
            </div>
          )}

          {isLayersOpen && (
            <aside
              className="viewer-drawer viewer-drawer-left"
              aria-label="Layers and scientific controls"
            >
              <div className="viewer-drawer-header">
                <span className="viewer-drawer-title">Layers &amp; Arrays</span>
                <button
                  type="button"
                  className="viewer-drawer-close"
                  onClick={() => setView({ isLayersOpen: false })}
                  aria-label="Close layers drawer"
                >
                  ✕
                </button>
              </div>
              <LayerSidebar
                selectBoxMode={v.selectBoxMode}
                onToggleSelectBox={v.toggleSelectBox}
                flaggedFraction={v.frameA?.flagged_fraction}
                frames={v.frames}
              />
            </aside>
          )}

          {isInspectorOpen && (
            <aside
              className="viewer-drawer viewer-drawer-right"
              aria-label="Scientific inspection and object properties"
            >
              <div className="viewer-drawer-header">
                <span className="viewer-drawer-title">Object &amp; Science Inspector</span>
                <button
                  type="button"
                  className="viewer-drawer-close"
                  onClick={() => setView({ isInspectorOpen: false })}
                  aria-label="Close inspector drawer"
                >
                  ✕
                </button>
              </div>
              <InspectorTabs
                activeFrame={v.frameA}
                release={release}
                warnings={v.warnings}
                spectrum={v.spectrum}
                objectDetail={v.objectDetail}
                associations={v.associations}
                suggestions={v.suggestions}
                detections={v.detections}
                tracks={v.track}
                onPickSuggestion={v.onTrackSuggestion}
                onTrackDetection={v.onTrackDetection}
                onOverrideAssociation={v.overrideAssociation}
                onRemoveTrack={v.removeTrack}
                onEphemerisResult={v.setEphemerisResult}
              />
            </aside>
          )}
        </div>
      )}
    </div>
  );
}
