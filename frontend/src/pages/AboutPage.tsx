import { useState } from "react";
import { Link } from "react-router-dom";
import { Callout, EpochPager, NewYorkerHeading } from "../design/components/Primitives";

const PURPOSE_PILLARS = [
  {
    title: "Native-frame precision",
    text: "Every centroid is measured on its unresampled SPHEREx exposure before reprojecting stacks for visual inspection.",
  },
  {
    title: "Uncertainty on display",
    text: "Measurements never appear without astrometric error ellipses, reduced chi-square fits, and contamination notes.",
  },
  {
    title: "Mover vs. variable",
    text: "Our benchmarks pair real brown dwarf movers alongside eruptive variable stars so brightness never masquerades as motion.",
  },
  {
    title: "Candidate humility",
    text: "SkyTrace never declares an unverified candidate confirmed; persistent object IDs preserve researcher annotations and doubt.",
  },
] as const;

const STUDIO_CHAPTERS = [
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
  const current = STUDIO_CHAPTERS[idx] ?? STUDIO_CHAPTERS[0];

  return (
    <>
      <section className="section">
        <div className="container">
          <div className="company-hero">
            <h1 id="main-heading" className="company-h1">
              An observatory built around the patience of time.
            </h1>
            <div style={{ display: "grid", gap: "var(--space-4)", justifyItems: "start" }}>
              <p
                style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: "var(--text-xl)",
                  color: "var(--ink-1)",
                  lineHeight: 1.5,
                }}
              >
                SkyTrace is an open scientific workspace for investigating real SPHEREx infrared
                observations across multiple epochs—helping researchers trace subtle motion and
                evaluate evidence with clarity.
              </p>
              <Link to="/v" className="btn btn-dark">
                Enter the Sky Viewer →
              </Link>
            </div>
          </div>

          <span className="drawer-kicker">Our Purpose</span>
          <div className="purpose-grid">
            {PURPOSE_PILLARS.map((item) => (
              <article key={item.title} className="purpose-col">
                <h3>{item.title}</h3>
                <p>{item.text}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

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
                total={STUDIO_CHAPTERS.length}
                unit="chapter"
                onChange={setIdx}
              />
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <NewYorkerHeading
            title="What We Value"
            subtitle="Principles borrowed equally from careful science and independent publishing."
          />

          <div className="values-layout">
            <div className="values-sticky">
              <h2 style={{ fontSize: "var(--text-2xl)" }}>Hold clarity and doubt together.</h2>
              <p
                style={{
                  marginTop: "var(--space-3)",
                  color: "var(--ink-1)",
                  fontSize: "var(--text-sm)",
                }}
              >
                Every detector carries persistence, blended neighbors, and calibration drift. Our
                design makes uncertainty impossible to crop out.
              </p>
            </div>

            <div className="values-cols">
              <div>
                <h3 style={{ fontSize: "var(--text-lg)", marginBottom: "var(--space-2)" }}>
                  Never fake a confirmation
                </h3>
                <p style={{ fontSize: "var(--text-sm)", color: "var(--ink-1)" }}>
                  Detections appear as hypotheses with confidence intervals and cross-match context,
                  leaving investigators free to accept, flag, or override automated assignments.
                </p>
              </div>
              <div>
                <h3 style={{ fontSize: "var(--text-lg)", marginBottom: "var(--space-2)" }}>
                  Calm tools over dashboard noise
                </h3>
                <p style={{ fontSize: "var(--text-sm)", color: "var(--ink-1)" }}>
                  We keep the landing journal serene and book-like, reserving dark precision
                  controls strictly for the astrometric viewer where pixels demand focus.
                </p>
              </div>
            </div>
          </div>

          <div style={{ marginTop: "var(--space-8)" }}>
            <Callout title="Data & Archival Acknowledgements">
              Built for NASA Space Apps around public datasets from NASA / IPAC IRSA, SPHEREx, Gaia
              DR3, CatWISE2020, and historical public-domain engravings courtesy of Wikimedia
              Commons and NASA Image Library.
            </Callout>
          </div>
        </div>
      </section>
    </>
  );
}
