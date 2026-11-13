import { useEffect, useState, type FormEvent } from "react";
import type { DataReleaseRead, ExampleTargetSummary } from "../../api/client";
import { formatSexagesimal, toGalacticAndEcliptic } from "../aladin/wcs";
import { parseCoordsInput, useViewStore, type ViewerMode } from "../state/viewStore";

interface ViewerToolbarProps {
  examples: ExampleTargetSummary[];
  releases: DataReleaseRead[];
  authenticated: boolean;
  onResolveName: (name: string) => Promise<void>;
  resolving: boolean;
  errorMsg?: string;
}

export function ViewerToolbar({
  examples,
  releases,
  authenticated,
  onResolveName,
  resolving,
  errorMsg,
}: ViewerToolbarProps) {
  const { target, ra, dec, fov, release, mode, setView } = useViewStore();
  const [input, setInput] = useState(
    target ||
      (ra !== undefined && dec !== undefined ? `${ra.toFixed(5)}, ${dec.toFixed(5)}` : "WISE 0855"),
  );

  useEffect(() => {
    if (target) {
      setInput(target);
    } else if (ra !== undefined && dec !== undefined) {
      setInput(`${ra.toFixed(5)}, ${dec.toFixed(5)}`);
    }
  }, [target, ra, dec]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;
    const parsed = parseCoordsInput(trimmed);
    if (parsed) {
      setView({ ra: parsed.ra, dec: parsed.dec, target: "", frame: 0 });
      return;
    }
    await onResolveName(trimmed);
  };

  const skyMeta = ra !== undefined && dec !== undefined ? toGalacticAndEcliptic(ra, dec) : null;
  const maxFov = authenticated ? 60 : 15;

  return (
    <header className="viewer-topbar">
      <form
        className="viewer-search-form"
        onSubmit={handleSubmit}
        role="search"
        aria-label="Target or coordinate search"
      >
        <label htmlFor="viewer-coord-input" className="visually-hidden">
          Target name or ICRS coordinates
        </label>
        <input
          id="viewer-coord-input"
          className="viewer-input"
          type="search"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Target or RA, Dec (e.g. WISE 0855)"
          spellCheck={false}
        />
        <button type="submit" className="viewer-btn viewer-btn-primary" disabled={resolving}>
          {resolving ? "Resolving…" : "Go"}
        </button>
      </form>

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
              setInput(ex.name);
              setView({
                target: ex.name,
                ra: ex.ra_deg,
                dec: ex.dec_deg,
                fov: Math.min(maxFov, ex.fov_arcmin),
                frame: 0,
              });
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
    </header>
  );
}
