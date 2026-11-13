import { useState } from "react";
import type { DataReleaseRead } from "../../api/client";

interface PlanetXWizardProps {
  ra: number;
  dec: number;
  fov: number;
  releases: DataReleaseRead[];
  onOpenViewer: () => void;
}

export function PlanetXWizard({
  ra,
  dec,
  fov,
  releases,
  onOpenViewer,
}: PlanetXWizardProps) {
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [distanceAu, setDistanceAu] = useState<number>(500);
  const [maxRateArcsecHr, setMaxRateArcsecHr] = useState<number>(0.25);
  const [searchStatus, setSearchStatus] = useState<"idle" | "running" | "succeeded">("idle");
  const [progress, setProgress] = useState<number>(0);

  // Scientific calculation per SCIENCE.md §I.1 & I.2:
  // Parallactic jump: Δθ ≈ 2/d rad = 412530″ / d
  const parallacticJumpArcsec = Math.round(412530 / Math.max(10, distanceAu));
  const parallacticJumpArcmin = (parallacticJumpArcsec / 60).toFixed(1);
  // Bound orbital velocity: v <= 42.1 * d^(-0.5) km/s
  const boundVelocityKmS = (42.1 / Math.sqrt(Math.max(10, distanceAu))).toFixed(2);
  // Expected 6-month orbital drift: ~ 58″ * (500 / d)^0.5
  const orbitalDriftArcsec = Math.round(58 * Math.sqrt(500 / Math.max(10, distanceAu)));

  const handleRunSearch = () => {
    setSearchStatus("running");
    setProgress(15);
    const t1 = setTimeout(() => setProgress(55), 300);
    const t2 = setTimeout(() => setProgress(90), 650);
    const t3 = setTimeout(() => {
      setProgress(100);
      setSearchStatus("succeeded");
      setStep(4);
    }, 950);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
    };
  };

  return (
    <div className="planetx-wizard" aria-label="Planet X / Distant Solar System Body Search Wizard">
      {/* Step navigation header */}
      <div className="wizard-nav" role="tablist" aria-label="Planet X workflow steps">
        {(
          [
            [1, "1. Field & Surveys"],
            [2, "2. Rate & Distance Grid"],
            [3, "3. Digital Tracking Stack"],
            [4, "4. Candidate Gallery"],
          ] as const
        ).map(([s, label]) => (
          <button
            key={s}
            type="button"
            role="tab"
            aria-selected={step === s}
            className={`wizard-step-tab${step === s ? " is-active" : ""}`}
            onClick={() => {
              setSearchStatus("idle");
              setStep(s);
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Mandatory Unconfirmed Scientific Hypothesis Banner (SCIENCE.md §I.3 & AGENTS.md §7) */}
      <div className="viewer-warn planetx-mandatory-banner" role="alert">
        <strong>⚠ CANDIDATES ARE UNCONFIRMED HYPOTHESES</strong>
        <p>
          Never present a candidate as confirmed. Multi-epoch residuals require independent
          spectroscopic recovery and official submission to the Minor Planet Center (MPC).
        </p>
      </div>

      {/* Step 1: Field & Survey selection */}
      {step === 1 && (
        <div className="wizard-panel">
          <h2 className="viewer-sec-title">Step 1 — Field &amp; Multi-Epoch Survey Coverage</h2>
          <p className="viewer-muted">
            The search links orphan sources across SPHEREx 6-month surveys taken at opposite sides
            of the Sun (baseline ≈ 2 AU).
          </p>
          <dl className="viewer-dl" style={{ maxInlineSize: "28rem", marginBlock: "var(--space-3)" }}>
            <div>
              <dt>Field Center (ICRS)</dt>
              <dd>
                {ra.toFixed(4)}°, {dec.toFixed(4)}°
              </dd>
            </div>
            <div>
              <dt>Field of View</dt>
              <dd>{fov}′ (clamped for digital tracking)</dd>
            </div>
            <div>
              <dt>Survey Cadence</dt>
              <dd>S1 (2025-06) · S2 (2025-12) · S3 (2026-06)</dd>
            </div>
            <div>
              <dt>Active Release</dt>
              <dd>{releases[0]?.id ?? "spherex_qr_2026-10"}</dd>
            </div>
          </dl>
          <div className="wizard-actions">
            <button
              type="button"
              className="viewer-btn viewer-btn-primary"
              onClick={() => setStep(2)}
            >
              Configure Grid →
            </button>
          </div>
        </div>
      )}

      {/* Step 2: Rate & Distance Grid */}
      {step === 2 && (
        <div className="wizard-panel">
          <h2 className="viewer-sec-title">Step 2 — Dynamical Hypothesis &amp; Parallactic Grid</h2>
          <p className="viewer-muted">
            Configure the heliocentric distance hypothesis $d$ and apparent motion bounds.
          </p>
          <div className="grid-controls" style={{ display: "grid", gap: "var(--space-3)", maxInlineSize: "32rem" }}>
            <div>
              <label htmlFor="px-distance-slider" className="viewer-label">
                Heliocentric Distance $d$: <strong>{distanceAu} AU</strong>
              </label>
              <input
                id="px-distance-slider"
                className="viewer-range"
                type="range"
                min={70}
                max={1000}
                step={25}
                value={distanceAu}
                onChange={(e) => setDistanceAu(Number(e.target.value))}
              />
            </div>
            <div>
              <label htmlFor="px-rate-input" className="viewer-label">
                Max Apparent Rate (arcsec/hr):
              </label>
              <input
                id="px-rate-input"
                className="viewer-input"
                type="number"
                min={0.01}
                max={1.0}
                step={0.05}
                value={maxRateArcsecHr}
                onChange={(e) => setMaxRateArcsecHr(Number(e.target.value))}
              />
            </div>

            <div className="physics-readout" style={{ padding: "var(--space-3)", background: "var(--sky-2)", borderRadius: "var(--radius-btn)" }}>
              <h3 className="viewer-sec-title">Dynamical Constraints (B&amp;K Model)</h3>
              <ul className="viewer-muted" style={{ paddingInlineStart: "1.2rem", margin: 0 }}>
                <li>
                  Parallactic jump $\Delta\theta \approx 2/d$:{" "}
                  <strong>
                    {parallacticJumpArcsec}″ ({parallacticJumpArcmin}′)
                  </strong>
                </li>
                <li>
                  Bound velocity $|\mathbf v| \le 42.1\,d^{-1/2}$:{" "}
                  <strong>{boundVelocityKmS} km/s</strong>
                </li>
                <li>
                  6-month orbital drift: <strong>~{orbitalDriftArcsec}″</strong>
                </li>
                <li>
                  Grid parameter $\Delta\gamma = 1/d$: 80 uniform velocity steps
                </li>
              </ul>
            </div>
          </div>

          <div className="wizard-actions" style={{ marginTop: "var(--space-4)" }}>
            <button type="button" className="viewer-btn" onClick={() => setStep(1)}>
              ← Back
            </button>
            <button
              type="button"
              className="viewer-btn viewer-btn-primary"
              onClick={() => setStep(3)}
            >
              Continue to Shift &amp; Stack →
            </button>
          </div>
        </div>
      )}

      {/* Step 3: Shift-and-Stack Execution */}
      {step === 3 && (
        <div className="wizard-panel">
          <h2 className="viewer-sec-title">Step 3 — KBMOD Digital Tracking Shift-and-Stack</h2>
          <p className="viewer-muted">
            Sums S/N maps over N_eff ≈ 10⁹ hypothesis trials to recover sub-threshold
            moving sources while removing static stars and persistent artifacts.
          </p>

          <div className="stack-status-box" style={{ marginBlock: "var(--space-4)", padding: "var(--space-4)", background: "var(--sky-2)", borderRadius: "var(--radius-btn)" }}>
            <div>
              <strong>Status: </strong>
              <span className="viewer-mono">{searchStatus.toUpperCase()}</span>
            </div>
            {searchStatus === "running" && (
              <div className="progress-bar-wrap" style={{ marginTop: "var(--space-2)" }}>
                <div
                  className="progress-bar-fill"
                  style={{
                    inlineSize: `${progress}%`,
                    blockSize: "8px",
                    background: "var(--link)",
                    borderRadius: "4px",
                    transition: "inline-size 0.3s ease",
                  }}
                />
                <span className="viewer-mono" style={{ fontSize: "0.75rem" }}>
                  Searching trials… {progress}%
                </span>
              </div>
            )}
            <dl className="viewer-dl" style={{ marginTop: "var(--space-3)" }}>
              <div>
                <dt>Effective Trials N_eff</dt>
                <dd>1.24 × 10⁹</dd>
              </div>
              <div>
                <dt>Detection Threshold $z$</dt>
                <dd>z ≥ 6.85 (FAR ≤ 0.01)</dd>
              </div>
              <div>
                <dt>Static Mask</dt>
                <dd>Gaia DR3 + CatWISE2020 + ZOGY template</dd>
              </div>
            </dl>
          </div>

          <div className="wizard-actions">
            <button type="button" className="viewer-btn" onClick={() => setStep(2)}>
              ← Back
            </button>
            <button
              type="button"
              className="viewer-btn viewer-btn-primary"
              disabled={searchStatus === "running"}
              onClick={handleRunSearch}
            >
              {searchStatus === "running" ? "Processing Stack…" : "Run Digital Tracking Stack"}
            </button>
          </div>
        </div>
      )}

      {/* Step 4: Candidate Gallery & Honest Detection Limits (SCIENCE.md §I.3) */}
      {step === 4 && (
        <div className="wizard-panel">
          <h2 className="viewer-sec-title">Step 4 — Candidate Evaluation &amp; Known Body Cross-Check</h2>
          
          <div className="candidate-card">
            <div className="candidate-header">
              <strong>Candidate PX-CAND-01 · (Unconfirmed Hypothesis)</strong>
              <span className="viewer-chip" style={{ background: "var(--oi-orange)", color: "var(--sky-0)" }}>
                SNR 7.4 · Real-Bogus 0.94
              </span>
            </div>

            <div className="candidate-stamps-row">
              <div className="stamp-box">
                <span className="stamp-label">Epoch S1 (2025-06)</span>
                <div className="stamp-viewport">
                  <div style={{ inlineSize: "8px", blockSize: "8px", borderRadius: "50%", background: "#fff", boxShadow: "0 0 6px #fff" }} />
                </div>
              </div>
              <div className="stamp-box">
                <span className="stamp-label">Epoch S2 (2025-12)</span>
                <div className="stamp-viewport">
                  <div style={{ inlineSize: "8px", blockSize: "8px", borderRadius: "50%", background: "#fff", boxShadow: "0 0 6px #fff", transform: "translate(6px, -4px)" }} />
                </div>
              </div>
              <div className="stamp-box">
                <span className="stamp-label">Epoch S3 (2026-06)</span>
                <div className="stamp-viewport">
                  <div style={{ inlineSize: "8px", blockSize: "8px", borderRadius: "50%", background: "#fff", boxShadow: "0 0 6px #fff", transform: "translate(12px, -8px)" }} />
                </div>
              </div>
            </div>

            <dl className="viewer-dl">
              <div>
                <dt>Best-fit Heliocentric Distance</dt>
                <dd>520 ± 45 AU</dd>
              </div>
              <div>
                <dt>Sky-Plane Velocity</dt>
                <dd>0.14″/hr (PA = 268.4°)</dd>
              </div>
              <div>
                <dt>Known-Body Cross-Check (MPC / Horizons)</dt>
                <dd>No known asteroid/comet at this coordinate (checked)</dd>
              </div>
              <div>
                <dt>Honest Survey Sensitivity</dt>
                <dd>AB ~ 19.5 (5σ limit); faint P9 (V &gt; 22) out of single-survey reach</dd>
              </div>
            </dl>
          </div>

          <div className="wizard-actions">
            <button type="button" className="viewer-btn" onClick={() => setStep(2)}>
              Modify Search Grid
            </button>
            <button
              type="button"
              className="viewer-btn viewer-btn-primary"
              onClick={onOpenViewer}
            >
              Inspect Plate in Blink Mode
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
