import "../design/viewer.css";
import { Link, useLocation } from "react-router-dom";
import { FrameStrip } from "../viewer/panels/FrameStrip";
import { ViewerStage } from "../viewer/panels/ViewerStage";
import { useViewerData } from "../viewer/state/useViewerData";
import { useViewStore } from "../viewer/state/viewStore";

export function EmbedPage() {
  const { search } = useLocation();
  const { fov } = useViewStore();
  const v = useViewerData(search, true);

  return (
    <main className="viewer-shell is-embed" data-theme="dark">
      <div className="embed-bar">
        <span className="viewer-mono">
          SKYTRACE · ICRS {v.activeRa.toFixed(4)}°, {v.activeDec.toFixed(4)}° · FOV {fov}′
        </span>
        <Link to={`/v${search}`} target="_blank" rel="noopener noreferrer" className="viewer-chip">
          Open in SkyTrace ↗
        </Link>
      </div>

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
        detections={[]}
        suggestions={[]}
        selectBoxMode={false}
        onVisibilityPause={v.setPausedByVis}
      />

      <FrameStrip frames={v.frames} compact />
    </main>
  );
}
