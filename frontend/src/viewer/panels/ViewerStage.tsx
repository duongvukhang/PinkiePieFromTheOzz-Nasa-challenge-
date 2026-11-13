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
import { WebGL2SkyCanvas } from "../gl/WebGL2SkyCanvas";
import { WcsSvgOverlay } from "../overlays/WcsSvgOverlay";
import { useViewStore } from "../state/viewStore";

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
}: ViewerStageProps) {
  const { op, stretch, colormap, swipe, hips, track, addTrack, setView } = useViewStore();

  const label = frameA
    ? `SPHEREx sky plate at ICRS ${ra.toFixed(4)}°, ${dec.toFixed(4)}°, ${frameA.epoch} (${frameA.utc_mid.slice(0, 10)})`
    : `Sky canvas centered at ICRS ${ra.toFixed(4)}°, ${dec.toFixed(4)}°`;

  return (
    <div className="viewer-stage">
      <div className="viewer-viewport">
        <AladinBase wcs={wcs} ra={ra} dec={dec} fov={fov} enabled={hips} />
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
            const nextLabel = String.fromCharCode(65 + (track.length % 26));
            addTrack(nextLabel, sRa, sDec);
            setView({ inspectorTab: "object" }, false);
          }}
        />
        {frameA && (
          <div className="viewer-frame-badge">
            <span>{frameA.epoch}</span>
            <span>{frameA.utc_mid.slice(0, 10)}</span>
            <span>{frameA.band_label ?? "SPHEREx"}</span>
            {op !== "none" && <span className="op-tag">{op.toUpperCase()}</span>}
          </div>
        )}
      </div>
    </div>
  );
}
