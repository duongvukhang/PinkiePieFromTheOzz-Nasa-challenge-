import { api, type CutoutFrame } from "../../api/client";
import {
  useViewStore,
  type ColormapMode,
  type ImageOp,
  type StretchMode,
} from "../state/viewStore";

const BAND_OPTIONS = [
  ["all", "All arrays (1–6)"],
  ["arr1", "Arr 1 · 0.75–1.32 µm"],
  ["arr2", "Arr 2 · 1.32–2.34 µm"],
  ["arr3", "Arr 3 · 2.34–3.12 µm"],
  ["arr4", "Arr 4 · 3.12–3.83 µm"],
  ["arr5", "Arr 5 · 3.83–4.42 µm"],
  ["arr6", "Arr 6 · 4.42–5.00 µm"],
  ["ch4-co2", "CH₄ / CO₂ (3.3 / 4.0 µm)"],
] as const;

const OP_OPTIONS: Array<[ImageOp, string]> = [
  ["none", "Single epoch"],
  ["sum", "A + B (Stack)"],
  ["diff", "A − B (Diff)"],
  ["ratio", "A / B (Ratio)"],
  ["swipe", "Swipe A | B"],
];

interface LayerSidebarProps {
  selectBoxMode: boolean;
  onToggleSelectBox: () => void;
  flaggedFraction?: number;
  frames?: CutoutFrame[];
  onDeleteFrame?: (frameId: string) => void;
  onAddFits?: () => void;
}

