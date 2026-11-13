import { useEffect, useRef, useState } from "react";
import type {
  AssociationHypothesis,
  CutoutFrame,
  DetectionRead,
  SpectrumResponse,
  SuggestionItem,
  TrackedObjectDetailResponse,
} from "../../api/client";
import { useViewStore, type InspectorTab } from "../state/viewStore";
import { nextTrackLabel } from "../state/useViewerData";
import { FlagsMetaPanel } from "./FlagsMetaPanel";
import { SpectrumChart } from "./SpectrumChart";

const OKABE_ITO = ["#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7"];

interface InspectorTabsProps {
  activeFrame?: CutoutFrame;
  release: string;
  warnings: string[];
  spectrum?: SpectrumResponse;
  objectDetail?: TrackedObjectDetailResponse;
  associations?: AssociationHypothesis[];
  suggestions: SuggestionItem[];
  detections: DetectionRead[];
  tracks?: string[];
  onPickSuggestion: (s: SuggestionItem) => void;
  onOverrideAssociation?: (
    epoch: string,
    action: "accept" | "reject" | "reassign",
    detectionId?: number | null,
    reason?: string,
  ) => Promise<void>;
  onRemoveTrack?: (label: string) => void;
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
  associations = [],
  suggestions,
  detections,
  tracks = [],
  onPickSuggestion,
  onOverrideAssociation,
  onRemoveTrack,
}: InspectorTabsProps) {
  const { inspectorTab, target, setView } = useViewStore();
  const listRef = useRef<HTMLDivElement>(null);
  const specRef = useRef<HTMLDivElement>(null);
  const flagRef = useRef<HTMLDivElement>(null);
  const objRef = useRef<HTMLDivElement>(null);
  const [reassigningEpoch, setReassigningEpoch] = useState<string | null>(null);
  const [reassignInput, setReassignInput] = useState<string>("");

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
    if (id) {
      setView({ inspectorTab: id }, false);
      listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
    }
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
          {/* Feature B: Tracked Objects List with persistent IDs */}
          {tracks.length > 0 && (
            <div className="tracked-objects-bar">
              <h3 className="viewer-sec-title">Tracked Objects ({tracks.length})</h3>
              <div className="viewer-row-wrap" role="group" aria-label="Tracked objects list">
                {tracks.map((t, idx) => {
                  const [lbl, coords] = t.split(":");
                  const isSel = (objectDetail?.label ?? "A") === lbl;
                  const col = OKABE_ITO[idx % OKABE_ITO.length] || "#faf9f5";
                  return (
                    <div
                      key={lbl}
                      className={`tracked-object-pill${isSel ? " is-active" : ""}`}
                      style={{ borderColor: col }}
                    >
                      <button
                        type="button"
                        className="pill-sel-btn"
                        onClick={() => setView({ inspectorTab: "object" })}
                      >
                        <span className="swatch" style={{ background: col, borderColor: col }} />
                        <strong>{lbl}</strong>
                        <span className="viewer-mono">{coords}</span>
                      </button>
                      {onRemoveTrack && (
                        <button
                          type="button"
                          className="pill-del-btn"
                          aria-label={`Untrack object ${lbl}`}
                          onClick={() => onRemoveTrack(lbl ?? "")}
                        >
                          ×
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {objectDetail ? (
            <div className="object-dossier">
              <div className="spectrum-head">
                <strong>
                  Object {objectDetail.label} · {objectDetail.public_id ?? "Candidate"}
                </strong>
                <span className="viewer-chip confidence-badge">
                  Conf {((objectDetail.identity_confidence ?? 0) * 100).toFixed(0)}%
                </span>
              </div>

              {/* Feature C: Motion fit panel */}
              {objectDetail.motion_fit && (
                <dl className="viewer-dl">
                  <div>
                    <dt>Motion Model</dt>
                    <dd>{objectDetail.motion_fit.model ?? "linear"}</dd>
                  </div>
                  <div>
                    <dt>Total Proper Motion μ</dt>
                    <dd>
                      {objectDetail.motion_fit.pm_total_masyr?.toFixed(1) ?? "—"} ±{" "}
                      {objectDetail.motion_fit.pm_total_err?.toFixed(1) ?? "—"} mas/yr
                    </dd>
                  </div>
                  <div>
                    <dt>Position Angle (PA)</dt>
                    <dd>{objectDetail.motion_fit.pa_deg?.toFixed(1) ?? "—"}°</dd>
                  </div>
                  <div>
                    <dt>Reduced χ² (dof)</dt>
                    <dd>
                      {objectDetail.motion_fit.chi2_red?.toFixed(2) ?? "—"} (
                      {objectDetail.motion_fit.dof ?? 2})
                    </dd>
                  </div>
                  {objectDetail.motion_fit.plx_mas != null && (
                    <div>
                      <dt>Parallax ϖ</dt>
                      <dd>
                        {objectDetail.motion_fit.plx_mas.toFixed(1)} ±{" "}
                        {objectDetail.motion_fit.plx_err?.toFixed(1) ?? "—"} mas
                      </dd>
                    </div>
                  )}
                </dl>
              )}

              {/* Feature C: Classification summary & unconfirmed caveat banner */}
              {objectDetail.classification && (
                <div className="classification-box">
                  <p className="viewer-note">
                    Top hypothesis:{" "}
                    <strong className="class-badge">{objectDetail.classification.top_class}</strong>{" "}
                    (p={objectDetail.classification.top_prob.toFixed(2)})
                  </p>
                  {Array.isArray(objectDetail.classification.evidence) &&
                    objectDetail.classification.evidence.length > 0 && (
                      <ul className="evidence-list">
                        {(objectDetail.classification.evidence as unknown[]).map((ev, i) => (
                          <li key={i}>{String(ev)}</li>
                        ))}
                      </ul>
                    )}
                </div>
              )}

              <p className="viewer-warn unconfirmed-banner" role="note">
                ⚠ Scientific Notice: All classification results are unconfirmed hypotheses.
                Variables change brightness, NOT position; verify proper motion against independent
                archives before confirmation.
              </p>

              {(objectDetail.caveats ?? []).map((c, i) => (
                <p key={i} className="viewer-warn">
                  ⚠ {c}
                </p>
              ))}

              {/* Feature B: Identity Inspector with per-epoch associations & Accept/Reject/Reassign */}
              <div className="associations-section">
                <h3 className="viewer-sec-title" style={{ marginTop: "var(--space-4)" }}>
                  Epoch Match Hypotheses
                </h3>
                {associations.length > 0 ? (
                  <ul className="associations-list">
                    {associations.map((a) => {
                      const epochLabel = (() => {
                        let best = `E${a.rank}`, minD = Infinity;
                        for (const m of objectDetail.measurements) {
                          if (m.ra != null && a.pred_ra != null) {
                            const d = Math.hypot(m.ra - a.pred_ra, (m.dec ?? 0) - (a.pred_dec ?? 0));
                            if (d < minD) { minD = d; best = m.epoch ?? best; }
                          }
                        }
                        return minD < 0.005 ? best : `E${a.rank}`;
                      })();
                      return (
                        <li key={a.id} className="association-item">
                          <div className="assoc-header">
                            <span>
                              <strong>{epochLabel}</strong> · Det #{a.detection_id ?? "none"}
                            </span>
                            <span className="viewer-mono">
                              Conf {((a.confidence ?? 0) * 100).toFixed(0)}% · {a.decided_by}
                            </span>
                          </div>
                          {a.reason && <p className="viewer-muted">{a.reason}</p>}
                          <div className="assoc-actions">
                            <button
                              type="button"
                              className="viewer-btn"
                              aria-label={`Accept match for epoch ${epochLabel}`}
                              onClick={() => onOverrideAssociation?.(epochLabel, "accept", a.detection_id, "User accepted hypothesis")}
                            >
                              Accept
                            </button>
                            <button
                              type="button"
                              className="viewer-btn"
                              aria-label={`Reject match for epoch ${epochLabel}`}
                              onClick={() => onOverrideAssociation?.(epochLabel, "reject", null, "User rejected match")}
                            >
                              Reject
                            </button>
                            <button
                              type="button"
                              className="viewer-btn"
                              aria-label={`Reassign match for epoch ${epochLabel}`}
                              onClick={() => setReassigningEpoch(reassigningEpoch === epochLabel ? null : epochLabel)}
                            >
                              Reassign…
                            </button>
                          </div>
                          {reassigningEpoch === epochLabel && (
                            <div className="reassign-form">
                              <label htmlFor={`reassign-input-${a.id}`} className="viewer-label">
                                Enter Detection ID to reassign:
                              </label>
                              <div className="viewer-row-wrap">
                                <input
                                  id={`reassign-input-${a.id}`}
                                  className="viewer-input"
                                  style={{ maxInlineSize: "10rem" }}
                                  type="number"
                                  placeholder="Det ID"
                                  value={reassignInput}
                                  onChange={(e) => setReassignInput(e.target.value)}
                                />
                                <button
                                  type="button"
                                  className="viewer-btn viewer-btn-primary"
                                  onClick={() => {
                                    const did = Number(reassignInput);
                                    if (did > 0) {
                                      onOverrideAssociation?.(
                                        epochLabel,
                                        "reassign",
                                        did,
                                        `User reassigned to #${did}`,
                                      );
                                      setReassigningEpoch(null);
                                      setReassignInput("");
                                    }
                                  }}
                                >
                                  Commit
                                </button>
                              </div>
                            </div>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <p className="viewer-muted">
                    No epoch association overrides yet recorded. Use Accept/Reject/Reassign to
                    refine trajectory matches.
                  </p>
                )}
              </div>
            </div>
          ) : (
            <p className="viewer-muted">
              Select a candidate or drag a selection box on the sky to inspect fit &amp;
              uncertainty.
            </p>
          )}

          {/* Feature A: Suggestions panel with reason badges */}
          <h3 className="viewer-sec-title" style={{ marginTop: "var(--space-4)" }}>
            Suggestions ({suggestions.length})
          </h3>
          <ul className="suggestion-list">
            {suggestions.map((s) => (
              <li key={s.detection_id} className="suggestion-card">
                <div className="suggestion-head">
                  <span>
                    Det #{s.detection_id} · ICRS {s.ra.toFixed(4)}°, {s.dec.toFixed(4)}°
                  </span>
                  <span className="viewer-chip">Score {(s.score * 100).toFixed(0)}%</span>
                </div>
                <div className="suggestion-reasons">
                  {s.reasons.map((r, i) => (
                    <span key={i} className="reason-badge">
                      {r}
                    </span>
                  ))}
                </div>
                <button
                  type="button"
                  className="viewer-btn viewer-btn-primary"
                  onClick={() => onPickSuggestion(s)}
                >
                  Track {nextTrackLabel(tracks)}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </aside>
  );
}
