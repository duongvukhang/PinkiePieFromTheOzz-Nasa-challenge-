import { useState } from "react";
import { Link } from "react-router-dom";
import { Callout, EpochPager, NewYorkerHeading } from "../design/components/Primitives";

const KEY_IDEAS = [
  {
    num: "01",
    title: "Native-Frame Astrometric Integrity",
    subtitle: "Untouched detector pixels before reprojection",
    body: "Every centroid and flux measurement is calculated on un-resampled Level-2 detector pixels. We avoid bicubic and bilinear interpolation that smears point-spread functions, distorts sub-pixel peaks, and ruins statistical error covariance.",
  },
  {
    num: "02",
    title: "Deterministic Identity Tracking",
    subtitle: "Kuhn-Munkres global bipartite assignment",
    body: "Candidate objects (A, B, C...) maintain persistent identity across epochs through Hungarian optimization. Unlike greedy nearest-neighbor matching, global assignment is mathematically immune to identity swap errors in crowded starfields.",
  },
  {
    num: "03",
    title: "Rigorous Mover vs. Variable Separation",
    subtitle: "Bayesian model selection against flaring stars",
    body: "Time-domain surveys are filled with pulsating variable stars (Mira, AGN, T Tauri) that change brightness by orders of magnitude without moving. SkyTrace applies ΔBIC model testing to ensure flux variation is never mistaken for physical motion.",
  },
  {
    num: "04",
    title: "Radical Epistemic Humility",
    subtitle: "Uncertainties, error ellipses, and verification",
    body: "Every detection carries its full 2×2 astrometric covariance, reduced chi-square, and false-positive flags. We hold clarity and doubt together: no candidate—including outer Solar System bodies—is ever presented as confirmed without independent proof.",
  },
  {
    num: "05",
    title: "URL-First Reproducible Science",
    subtitle: "Shareable astronomical states",
    body: "Every target coordinate, zoom factor, active color stretch, blink sequence, and candidate bounding box is encoded in browser URL parameters. Any researcher on Earth can inspect, reproduce, and dispute an observation with a single click.",
  },
] as const;

const USAGE_STEPS = [
  {
    step: "01",
    kicker: "Target Entry",
    title: "Enter Sky Coordinates or Pick a Benchmark",
    description:
      "Enter coordinates in sexagesimal (e.g. 08h55m10.83s, -07d14m42.5s) or decimal degrees (133.795, -7.245). Alternatively, launch directly from five pre-calibrated benchmark targets including WISE 0855 and Barnard's Star.",
    linkText: "Try with WISE 0855 →",
    linkTo: "/v?target=WISE%200855",
  },
  {
    step: "02",
    kicker: "Blink Comparator",
    title: "Inspect Multi-Epoch Infrared Exposures",
    description:
      "Use the bottom timeline frame-strip to navigate between survey epochs. Press Play to blink exposures at 0.5 to 10 frames per second, mimicking classic glass-plate blink comparators to immediately spot shifting infrared sources.",
    linkText: "Open Sky Viewer →",
    linkTo: "/v",
  },
  {
    step: "03",
    kicker: "Interactive Centroiding",
    title: "Click-Drag Box on Any Moving Candidate",
    description:
      "Draw a bounding box across an intriguing object. SkyTrace automatically performs Point Spread Function (PSF) fitting and local background subtraction on native pixels, assigning a persistent object ID (e.g., Object A).",
    linkText: "Read Centroiding Math →",
    linkTo: "/methodology#astrometry",
  },
  {
    step: "04",
    kicker: "Kinematic Solution",
    title: "Review Proper Motion & Parallax Trajectory",
    description:
      "The Inspector panel calculates the proper motion vector (μ in arcsec/year, position angle PA) and fits annual trigonometric parallax (ϖ in mas) using Weighted Least Squares, deriving physical distance with confidence bounds.",
    linkText: "Explore Kinematic Formulas →",
    linkTo: "/methodology#kinematics",
  },
  {
    step: "05",
    kicker: "Catalog Cross-Match",
    title: "Verify Against Gaia, CatWISE & JPL Horizons",
    description:
      "Cross-check your candidate against Gaia DR3 (astrometric stars), CatWISE2020 (mid-infrared catalog), SIMBAD, and the JPL Horizons Solar System ephemeris to verify if the object is an asteroid, known star, or uncataloged mover.",
    linkText: "Check Validation Criteria →",
    linkTo: "/methodology#benchmarks",
  },
  {
    step: "06",
    kicker: "Differencing & Workspaces",
    title: "Compare Epochs & Save Project Findings",
    description:
      "Switch to Compare Mode to subtract two epochs (A − B), revealing transient dipoles while canceling fixed stars. Save candidate tracks, astrometric notes, and FITS cutouts into private or collaborative project workspaces.",
    linkText: "View Your Workspaces →",
    linkTo: "/projects",
  },
] as const;

