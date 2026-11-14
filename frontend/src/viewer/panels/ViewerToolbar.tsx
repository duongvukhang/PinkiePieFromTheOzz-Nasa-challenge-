import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { DataReleaseRead, ExampleTargetSummary } from "../../api/client";
import { formatSexagesimal, toGalacticAndEcliptic } from "../aladin/wcs";
import { useViewStore, type ViewerMode } from "../state/viewStore";
import { ArchiveSearchModal } from "./ArchiveSearchModal";
import { UploadFitsModal } from "./UploadFitsModal";

interface ViewerToolbarProps {
  examples: ExampleTargetSummary[];
  releases: DataReleaseRead[];
  authenticated: boolean;
  errorMsg?: string;
  isLayersOpen?: boolean;
  onToggleLayers?: () => void;
  isTimelineOpen?: boolean;
  onToggleTimeline?: () => void;
  isInspectorOpen?: boolean;
  onToggleInspector?: () => void;
}

export function ViewerToolbar({
  examples,
  releases,
  authenticated,
  errorMsg,
  isLayersOpen,
  onToggleLayers,
  isTimelineOpen,
  onToggleTimeline,
  isInspectorOpen,
  onToggleInspector,
}: ViewerToolbarProps) {
  const navigate = useNavigate();
  const { target, ra, dec, fov, from, to, release, mode, setView } = useViewStore();
  const [isArchiveModalOpen, setIsArchiveModalOpen] = useState(false);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);

  const skyMeta = ra !== undefined && dec !== undefined ? toGalacticAndEcliptic(ra, dec) : null;
  const maxFov = authenticated ? 60 : 15;

  return (
    <header className="viewer-topbar">
      <div className="viewer-control-group">
        <Link to="/" className="viewer-brand-link" title="SkyTrace Astronomical Observatory">
          <span className="viewer-brand-title">SkyTrace</span>
          <span className="viewer-brand-pill">SPHEREx</span>
        </Link>

        <button
          type="button"
          className="viewer-btn viewer-btn-primary"
          onClick={() => setIsArchiveModalOpen(true)}
          title="Search NASA astronomical archives (SPHEREx, WISE, 2MASS, SkyView)"
        >
          🔍 Search NASA Archives
        </button>

        <button
          type="button"
          className="viewer-btn"
          onClick={() => setIsUploadModalOpen(true)}
          title="Upload custom FITS file for WCS inspection and comparison"
        >
          📁 Upload FITS
        </button>

        <div className="viewer-field-badge" title="Current sky field coordinates and target">
          <span style={{ color: "var(--sky-muted)" }}>Field:</span>
          <strong>
            {target ||
              (ra !== undefined && dec !== undefined
                ? `${ra.toFixed(4)}°, ${dec.toFixed(4)}°`
                : "WISE 0855")}
          </strong>
          {from && to && (
            <span style={{ color: "var(--sky-muted)", fontSize: "0.7rem" }}>
              ({from} → {to})
            </span>
          )}
        </div>
      </div>

      <div className="viewer-control-group">
        <label htmlFor="viewer-fov" className="viewer-label">
          FOV
        </label>
        <select
          id="viewer-fov"
          className="viewer-select"
          value={fov}
          onChange={(e) => setView({ fov: Number(e.target.value) })}
        >
          {[5, 8, 10, 15, ...(maxFov >= 30 ? [30, 60] : [])].map((v) => (
            <option key={v} value={v}>
              {v}′
            </option>
          ))}
        </select>

        <label htmlFor="viewer-release" className="viewer-label">
          Release
        </label>
        <select
          id="viewer-release"
          className="viewer-select"
          value={release}
          onChange={(e) => setView({ release: e.target.value })}
        >
          <option value="latest">latest</option>
          {releases.map((r) => (
            <option key={r.id} value={r.id}>
              {r.version_label ?? r.id}
            </option>
          ))}
        </select>

        <div className="viewer-seg" role="group" aria-label="Viewer mode">
          {(["blink", "compare", "planetx"] as ViewerMode[]).map((m) => (
            <button
              key={m}
              type="button"
              className="viewer-btn"
              aria-pressed={mode === m}
              onClick={() =>
                setView({
                  mode: m,
                  op:
                    m === "compare" && useViewStore.getState().op === "none"
                      ? "diff"
                      : useViewStore.getState().op,
                })
              }
            >
              {m === "blink" ? "Blink" : m === "compare" ? "Compare" : "Planet X"}
            </button>
          ))}
        </div>
      </div>

      <div className="viewer-presets" aria-label="Mandatory calibration targets">
        {examples.map((ex) => (
          <button
            key={ex.id}
            type="button"
            className="viewer-chip"
            aria-pressed={target.toLowerCase() === ex.name.toLowerCase()}
            onClick={() => {
              setView({
                target: ex.name,
                ra: ex.ra_deg,
                dec: ex.dec_deg,
                fov: Math.min(maxFov, ex.fov_arcmin),
                frame: 0,
              });
              navigate(`/v?target=${encodeURIComponent(ex.name)}`);
            }}
          >
            {ex.name}
          </button>
        ))}
        {ra !== undefined && dec !== undefined && skyMeta && (
          <span className="viewer-sky-readout">
            {formatSexagesimal(ra, dec)} · l={skyMeta.l.toFixed(1)}° b={skyMeta.b.toFixed(1)}°
          </span>
        )}
        {errorMsg && <span className="viewer-error-text">{errorMsg}</span>}
      </div>

      <div className="viewer-panel-toggles" role="group" aria-label="Observatory panel toggles">
        {onToggleLayers && (
          <button
            type="button"
            className={`viewer-btn${isLayersOpen ? " viewer-btn-primary" : ""}`}
            aria-pressed={isLayersOpen}
            onClick={onToggleLayers}
            title="Toggle Layers and Arrays Drawer (L)"
          >
            ☰ Layers
          </button>
        )}
        {onToggleTimeline && (
          <button
            type="button"
            className={`viewer-btn${isTimelineOpen ? " viewer-btn-primary" : ""}`}
            aria-pressed={isTimelineOpen}
            onClick={onToggleTimeline}
            title="Toggle Multi-Epoch Timeline (T)"
          >
            ⏱ Timeline
          </button>
        )}
        {onToggleInspector && (
          <button
            type="button"
            className={`viewer-btn${isInspectorOpen ? " viewer-btn-primary" : ""}`}
            aria-pressed={isInspectorOpen}
            onClick={onToggleInspector}
            title="Toggle Science & Object Inspector (I)"
          >
            📊 Inspector
          </button>
        )}
      </div>

      <ArchiveSearchModal
        isOpen={isArchiveModalOpen}
        onClose={() => setIsArchiveModalOpen(false)}
        initialRa={ra ?? 133.79513}
        initialDec={dec ?? -7.24515}
        initialFrom={from || "2025-01-01"}
        initialTo={to || "2025-12-31"}
        examples={examples}
        onApplyInterval={({
          ra: newRa,
          dec: newDec,
          from: newFrom,
          to: newTo,
          bands: newBands,
          source: newSource,
        }) => {
          setView({
            ra: newRa,
            dec: newDec,
            from: newFrom || "",
            to: newTo || "",
            bands: newBands || "all",
            source: newSource || "spherex_irsa",
            target: "",
            frame: 0,
          });
          const qs = new URLSearchParams();
          qs.set("ra", newRa.toFixed(5));
          qs.set("dec", newDec.toFixed(5));
          if (newFrom) qs.set("from", newFrom);
          if (newTo) qs.set("to", newTo);
          if (newBands && newBands !== "all") qs.set("bands", newBands);
          if (newSource && newSource !== "spherex_irsa") qs.set("source", newSource);
          navigate(`/v?${qs.toString()}`);
        }}
      />

      <UploadFitsModal
        isOpen={isUploadModalOpen}
        onClose={() => setIsUploadModalOpen(false)}
        onLoadedField={({ ra: newRa, dec: newDec, fov: newFov, fitsId: newFitsId }) => {
          setView({
            ra: newRa,
            dec: newDec,
            fov: newFov ? Math.min(maxFov, Math.max(1, Math.round(newFov))) : fov,
            fitsId: newFitsId ?? undefined,
            target: "",
            frame: 0,
          });
          const qs = new URLSearchParams();
          qs.set("ra", newRa.toFixed(5));
          qs.set("dec", newDec.toFixed(5));
          if (newFov) qs.set("fov", String(Math.min(maxFov, Math.max(1, Math.round(newFov)))));
          if (newFitsId) qs.set("fitsId", newFitsId);
          navigate(`/v?${qs.toString()}`);
        }}
      />
    </header>
  );
}
