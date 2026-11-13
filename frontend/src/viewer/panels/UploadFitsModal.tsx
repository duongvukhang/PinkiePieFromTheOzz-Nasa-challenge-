import { useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import { api, type UploadCompatibilityReport } from "../../api/client";

interface UploadFitsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onLoadedField?: (coords: { ra: number; dec: number; fov?: number }) => void;
}

export function UploadFitsModal({ isOpen, onClose, onLoadedField }: UploadFitsModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [userEpoch, setUserEpoch] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<UploadCompatibilityReport | null>(null);

  if (!isOpen) return null;

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) validateAndSetFile(dropped);
  };

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected) validateAndSetFile(selected);
  };

  const validateAndSetFile = (f: File) => {
    setError(null);
    setReport(null);
    const validExts = [".fits", ".fit", ".fts", ".fits.gz", ".fz"];
    if (!validExts.some((ext) => f.name.toLowerCase().endsWith(ext))) {
      setError("Please select a valid FITS file (.fits, .fit, .fts, .fits.gz, .fz)");
      return;
    }
    setFile(f);
  };

  const handleUpload = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) return;

    setLoading(true);
    setError(null);
    setReport(null);

    try {
      const res = await api.uploadFits(file, userEpoch.trim() || undefined);
      // Wait for job completion if queued/running
      let status = res.status;
      let delay = 150;
      for (let i = 0; i < 20 && status !== "succeeded" && status !== "failed"; i++) {
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(1000, Math.round(delay * 1.25));
        const job = await api.getJob(res.job_id);
        status = job.status as typeof status;
      }

      const rep = await api.getUploadReport(res.upload_id);
      setReport(rep);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "FITS upload failed");
    } finally {
      setLoading(false);
    }
  };

  const handleApplyToViewer = () => {
    if (!report || !report.valid) return;
    const ra = report.ra_center_deg ?? 133.79513;
    const dec = report.dec_center_deg ?? -7.24515;
    const fov = report.pixscale_arcsec ? Math.min(60, (report.pixscale_arcsec * 256) / 60) : 10;
    onLoadedField?.({ ra, dec, fov });
    onClose();
  };

  return (
    <div
      className="archive-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Upload custom FITS file"
    >
      <div className="archive-modal-content stellarium-glass-dark">
        <div className="archive-modal-header">
          <div className="archive-modal-title-group">
            <h2 className="archive-modal-title">📁 Upload Custom FITS</h2>
            <p className="archive-modal-subtitle">
              Inspect WCS plate solutions, detector calibration, and multi-epoch compatibility.
            </p>
          </div>
          <button
            type="button"
            className="archive-modal-close"
            onClick={onClose}
            aria-label="Close upload modal"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleUpload} className="archive-modal-form">
          <div
            className={`fits-dropzone${isDragOver ? " is-dragover" : ""}${file ? " has-file" : ""}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <input
              type="file"
              id="fits-file-input"
              className="visually-hidden"
              accept=".fits,.fit,.fts,.fits.gz,.fz"
              onChange={handleFileChange}
            />
            <label htmlFor="fits-file-input" className="fits-dropzone-label">
              <span className="fits-dropzone-icon">🌌</span>
              <strong className="fits-dropzone-title">
                {file ? file.name : "Drag & drop a FITS file here, or click to browse"}
              </strong>
              <span className="fits-dropzone-sub">
                {file
                  ? `${(file.size / (1024 * 1024)).toFixed(2)} MB · Ready to inspect`
                  : "Supports .fits, .fit, .fts, .fits.gz, .fz (up to 200 MB)"}
              </span>
            </label>
          </div>

          <div className="archive-form-row">
            <label htmlFor="upload-user-epoch" className="archive-form-label">
              Manual Epoch / Date-Obs{" "}
              <span className="viewer-muted">(optional, if header lacks DATE-OBS)</span>
            </label>
            <input
              id="upload-user-epoch"
              type="text"
              className="viewer-input"
              placeholder="e.g. 60847.41 or 2025-06-21T09:50:24"
              value={userEpoch}
              onChange={(e) => setUserEpoch(e.target.value)}
            />
          </div>

          {error && (
            <div className="archive-error-alert" role="alert">
              {error}
            </div>
          )}

          {report && (
            <div className={`upload-report-card ${report.valid ? "is-valid" : "is-invalid"}`}>
              <div className="report-badge-row">
                <span className={`report-status-badge ${report.compatibility_status}`}>
                  {report.compatibility_status.toUpperCase()}
                </span>
                <span className="viewer-mono">
                  {report.band_label ? `Band: ${report.band_label}` : "Band: Unspecified"} ·{" "}
                  {report.pixscale_arcsec
                    ? `${report.pixscale_arcsec.toFixed(2)}″/px`
                    : "Scale: Unknown"}
                  {report.ra_center_deg !== undefined &&
                  report.ra_center_deg !== null &&
                  report.dec_center_deg !== undefined &&
                  report.dec_center_deg !== null ? (
                    <>
                      {" "}
                      · Center: {report.ra_center_deg.toFixed(4)}°,{" "}
                      {report.dec_center_deg.toFixed(4)}°
                    </>
                  ) : null}
                </span>
              </div>

              {(["warnings", "errors"] as const).map((key) => {
                const items = report[key];
                if (!items?.length) return null;
                const isWarn = key === "warnings";
                return (
                  <ul key={key} className="report-notes-list">
                    {items.map((item, i) => (
                      <li key={i} className={isWarn ? "report-warning-item" : "report-error-item"}>
                        {isWarn ? "⚠ " : "✕ "}
                        {item}
                      </li>
                    ))}
                  </ul>
                );
              })}
            </div>
          )}

          <div className="archive-modal-footer">
            <button type="button" className="viewer-btn" onClick={onClose}>
              Cancel
            </button>
            {!report?.valid ? (
              <button
                type="submit"
                className="viewer-btn viewer-btn-primary"
                disabled={!file || loading}
              >
                {loading ? "Validating FITS..." : "Upload & Validate"}
              </button>
            ) : (
              <button
                type="button"
                className="viewer-btn viewer-btn-primary"
                onClick={handleApplyToViewer}
              >
                Open in Observatory
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