const HISTORIC_CHAPTERS = [
  {
    label: "1897 · Refractors & Glass",
    title: "Stewardship of long baselines",
    body: "Modern infrared astrometry continues work begun in 19th-century observatory domes: keeping careful coordinates across decades so subtle celestial drift becomes legible.",
    image: "/images/about-observatory.jpg",
    alt: "Historic monochrome photograph of the Yerkes 40-inch refractor inside its ribbed dome",
  },
  {
    label: "1919 · Measurement Plates",
    title: "Transparent archival provenance",
    body: "We maintain a strict line between historical plates and live survey products. Every figure on SkyTrace states its telescope, epoch, and photometric band.",
    image: "/images/plate-harvard-1891.jpg",
    alt: "Archival 1919 glass photographic negative with reference measurement ticks",
  },
  {
    label: "2025 · All-Sky Infrared",
    title: "Open tools for independent inquiry",
    body: "Built around open scientific libraries—Astropy, photutils, SEP, and Aladin Lite—so any investigator can inspect, contest, and share a patch of sky by URL.",
    image: "/images/obs-spherex-ir.jpg",
    alt: "NASA WISE infrared deep-sky image showing glowing dust and faint red stars",
  },
] as const;

export function AboutPage() {
  const [idx, setIdx] = useState(0);
  const current = HISTORIC_CHAPTERS[idx] ?? HISTORIC_CHAPTERS[0];

  return (
    <>
      {/* Hero Header */}
      <section className="section">
        <div className="container">
          <div className="company-hero">
            <h1 id="main-heading" className="company-h1">
              Observing the universe in motion.
            </h1>
            <div style={{ display: "grid", gap: "var(--space-4)", justifyItems: "start" }}>
              <p
                style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: "var(--text-xl)",
                  color: "var(--ink-1)",
                  lineHeight: 1.55,
                }}
              >
                SkyTrace is an open-access scientific web platform and observational workspace
                engineered for discovering, verifying, and tracking moving celestial objects across
                multi-epoch SPHEREx infrared observations, CatWISE2020, and Gaia DR3.
              </p>
              <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap" }}>
                <Link to="/v" className="btn btn-dark">
                  Launch the Sky Viewer →
                </Link>
                <Link to="/methodology" className="btn btn-stone">
                  Inspect Methodology & Formulas →
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Project Description & The Challenge */}
      <section className="section" style={{ paddingTop: 0 }}>
        <div className="container">
          <div className="values-layout" style={{ borderTop: "none", paddingTop: 0 }}>
            <div>
              <span className="drawer-kicker">Project Overview</span>
              <h2 style={{ fontSize: "var(--text-3xl)", marginBottom: "var(--space-3)" }}>
                Why SkyTrace Exists
              </h2>
              <p
                style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: "var(--text-lg)",
                  color: "var(--ink-1)",
                  lineHeight: 1.6,
                }}
              >
                To the naked eye, the night sky appears immutable. But over months and years, the
                infrared heavens are filled with motion: nearby rogue brown dwarfs cooling in the
                solar neighborhood, fast-moving high-proper-motion stars, Near-Earth Asteroids,
                Kuiper Belt objects, and potentially undiscovered worlds in the outer Solar System.
              </p>
            </div>
            <div style={{ display: "grid", gap: "var(--space-4)", color: "var(--ink-1)", fontSize: "var(--text-base)" }}>
              <p>
                NASA’s <strong>SPHEREx</strong> (Spectro-Photometer for the History of the
                Universe, Epoch of Reionization and Ices Explorer) surveys the entire sky every six
                months across 102 spectroscopic infrared bands (0.75 µm to 5.0 µm). Comparing these
                recurrent survey passes enables unprecedented time-domain astrometry.
              </p>
              <p>
                However, time-domain infrared searches face profound scientific challenges:
                coarse detector pixels (6.2″), complex optical distortions across six independent
                focal-plane arrays, intense zodiacal foreground light, and millions of flaring
                variable stars that produce false-positive motion signals.
              </p>
              <p>
                <strong>SkyTrace</strong> was built to solve these problems. It replaces ad-hoc
                guessing with rigorous mathematical formulations: native-frame PSF centroiding,
                heteroskedastic inverse-variance weighting, Bayesian mover-versus-variable testing,
                and Kuhn-Munkres global bipartite assignment.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Key Ideas */}
      <section className="section" style={{ background: "var(--paper-1)" }}>
        <div className="container">
          <NewYorkerHeading
            title="Key Ideas & Scientific Principles"
            subtitle="The core technical tenets that distinguish SkyTrace from conventional astronomical viewers."
          />

          <div style={{ display: "grid", gap: "var(--space-6)", marginTop: "var(--space-8)" }}>
            {KEY_IDEAS.map((item) => (
              <article
                key={item.num}
                style={{
                  background: "var(--paper-0)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-card)",
                  padding: "var(--space-6)",
                  display: "grid",
                  gap: "var(--space-2)",
                }}
              >
                <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-3)" }}>
                  <span
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: "var(--text-lg)",
                      fontWeight: 700,
                      color: "var(--accent-clay)",
                    }}
                  >
                    {item.num}
                  </span>
                  <h3 style={{ fontSize: "var(--text-xl)" }}>{item.title}</h3>
                </div>
                <div
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "var(--text-xs)",
                    color: "var(--ink-2)",
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                  }}
                >
                  {item.subtitle}
                </div>
                <p style={{ color: "var(--ink-1)", fontSize: "var(--text-sm)", lineHeight: 1.6 }}>
                  {item.body}
                </p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* How To Use It */}
      <section className="section">
        <div className="container">
          <NewYorkerHeading
            title="How to Use SkyTrace"
            subtitle="A step-by-step researcher walkthrough: from celestial coordinate entry to trajectory confirmation."
          />

          <div className="step-grid">
            {USAGE_STEPS.map((s) => (
              <article key={s.step} className="step-card">
                <div>
                  <span className="step-num">{s.step}</span>
                  <span className="drawer-kicker" style={{ marginTop: "var(--space-2)" }}>
                    {s.kicker}
                  </span>
                  <h3 style={{ fontSize: "var(--text-lg)", marginBottom: "var(--space-2)" }}>
                    {s.title}
                  </h3>
                  <p style={{ fontSize: "var(--text-sm)", color: "var(--ink-1)", lineHeight: 1.55 }}>
                    {s.description}
                  </p>
                </div>
                <div style={{ marginTop: "var(--space-4)" }}>
                  <Link
                    to={s.linkTo}
                    className="nav-pill"
                    style={{ display: "inline-flex", fontSize: "var(--text-xs)" }}
                  >
                    {s.linkText}
                  </Link>
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* Historical Lineage Carousel */}
      <section className="section" style={{ paddingTop: 0 }}>
        <div className="container">
          <div className="oat-band oat-band-3">
            <div>
              <h2 style={{ fontSize: "var(--text-2xl)" }}>From Plate Vault to Infrared Canvas</h2>
              <p
                style={{
                  marginTop: "var(--space-3)",
                  fontSize: "var(--text-sm)",
                  color: "var(--ink-1)",
                }}
              >
                Three chapters guiding how we design instruments for astronomical scrutiny.
              </p>
            </div>

            <img
              src={current.image}
              alt={current.alt}
              style={{
                inlineSize: "100%",
                aspectRatio: "1 / 1",
                objectFit: "cover",
                borderRadius: "var(--radius-image)",
              }}
            />

            <div>
              <span className="drawer-kicker">{current.label}</span>
              <h3 style={{ fontSize: "var(--text-xl)", marginBottom: "var(--space-2)" }}>
                {current.title}
              </h3>
              <p style={{ fontSize: "var(--text-sm)", color: "var(--ink-1)" }}>{current.body}</p>
              <EpochPager
                index={idx}
                total={HISTORIC_CHAPTERS.length}
                unit="chapter"
                onChange={setIdx}
              />
            </div>
          </div>
        </div>
      </section>

      {/* Acknowledgements & Caveats */}
      <section className="section" style={{ paddingTop: 0 }}>
        <div className="container">
          <Callout title="Open Science & Provenance Acknowledgements">
            SkyTrace is developed around open scientific Python and astronomical infrastructure:
            Astropy, photutils, SEP, reproject, and Aladin Lite v3. Observational data is served from
            NASA / IPAC Infrared Science Archive (IRSA), SPHEREx, Gaia DR3 (ESA), CatWISE2020, and
            the NASA Image Library. All candidate motions are presented as hypotheses with
            associated astrometric error ellipses; follow-up confirmation is required before
            announcing new discoveries.
          </Callout>
        </div>
      </section>
    </>
  );
}
