import { useEffect, useState, type FormEvent } from "react";
import { useViewStore } from "../state/viewStore";
import {
  api,
  type ArchiveSearchParams,
  type ArchiveSearchResponse,
  type ArchiveSourceId,
  type ExampleTargetSummary,
  type ObservationEpoch,
  type EpochProduct,
} from "../../api/client";

export interface ArchiveSearchApplyParams {
  ra: number;
  dec: number;
  fov?: number;
  from?: string;
  to?: string;
  bands?: string;
  source: ArchiveSourceId;
}

export function computeFovArcmin(rStr: string, unit: "arcsec" | "arcmin" | "deg"): number {
  const r = Number.parseFloat(rStr) || 5.0;
  const rArcmin = unit === "deg" ? r * 60.0 : unit === "arcsec" ? r / 60.0 : r;
  return Math.max(0.1, Math.min(180.0, rArcmin * 2.0));
}

interface ArchiveSearchModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialRa?: number;
  initialDec?: number;
  initialFrom?: string;
  initialTo?: string;
  examples?: ExampleTargetSummary[];
  onApplyInterval: (params: ArchiveSearchApplyParams) => void;
}

const ARCHIVE_SOURCES: { id: ArchiveSourceId; label: string; desc: string }[] = [
  {
    id: "spherex_irsa",
    label: "NASA / IPAC IRSA — SPHEREx (ObsCore)",
    desc: "Primary all-sky spectral survey (Bands D1–D6, 0.75–5.0 µm)",
  },
];

