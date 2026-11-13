interface SelectedObjectBarProps {
  label: string;
  ra: number;
  dec: number;
  snr?: number | null;
  isTrack: boolean;
  onCenter: () => void;
  onInspect: () => void;
  onOpenSpectrum: () => void;
  onTrack?: () => void;
  onDeselect: () => void;
}

export function SelectedObjectBar({
  label,
  ra,
  dec,
  snr,
  isTrack,
  onCenter,
  onInspect,
  onOpenSpectrum,
  onTrack,
  onDeselect,
}: SelectedObjectBarProps) {
  return (
    <div className="viewer-selected-bar" role="toolbar" aria-label="Selected object actions">
      <div className="viewer-selected-info">
        <span className="viewer-selected-badge">{label}</span>
        <span className="viewer-selected-coords">
          ICRS {ra.toFixed(4)}°, {dec.toFixed(4)}°
        </span>
        {snr != null && <span className="viewer-selected-snr">SNR {snr.toFixed(1)}σ</span>}
      </div>
      <div className="viewer-selected-actions">
        <button
          type="button"
          className="viewer-btn-sm"
          onClick={onCenter}
          title="Center on object in focal plane"
        >
          ⊙ Center
        </button>
        <button
          type="button"
          className="viewer-btn-sm"
          onClick={onInspect}
          title="Open object inspector"
        >
          ⌕ Inspect
        </button>
        <button
          type="button"
          className="viewer-btn-sm"
          onClick={onOpenSpectrum}
          title="View infrared spectrum"
        >
          ⚡ Spectrum
        </button>
        {!isTrack && onTrack && (
          <button
            type="button"
            className="viewer-btn-sm"
            onClick={onTrack}
            title="Track object across observation epochs"
          >
            ⊕ Track
          </button>
        )}
        <button
          type="button"
          className="viewer-btn-sm viewer-btn-close"
          onClick={onDeselect}
          title="Deselect object"
          aria-label="Deselect object"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
