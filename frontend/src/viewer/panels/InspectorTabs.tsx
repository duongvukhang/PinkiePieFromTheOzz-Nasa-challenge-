import { useEffect, useRef, useState } from "react";
import {
  api,
  type AssociationHypothesis,
  type CutoutFrame,
  type DetectionRead,
  type EphemerisPredictionResult,
  type SpectrumResponse,
  type SuggestionItem,
  type TrackedObjectDetailResponse,
} from "../../api/client";
import { useViewStore, MAX_TRACKED_OBJECTS, type InspectorTab } from "../state/viewStore";
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
  onTrackDetection?: (d: DetectionRead) => void;
  onOverrideAssociation?: (
    epoch: string,
    action: "accept" | "reject" | "reassign",
    detectionId?: number | null,
    reason?: string,
  ) => Promise<void>;
  onRemoveTrack?: (label: string) => void;
  onEphemerisResult?: (result: EphemerisPredictionResult) => void;
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
  onTrackDetection,
  onOverrideAssociation,
  onRemoveTrack,
  onEphemerisResult,
}: InspectorTabsProps) {
  const { inspectorTab, target, setView, activeBox, setActiveBox } = useViewStore();
  const [boxSortKey, setBoxSortKey] = useState<"flux" | "snr" | "dist" | "pm">("flux");
  const activeTab: InspectorTab =
    inspectorTab === "tracks" || inspectorTab === "object"
      ? "object"
      : inspectorTab === "layers"
        ? "flags"
        : inspectorTab;
  const listRef = useRef<HTMLDivElement>(null);
  const specRef = useRef<HTMLDivElement>(null);
  const flagRef = useRef<HTMLDivElement>(null);
  const objRef = useRef<HTMLDivElement>(null);
  const [reassigningEpoch, setReassigningEpoch] = useState<string | null>(null);
  const [reassignInput, setReassignInput] = useState<string>("");

  const [excludedDets, setExcludedDets] = useState<Set<number>>(new Set());
  const [isRecomputing, setIsRecomputing] = useState(false);
  const [recomputeMsg, setRecomputeMsg] = useState<string | null>(null);

  const [predDays, setPredDays] = useState(30);
  const [isPredicting, setIsPredicting] = useState(false);
  const [ephemerisResult, setEphemerisResult] = useState<{
    object_id: string;
    trajectory: Array<{
      time_mjd: number;
      date_iso: string;
      ra_deg: number;
      dec_deg: number;
      uncertainty_semi_major_arcsec: number;
      uncertainty_semi_minor_arcsec: number;
      pa_deg: number;
      is_extrapolated: boolean;
      method: string;
    }>;
    preferred_model?: string;
    baseline_days: number;
  } | null>(null);

  const handleRecompute = async () => {
    if (!objectDetail?.id) return;
    setIsRecomputing(true);
    setRecomputeMsg(null);
    try {
      const res = await api.recomputeTrajectory(objectDetail.id, {
        excluded_detection_ids: Array.from(excludedDets),
      });
      setRecomputeMsg(`Trajectory refit job queued: ${res.refit_job_id.slice(0, 8)}`);
    } catch (err: unknown) {
      setRecomputeMsg(`Error: ${err instanceof Error ? err.message : "Recompute failed"}`);
    } finally {
      setIsRecomputing(false);
    }
  };

  const handlePredict = async () => {
    if (!objectDetail?.id) return;
    setIsPredicting(true);
    try {
      const res = await api.predictEphemeris(objectDetail.id, {
        extrapolation_days: predDays,
        steps: 10,
      });
      setEphemerisResult(res);
      onEphemerisResult?.(res);
    } catch (err) {
      console.error("Ephemeris prediction error:", err);
    } finally {
      setIsPredicting(false);
    }
  };

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
            aria-selected={activeTab === id}
            tabIndex={activeTab === id ? 0 : -1}
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
        tabIndex={activeTab === "spectrum" ? 0 : undefined}
        hidden={activeTab !== "spectrum" ? ("until-found" as unknown as boolean) : false}
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
        tabIndex={activeTab === "flags" ? 0 : undefined}
        hidden={activeTab !== "flags" ? ("until-found" as unknown as boolean) : false}
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
        tabIndex={activeTab === "object" ? 0 : undefined}
        hidden={activeTab !== "object" ? ("until-found" as unknown as boolean) : false}
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

          {/* Box Candidate Selection Panel with Sorting & 2-Object Tracking Cap */}
          {activeBox && (() => {
            const inRa = (ra: number) =>
              activeBox.raMin <= activeBox.raMax
                ? ra >= activeBox.raMin && ra <= activeBox.raMax
                : ra >= activeBox.raMin || ra <= activeBox.raMax;
            const inDec = (dec: number) =>
              activeBox.decMin <= activeBox.decMax
                ? dec >= activeBox.decMin && dec <= activeBox.decMax
                : dec >= activeBox.decMax && dec <= activeBox.decMin;

            const boxCenterRa =
              activeBox.raMin <= activeBox.raMax
                ? 0.5 * (activeBox.raMin + activeBox.raMax)
                : (0.5 * (activeBox.raMin + activeBox.raMax + 360.0)) % 360.0;
            const boxCenterDec = 0.5 * (activeBox.decMin + activeBox.decMax);

            const inBoxDets = (detections || []).filter((d) => inRa(d.ra) && inDec(d.dec));
            const sortedBoxDets = [...inBoxDets].sort((a, b) => {
              if (boxSortKey === "flux") return (b.flux_ujy ?? 0) - (a.flux_ujy ?? 0);
              if (boxSortKey === "snr") return (b.snr ?? 0) - (a.snr ?? 0);
              if (boxSortKey === "dist") {
                const da = Math.hypot(a.ra - boxCenterRa, a.dec - boxCenterDec);
                const db = Math.hypot(b.ra - boxCenterRa, b.dec - boxCenterDec);
                return da - db;
              }
              return 0;
            });

            return (
              <div
                className="box-selection-panel"
                style={{
                  marginTop: "var(--space-3)",
                  marginBottom: "var(--space-3)",
                  padding: "10px",
                  background: "rgba(255,255,255,0.03)",
                  borderRadius: "6px",
                  border: "1px solid var(--border-subtle)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "8px",
                  }}
                >
                  <h3 className="viewer-sec-title" style={{ margin: 0 }}>
                    Objects in Box ({inBoxDets.length})
                  </h3>
                  <button
                    type="button"
                    className="viewer-btn"
                    style={{ fontSize: "0.75rem", padding: "2px 8px" }}
                    onClick={() => setActiveBox(null)}
                  >
                    Clear Box
                  </button>
                </div>

                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    marginBottom: "10px",
                  }}
                >
                  <label
                    htmlFor="box-sort-select"
                    className="viewer-label"
                    style={{ fontSize: "0.75rem" }}
                  >
                    Sort:
                  </label>
                  <select
                    id="box-sort-select"
                    className="viewer-input"
                    style={{ fontSize: "0.75rem", padding: "2px 6px" }}
                    value={boxSortKey}
                    onChange={(e) => setBoxSortKey(e.target.value as any)}
                  >
                    <option value="flux">Highest Flux / Brightness</option>
                    <option value="snr">Highest S/N</option>
                    <option value="dist">Closest to Box Center</option>
                    <option value="pm" disabled={!objectDetail?.motion_fit}>
                      Proper-Motion Significance (requires trajectory fit)
                    </option>
                  </select>
                </div>

                {sortedBoxDets.length > 0 ? (
                  <ul className="suggestion-list" style={{ margin: 0, padding: 0, listStyle: "none" }}>
                    {sortedBoxDets.map((d) => {
                      const isTracked = tracks.some((t) => {
                        const [, coords] = t.split(":");
                        const [r, dec] = (coords ?? "").split(",").map(Number);
                        return (
                          r != null &&
                          dec != null &&
                          Math.abs(r - d.ra) < 0.001 &&
                          Math.abs(dec - d.dec) < 0.001
                        );
                      });
                      const capReached = tracks.length >= MAX_TRACKED_OBJECTS;

                      return (
                        <li
                          key={d.id}
                          className="suggestion-card"
                          style={{ marginBottom: "6px", padding: "8px" }}
                        >
                          <div className="suggestion-head">
                            <span style={{ fontSize: "0.8rem" }}>
                              <strong>Det #{d.id}</strong> · {d.ra.toFixed(4)}°, {d.dec.toFixed(4)}°
                            </span>
                            <span className="viewer-chip" style={{ fontSize: "0.7rem" }}>
                              SNR {d.snr != null ? d.snr.toFixed(1) : "—"}
                            </span>
                          </div>
                          <div
                            style={{
                              fontSize: "0.75rem",
                              color: "var(--text-muted)",
                              marginTop: "4px",
                            }}
                          >
                            Flux: {d.flux_ujy != null ? `${d.flux_ujy.toFixed(1)} µJy` : "—"}
                          </div>
                          <div style={{ marginTop: "6px" }}>
                            {isTracked ? (
                              <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                                <span className="viewer-chip" style={{ color: "var(--oi-green)" }}>
                                  Tracked
                                </span>
                                {onRemoveTrack && (
                                  <button
                                    type="button"
                                    className="viewer-btn viewer-btn-secondary"
                                    style={{ fontSize: "0.72rem", padding: "2px 6px" }}
                                    title="Untrack this object"
                                    onClick={() => {
                                      const match = tracks.find((t) => {
                                        const [, coords] = t.split(":");
                                        const [r, dec] = (coords ?? "").split(",").map(Number);
                                        return (
                                          r != null &&
                                          dec != null &&
                                          Math.abs(r - d.ra) < 0.001 &&
                                          Math.abs(dec - d.dec) < 0.001
                                        );
                                      });
                                      if (match) {
                                        onRemoveTrack(match.split(":")[0]);
                                      }
                                    }}
                                  >
                                    Untrack
                                  </button>
                                )}
                              </div>
                            ) : (
                              <button
                                type="button"
                                className="viewer-btn viewer-btn-primary"
                                style={{ fontSize: "0.75rem", padding: "3px 8px" }}
                                disabled={capReached}
                                title={
                                  capReached
                                    ? "Maximum 2 objects can be tracked concurrently. Untrack an existing object first."
                                    : undefined
                                }
                                onClick={() => {
                                  if (onTrackDetection) {
                                    onTrackDetection(d);
                                  } else {
                                    const lbl = nextTrackLabel(tracks);
                                    useViewStore.getState().addTrack(lbl, d.ra, d.dec);
                                    useViewStore.getState().setView({
                                      obj: String(d.id),
                                      inspectorTab: "object",
                                    });
                                  }
                                }}
                              >
                                {capReached ? "Cap Reached (Max 2)" : `Track ${nextTrackLabel(tracks)}`}
                              </button>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <p className="viewer-muted" style={{ fontSize: "0.8rem" }}>
                    No detections inside selected box area.
                  </p>
                )}
              </div>
            );
          })()}

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

              {/* Feature: Epoch Curation & Trajectory Refitting */}
              <div className="epoch-curation-section" style={{ marginTop: "var(--space-4)" }}>
                <div
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
                >
                  <h3 className="viewer-sec-title">Trajectory Epoch Curation</h3>
                  <button
                    type="button"
                    className="viewer-btn viewer-btn-primary"
                    disabled={isRecomputing}
                    onClick={handleRecompute}
                  >
                    {isRecomputing ? "Refitting…" : "Recompute Trajectory"}
                  </button>
                </div>
                {recomputeMsg && (
                  <p className="viewer-note" style={{ marginTop: "var(--space-1)" }}>
                    {recomputeMsg}
                  </p>
                )}
                <ul
                  className="epoch-checklist"
                  style={{ listStyle: "none", padding: 0, marginTop: "var(--space-2)" }}
                >
                  {objectDetail.measurements.map((m, idx) => {
                    const detId = (m as unknown as { detection_id?: number }).detection_id ?? idx;
                    const isExcluded = excludedDets.has(detId);
                    const snrStr =
                      m.flux_ujy != null && m.flux_err_ujy != null && m.flux_err_ujy > 0
                        ? `${(m.flux_ujy / m.flux_err_ujy).toFixed(1)}σ`
                        : "N/A";
                    return (
                      <li
                        key={`m-${idx}`}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          padding: "4px 8px",
                          borderBottom: "1px solid var(--border-subtle)",
                          opacity: isExcluded ? 0.5 : 1,
                        }}
                      >
                        <label
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "8px",
                            cursor: "pointer",
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={!isExcluded}
                            onChange={(e) => {
                              const next = new Set(excludedDets);
                              if (e.target.checked) {
                                next.delete(detId);
                              } else {
                                next.add(detId);
                              }
                              setExcludedDets(next);
                            }}
                          />
                          <span>
                            <strong>{m.epoch || `Epoch ${idx + 1}`}</strong>
                            <span
                              className="viewer-mono"
                              style={{ fontSize: "0.75rem", marginLeft: "6px" }}
                            >
                              {m.ra != null && m.dec != null
                                ? `${m.ra.toFixed(4)}°, ${m.dec.toFixed(4)}°`
                                : "No pos"}
                            </span>
                          </span>
                        </label>
                        <span className="viewer-chip" style={{ fontSize: "0.7rem" }}>
                          {snrStr}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>

              {/* Feature: Future Ephemeris Extrapolation (Kinematic Prediction) */}
              <div className="ephemeris-prediction-section" style={{ marginTop: "var(--space-4)" }}>
                <h3 className="viewer-sec-title">Future Ephemeris Extrapolation</h3>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    marginTop: "var(--space-2)",
                  }}
                >
                  <label htmlFor="extrap-days" className="viewer-label">
                    Days:
                  </label>
                  <input
                    id="extrap-days"
                    type="number"
                    min={1}
                    max={365}
                    className="viewer-input"
                    style={{ width: "5rem" }}
                    value={predDays}
                    onChange={(e) => setPredDays(Number(e.target.value) || 30)}
                  />
                  <button
                    type="button"
                    className="viewer-btn"
                    disabled={isPredicting}
                    onClick={handlePredict}
                  >
                    {isPredicting ? "Predicting…" : "Predict Ephemeris"}
                  </button>
                </div>
                {ephemerisResult && (
                  <div
                    style={{ marginTop: "var(--space-2)", maxHeight: "12rem", overflowY: "auto" }}
                  >
                    <table
                      style={{
                        width: "100%",
                        fontSize: "0.75rem",
                        fontFamily: "monospace",
                        borderCollapse: "collapse",
                      }}
                    >
                      <thead>
                        <tr
                          style={{
                            borderBottom: "1px solid var(--border-subtle)",
                            textAlign: "left",
                          }}
                        >
                          <th>Date</th>
                          <th>RA (deg)</th>
                          <th>Dec (deg)</th>
                          <th>±1σ (″)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ephemerisResult.trajectory.map((pt, i) => (
                          <tr
                            key={i}
                            style={{
                              borderBottom: "1px solid var(--border-subtle)",
                              opacity: pt.is_extrapolated ? 1 : 0.6,
                            }}
                          >
                            <td>{pt.date_iso.slice(0, 10)}</td>
                            <td>{pt.ra_deg.toFixed(4)}</td>
                            <td>{pt.dec_deg.toFixed(4)}</td>
                            <td>±{pt.uncertainty_semi_major_arcsec.toFixed(2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Feature B: Identity Inspector with per-epoch associations & Accept/Reject/Reassign */}
              <div className="associations-section">
                <h3 className="viewer-sec-title" style={{ marginTop: "var(--space-4)" }}>
                  Epoch Match Hypotheses
                </h3>
                {associations.length > 0 ? (
                  <ul className="associations-list">
                    {associations.map((a) => {
                      const epochLabel = (() => {
                        let best = `E${a.rank}`,
                          minD = Infinity;
                        for (const m of objectDetail.measurements) {
                          if (m.ra != null && a.pred_ra != null) {
                            const d = Math.hypot(
                              m.ra - a.pred_ra,
                              (m.dec ?? 0) - (a.pred_dec ?? 0),
                            );
                            if (d < minD) {
                              minD = d;
                              best = m.epoch ?? best;
                            }
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
                              onClick={() =>
                                onOverrideAssociation?.(
                                  epochLabel,
                                  "accept",
                                  a.detection_id,
                                  "User accepted hypothesis",
                                )
                              }
                            >
                              Accept
                            </button>
                            <button
                              type="button"
                              className="viewer-btn"
                              aria-label={`Reject match for epoch ${epochLabel}`}
                              onClick={() =>
                                onOverrideAssociation?.(
                                  epochLabel,
                                  "reject",
                                  null,
                                  "User rejected match",
                                )
                              }
                            >
                              Reject
                            </button>
                            <button
                              type="button"
                              className="viewer-btn"
                              aria-label={`Reassign match for epoch ${epochLabel}`}
                              onClick={() =>
                                setReassigningEpoch(
                                  reassigningEpoch === epochLabel ? null : epochLabel,
                                )
                              }
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
                  disabled={tracks.length >= MAX_TRACKED_OBJECTS}
                  title={
                    tracks.length >= MAX_TRACKED_OBJECTS
                      ? "Maximum 2 objects can be tracked concurrently. Untrack an existing object first."
                      : undefined
                  }
                  onClick={() => onPickSuggestion(s)}
                >
                  {tracks.length >= MAX_TRACKED_OBJECTS
                    ? "Cap Reached (Max 2)"
                    : `Track ${nextTrackLabel(tracks)}`}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </aside>
  );
}
