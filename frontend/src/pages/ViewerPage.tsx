import "../design/viewer.css";
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
  const { mode, fov, release, op, swipe, setView } = useViewStore();

  const v = useViewerData(search);

  return (
    <div className="viewer-shell" data-theme="dark">
      <h1 id="main-heading" className="visually-hidden">
        SkyTrace Multi-Epoch Observatory Viewer
      </h1>

      <ViewerToolbar
        examples={v.examples}
        releases={v.releases}
        authenticated={Boolean(user)}
        errorMsg={v.errorMsg ?? v.resolveError}
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
        <main className="viewer-workspace" id="main-content">
          <LayerSidebar
            selectBoxMode={v.selectBoxMode}
            onToggleSelectBox={v.toggleSelectBox}
            flaggedFraction={v.frameA?.flagged_fraction}
          />

          <div className="viewer-center">
            <ViewerStage
              wcs={v.wcs}
              ra={v.activeRa}
              dec={v.activeDec}
              fov={fov}
              frameA={v.frameA}
              bufA={v.bufA}
              bufB={v.bufB}
              gaiaSources={v.gaiaSources}
              catwiseSources={v.catwiseSources}
              detections={v.detections}
              suggestions={v.suggestions}
              selectBoxMode={v.selectBoxMode}
              objectDetail={v.objectDetail}
              onBoxSelect={v.onBoxSelect}
              onVisibilityPause={v.setPausedByVis}
            />
            <FrameStrip frames={v.frames} />
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
            onOverrideAssociation={v.overrideAssociation}
            onRemoveTrack={v.removeTrack}
          />
        </main>
      )}
    </div>
  );
}