export function ArchiveSearchModal({
  isOpen,
  onClose,
  initialRa = 133.79513,
  initialDec = -7.24515,
  initialFrom = "2025-01-01",
  initialTo = "2025-12-31",
  examples,
  onApplyInterval,
}: ArchiveSearchModalProps) {
  const [source, setSource] = useState<ArchiveSourceId>("spherex_irsa");
  const [ra, setRa] = useState(initialRa.toFixed(5));
  const [dec, setDec] = useState(initialDec.toFixed(5));
  const [radius, setRadius] = useState("5.0");
  const [radiusUnit, setRadiusUnit] = useState<"arcsec" | "arcmin" | "deg">("arcmin");
  const [startDate, setStartDate] = useState(initialFrom);
  const [endDate, setEndDate] = useState(initialTo);
  const [band, setBand] = useState("ALL");

  const [loading, setLoading] = useState(false);
  const [loadingEpoch, setLoadingEpoch] = useState<string | null>(null);
  const [loadingAll, setLoadingAll] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchResult, setSearchResult] = useState<ArchiveSearchResponse | null>(null);

  useEffect(() => {
    if (isOpen) {
      setRa(initialRa.toFixed(5));
      setDec(initialDec.toFixed(5));
      if (initialFrom) setStartDate(initialFrom);
      if (initialTo) setEndDate(initialTo);
    }
  }, [isOpen, initialRa, initialDec, initialFrom, initialTo]);

  // Handle escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSearch = async (e?: FormEvent) => {
    if (e) e.preventDefault();
    setError(null);
    const raNum = Number.parseFloat(ra);
    const decNum = Number.parseFloat(dec);
    const radNum = Number.parseFloat(radius);

    if (!Number.isFinite(raNum) || raNum < 0 || raNum > 360) {
      setError("RA must be between 0.0° and 360.0°");
      return;
    }
    if (!Number.isFinite(decNum) || decNum < -90 || decNum > 90) {
      setError("Dec must be between -90.0° and +90.0°");
      return;
    }
    if (!Number.isFinite(radNum) || radNum <= 0) {
      setError("Radius must be greater than 0");
      return;
    }
    if (startDate && endDate && startDate > endDate) {
      setError("Start date cannot be after end date");
      return;
    }

    setLoading(true);
    try {
      const q: ArchiveSearchParams = {
        ra: raNum,
        dec: decNum,
        radius: radNum,
        radius_unit: radiusUnit,
        source,
        from: startDate || undefined,
        to: endDate || undefined,
        band: band === "ALL" ? undefined : band,
        max_records: 50,
      };
      const res = await api.searchArchive(q);
      setSearchResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Archive query failed");
    } finally {
      setLoading(false);
    }
  };

  const handleLoadEpoch = async (ep: ObservationEpoch) => {
    const raNum = Number.parseFloat(ra);
    const decNum = Number.parseFloat(dec);
    const fovArcmin = computeFovArcmin(radius, radiusUnit);
    useViewStore.getState().setView({ fov: fovArcmin });
    let fromVal = startDate || undefined;
    let toVal = endDate || undefined;
    if (!fromVal || !toVal || fromVal === toVal) {
      const dateStr = ep.date_obs
        ? ep.date_obs.slice(0, 10)
        : startDate || new Date().toISOString().slice(0, 10);
      const base = new Date(`${dateStr}T12:00:00Z`);
      const prevDay = new Date(base.getTime() - 86400000).toISOString().slice(0, 10);
      const nextDay = new Date(base.getTime() + 86400000).toISOString().slice(0, 10);
      fromVal = prevDay;
      toVal = nextDay;
    }
    const activeProd =
      (ep.products ?? []).find((p) => p.obs_id === ep.selected_product_id) ??
      (ep.products ?? [])[0];
    if (activeProd) {
      setLoadingEpoch(ep.epoch_id);
      try {
        const loaded = await api.loadArchiveProduct({
          obs_id: activeProd.obs_id,
          access_url: activeProd.access_url || undefined,
          source,
          band: activeProd.band || undefined,
          ra_deg: raNum,
          dec_deg: decNum,
          mjd_obs: ep.mjd_obs ?? undefined,
          fov_arcmin: fovArcmin,
        });
        if (loaded?.fits_file_id) {
          useViewStore.getState().addFitsId(loaded.fits_file_id);
        }
      } catch (err) {
        console.warn("Failed to load archive product:", err);
      } finally {
        setLoadingEpoch(null);
      }
    }

    onApplyInterval({
      ra: raNum,
      dec: decNum,
      fov: fovArcmin,
      from: fromVal,
      to: toVal,
      bands: band === "ALL" ? "all" : band.toLowerCase(),
      source,
    });
    onClose();
  };

  const handleLoadAllInterval = async () => {
    const raNum = Number.parseFloat(ra);
    const decNum = Number.parseFloat(dec);
    const fovArcmin = computeFovArcmin(radius, radiusUnit);
    useViewStore.getState().setView({ fov: fovArcmin });
    const epochs = searchResult?.epochs ?? [];

    setLoadingAll(true);
    let resolvedBand = band === "ALL" ? "all" : band.toLowerCase();
    try {
      const refEpoch = epochs[0];
      const refProd =
        (refEpoch?.products ?? []).find((p) => p.obs_id === refEpoch.selected_product_id) ??
        (refEpoch?.products ?? [])[0];
      const refBand = refProd?.detector || refProd?.band;
      if (refBand) {
        resolvedBand = refBand.toLowerCase();
      }

      const prodsToLoad = epochs
        .map((ep) => {
          const prods = ep.products ?? [];
          const matched =
            prods.find(
              (p) =>
                (refBand && (p.detector === refBand || p.band === refBand)) ||
                p.obs_id === ep.selected_product_id,
            ) ?? prods[0];
          return { prod: matched, mjd_obs: ep.mjd_obs };
        })
        .filter((item): item is { prod: EpochProduct; mjd_obs: number | null | undefined } => Boolean(item.prod));

      // Limit concurrency: sequential load and immediate store update before navigation
      for (const { prod, mjd_obs } of prodsToLoad) {
        try {
          const res = await api.loadArchiveProduct({
            obs_id: prod.obs_id,
            access_url: prod.access_url || undefined,
            source,
            band: prod.band || undefined,
            ra_deg: raNum,
            dec_deg: decNum,
            mjd_obs: mjd_obs ?? undefined,
            fov_arcmin: fovArcmin,
          });
          if (res?.fits_file_id) {
            useViewStore.getState().addFitsId(res.fits_file_id);
          }
        } catch {
          // Continue loading remaining products
        }
      }
    } finally {
      setLoadingAll(false);
    }

    onApplyInterval({
      ra: raNum,
      dec: decNum,
      fov: fovArcmin,
      from: startDate || undefined,
      to: endDate || undefined,
      bands: resolvedBand,
      source,
    });
    onClose();
  };

  return (
    <div
      className="archive-modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="archive-modal-title"
    >
      <div className="archive-modal-window">
        {/* Header */}
        <div className="archive-modal-header">
          <div>
            <h2 id="archive-modal-title" className="archive-modal-heading">
              Search NASA Astronomical Archives
            </h2>
            <p className="archive-modal-subheading">
              Query multi-epoch infrared surveys, filter temporal intervals, and inspect verified
              observation epochs.
            </p>
          </div>
          <button
            type="button"
            className="archive-modal-close"
            onClick={onClose}
            aria-label="Close archive dialog"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="archive-modal-body">
          {/* Data Source Selector */}
          <div className="archive-form-group">
            <label htmlFor="archive-source-select" className="viewer-label">
              NASA Data Source
            </label>
            <select
              id="archive-source-select"
              className="viewer-select"
              style={{ width: "100%" }}
              value={source}
              onChange={(e) => {
                setSource(e.target.value as ArchiveSourceId);
                setBand("ALL");
              }}
            >
              {ARCHIVE_SOURCES.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
            <span className="viewer-muted" style={{ fontSize: "0.75rem", marginTop: "2px" }}>
              {ARCHIVE_SOURCES.find((s) => s.id === source)?.desc}
            </span>
          </div>

          {/* Quick Presets from dynamic examples */}
          {examples && examples.length > 0 && (
            <div className="archive-presets-row">
              <span className="viewer-muted" style={{ fontSize: "0.75rem" }}>
                Presets:
              </span>
              {examples.map((p) => (
                <button
                  key={p.name}
                  type="button"
                  className="viewer-chip"
                  onClick={() => {
                    setRa(p.ra_deg.toFixed(5));
                    setDec(p.dec_deg.toFixed(5));
                  }}
                >
                  {p.name}
                </button>
              ))}
            </div>
          )}

          {/* Coordinates & Radius Row */}
          <div className="archive-form-grid">
            <div className="archive-form-group">
              <label htmlFor="archive-ra" className="viewer-label">
                Right Ascension (deg)
              </label>
              <input
                id="archive-ra"
                type="number"
                step="0.00001"
                className="viewer-input"
                value={ra}
                onChange={(e) => setRa(e.target.value)}
                placeholder="e.g. 133.79513"
              />
            </div>
            <div className="archive-form-group">
              <label htmlFor="archive-dec" className="viewer-label">
                Declination (deg)
              </label>
              <input
                id="archive-dec"
                type="number"
                step="0.00001"
                className="viewer-input"
                value={dec}
                onChange={(e) => setDec(e.target.value)}
                placeholder="e.g. -7.24515"
              />
            </div>
            <div className="archive-form-group">
              <label htmlFor="archive-radius" className="viewer-label">
                Cone Radius
              </label>
              <div style={{ display: "flex", gap: "6px" }}>
                <input
                  id="archive-radius"
                  type="number"
                  step="0.1"
                  min="0.1"
                  className="viewer-input"
                  style={{ width: "65%" }}
                  value={radius}
                  onChange={(e) => setRadius(e.target.value)}
                />
                <select
                  aria-label="Radius unit"
                  className="viewer-select"
                  style={{ width: "35%" }}
                  value={radiusUnit}
                  onChange={(e) => setRadiusUnit(e.target.value as "arcsec" | "arcmin" | "deg")}
                >
                  <option value="arcsec">arcsec</option>
                  <option value="arcmin">arcmin</option>
                  <option value="deg">deg</option>
                </select>
              </div>
            </div>
          </div>

          {/* Time Interval & Band Row */}
          <div className="archive-form-grid">
            <div className="archive-form-group">
              <label htmlFor="archive-start-date" className="viewer-label">
                Start Date (from)
              </label>
              <input
                id="archive-start-date"
                type="date"
                className="viewer-input"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </div>
            <div className="archive-form-group">
              <label htmlFor="archive-end-date" className="viewer-label">
                End Date (to)
              </label>
              <input
                id="archive-end-date"
                type="date"
                className="viewer-input"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
              />
            </div>
            <div className="archive-form-group">
              <label htmlFor="archive-band" className="viewer-label">
                Band / Detector
              </label>
              <select
                id="archive-band"
                className="viewer-select"
                value={band}
                onChange={(e) => setBand(e.target.value)}
              >
                <option value="ALL">ALL Bands</option>
                <option value="D1">Band 1 (LVF1 0.75–1.11 µm)</option>
                <option value="D2">Band 2 (LVF2 1.11–1.64 µm)</option>
                <option value="D3">Band 3 (LVF3 1.64–2.42 µm)</option>
                <option value="D4">Band 4 (LVF4 2.42–3.82 µm)</option>
                <option value="D5">Band 5 (LVF5 3.82–4.42 µm)</option>
                <option value="D6">Band 6 (LVF6 4.42–5.00 µm)</option>
              </select>
            </div>
          </div>

          {/* Action Row */}
          <div style={{ display: "flex", gap: "10px", marginTop: "12px", alignItems: "center" }}>
            <button
              type="button"
              className="viewer-btn viewer-btn-primary"
              style={{ padding: "8px 18px", fontWeight: 600 }}
              disabled={loading}
              onClick={() => handleSearch()}
            >
              {loading ? "Searching NASA Archive…" : "Search Archive"}
            </button>
            <button
              type="button"
              className="viewer-btn"
              onClick={() => {
                setRa(initialRa.toFixed(5));
                setDec(initialDec.toFixed(5));
                setStartDate("2025-01-01");
                setEndDate("2025-12-31");
                setBand("ALL");
                setSearchResult(null);
                setError(null);
              }}
            >
              Reset
            </button>
            {error && <span className="archive-error-text">{error}</span>}
          </div>

          {/* Results Area */}
          {searchResult && (
            <div className="archive-results-container">
              <div className="archive-results-header">
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span className="archive-status-tag" data-status={searchResult.service_status}>
                    {searchResult.service_status === "LIVE_ARCHIVE"
                      ? "● NASA Live Archive"
                      : searchResult.service_status === "CACHED_FALLBACK"
                        ? "▲ Verified Cache"
                        : "◆ Synthetic Survey Demo"}
                  </span>
                  <span style={{ fontSize: "0.8rem", color: "var(--sky-muted)" }}>
                    Found {searchResult.total_epochs ?? 0} epochs ({searchResult.total_found ?? 0}{" "}
                    products)
                  </span>
                </div>
                {(searchResult.total_epochs ?? 0) > 0 && (
                  <button
                    type="button"
                    className="viewer-btn viewer-btn-primary"
                    style={{ fontSize: "0.75rem", padding: "4px 10px" }}
                    disabled={Boolean(loadingEpoch || loadingAll)}
                    onClick={handleLoadAllInterval}
                  >
                    {loadingAll ? "Loading All FITS…" : "Load All in Interval →"}
                  </button>
                )}
              </div>

              {(searchResult.notes ?? []).length > 0 && (
                <div className="archive-notes-box">
                  {(searchResult.notes ?? []).map((n, i) => (
                    <div key={i}>{n}</div>
                  ))}
                </div>
              )}

              {/* Epoch List */}
              <div className="archive-epoch-list">
                {(searchResult.epochs ?? []).map((ep) => {
                  const deltaText =
                    ep.time_delta_days === 0
                      ? "Reference Epoch (t₀)"
                      : ep.time_delta_days !== null && ep.time_delta_days !== undefined
                        ? `Δt: ${ep.time_delta_days > 0 ? "+" : ""}${ep.time_delta_days.toFixed(1)} days`
                        : "Undated";

                  return (
                    <div key={ep.epoch_id} className="archive-epoch-card">
                      <div className="archive-epoch-card-left">
                        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                          <span className="archive-epoch-badge">Epoch {ep.epoch_index + 1}</span>
                          <span style={{ fontWeight: 600 }}>
                            {ep.date_obs ? ep.date_obs.slice(0, 10) : "Undated"}
                          </span>
                          <span className="archive-delta-badge">{deltaText}</span>
                        </div>
                        <div
                          style={{
                            fontSize: "0.75rem",
                            color: "var(--sky-muted)",
                            marginTop: "4px",
                          }}
                        >
                          MJD: {ep.mjd_obs ? ep.mjd_obs.toFixed(3) : "—"} · Products:{" "}
                          {(ep.products ?? [])
                            .map((p) => p.band || p.detector || "Science")
                            .join(", ")}
                        </div>
                      </div>
                      <button
                        type="button"
                        className="viewer-btn"
                        style={{ fontSize: "0.75rem", minBlockSize: "28px" }}
                        disabled={Boolean(loadingEpoch || loadingAll)}
                        onClick={() => handleLoadEpoch(ep)}
                      >
                        {loadingEpoch === ep.epoch_id ? "Loading FITS…" : "Load Epoch"}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
