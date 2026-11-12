import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArchivalCard,
  EpochPager,
  NewYorkerHeading,
  OatCard,
} from "../design/components/Primitives";

const PLATES = [
  {
    tag: "Cartography",
    date: "1660",
    image: "/images/plate-cellarius-1660.jpg",
    alt: "Harmonia Macrocosmica Ptolemaic celestial spheres engraving by Andreas Cellarius",
    provenance: "Archival Illustration · Cellarius",
    title: "Harmonia Macrocosmica",
    description:
      "Hand-colored copperplate cosmographies mapped celestial circles before photographic plates fixed individual stars.",
    to: "/methodology",
  },
  {
    tag: "Star Atlas",
    date: "1801",
    image: "/images/plate-bode-1801.jpg",
    alt: "Johann Bode Uranographia star chart engraving of Cetus and Eridanus",
    provenance: "Archival Illustration · Bode",
    title: "Bode's Uranographia",
    description:
      "Precision copperplate grids plotted thousands of coordinates against myth, turning the night sky into an index.",
    to: "/methodology",
  },
  {
    tag: "Astrometry",
    date: "1919",
    image: "/images/plate-harvard-1891.jpg",
    alt: "1919 Eddington photographic glass plate negative showing eclipse starfield tick marks",
    provenance: "Archival Plate · Royal Society",
    title: "Photographic Glass Plates",
    description:
      "Silver-halide glass negatives froze star positions so astronomers could measure tiny arcsecond shifts months apart.",
    to: "/about",
  },
  {
    tag: "Infrared Survey",
    date: "2025",
    image: "/images/obs-spherex-ir.jpg",
    alt: "NASA WISE infrared false-color starfield observation revealing faint cool objects",
    provenance: "NASA Infrared Observation · WISE",
    title: "All-Sky Infrared Epochs",
    description:
      "Repeating multi-band infrared passes reveal cold brown dwarfs and moving objects invisible to optical plates.",
    to: "/v?target=WISE%200855",
  },
] as const;

const BLINK_EPOCHS = [
  { id: "Epoch 1", date: "Jun 2025", mjd: "60840.2", dx: -18, dy: 8, fluxScale: 0.72 },
  { id: "Epoch 2", date: "Dec 2025", mjd: "61021.7", dx: 0, dy: 0, fluxScale: 1.0 },
  { id: "Epoch 3", date: "Jun 2026", mjd: "61206.1", dx: 18, dy: -8, fluxScale: 0.62 },
] as const;

const CALIBRATION_CARDS = [
  {
    tag: "True Mover",
    date: "8.1″ / yr",
    title: "WISE 0855−0714",
    description:
      "A frigid Y-type brown dwarf 7.4 light-years away whose steady proper motion crosses the background starfield.",
    rows: [
      ["Proper Motion", "~8.1″ / yr"],
      ["Infrared Band", "3.4–4.8 µm"],
      ["Identity Status", "Confirmed Mover"],
    ],
    ctaLabel: "Inspect WISE 0855",
    to: "/v?target=WISE%200855",
  },
  {
    tag: "Benchmark",
    date: "10.3″ / yr",
    title: "Barnard's Star",
    description:
      "The fastest proper-motion star in Earth's sky, serving as our astrometric plate-scale and centroid benchmark.",
    rows: [
      ["Proper Motion", "10.3″ / yr"],
      ["Reference Frame", "ICRS J2016.0"],
      ["Identity Status", "Calibration Anchor"],
    ],
    ctaLabel: "Inspect Barnard's Star",
    to: "/v?target=Barnard%27s%20Star",
  },
  {
    tag: "Variable Control",
    date: "μ ≈ 0",
    title: "RW Aurigae",
    description:
      "An eruptive young star that dims and brightens dramatically without moving—teaching us never to mistake flux for motion.",
    rows: [
      ["Centroid Shift", "0.00″ ± 0.08″"],
      ["Flux Swing", "Δmag > 1.5"],
      ["Identity Status", "Stationary Variable"],
    ],
    ctaLabel: "Inspect RW Aurigae",
    to: "/v?target=RW%20Aurigae",
  },
] as const;

