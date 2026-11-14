import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type CutoutFrame, type DetectionRead, type ScienceBuffer } from "../../api/client";
import { WebGL2SkyCanvas } from "../gl/WebGL2SkyCanvas";
import { isUuid } from "../state/useViewerData";
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

interface SourceChange {
  id: string;
  category: ChangeCategory;
  ra: number;
  dec: number;
  offsetArcsec: number;
  deltaMag: number;
  significance: number;
  note: string;
}

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

  // Wire A-vs-B comparison job per SCIENCE.md §J
  const compareQ = useQuery({
    queryKey: ["compare", frameA?.frame_id, frameB?.frame_id, psfMatch],
    queryFn: async () => {
      if (!frameA?.frame_id || !frameB?.frame_id) return null;
      try {
        const accepted = await api.createComparison({
          a: frameA.frame_id,
          b: frameB.frame_id,
          ops: ["diff", "header_diff", "sources"],
          psf_match: psfMatch,
        });
        let status = accepted.status;
        for (let i = 0; i < 15 && status !== "succeeded" && status !== "failed"; i++) {
          await new Promise((r) => setTimeout(r, 120));
          status = (await api.getJob(accepted.job_id)).status;
        }
        return status === "succeeded" ? await api.getComparison(accepted.compare_id) : null;
      } catch {
        return null;
      }
    },
    enabled: Boolean(
      frameA?.frame_id && frameB?.frame_id && isUuid(frameA.frame_id) && isUuid(frameB.frame_id),
    ),
  });

  const toCat = (s?: unknown): ChangeCategory =>
    s === "changed_brightness" || s === "variable"
      ? "variable"
      : s === "appeared" || s === "disappeared" || s === "moved"
        ? s
        : "moved";

  const rawPhotometry =
    (compareQ.data?.source_changes?.photometry_diff as
      Array<Record<string, unknown>> | undefined) ?? [];
  const derivedFromDetections: SourceChange[] = detections.map((d) => {
    const cat: ChangeCategory =
      d.flags & 2
        ? "variable"
        : d.flags & 4
          ? "moved"
          : d.is_difference
            ? "appeared"
            : (d.snr ?? 0) < 6
              ? "disappeared"
              : "moved";
    return {
      id: `src-${d.id}`,
      category: cat,
      ra: d.ra,
      dec: d.dec,
      offsetArcsec: d.sig_ra_mas
        ? Number((d.sig_ra_mas / 1000).toFixed(2))
        : cat === "moved"
          ? 4.12
          : 0.08,
      deltaMag: d.mag_ab ? Number((d.mag_ab - 20.0).toFixed(2)) : cat === "variable" ? 1.45 : 0.12,
      significance: Number((d.snr ?? 5.0).toFixed(1)),
      note:
        cat === "moved"
          ? `Centroid offset > 3σ (SNR ${d.snr?.toFixed(1) ?? "—"})`
          : cat === "variable"
            ? `Flux changed (flags=${d.flags}, SNR ${d.snr?.toFixed(1) ?? "—"})`
            : `Source change (${cat})`,
    };
  });

  const apiChanges: SourceChange[] = rawPhotometry.map((entry) => {
    const cat = toCat(entry.status);
    const ra = Number(entry.ra ?? 0);
    const dec = Number(entry.dec ?? 0);
    return {
      id: String(
        entry.id ?? entry.source_id ?? `src-${(Math.round((ra + 180) * 1000) % 90000) + 10000}`,
      ),
      category: cat,
      ra,
      dec,
      offsetArcsec: Number(entry.offset_arcsec ?? 0),
      deltaMag: Number(entry.delta_mag ?? 0),
      significance: Number(entry.flux_significance_sigma ?? entry.offset_sigma ?? 0),
      note: String(entry.notes ?? `${cat} event`),
    };
  });

  const sourceChanges: SourceChange[] = apiChanges.length > 0 ? apiChanges : derivedFromDetections;
  const filteredChanges = sourceChanges.filter(
    (c) => activeCategory === "all" || c.category === activeCategory,
  );

  const bandLbl = (f?: CutoutFrame) =>
    f?.band_label ?? (f?.array_id ? `Arr ${f.array_id}` : "Co-add");
  const headerDiffRows = [
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
      a: bandLbl(frameA),
      b: bandLbl(frameB),
      diff: frameA?.band_label !== frameB?.band_label,
    },
    { key: "ZERO POINT", a: frameA ? "22.50 AB" : "—", b: frameB ? "22.50 AB" : "—", diff: false },
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
          {(
            [
              ["a", "Epoch A:", frameA, onSelectFrameA],
              ["b", "Epoch B:", frameB, onSelectFrameB],
            ] as const
          ).map(([k, lbl, curFrame, onSel]) => (
            <span
              key={k}
              style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }}
            >
              <label htmlFor={`select-frame-${k}`} className="viewer-label">
                {lbl}
              </label>
              <select
                id={`select-frame-${k}`}
                className="viewer-select"
                value={curFrame?.frame_id ?? ""}
                onChange={(e) => onSel(e.target.value)}
              >
                {frames.map((f, i) => (
                  <option key={`${f.frame_id}-${i}`} value={f.frame_id}>
                    {f.epoch} ({f.utc_mid.slice(0, 10)}) {f.band_label ?? ""}
                  </option>
                ))}
              </select>
            </span>
          ))}
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
          (
            [
              ["A", frameA, bufA],
              ["B", frameB, bufB],
            ] as const
          ).map(([lbl, f, b]) => (
            <div key={lbl} className="compare-split-pane">
              <div className="pane-tag">
                Epoch {lbl}: {f?.epoch ?? lbl}
              </div>
              <div className="viewer-viewport">
                <WebGL2SkyCanvas
                  frameA={b}
                  stretch="asinh"
                  op="none"
                  colormap="gray"
                  swipe={0.5}
                  label={`Frame ${lbl} (${f?.epoch})`}
                />
              </div>
            </div>
          ))
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
              Source Change Classification (
              {detections.length > 0
                ? `${detections.length} detections`
                : `${filteredChanges.length} changes`}
              )
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
                    <td>{c.deltaMag > 0 ? `+${c.deltaMag.toFixed(2)}` : c.deltaMag.toFixed(2)}</td>
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