export function LayerSidebar({
  selectBoxMode,
  onToggleSelectBox,
  flaggedFraction,
  frames = [],
  onDeleteFrame,
  onAddFits,
}: LayerSidebarProps) {
  const { bands, op, stretch, colormap, overlay, hips, frame, setView, toggleOverlay } =
    useViewStore();

  const handleDelete = (frameId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (onDeleteFrame) {
      onDeleteFrame(frameId);
    } else {
      api.deleteUpload(frameId).catch(() => {});
      useViewStore.getState().removeFrame(frameId);
    }
  };

  const handleAdd = () => {
    if (onAddFits) {
      onAddFits();
    } else {
      useViewStore.getState().setUploadModalOpen(true);
    }
  };

  return (
    <aside className="viewer-panel viewer-left" aria-label="Layers and scientific controls">
      <section className="viewer-panel-sec">
        <h2 className="viewer-sec-title">SPHEREx Bands &amp; Arrays</h2>
        <div className="viewer-btn-col">
          {BAND_OPTIONS.map(([val, label]) => (
            <button
              key={val}
              type="button"
              className="viewer-layer-btn"
              aria-pressed={bands === val}
              onClick={() => setView({ bands: val, frame: 0 })}
            >
              {label}
            </button>
          ))}
        </div>
      </section>

      <section className="viewer-panel-sec">
        <h2 className="viewer-sec-title">WebGL Operations</h2>
        <div className="viewer-btn-col">
          {OP_OPTIONS.map(([val, label]) => (
            <button
              key={val}
              type="button"
              className="viewer-layer-btn"
              aria-pressed={op === val}
              onClick={() =>
                setView({
                  op: val,
                  playing: val === "none" ? useViewStore.getState().playing : false,
                })
              }
            >
              {label}
            </button>
          ))}
        </div>
      </section>

      <section className="viewer-panel-sec">
        <h2 className="viewer-sec-title">Stretch &amp; Palette</h2>
        {(
          [
            [
              ["asinh", "log", "sqrt", "linear"],
              stretch,
              (v: string) => ({ stretch: v as StretchMode }),
              false,
            ],
            [
              ["gray", "sepia", "viridis"],
              colormap,
              (v: string) => ({ colormap: v as ColormapMode }),
              true,
            ],
          ] as const
        ).map(([opts, cur, mkPatch, spaced], gi) => (
          <div
            key={gi}
            className="viewer-row-wrap"
            style={spaced ? { marginTop: "var(--space-2)" } : undefined}
          >
            {opts.map((opt) => (
              <button
                key={opt}
                type="button"
                className="viewer-btn"
                aria-pressed={cur === opt}
                onClick={() => setView(mkPatch(opt))}
              >
                {opt}
              </button>
            ))}
          </div>
        ))}
      </section>

      <section className="viewer-panel-sec">
        <h2 className="viewer-sec-title">Catalogs &amp; Selection</h2>
        <div className="viewer-btn-col">
          <button
            type="button"
            className="viewer-layer-btn"
            aria-pressed={overlay.includes("gaia")}
            onClick={() => toggleOverlay("gaia")}
          >
            <span className="swatch" style={{ borderColor: "var(--oi-sky)" }} /> Gaia DR3 (ICRS)
          </button>
          <button
            type="button"
            className="viewer-layer-btn"
            aria-pressed={overlay.includes("catwise")}
            onClick={() => toggleOverlay("catwise")}
          >
            <span className="swatch" style={{ borderColor: "var(--oi-orange)" }} /> CatWISE2020
            (W1/W2)
          </button>
          <button
            type="button"
            className="viewer-layer-btn"
            aria-pressed={hips}
            onClick={() => setView({ hips: !hips })}
          >
            HiPS Sky Context (Aladin)
          </button>
          <button
            type="button"
            className="viewer-layer-btn"
            aria-pressed={selectBoxMode}
            onClick={onToggleSelectBox}
          >
            Box-Select Object
          </button>
        </div>
        {flaggedFraction !== undefined && (
          <p className="viewer-note">Frame flagged pixels: {(flaggedFraction * 100).toFixed(1)}%</p>
        )}
      </section>

      <section className="viewer-panel-sec">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "var(--space-2)",
          }}
        >
          <h2 className="viewer-sec-title" style={{ margin: 0 }}>
            Active FITS Files (1 per Epoch)
          </h2>
          <button
            type="button"
            className="viewer-btn"
            style={{ minBlockSize: "24px", padding: "2px 8px", fontSize: "0.72rem" }}
            title="Add FITS File"
            aria-label="Add FITS File"
            onClick={handleAdd}
          >
            + Add FITS
          </button>
        </div>
        <div className="viewer-fits-manager" role="list" aria-label="Loaded FITS epochs">
          {frames.length === 0 ? (
            <p className="viewer-note">No FITS epochs loaded</p>
          ) : (
            frames.map((f, i) => {
              const isUpload =
                f.fits_url?.includes("/uploads/") || f.epoch.toLowerCase().includes("upload");
              const isArchive =
                f.epoch.toLowerCase().includes("archive") || f.fits_url?.includes("archive");
              const originLabel = isUpload ? "Uploaded" : isArchive ? "Live Archive" : "Cutout";
              const badgeClass = isUpload
                ? "viewer-fits-badge viewer-fits-badge-upload"
                : isArchive
                  ? "viewer-fits-badge viewer-fits-badge-archive"
                  : "viewer-fits-badge viewer-fits-badge-cutout";

              return (
                <div
                  key={`${f.frame_id}-${i}`}
                  className="viewer-fits-item"
                  role="listitem"
                  style={{
                    borderColor: i === frame ? "var(--sky-text)" : undefined,
                    cursor: "pointer",
                  }}
                  onClick={() => setView({ frame: i, playing: false })}
                >
                  <div className="viewer-fits-info">
                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      <span className={badgeClass}>{originLabel}</span>
                      <span className="viewer-fits-name" title={f.epoch}>
                        {f.epoch}
                      </span>
                    </div>
                    <span className="viewer-fits-meta">
                      {f.utc_mid?.slice(0, 10)} · MJD {f.mjd_tdb_mid.toFixed(2)}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="viewer-fits-del-btn"
                    title={`Delete ${f.epoch}`}
                    aria-label={`Delete ${f.epoch}`}
                    onClick={(e) => handleDelete(f.frame_id, e)}
                  >
                    ✕
                  </button>
                </div>
              );
            })
          )}
        </div>
      </section>
    </aside>
  );
}
