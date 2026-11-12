import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { MANDATORY_TARGETS } from "../app/layout/Sidebar";
import {
  Badge,
  Callout,
  CircleLink,
  GalleryMat,
  Kicker,
  SpotMark,
} from "../design/components/Primitives";

const EPOCHS = [
  {
    id: "E1",
    date: "2025-06-14",
    mjd: "60840.2",
    dx: -4,
    dy: 2,
    ra: "08h55m10.831s",
    dec: "-07°14′42.53″",
  },
  {
    id: "E2",
    date: "2025-12-12",
    mjd: "61021.7",
    dx: 0,
    dy: 0,
    ra: "08h55m10.562s",
    dec: "-07°14′42.08″",
  },
  {
    id: "E3",
    date: "2026-06-15",
    mjd: "61206.1",
    dx: 4,
    dy: -2,
    ra: "08h55m10.295s",
    dec: "-07°14′41.62″",
  },
] as const;

const MILESTONES = [
  {
    year: "c. 1600 BCE",
    icon: "astrolabe" as const,
    title: "Planētai Ledgers",
    text: "Babylonian tablets track five wandering lights against fixed stars across seasons.",
  },
  {
    year: "1887",
    icon: "plate" as const,
    title: "Carte du Ciel",
    text: "Photographic glass plates freeze stellar coordinates so future decades can measure drift.",
  },
  {
    year: "1930",
    icon: "blink" as const,
    title: "Blink Comparator",
    text: "Alternating two epochs through an optical prism makes faint proper motion leap out.",
  },
  {
    year: "2025",
    icon: "cryo" as const,
    title: "SPHEREx All-Sky",
    text: "102 spectral bands across 6-month cadences turn infrared history into astrometry.",
  },
];

const CASES = [
  {
    rubric: "True Mover",
    title: "WISE 0855",
    summary:
      "Monotonic shift (~8.1″/yr) across repeated epochs; symmetric positive/negative dipole in A−B.",
    specs: [
      ["Centroid Shift", "≈ 0.8 px / 6 mo"],
      ["Flux Stability", "Consistent W2/4.6 µm"],
      ["Verdict", "Confirmed PM (χ²ν ≈ 1.0)"],
    ],
  },
  {
    rubric: "Variable Star",
    title: "RW Aurigae & R Dra",
    summary:
      "Dramatic brightness swings without genuine displacement—never mistake luminosity for motion.",
    specs: [
      ["Centroid Shift", "0.00″ ± 0.08″"],
      ["Flux Variation", "Δmag > 1.5"],
      ["Verdict", "Stationary Variable"],
    ],
  },
  {
    rubric: "Detector Artifact",
    title: "Persistence & Cosmic Ray",
    summary:
      "Single-epoch hits and bright-star afterimages mimic transients until bitmasks and multi-frame stacks reject them.",
    specs: [
      ["Frame Presence", "1 of N exposures"],
      ["PSF Shape", "Sharp / Unsampled"],
      ["Verdict", "Flagged & Rejected"],
    ],
  },
];

