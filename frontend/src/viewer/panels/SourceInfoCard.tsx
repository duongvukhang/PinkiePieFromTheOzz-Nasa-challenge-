import { useState } from "react";
import { formatSexagesimal, skyToPixel, type WcsParams } from "../aladin/wcs";

export interface SelectedSourceData {
  id: string;
  label: string;
  ra: number;
  dec: number;
  pixel_x?: number;
  pixel_y?: number;
  pos_err_arcsec?: number;
  peak_flux?: number;
  flux_unit?: string;
  snr?: number | null;
  fwhm_pixels?: number;
  isTrack: boolean;
}

interface SourceInfoCardProps {
  source: SelectedSourceData;
  wcs?: WcsParams;
  onClose: () => void;
  onInspect: () => void;
  onCenter: () => void;
  onOpenSpectrum: () => void;
  onPredictMotion?: () => void;
  onTrack?: () => void;
}

export function SourceInfoCard({
  source,
  wcs,
  onClose,
  onInspect,
  onCenter,
  onOpenSpectrum,
  onPredictMotion,
  onTrack,
}: SourceInfoCardProps) {
  const [isPinned, setIsPinned] = useState(false);

  const displayId = source.label || source.id;
  let px = source.pixel_x;
  let py = source.pixel_y;
  if ((px == null || py == null) && wcs) {
    const pt = skyToPixel(source.ra, source.dec, wcs);
    px = pt.x;
    py = pt.y;
  }
  const posErr = source.pos_err_arcsec;
  const peakFlux = source.peak_flux;
  const fluxUnit = source.flux_unit ?? "uJy";
  const snrVal = source.snr;
  const fwhmVal = source.fwhm_pixels;

  return (
    <div
      className="source-info-card stellarium-glass"
      role="region"
      aria-label={`Source information for ${displayId}`}
    >
      <div className="card-header">
        <div className="card-title-group">
          <div className="card-icon" aria-hidden="true">
            <svg
              viewBox="0 0 24 24"
              width="16"
              height="16"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="9" />
              <line x1="12" y1="3" x2="12" y2="7" />
              <line x1="12" y1="17" x2="12" y2="21" />
              <line x1="3" y1="12" x2="7" y2="12" />
              <line x1="17" y1="12" x2="21" y2="12" />
            </svg>
          </div>
          <div>
            <div className="card-id">{displayId}</div>
            <div className="card-sub">
              {source.isTrack ? "Tracked Multi-Epoch Source" : "SPHEREx Candidate Source"}
            </div>
          </div>
        </div>

        <div className="card-actions">
          <button
            type="button"
            className={`card-tool-btn ${isPinned ? "is-active" : ""}`}
            onClick={() => setIsPinned(!isPinned)}
            title={isPinned ? "Unpin Card" : "Pin Card"}
            aria-label={isPinned ? "Unpin Card" : "Pin Card"}
          >
            📌
          </button>
          <button
            type="button"
            className="card-tool-btn"
            onClick={onClose}
            title="Close Card"
            aria-label="Close Card"
          >
            ✕
          </button>
        </div>
      </div>

      <div className="card-metrics-block">
        <div className="metric-row">
          <span className="metric-label">RA, Dec (deg):</span>
          <span className="metric-val metric-val-bold">
            {source.ra.toFixed(4)}°, {source.dec.toFixed(4)}°
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Sexagesimal:</span>
          <span className="metric-val metric-val-dim">
            {formatSexagesimal(source.ra, source.dec)}
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Pixel (X, Y):</span>
          <span className="metric-val">
            {px != null && py != null ? `${px.toFixed(1)}, ${py.toFixed(1)}` : "N/A"}
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-label">Pos. Error (1σ):</span>
          <span className="metric-val metric-cyan">
            {posErr != null ? `±${posErr.toFixed(3)}″` : "N/A"}
          </span>
        </div>
      </div>

      <div className="card-metrics-block">
        <div className="metric-row">
          <span className="metric-label">Peak Surface Flux:</span>
          <span className="metric-val metric-cyan metric-val-bold">
            {peakFlux != null ? `${peakFlux.toFixed(2)} ${fluxUnit}` : "N/A"}
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-label">SNR:</span>
          <span className="metric-val metric-val-bold">
            {snrVal != null ? `${snrVal.toFixed(1)} σ` : "N/A"}
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-label">FWHM:</span>
          <span className="metric-val">{fwhmVal != null ? `${fwhmVal.toFixed(1)} px` : "N/A"}</span>
        </div>
      </div>

      {onTrack && (
        <button
          type="button"
          className="card-track-btn"
          onClick={onTrack}
          title="Track source across observation epochs"
        >
          <span className="track-icon">🧭</span>
          <span>Track Across Epochs</span>
        </button>
      )}

      <div className="card-grid-actions">
        <button
          type="button"
          className="card-grid-btn card-grid-btn-primary"
          onClick={onInspect}
          title="Inspect object details and associations"
        >
          <span>⌕</span>
          <span>Inspect Object</span>
        </button>
        <button
          type="button"
          className="card-grid-btn"
          onClick={onCenter}
          title="Center object in focal plane"
        >
          <span>⤢</span>
          <span>Center View</span>
        </button>
        <button
          type="button"
          className="card-grid-btn"
          onClick={onOpenSpectrum}
          title="View infrared spectrum"
        >
          <span>⚡</span>
          <span>View Spectrum</span>
        </button>
        <button
          type="button"
          className="card-grid-btn card-grid-btn-amber"
          onClick={onPredictMotion || onInspect}
          title="Predict proper motion and orbital trajectory"
        >
          <span>↗</span>
          <span>Predict Motion</span>
        </button>
      </div>
    </div>
  );
}
