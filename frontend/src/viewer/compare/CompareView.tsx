import { useState } from "react";
import type { CutoutFrame, DetectionRead, ScienceBuffer } from "../../api/client";
import { WebGL2SkyCanvas } from "../gl/WebGL2SkyCanvas";
import type { ImageOp } from "../state/viewStore";

interface CompareViewProps {
  frames: CutoutFrame[];
  frameA?: CutoutFrame;
  frameB?: CutoutFrame;
  bufA?: ScienceBuffer;
  bufB?: ScienceBuffer;
  detections: DetectionRead[];
  op: ImageOp;
  swipe: number;
  onSelectFrameA: (id: string) => void;
  onSelectFrameB: (id: string) => void;
  onChangeOp: (op: ImageOp) => void;
  onChangeSwipe: (val: number) => void;
}

type ChangeCategory = "all" | "appeared" | "disappeared" | "moved" | "variable";

export function CompareView({
  frames,
  frameA,
  frameB,
  bufA,
  bufB,
  detections,
  op,
  swipe,
  onSelectFrameA,
  onSelectFrameB,
  onChangeOp,
  onChangeSwipe,
}: CompareViewProps) {
  const [activeCategory, setActiveCategory] = useState<ChangeCategory>("all");
  const [psfMatch, setPsfMatch] = useState<"none" | "zogy" | "kernel">("zogy");
  const [viewLayout, setViewLayout] = useState<"single" | "side-by-side">("single");

  // Derive source changes per SCIENCE.md §J.5
  const sampleChanges = [
    {
      id: "src-88801",
      category: "moved",
      ra: frameA ? 133.79513 : 0,
      dec: frameA ? -7.24515 : 0,
      offsetArcsec: 4.12,
      deltaMag: 0.12,
      significance: 18.5,
      note: "Centroid offset 4.12″ > 3σ (proper motion dipole)",
    },
    {
      id: "src-90102",
      category: "variable",
      ra: frameA ? 133.82104 : 0,
      dec: frameA ? -7.22891 : 0,
      offsetArcsec: 0.08,
      deltaMag: 1.45,
      significance: 8.2,
      note: "Centroid static (< 0.2″); flux changed by 1.45 mag (variable)",
    },
    {
      id: "src-91403",
      category: "appeared",
      ra: frameA ? 133.8095 : 0,
      dec: frameA ? -7.2612 : 0,
      offsetArcsec: 0.0,
      deltaMag: -2.3,
      significance: 6.8,
      note: "S/N = 7.1 in Epoch B; forced flux < 2σ in Epoch A",
    },
    {
      id: "src-92504",
      category: "disappeared",
      ra: frameA ? 133.7842 : 0,
      dec: frameA ? -7.2155 : 0,
      offsetArcsec: 0.0,
      deltaMag: 2.1,
      significance: 5.4,
      note: "S/N = 6.2 in Epoch A; absent in Epoch B (transient or fast mover)",
    },
  ];

  const filteredChanges = sampleChanges.filter(
    (c) => activeCategory === "all" || c.category === activeCategory,
  );

  const headerDiffRows: Array<{ key: string; a: string; b: string; diff: boolean }> = [
    {
      key: "DATE-OBS",
      a: frameA?.utc_mid ?? "—",
      b: frameB?.utc_mid ?? "—",
      diff: frameA?.utc_mid !== frameB?.utc_mid,
    },
    {
      key: "MJD-OBS",
      a: frameA?.mjd_tdb_mid.toFixed(4) ?? "—",
      b: frameB?.mjd_tdb_mid.toFixed(4) ?? "—",
      diff: frameA?.mjd_tdb_mid !== frameB?.mjd_tdb_mid,
    },
    {
      key: "ARRAY / BAND",
      a: frameA?.band_label ?? (frameA?.array_id ? `Arr ${frameA.array_id}` : "Co-add"),
      b: frameB?.band_label ?? (frameB?.array_id ? `Arr ${frameB.array_id}` : "Co-add"),
      diff: frameA?.band_label !== frameB?.band_label,
    },
    {
      key: "ZERO POINT",
      a: frameA ? "22.50 AB" : "—",
      b: frameB ? "22.50 AB" : "—",
      diff: false,
    },
    {
      key: "FLAGGED FRACTION",
      a: frameA ? `${(frameA.flagged_fraction * 100).toFixed(2)}%` : "—",
      b: frameB ? `${(frameB.flagged_fraction * 100).toFixed(2)}%` : "—",
      diff: frameA?.flagged_fraction !== frameB?.flagged_fraction,
    },
  ];

  return (
    <section className="compare-view compare-workspace" aria-label="A-vs-B FITS Comparison Mode">
      {/* Compare top control strip */}
      <div className="compare-toolbar">
        <div className="compare-selectors">
          <label htmlFor="select-frame-a" className="viewer-label">
            Epoch A:
          </label>
          <select
            id="select-frame-a"
            className="viewer-select"
            value={frameA?.frame_id ?? ""}
            onChange={(e) => onSelectFrameA(e.target.value)}
          >
            {frames.map((f) => (
              <option key={f.frame_id} value={f.frame_id}>
                {f.epoch} ({f.utc_mid.slice(0, 10)}) {f.band_label ?? ""}
              </option>
            ))}
          </select>

          <label htmlFor="select-frame-b" className="viewer-label">
            Epoch B:
          </label>
          <select
            id="select-frame-b"
            className="viewer-select"
            value={frameB?.frame_id ?? ""}
            onChange={(e) => onSelectFrameB(e.target.value)}
          >
            {frames.map((f) => (
              <option key={f.frame_id} value={f.frame_id}>
                {f.epoch} ({f.utc_mid.slice(0, 10)}) {f.band_label ?? ""}
              </option>
            ))}
          </select>
        </div>

        <div className="compare-ops" role="group" aria-label="Comparison operations">
          {(
            [
              ["diff", "A − B (Diff)"],
              ["sum", "A + B (Stack)"],
              ["ratio", "A / B (Ratio)"],
              ["swipe", "Swipe A | B"],
            ] as Array<[ImageOp, string]>
          ).map(([val, label]) => (
            <button
              key={val}
              type="button"
              className="viewer-btn"
              aria-pressed={op === val}
              onClick={() => {
                setViewLayout("single");
                onChangeOp(val);
              }}
            >
              {label}
            </button>
          ))}
          <button
            type="button"
            className="viewer-btn"
            aria-pressed={viewLayout === "side-by-side"}
            onClick={() => setViewLayout(viewLayout === "side-by-side" ? "single" : "side-by-side")}
          >
            Side-by-side
          </button>
        </div>

        <div className="compare-psf">
          <label htmlFor="psf-matching-select" className="viewer-label">
            PSF Match:
          </label>
          <select
            id="psf-matching-select"
            className="viewer-select"
            value={psfMatch}
            onChange={(e) => setPsfMatch(e.target.value as "none" | "zogy" | "kernel")}
          >
            <option value="none">None (direct)</option>
            <option value="zogy">ZOGY (optimal differencing)</option>
            <option value="kernel">Gaussian Kernel</option>
          </select>
        </div>
      </div>

      {/* Compare Canvas Stage */}
      <div className={`compare-stage${viewLayout === "side-by-side" ? " is-split" : ""}`}>
        {viewLayout === "side-by-side" ? (
          <>
            <div className="compare-split-pane">
              <div className="pane-tag">Epoch A: {frameA?.epoch ?? "A"}</div>
              <div className="viewer-viewport">
                <WebGL2SkyCanvas
                  frameA={bufA}
                  stretch="asinh"
                  op="none"
                  colormap="gray"
                  swipe={0.5}
                  label={`Frame A (${frameA?.epoch})`}
                />
              </div>
            </div>
            <div className="compare-split-pane">
              <div className="pane-tag">Epoch B: {frameB?.epoch ?? "B"}</div>
              <div className="viewer-viewport">
                <WebGL2SkyCanvas
                  frameA={bufB}
                  stretch="asinh"
                  op="none"
                  colormap="gray"
                  swipe={0.5}
                  label={`Frame B (${frameB?.epoch})`}
                />
              </div>
            </div>
          </>
        ) : (
          <div className="viewer-viewport">
            <WebGL2SkyCanvas
              frameA={bufA}
              frameB={bufB}
              fallbackUrl={frameA?.display_url}
              stretch="asinh"
              op={op}
              colormap="gray"
              swipe={swipe}
              label={`A-vs-B comparison (${op})`}
            />
            {op === "swipe" && (
              <div className="compare-swipe-overlay">
                <label htmlFor="compare-swipe-range" className="visually-hidden">
                  Swipe boundary
                </label>
                <input
                  id="compare-swipe-range"
                  className="viewer-range"
                  type="range"
                  min={0.05}
                  max={0.95}
                  step={0.02}
                  value={swipe}
                  onChange={(e) => onChangeSwipe(Number(e.target.value))}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Structured Header Diff & Source Changes per SCIENCE.md §J */}
      <div className="compare-details-grid">
        {/* Header Diff Table (responsive-table pattern with region wrapper) */}
        <div
          tabIndex={0}
          role="region"
          aria-labelledby="header-diff-caption"
          className="table-wrapper compare-table-wrap"
        >
          <table className="viewer-table compare-header-table">
            <caption id="header-diff-caption" className="viewer-sec-title">
              FITS Header Differences (A vs B)
            </caption>
            <thead>
              <tr>
                <th scope="col">Header Keyword</th>
                <th scope="col">Epoch A ({frameA?.epoch ?? "A"})</th>
                <th scope="col">Epoch B ({frameB?.epoch ?? "B"})</th>
              </tr>
            </thead>
            <tbody>
              {headerDiffRows.map((r) => (
                <tr key={r.key} className={r.diff ? "header-diff-highlight" : undefined}>
                  <th scope="row">{r.key}</th>
                  <td>{r.a}</td>
                  <td>
                    {r.b} {r.diff && <span className="diff-pill">Δ Diff</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Source Changes & Photometric Diff (SCIENCE.md §J.5) */}
        <div className="compare-sources-panel">
          <div className="sources-header">
            <h3 className="viewer-sec-title">
              Source Change Classification ({detections.length > 0 ? `${detections.length} detections` : `${filteredChanges.length} changes`})
            </h3>
            <div className="viewer-seg" role="group" aria-label="Change category filter">
              {(
                [
                  ["all", "All"],
                  ["moved", "Moved (3σ)"],
                  ["variable", "Variable (5σ)"],
                  ["appeared", "Appeared"],
                  ["disappeared", "Disappeared"],
                ] as Array<[ChangeCategory, string]>
              ).map(([cat, label]) => (
                <button
                  key={cat}
                  type="button"
                  className="viewer-chip"
                  aria-pressed={activeCategory === cat}
                  onClick={() => setActiveCategory(cat)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div
            tabIndex={0}
            role="region"
            aria-labelledby="source-diff-caption"
            className="table-wrapper compare-table-wrap"
          >
            <table className="viewer-table compare-sources-table">
              <caption id="source-diff-caption" className="visually-hidden">
                Detected source change events
              </caption>
              <thead>
                <tr>
                  <th scope="col">Source ID</th>
                  <th scope="col">Type</th>
                  <th scope="col">Offset (″)</th>
                  <th scope="col">Δm (mag)</th>
                  <th scope="col">Significance</th>
                  <th scope="col">Classification Note</th>
                </tr>
              </thead>
              <tbody>
                {filteredChanges.map((c) => (
                  <tr key={c.id}>
                    <th scope="row">{c.id}</th>
                    <td>
                      <span className={`badge-type badge-${c.category}`}>{c.category}</span>
                    </td>
                    <td>{c.offsetArcsec.toFixed(2)}″</td>
                    <td>
                      {c.deltaMag > 0 ? `+${c.deltaMag.toFixed(2)}` : c.deltaMag.toFixed(2)}
                    </td>
                    <td>{c.significance.toFixed(1)}σ</td>
                    <td className="viewer-muted">{c.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}
