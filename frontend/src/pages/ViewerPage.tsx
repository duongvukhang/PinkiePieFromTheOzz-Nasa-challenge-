import "../design/viewer.css";
import { useState } from "react";
import { useLocation } from "react-router-dom";
import { useAuthStore } from "../app/authStore";
import { FrameStrip } from "../viewer/panels/FrameStrip";
import { InspectorTabs } from "../viewer/panels/InspectorTabs";
import { LayerSidebar } from "../viewer/panels/LayerSidebar";
import { ViewerStage } from "../viewer/panels/ViewerStage";
import { ViewerToolbar } from "../viewer/panels/ViewerToolbar";
import { useViewerData } from "../viewer/state/useViewerData";
import { useViewStore } from "../viewer/state/viewStore";

export function ViewerPage() {
  const { search } = useLocation();
  const user = useAuthStore((s) => s.user);
  const { mode, fov, release, setView, addTrack } = useViewStore();
  const [selectBoxMode, setSelectBoxMode] = useState(false);

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
        resolving={v.resolving}
        errorMsg={v.resolveError}
        onResolveName={async (name) => {
          setView({ target: name, ra: undefined, dec: undefined });
        }}
      />

      {mode === "planetx" && (
        <div className="viewer-banner" role="note">
          <strong>Planet X Shift-and-Stack Mode (Phase 2 Backend Scheduled):</strong> Inspect
          multi-epoch residuals here; candidates remain unconfirmed hypotheses and require
          MPC/Horizons verification.
        </div>
      )}

      <div className="viewer-workspace">
        <LayerSidebar
          selectBoxMode={selectBoxMode}
          onToggleSelectBox={() => setSelectBoxMode((b) => !b)}
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
            selectBoxMode={selectBoxMode}
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
          suggestions={v.suggestions}
          detections={v.detections}
          onPickSuggestion={(s) => {
            addTrack("A", s.ra, s.dec);
            setView({ obj: String(s.detection_id), inspectorTab: "object" });
          }}
        />
      </div>
    </div>
  );
}
