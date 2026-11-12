import { useEffect, useRef } from "react";
import type {
  CutoutFrame,
  DetectionRead,
  SpectrumResponse,
  SuggestionItem,
  TrackedObjectDetailResponse,
} from "../../api/client";
import { useViewStore, type InspectorTab } from "../state/viewStore";
import { FlagsMetaPanel } from "./FlagsMetaPanel";
import { SpectrumChart } from "./SpectrumChart";

interface InspectorTabsProps {
  activeFrame?: CutoutFrame;
  release: string;
  warnings: string[];
  spectrum?: SpectrumResponse;
  objectDetail?: TrackedObjectDetailResponse;
  suggestions: SuggestionItem[];
  detections: DetectionRead[];
  onPickSuggestion: (s: SuggestionItem) => void;
}

const TABS: Array<[InspectorTab, string]> = [
  ["spectrum", "Spectrum"],
  ["flags", "Metadata & Flags"],
  ["object", "Object & Sources"],
];

export function InspectorTabs({
  activeFrame,
  release,
  warnings,
  spectrum,
  objectDetail,
  suggestions,
  detections,
  onPickSuggestion,
}: InspectorTabsProps) {
  const { inspectorTab, target, setView } = useViewStore();
  const listRef = useRef<HTMLDivElement>(null);
  const specRef = useRef<HTMLDivElement>(null);
  const flagRef = useRef<HTMLDivElement>(null);
  const objRef = useRef<HTMLDivElement>(null);

  // Sync beforematch so Ctrl+F inside hidden-until-found panels activates that tab (tabs guide)
  useEffect(() => {
    const entries: Array<[HTMLDivElement | null, InspectorTab]> = [
      [specRef.current, "spectrum"],
      [flagRef.current, "flags"],
      [objRef.current, "object"],
    ];
    const cleanups = entries.map(([node, name]) => {
      if (!node) return () => undefined;
      const onMatch = () => setView({ inspectorTab: name }, false);
      node.addEventListener("beforematch", onMatch);
      return () => node.removeEventListener("beforematch", onMatch);
    });
    return () => cleanups.forEach((fn) => fn());
  }, [setView]);

  const onTabKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const order = TABS.map(([id]) => id);
    const idx = order.indexOf(inspectorTab);
    let next = idx;
    if (e.key === "ArrowRight") next = (idx + 1) % order.length;
    else if (e.key === "ArrowLeft") next = (idx + order.length - 1) % order.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = order.length - 1;
    else return;
    e.preventDefault();
    const id = order[next];
    setView({ inspectorTab: id }, false);
    listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  };

  const activeMask =
    objectDetail?.measurements.reduce((acc, m) => acc | (m.flags ?? 0), 0) ??
    detections[0]?.flags ??
    0;

  return (
    <aside className="viewer-panel viewer-right" aria-label="Science inspector">
      <div
        ref={listRef}
        role="tablist"
        aria-label="Inspector sections"
        onKeyDown={onTabKeyDown}
        className="viewer-tablist"
      >
        {TABS.map(([id, label]) => (
          <button
            key={id}
            id={`tab-${id}`}
            type="button"
            role="tab"
            className="viewer-tab"
            aria-selected={inspectorTab === id}
            tabIndex={inspectorTab === id ? 0 : -1}
            onClick={() => setView({ inspectorTab: id }, false)}
          >
            {label}
          </button>
        ))}
      </div>

      <div
        ref={specRef}
        role="tabpanel"
        aria-labelledby="tab-spectrum"
        tabIndex={inspectorTab === "spectrum" ? 0 : undefined}
        hidden={inspectorTab !== "spectrum" ? ("until-found" as unknown as boolean) : false}
      >
        <div className="viewer-tabpanel-body">
          <SpectrumChart
            spectrum={spectrum}
            title={objectDetail?.public_id ?? (target || "Selected Field Source")}
          />
        </div>
      </div>

      <div
        ref={flagRef}
        role="tabpanel"
        aria-labelledby="tab-flags"
        tabIndex={inspectorTab === "flags" ? 0 : undefined}
        hidden={inspectorTab !== "flags" ? ("until-found" as unknown as boolean) : false}
      >
        <div className="viewer-tabpanel-body">
          <FlagsMetaPanel
            frame={activeFrame}
            release={release}
            warnings={warnings}
            activeMask={activeMask}
          />
        </div>
      </div>

      <div
        ref={objRef}
        role="tabpanel"
        aria-labelledby="tab-object"
        tabIndex={inspectorTab === "object" ? 0 : undefined}
        hidden={inspectorTab !== "object" ? ("until-found" as unknown as boolean) : false}
      >
        <div className="viewer-tabpanel-body">
          {objectDetail ? (
            <div className="object-dossier">
              <div className="spectrum-head">
                <strong>
                  Object {objectDetail.label} · {objectDetail.public_id ?? "Candidate"}
                </strong>
                <span className="viewer-mono">
                  Conf {((objectDetail.identity_confidence ?? 0) * 100).toFixed(0)}%
                </span>
              </div>
              {objectDetail.motion_fit && (
                <dl className="viewer-dl">
                  <div>
                    <dt>Total μ</dt>
                    <dd>
                      {objectDetail.motion_fit.pm_total_masyr?.toFixed(1) ?? "—"} ±{" "}
                      {objectDetail.motion_fit.pm_total_err?.toFixed(1) ?? "—"} mas/yr
                    </dd>
                  </div>
                  <div>
                    <dt>Position Angle</dt>
                    <dd>{objectDetail.motion_fit.pa_deg?.toFixed(1) ?? "—"}°</dd>
                  </div>
                  <div>
                    <dt>Reduced χ²</dt>
                    <dd>{objectDetail.motion_fit.chi2_red?.toFixed(2) ?? "—"}</dd>
                  </div>
                </dl>
              )}
              {objectDetail.classification && (
                <p className="viewer-note">
                  Top hypothesis (unconfirmed):{" "}
                  <strong>{objectDetail.classification.top_class}</strong> (p=
                  {objectDetail.classification.top_prob.toFixed(2)})
                </p>
              )}
              {(objectDetail.caveats ?? []).map((c, i) => (
                <p key={i} className="viewer-warn">
                  ⚠ {c}
                </p>
              ))}
            </div>
          ) : (
            <p className="viewer-muted">
              Select a candidate below to inspect fit &amp; uncertainty.
            </p>
          )}

          <h3 className="viewer-sec-title" style={{ marginTop: "var(--space-4)" }}>
            Suggestions ({suggestions.length})
          </h3>
          <ul className="suggestion-list">
            {suggestions.map((s) => (
              <li key={s.detection_id}>
                <button
                  type="button"
                  className="viewer-layer-btn"
                  onClick={() => onPickSuggestion(s)}
                >
                  <span>
                    Det #{s.detection_id} · score {(s.score * 100).toFixed(0)}%
                  </span>
                  <span className="viewer-mono">{s.reasons.join(", ")}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </aside>
  );
}