export function Home() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [epochIdx, setEpochIdx] = useState(0);
  const currentEpoch = EPOCHS[epochIdx] ?? EPOCHS[0];

  const handleSearch = (e: FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) {
      navigate("/v?target=WISE%200855");
      return;
    }
    const coords = q.match(/^(-?\d+(?:\.\d+)?)[,\s]+(-?\d+(?:\.\d+)?)$/);
    navigate(coords ? `/v?ra=${coords[1]}&dec=${coords[2]}` : `/v?target=${encodeURIComponent(q)}`);
  };

  return (
    <>
      {/* 1. Thesis & First Light */}
      <section className="section">
        <div className="container">
          <Kicker>Observational Journal · SPHEREx Multi-Epoch Archive</Kicker>
          <h1 id="main-heading" className="hero-thesis" style={{ marginTop: "var(--space-4)" }}>
            <span className="hero-thesis-lead">
              Every discovery in the sky begins by comparing tonight against yesterday.
            </span>{" "}
            SkyTrace is an independent observatory for investigating real SPHEREx infrared frames
            across time—measuring motion, weighing noise, and preserving uncertainty.
          </h1>

          <search className="coord-bar">
            <form onSubmit={handleSearch}>
              <div className="coord-bar-row">
                <div className="field" style={{ marginBlockEnd: 0 }}>
                  <label className="field-label" htmlFor="hero-coord-input">
                    Target name or ICRS coordinates (J2000 / J2016.0)
                  </label>
                  <span id="hero-coord-hint" className="field-hint">
                    Examples: 133.795, -7.245 · 08:55:10.8 -07:14:42 · WISE 0855
                  </span>
                  <input
                    id="hero-coord-input"
                    name="target"
                    type="search"
                    className="input"
                    aria-describedby="hero-coord-hint"
                    placeholder="WISE 0855 or 133.7951, -7.2451"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    spellCheck={false}
                    autoCorrect="off"
                    autoCapitalize="none"
                  />
                </div>
                <button type="submit" className="btn btn-primary" style={{ minBlockSize: 48 }}>
                  Inspect field →
                </button>
              </div>
            </form>
            <div className="quick-targets">
              <span>Calibration presets:</span>
              {MANDATORY_TARGETS.slice(0, 3).map((t) => (
                <Link key={t.name} to={t.to} className="badge" style={{ textDecoration: "none" }}>
                  {t.name}
                </Link>
              ))}
            </div>
          </search>

          <div className="diptych">
            <GalleryMat
              caption="Schematic A: Historical astrometric plate grid"
              provenance="ARCHIVAL REFERENCE · EPOCH 1891"
            >
              <svg
                viewBox="0 0 360 180"
                width="360"
                height="180"
                style={{
                  inlineSize: "100%",
                  blockSize: "auto",
                  background: "#FAF9F5",
                  border: "1px solid var(--rule)",
                }}
                role="img"
                aria-label="Schematic of an 1891 glass plate reticle with a plotted star field"
              >
                <g stroke="#DDD8CC" strokeWidth="1">
                  <line x1="90" y1="0" x2="90" y2="180" />
                  <line x1="180" y1="0" x2="180" y2="180" />
                  <line x1="270" y1="0" x2="270" y2="180" />
                  <line x1="0" y1="60" x2="360" y2="60" />
                  <line x1="0" y1="120" x2="360" y2="120" />
                </g>
                <circle cx="110" cy="48" r="2.2" fill="#141413" />
                <circle cx="242" cy="134" r="3" fill="#141413" />
                <circle cx="295" cy="52" r="2" fill="#141413" />
                <circle cx="176" cy="96" r="3.5" fill="none" stroke="#B84318" strokeWidth="1.5" />
                <text x="16" y="164" fontFamily="monospace" fontSize="10" fill="#66635C">
                  PLATE I · MERIDIAN RETICLE (SCHEMATIC)
                </text>
              </svg>
            </GalleryMat>

            <GalleryMat
              caption="Schematic B: Multi-epoch SPHEREx trajectory overlay"
              provenance="SCHEMATIC · 6.2″/PX · 1σ/2σ ELLIPSES"
            >
              <svg
                viewBox="0 0 360 180"
                width="360"
                height="180"
                style={{
                  inlineSize: "100%",
                  blockSize: "auto",
                  background: "#07090D",
                  border: "1px solid var(--sky-rule)",
                }}
                role="img"
                aria-label="Schematic of three infrared epochs showing a moving object vector and uncertainty ellipses"
              >
                <circle cx="110" cy="48" r="2.2" fill="#EDEBE4" opacity="0.6" />
                <circle cx="242" cy="134" r="2.5" fill="#EDEBE4" opacity="0.7" />
                <circle cx="295" cy="52" r="2" fill="#EDEBE4" opacity="0.5" />
                <line
                  x1="145"
                  y1="108"
                  x2="215"
                  y2="72"
                  stroke="#E69F00"
                  strokeWidth="1.5"
                  strokeDasharray="4 3"
                />
                <circle cx="155" cy="103" r="6" fill="none" stroke="#56B4E9" strokeWidth="1.2" />
                <circle cx="180" cy="90" r="7" fill="none" stroke="#E69F00" strokeWidth="1.5" />
                <circle cx="205" cy="77" r="6" fill="none" stroke="#009E73" strokeWidth="1.2" />
                <circle cx="180" cy="90" r="2.5" fill="#E69F00" />
                <text x="16" y="164" fontFamily="monospace" fontSize="10" fill="#8F98A6">
                  OBJECT A · PROPER MOTION μ + ERROR ELLIPSES (SCHEMATIC)
                </text>
              </svg>
            </GalleryMat>
          </div>
        </div>
      </section>

      {/* 2. Evolution of Observation */}
      <section className="section">
        <div className="container">
          <div className="chapter-head">
            <div>
              <Kicker>I. Lineage of the Plate</Kicker>
              <h2 className="chapter-title" style={{ marginTop: "var(--space-2)" }}>
                How we learned to see what moves.
              </h2>
            </div>
            <p className="prose-lead">
              Long before infrared focal planes, astronomers discovered worlds by aligning two views
              of the same patch of sky separated by months—and asking which point refused to stay
              put.
            </p>
          </div>

          <div className="col-grid-4">
            {MILESTONES.map((m) => (
              <article key={m.year} className="col-divided">
                <SpotMark kind={m.icon} />
                <div className="year-numeral">{m.year}</div>
                <h3
                  style={{
                    fontFamily: "var(--font-sans)",
                    fontSize: "var(--text-base)",
                    fontWeight: 600,
                  }}
                >
                  {m.title}
                </h3>
                <p style={{ fontSize: "var(--text-sm)", color: "var(--ink-1)" }}>{m.text}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* 3. Time as an Instrument */}
      <section className="section section-oat">
        <div className="container">
          <div className="chapter-head">
            <div>
              <Kicker>II. Multi-Epoch Evidence</Kicker>
              <h2 className="chapter-title" style={{ marginTop: "var(--space-2)" }}>
                A single frame is a portrait. Three epochs are a trajectory.
              </h2>
            </div>
            <p className="prose-lead">
              Step through three schematic epochs of a high-proper-motion brown dwarf field.
              SkyTrace aligns astrometric frames to ICRS before blinking so stationary stars remain
              anchored.
            </p>
          </div>

          <div className="epoch-grid">
            <div>
              <div className="epoch-tabs" role="group" aria-label="Epoch selector">
                {EPOCHS.map((ep, idx) => (
                  <button
                    key={ep.id}
                    type="button"
                    className="epoch-tab"
                    aria-pressed={epochIdx === idx}
                    onClick={() => setEpochIdx(idx)}
                  >
                    <Kicker>{ep.id}</Kicker>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--text-sm)",
                        fontWeight: 500,
                      }}
                    >
                      {ep.date}
                    </span>
                  </button>
                ))}
              </div>
              <dl className="spec-list" aria-live="polite">
                <div className="spec-row">
                  <dt>Active Epoch</dt>
                  <dd>
                    {currentEpoch.id} ({currentEpoch.date})
                  </dd>
                </div>
                <div className="spec-row">
                  <dt>Time Standard</dt>
                  <dd>MJD {currentEpoch.mjd} TDB</dd>
                </div>
                <div className="spec-row">
                  <dt>ICRS Centroid</dt>
                  <dd>
                    {currentEpoch.ra} · {currentEpoch.dec}
                  </dd>
                </div>
              </dl>
            </div>

            <GalleryMat
              caption={`Schematic reticle · ${currentEpoch.id} (${currentEpoch.date})`}
              provenance="ILLUSTRATIVE MOTION PREVIEW"
            >
              <svg
                viewBox="0 0 260 170"
                width="260"
                height="170"
                style={{ inlineSize: "100%", blockSize: "auto", background: "#07090D" }}
                role="img"
                aria-label={`Schematic star field showing centroid position at ${currentEpoch.date}`}
              >
                <circle cx="52" cy="40" r="2" fill="#EDEBE4" opacity="0.65" />
                <circle cx="206" cy="126" r="2.5" fill="#EDEBE4" opacity="0.7" />
                <circle cx="212" cy="44" r="1.8" fill="#EDEBE4" opacity="0.55" />
                <line x1="130" y1="20" x2="130" y2="150" stroke="#252E3B" strokeDasharray="3 3" />
                <line x1="40" y1="85" x2="220" y2="85" stroke="#252E3B" strokeDasharray="3 3" />
                <circle
                  cx={130 + currentEpoch.dx * 7}
                  cy={85 + currentEpoch.dy * 7}
                  r="11"
                  fill="none"
                  stroke="#E69F00"
                  strokeWidth="1.5"
                />
                <circle
                  cx={130 + currentEpoch.dx * 7}
                  cy={85 + currentEpoch.dy * 7}
                  r="3"
                  fill="#E69F00"
                />
              </svg>
            </GalleryMat>
          </div>
        </div>
      </section>

      {/* 4. Signal & Uncertainty */}
      <section className="section">
        <div className="container">
          <div className="chapter-head">
            <div>
              <Kicker>III. Signal &amp; Uncertainty</Kicker>
              <h2 className="chapter-title" style={{ marginTop: "var(--space-2)" }}>
                Never confuse a flickering star for a moving world.
              </h2>
            </div>
            <p className="prose-lead">
              Real infrared surveys contain detector persistence, blended neighbors, and eruptive
              variables. SkyTrace pairs difference imaging with calibrated χ²ν motion fits and
              explicit confidence flags.
            </p>
          </div>

          <div className="col-grid-3">
            {CASES.map((c) => (
              <article key={c.title} className="col-divided">
                <Kicker>{c.rubric}</Kicker>
                <h3 style={{ fontSize: "var(--text-2xl)" }}>{c.title}</h3>
                <p style={{ fontSize: "var(--text-sm)", color: "var(--ink-1)" }}>{c.summary}</p>
                <dl className="spec-list">
                  {c.specs.map(([k, v]) => (
                    <div key={k} className="spec-row">
                      <dt>{k}</dt>
                      <dd>{v}</dd>
                    </div>
                  ))}
                </dl>
              </article>
            ))}
          </div>

          <div style={{ marginTop: "var(--space-8)" }}>
            <Callout title="Epistemic Standard">
              SkyTrace never labels a moving-object candidate or Planet X search result as
              confirmed. Every detection reports astrometric residuals, contamination flags, and
              cross-match provenance so researchers can challenge or override assignments.
            </Callout>
          </div>
        </div>
      </section>

      {/* 5. Dark Threshold: Inside the Observatory */}
      <section className="section" data-theme="dark">
        <div className="container">
          <div className="chapter-head">
            <div>
              <Kicker>IV. Inside the Observatory</Kicker>
              <h2 className="chapter-title" style={{ marginTop: "var(--space-2)" }}>
                Measure the frame. Keep the doubt.
              </h2>
            </div>
            <p className="prose-lead">
              Step from the journal into a float32 WebGL2 sky canvas synced with Aladin Lite,
              102-band spectral energy distributions, A−B difference imaging, and persistent object
              identities (A, B, C…).
            </p>
          </div>

          <div className="observatory-grid">
            <div style={{ display: "grid", gap: "var(--space-4)" }}>
              <GalleryMat
                caption="Observatory workspace preview · WISE 0855 calibration preset"
                provenance="SCHEMATIC VIEWER · 10′ FOV · ASINH STRETCH"
              >
                <div
                  style={{
                    padding: "var(--space-6)",
                    background: "var(--sky-0)",
                    border: "1px solid var(--sky-rule)",
                    display: "grid",
                    gap: "var(--space-4)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      gap: "var(--space-2)",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <Badge>OBJECT A · WISE 0855</Badge>
                    <Badge warn>UNCERTAIN MATCH FLAG EXAMPLE</Badge>
                  </div>
                  <dl className="spec-list">
                    <div className="spec-row">
                      <dt>Proper Motion μ</dt>
                      <dd>8151.6 ± 12.4 mas/yr</dd>
                    </div>
                    <div className="spec-row">
                      <dt>Channels</dt>
                      <dd>SPHEREx Arrays 5–6 (3.2–4.8 µm)</dd>
                    </div>
                    <div className="spec-row">
                      <dt>Shareable State</dt>
                      <dd>/v?target=WISE%200855&amp;mode=blink</dd>
                    </div>
                  </dl>
                </div>
              </GalleryMat>
              <CircleLink to="/v?target=WISE%200855">Open WISE 0855 in Observatory</CircleLink>
            </div>

            <div>
              <Kicker>Mandatory Validation Suite (5 Targets)</Kicker>
              <div className="target-list" style={{ marginTop: "var(--space-3)" }}>
                {MANDATORY_TARGETS.map((t) => (
                  <Link key={t.name} to={t.to} className="target-item">
                    <strong>{t.name}</strong>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--text-xs)",
                        color: "var(--accent)",
                      }}
                    >
                      Open →
                    </span>
                    <span className="target-reveal">{t.note}</span>
                  </Link>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* 6. Continue Exploring */}
      <section
        className="section"
        data-theme="dark"
        style={{ background: "var(--sky-1)", borderBlockStart: "1px solid var(--sky-rule)" }}
      >
        <div
          className="container"
          style={{ display: "grid", gap: "var(--space-6)", justifyItems: "start" }}
        >
          <Kicker>V. Begin Investigation</Kicker>
          <h2 className="chapter-title" style={{ maxWidth: "20ch" }}>
            The sky is already in the archive.
          </h2>
          <p className="prose-lead">
            Open any coordinate field in the Sky Viewer, read the astrometric methodology, or sign
            in to save projects, notes, and manual match overrides.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-4)" }}>
            <Link to="/v" className="btn btn-primary">
              Launch Sky Viewer →
            </Link>
            <Link to="/methodology" className="btn btn-secondary">
              Read Methodology &amp; Limits
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}