export function Home() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [epochIdx, setEpochIdx] = useState(0);
  const [mode, setMode] = useState<"mover" | "variable">("mover");

  const epoch = BLINK_EPOCHS[epochIdx] ?? BLINK_EPOCHS[0];
  const starX = mode === "mover" ? 160 + epoch.dx : 160;
  const starY = mode === "mover" ? 95 + epoch.dy : 95;
  const starR = mode === "variable" ? 5 + epoch.fluxScale * 5 : 6;

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
      {/* 1. Anthropic Mixed Thesis + Outrider Word-in-Image Hero */}
      <section className="section">
        <div className="container">
          <h1 id="main-heading" className="thesis">
            <span className="thesis-lead">
              Every discovery in the sky begins by comparing tonight against yesterday.
            </span>{" "}
            SkyTrace is an independent observational journal for tracing moving worlds across
            SPHEREx epochs—with uncertainty intact.
          </h1>

          <div className="hero-canvas">
            <img
              src="/images/hero-sky.jpg"
              alt="Deep-sky infrared mosaic of dust and stars captured by NASA Spitzer Space Telescope"
              className="hero-bg-img"
              fetchPriority="high"
            />
            <div className="hero-scrim" aria-hidden="true" />

            <div className="hero-overlay">
              <h2 className="hero-display-title">The Memory of Light</h2>
              <p className="hero-overlay-copy">
                Enter an astronomical target or celestial coordinates to inspect multi-epoch
                infrared plates across time.
              </p>

              <search style={{ inlineSize: "100%", display: "flex", justifyContent: "center" }}>
                <form className="hero-search-pill" onSubmit={handleSearch}>
                  <label htmlFor="hero-coord-input" className="visually-hidden">
                    Target name or ICRS coordinates
                  </label>
                  <input
                    id="hero-coord-input"
                    type="search"
                    placeholder="Try WISE 0855, Barnard's Star, or 133.795, -7.245"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    spellCheck={false}
                    autoCorrect="off"
                    autoCapitalize="none"
                  />
                  <button type="submit" className="btn btn-dark">
                    Inspect sky →
                  </button>
                </form>
              </search>

              <div className="hero-presets">
                <Link to="/v?target=WISE%200855" className="hero-preset-pill">
                  WISE 0855
                </Link>
                <Link to="/v?target=Barnard%27s%20Star" className="hero-preset-pill">
                  Barnard&apos;s Star
                </Link>
                <Link to="/v?target=eps%20Ind%20B" className="hero-preset-pill">
                  ε Indi B
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* 2. New Yorker Display Header + Anthropic 4-Up 5:6 Archival Portrait Grid */}
      <section className="section">
        <div className="container">
          <NewYorkerHeading
            title="Lineage of the Plate"
            subtitle="From hand-engraved copperplate spheres to multi-epoch infrared photometry."
          />
          <div className="archival-grid">
            {PLATES.map((plate) => (
              <ArchivalCard key={plate.title} {...plate} />
            ))}
          </div>
        </div>
      </section>

      {/* 3. Anthropic #F0EEE6 Smooth Interactive Epoch Stage */}
      <section className="section">
        <div className="container">
          <div className="oat-band">
            <div>
              <h2 style={{ fontSize: "var(--text-2xl)" }}>Time as an observational instrument</h2>
              <p style={{ marginTop: "var(--space-3)", color: "var(--ink-1)" }}>
                Step through three six-month epochs. A true mover glides steadily across anchored
                background stars; an eruptive variable shifts in brightness without leaving its
                coordinates.
              </p>

              <div className="mode-pills" role="group" aria-label="Comparison object mode">
                <button
                  type="button"
                  className="nav-pill"
                  aria-pressed={mode === "mover"}
                  onClick={() => setMode("mover")}
                >
                  Moving Dwarf (WISE 0855)
                </button>
                <button
                  type="button"
                  className="nav-pill"
                  aria-pressed={mode === "variable"}
                  onClick={() => setMode("variable")}
                >
                  Variable Star (RW Aurigae)
                </button>
              </div>

              <EpochPager
                index={epochIdx}
                total={BLINK_EPOCHS.length}
                unit="epoch"
                onChange={setEpochIdx}
                meta={`${epoch.date} · MJD ${epoch.mjd}`}
              />
            </div>

            <figure className="blink-plate">
              <svg
                className="blink-svg"
                viewBox="0 0 320 190"
                width="320"
                height="190"
                role="img"
                aria-label={`Interactive epoch plate at ${epoch.date} showing ${mode === "mover" ? "moving brown dwarf" : "stationary variable star"}`}
              >
                <g stroke="#34322e" strokeWidth="1" strokeDasharray="3 4">
                  <line x1="160" y1="15" x2="160" y2="175" />
                  <line x1="30" y1="95" x2="290" y2="95" />
                </g>
                <circle cx="62" cy="48" r="2" fill="#dedcd1" opacity="0.7" />
                <circle cx="248" cy="142" r="2.5" fill="#dedcd1" opacity="0.75" />
                <circle cx="256" cy="52" r="1.8" fill="#dedcd1" opacity="0.6" />
                <circle cx="94" cy="148" r="2" fill="#dedcd1" opacity="0.55" />

                <g
                  className="blink-star"
                  style={{ transform: `translate(${starX - 160}px, ${starY - 95}px)` }}
                >
                  <circle
                    cx="160"
                    cy="95"
                    r="14"
                    fill="none"
                    stroke="#9cc4e4"
                    strokeWidth="1.2"
                    opacity="0.75"
                  />
                  <circle cx="160" cy="95" r={starR} fill="#faf9f5" />
                </g>
              </svg>
              <figcaption
                style={{
                  marginTop: "var(--space-2)",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--text-xs)",
                  color: "var(--ink-2)",
                  display: "flex",
                  justifyContent: "space-between",
                }}
              >
                <span>
                  {epoch.id} ({epoch.date})
                </span>
                <span>
                  {mode === "mover" ? "ICRS displacement observed" : "Fixed coordinate · Δflux"}
                </span>
              </figcaption>
            </figure>
          </div>
        </div>
      </section>

      {/* 4. Anthropic 3-Up Borderless Oat Cards */}
      <section className="section">
        <div className="container">
          <NewYorkerHeading
            title="Calibration Fields"
            subtitle="Open a verified reference object in the interactive viewer or compare epochs yourself."
          />
          <div className="oat-grid">
            {CALIBRATION_CARDS.map((card) => (
              <OatCard key={card.title} {...card} />
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
