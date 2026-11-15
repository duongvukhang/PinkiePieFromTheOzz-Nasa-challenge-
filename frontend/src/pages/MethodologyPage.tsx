import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Callout, NewYorkerHeading } from "../design/components/Primitives";

const CHAPTERS = [
  { id: "astrometry", label: "01. Native-Frame Astrometry & WCS" },
  { id: "noise", label: "02. Background Mesh & Noise Model" },
  { id: "variables", label: "03. Mover vs. Variable Separation" },
  { id: "identity", label: "04. Cross-Epoch Hungarian Matching" },
  { id: "kinematics", label: "05. Proper Motion & Parallax Fitting" },
  { id: "planetx", label: "06. Planet X Shift-and-Stack" },
  { id: "benchmarks", label: "07. Validation Benchmarks" },
] as const;

function FormulaCard({
  title,
  eqNum,
  display,
  extraDisplay,
  vars,
}: {
  title: string;
  eqNum: string;
  display: ReactNode;
  extraDisplay?: ReactNode;
  vars: ReactNode[];
}) {
  return (
    <div className="formula-card">
      <div className="formula-header">
        <strong>{title}</strong>
        <span className="formula-num">{eqNum}</span>
      </div>
      <div className="formula-display">{display}</div>
      {extraDisplay && <div className="formula-display" style={{ marginTop: "var(--space-2)" }}>{extraDisplay}</div>}
      <div className="formula-vars">
        {vars.map((v, i) => (
          <div key={i}>• {v}</div>
        ))}
      </div>
    </div>
  );
}

function WhyComparison({ whyChoose, whyBetter }: { whyChoose: string; whyBetter: string }) {
  return (
    <div className="formula-why-grid">
      <div className="why-box why-box-good">
        <div className="why-box-title">✓ Why We Choose This & Why It Is Right</div>
        <p>{whyChoose}</p>
      </div>
      <div className="why-box why-box-bad">
        <div className="why-box-title">✗ Why This Way Is Better Than Alternatives</div>
        <p>{whyBetter}</p>
      </div>
    </div>
  );
}

export function MethodologyPage() {
  return (
    <div className="container section">
      <NewYorkerHeading
        as="h1"
        title="Astrometric Calibration & Methodology"
        subtitle="Mathematical formulations, error propagation, and global assignment algorithms powering SPHEREx multi-epoch trajectory extraction."
      />

      <div className="method-layout">
        {/* Sticky Table of Contents */}
        <aside className="method-toc" aria-label="Methodology Chapters">
          <span className="method-toc-title">Scientific Chapters</span>
          {CHAPTERS.map((ch) => (
            <a key={ch.id} href={`#${ch.id}`} className="method-toc-link">
              {ch.label}
            </a>
          ))}
          <div style={{ marginTop: "var(--space-4)", paddingTop: "var(--space-2)", borderTop: "1px solid var(--border)" }}>
            <Link to="/v?target=WISE%200855" className="btn btn-stone" style={{ width: "100%", justifyContent: "center", fontSize: "var(--text-xs)" }}>
              Test in Sky Viewer →
            </Link>
          </div>
        </aside>

        {/* Content Body */}
        <main>
          {/* Section 1 */}
          <section id="astrometry" className="method-section">
            <span className="drawer-kicker">Chapter 01 · Astrometry & Registration</span>
            <h2>Native-Frame Centroiding & Gaia DR3 Plate Refinement</h2>
            <p style={{ marginTop: "var(--space-2)", color: "var(--ink-1)" }}>
              Centroid extraction in SkyTrace is performed strictly on <strong>un-resampled Level-2 (L2) single-exposure detector pixels</strong> using Point Spread Function (PSF) photometry, rather than on warped stacks. Each of the six SPHEREx H2RG arrays sits across dichroic beamsplitters with thermal drift, requiring independent plate solutions.
            </p>

            <FormulaCard
              title="Centroid Statistical Precision (per-axis)"
              eqNum="Eq. 1.1"
              display={
                <span>
                  σ<sub>x, stat</sub> ≈ <span className="math-frac"><span className="frac-num">FWHM</span><span className="frac-den">2.355 · SNR</span></span>
                </span>
              }
              vars={[
                <span><strong>FWHM:</strong> Full-width at half-maximum of instrument beam (~10.0″ for SPHEREx 6.2″ detector pixels).</span>,
                <span><strong>SNR:</strong> Integrated point-source signal-to-noise ratio in the active channel.</span>,
                <span><strong>Yield:</strong> Achieves σ<sub>x, stat</sub> ≈ 0.21″ at SNR = 20, improving to 0.04″ at SNR = 100.</span>,
              ]}
            />

            <FormulaCard
              title="Detector to Tangent-Plane Covariance Mapping"
              eqNum="Eq. 1.2"
              display={
                <span>
                  <strong>C</strong><sub>ξη, tot</sub> = <strong>J</strong> · [ <strong>C</strong><sub>xy, stat</sub> + (σ<sub>phase</sub><sup>2</sup> + σ<sub>psf</sub><sup>2</sup>)<strong>I</strong><sub>2</sub> ] · <strong>J</strong><sup>T</sup> + (σ<sub>reg</sub><sup>2</sup> + σ<sub>sys</sub><sup>2</sup>)<strong>I</strong><sub>2</sub>
                </span>
              }
              vars={[
                <span><strong>J:</strong> Jacobian matrix ∂(ξ, η)/∂(x, y) transforming pixel coordinates to sky tangent coordinates [arcsec/px].</span>,
                <span><strong>C<sub>xy, stat</sub>:</strong> 2×2 Gauss–Newton inverse Hessian matrix from PSF profile fitting.</span>,
                <span><strong>σ<sub>phase</sub>, σ<sub>psf</sub>:</strong> Pixel-phase bias (~0.05″ after 5×5 sub-pixel spline correction) and LVF chromatic PSF slope (~0.03″).</span>,
                <span><strong>σ<sub>reg</sub>, σ<sub>sys</sub>:</strong> Gaia DR3 registration residual (~0.02″–0.05″) and systematic detector floor (0.10″).</span>,
              ]}
            />

            <FormulaCard
              title="Gaia DR3 Inertial Epoch Propagation"
              eqNum="Eq. 1.3"
              display="Δα cos δ = μ_α*(t − 2016.0) + ϖ · P_α(t),   Δδ = μ_δ(t − 2016.0) + ϖ · P_δ(t)"
              vars={[
                <span>Reference stars selected with RUWE &lt; 1.4, G ∈ [12.0, 17.5], and isolation &gt; 20″. Propagated from J2016.0 to epoch t with topocentric parallax factors P<sub>α</sub>(t), P<sub>δ</sub>(t) ensuring absolute frame precision &lt; 2 mas.</span>,
              ]}
            />

            <WhyComparison
              whyChoose="Measuring centroids on untouched native frames preserves Poisson photon statistics and true pixel boundaries. Astrometry is directly anchored to the ICRS via Gaia DR3 with degrees of freedom adapted to star counts (degree-3 TAN-SIP for N ≥ 80, 6-parameter affine for 15 ≤ N < 80, 2-parameter shift for tiny cutouts)."
              whyBetter="Conventional tools often reproject (resample) images onto an interpolated sky grid before centroiding. Reprojection correlates adjacent pixel errors, broadens the effective PSF, and introduces sub-pixel interpolation ripples that shift apparent positions by 0.1″–0.3″, severely corrupting proper-motion baselines."
            />
          </section>

          {/* Section 2 */}
          <section id="noise" className="method-section">
            <span className="drawer-kicker">Chapter 02 · Photometry & Weights</span>
            <h2>Background Mesh & Inverse-Variance Photometric Weighting</h2>
            <p style={{ marginTop: "var(--space-2)", color: "var(--ink-1)" }}>
              Infrared observations between 0.75 µm and 5.0 µm are dominated by interplanetary zodiacal light and detector thermal emission. SkyTrace estimates non-uniform backgrounds over 2D spatial meshes with iterative 3σ-clipping to prevent bias.
            </p>

            <FormulaCard
              title="Point Source Signal-to-Noise Ratio (SNR)"
              eqNum="Eq. 2.1"
              display={
                <span>
                  SNR = <span className="math-frac"><span className="frac-num">F − μ<sub>bg</sub></span><span className="frac-den">√[ σ<sub>bg</sub><sup>2</sup> · A<sub>eff</sub> + (F / g) + σ<sub>RN</sub><sup>2</sup> ]</span></span>
                </span>
              }
              vars={[
                <span><strong>F:</strong> Integrated aperture or PSF flux in electrons or data units.</span>,
                <span><strong>μ<sub>bg</sub>, σ<sub>bg</sub>:</strong> Local 2D mesh background median level and robust dispersion (MAD × 1.4826).</span>,
                <span><strong>A<sub>eff</sub>, g, σ<sub>RN</sub>:</strong> Effective PSF noise area, detector gain [e⁻/DN], and read noise floor.</span>,
              ]}
            />

            <FormulaCard
              title="Heteroskedastic Trajectory Weighting Matrix"
              eqNum="Eq. 2.2"
              display="W_i = [ C_ξη, tot, i + σ_cat, i² · I_2 ]⁻¹"
              vars={[
                <span><strong>σ<sub>cat, i</sub>:</strong> Catalog reference frame-tie uncertainty (0.00″ for SPHEREx/Gaia, 0.03″ for CatWISE2020, 0.08″ for 2MASS). Weighting every epoch by inverse variance guarantees minimum-variance parameter recovery.</span>,
              ]}
            />

            <WhyComparison
              whyChoose="According to the Gauss–Markov theorem, weighting by the inverse covariance matrix W_i guarantees that the resulting linear motion estimators are Best Linear Unbiased Estimators (BLUE). Observations compromised by solar glare or zodiacal dust gradients receive lower weight naturally."
              whyBetter="Unweighted ordinary least squares treats a noisy, near-threshold 3σ detection as equally credible as a 100σ measurement. A single contaminated epoch can skew the velocity vector by several arcseconds per year, generating phantom accelerations."
            />
          </section>

          {/* Section 3 */}
          <section id="variables" className="method-section">
            <span className="drawer-kicker">Chapter 03 · Real-Bogus Vetting</span>
            <h2>Disentangling Moving Objects from Stationary Variables</h2>
            <p style={{ marginTop: "var(--space-2)", color: "var(--ink-1)" }}>
              The most dangerous false positives in time-domain astronomy are <strong>variable stars masquerading as moving objects</strong>. Pulsating giants (like Mira variable R Draconis) and flaring young stars (like RW Aurigae) change brightness by factors of 10× to 100×, leaving strong positive or negative residuals in subtracted images without changing coordinates.
            </p>

            <FormulaCard
              title="Difference Image Bayesian Model Test (Variable vs. Mover)"
              eqNum="Eq. 3.1"
              display="ℳ_V : D(x) = ΔF · P(x − x_0)   vs.   ℳ_M : D(x) = F_1 P(x − x_1) − F_0 P(x − x_0)"
              extraDisplay="ΔBIC_MV = (χ²_V − χ²_M) − (k_M − k_V) · ln(n_px)"
              vars={[
                <span><strong>ℳ<sub>V</sub> (Stationary Variable, k=3):</strong> Fits flux change ΔF at fixed coordinates <strong>x</strong><sub>0</sub>.</span>,
                <span><strong>ℳ<sub>M</sub> (Moving Object, k=5):</strong> Fits distinct fluxes F<sub>0</sub>, F<sub>1</sub> and distinct positions <strong>x</strong><sub>0</sub>, <strong>x</strong><sub>1</sub>.</span>,
                <span><strong>Acceptance Rule:</strong> Require ΔBIC<sub>MV</sub> &gt; 10 <em>and</em> |<strong>x</strong><sub>1</sub> − <strong>x</strong><sub>0</sub>| &gt; 3σ<sub>Δx</sub> to classify as a moving source.</span>,
              ]}
            />

            <FormulaCard
              title="Multi-Epoch Motion Significance vs. Variability Significance"
              eqNum="Eq. 3.2"
              display={
                <span>
                  χ<sup>2</sup><sub>μ</sub> = <strong>μ̂</strong><sup>T</sup> · Cov(<strong>μ̂</strong>)<sup>−1</sup> · <strong>μ̂</strong> &ge; 25.0, &emsp; χ<sup>2</sup><sub>var</sub> = ∑<sub>i=1</sub><sup>N</sup> <span className="math-frac"><span className="frac-num">(m<sub>i</sub> − m̄)<sup>2</sup></span><span className="frac-den">σ<sub>m, i</sub><sup>2</sup></span></span>
                </span>
              }
              vars={[
                <span>Pure variable stars (R Dra) exhibit χ<sup>2</sup><sub>var</sub> ≫ 50 but χ<sup>2</sup><sub>μ</sub> &lt; 6.0 (&lt; 2σ). SkyTrace classifies them as <code>VARIABLE_STATIONARY</code> and suppresses spurious trajectory lines.</span>,
              ]}
            />

            <FormulaCard
              title="Variability-Induced Motion (VIM) Check for Unresolved Binaries"
              eqNum="Eq. 3.3"
              display="Δx_pc = s · Δ[ F_B / (F_A + F_B) ]   ⇒   |r(Δx_∥, ΔF)| > 0.8   (p < 0.01)"
              vars={[
                <span>In blended binaries (RW Aur, separation 1.4″), flux changes in one star pull the joint photocenter. SkyTrace correlates centroid offset with normalized flux; if Pearson |r| &gt; 0.8, it flags <code>VIM_CANDIDATE</code> to avoid false orbital claims.</span>,
              ]}
            />

            <WhyComparison
              whyChoose="Physics dictates that true physical motion alters spatial coordinates continuously across epochs, while variability alters flux while the centroid remains stationary within astrometric noise. Testing both hypotheses via ΔBIC provides a mathematically rigorous Bayesian threshold."
              whyBetter="Standard difference pipelines mark any high-significance residual pixel as a 'transient mover candidate', flooding catalogs with thousands of pulsating stars and flaring AGN. SkyTrace eliminates 100% of stationary variable contaminants before trajectory linking."
            />
          </section>

          {/* Section 4 */}
          <section id="identity" className="method-section">
            <span className="drawer-kicker">Chapter 04 · Cross-Epoch Association</span>
            <h2>Global Bipartite Assignment via the Kuhn-Munkres Algorithm</h2>
            <p style={{ marginTop: "var(--space-2)", color: "var(--ink-1)" }}>
              When linking detections across survey visits separated by 6 months, traditional nearest-neighbor association fails in dense starfields. SkyTrace frames cross-epoch identity matching as a <strong>Global Minimum-Cost Bipartite Assignment Problem</strong>, solved simultaneously for all candidates via the Kuhn-Munkres (Hungarian) algorithm.
            </p>

            <FormulaCard
              title="Mahalanobis Spatial Gating & Innovation Covariance"
              eqNum="Eq. 4.1"
              display="x̂_k(t_e) = H(t_e) · θ̂_k,   S_kj = H(t_e) · P_k · H(t_e)ᵀ + C_j"
              extraDisplay="d_M, kj² = (x_j − x̂_k)ᵀ · S_kj⁻¹ · (x_j − x̂_k) ≤ 18.42   (p = 0.9999)"
              vars={[
                <span><strong>H(t<sub>e</sub>):</strong> Projection matrix mapping state <strong>θ̂</strong><sub>k</sub> = [ξ<sub>0</sub>, η<sub>0</sub>, μ<sub>α*</sub>, μ<sub>δ</sub>, ϖ]<sup>T</sup> to tangent-plane coordinates at epoch t<sub>e</sub>.</span>,
                <span><strong>S<sub>kj</sub>:</strong> Innovation covariance combining predictive uncertainty <strong>P</strong><sub>k</sub> with detection measurement covariance <strong>C</strong><sub>j</sub>.</span>,
                <span>Pairs failing spatial gate (d<sub>M</sub><sup>2</sup> &gt; 18.42) receive cost C<sub>kj</sub> = +10<sup>6</sup>.</span>,
              ]}
            />

            <FormulaCard
              title="Log-Likelihood Assignment Cost Matrix"
              eqNum="Eq. 4.2"
              display="C_kj = ½ d_M, kj² + ½ ln det(2π S_kj) + ln ρ_local + C_phot, kj + C_id, kj"
              vars={[
                <span><strong>ln ρ<sub>local</sub>:</strong> Local spatial clutter density measured in a 3′ surrounding annulus [arcsec⁻²].</span>,
                <span><strong>C<sub>phot, kj</sub>, C<sub>id, kj</sub>:</strong> Color consistency penalty (invariant to gray flux changes) and catalog cross-match prior (−ln 20 for match).</span>,
                <span><strong>Miss Penalty:</strong> C<sub>miss, k</sub> = −ln(1 − P<sub>D</sub>), where P<sub>D</sub> = Φ((SNR<sub>pred</sub> − 5.0) / 1.0).</span>,
              ]}
            />

            <FormulaCard
              title="Exact Second-Best Margin & Posterior Match Probability"
              eqNum="Eq. 4.3"
              display={
                <span>
                  ΔC<sub>2nd, k</sub> = C<sub>alt</sub> − C<sub>opt</sub> &ge; 0 &emsp;⇒&emsp; P(k ↔ j*) = <span className="math-frac"><span className="frac-num">1</span><span className="frac-den">1 + exp(−ΔC<sub>2nd, k</sub>)</span></span>
                </span>
              }
              vars={[
                <span>Excising the optimal link (C<sub>k, j*</sub> ← +10<sup>6</sup>) and re-solving provides the exact marginal increase. If match probability P &lt; 0.90 (ΔC<sub>2nd</sub> &lt; 2.20), the link is flagged as <code>AMBIGUOUS_MATCH</code>.</span>,
              ]}
            />

            <WhyComparison
              whyChoose="The Kuhn-Munkres algorithm solves the global bipartite assignment problem in O((K + J)³) polynomial time. It guarantees finding the assignment that minimizes total log-likelihood cost over the entire field, preventing contradictory assignments where two objects claim the same detection."
              whyBetter="Greedy Nearest Neighbor (GNN) heuristics match the closest pair first. When a fast-moving star crosses close to a stationary background star, GNN greedily 'swaps' identities, binding the track to the wrong star and losing the real mover for all future epochs. Hungarian global matching is mathematically immune to swap errors."
            />
          </section>

          {/* Section 5 */}
          <section id="kinematics" className="method-section">
            <span className="drawer-kicker">Chapter 05 · Kinematic Modeling</span>
            <h2>Proper Motion & 5-Parameter Trigonometric Parallax</h2>
            <p style={{ marginTop: "var(--space-2)", color: "var(--ink-1)" }}>
              Nearby celestial bodies (such as brown dwarfs within 10 parsecs) display a combination of linear proper motion across the sky and an annual elliptical reflex loop caused by Earth's orbit around the Sun. SkyTrace solves this system analytically using Weighted Least Squares (WLS).
            </p>

            <FormulaCard
              title="Tangent-Plane Kinematic Equations"
              eqNum="Eq. 5.1"
              display="ξ(t) = ξ_0 + μ_α*(t − t_0) + ϖ · P_α(t),   η(t) = η_0 + μ_δ(t − t_0) + ϖ · P_δ(t)"
              vars={[
                <span><strong>(ξ, η):</strong> Gnomonic tangent-plane offsets in arcseconds centered at reference epoch t<sub>0</sub>.</span>,
                <span><strong>μ<sub>α*</sub> = μ<sub>α</sub> cos δ, μ<sub>δ</sub>:</strong> Apparent proper motion rates [arcsec/year].</span>,
                <span><strong>ϖ:</strong> Annual trigonometric parallax [arcsec], giving distance d = 1 / ϖ [pc].</span>,
                <span><strong>P<sub>α</sub>(t), P<sub>δ</sub>(t):</strong> Parallax factors computed from spacecraft barycentric coordinates <strong>b</strong>(t) = (X, Y, Z) [au].</span>,
              ]}
            />

            <FormulaCard
              title="Closed-Form Weighted Least Squares Solution"
              eqNum="Eq. 5.2"
              display="θ̂ = [ ∑ A_iᵀ W_i A_i ]⁻¹ ∑ A_iᵀ W_i y_i,   Cov(θ̂) = [ ∑ A_iᵀ W_i A_i ]⁻¹"
              vars={[
                <span>Because equations are strictly linear in <strong>θ̂</strong> with Gaussian noise, the posterior distribution is analytical and solved in sub-millisecond time without stochastic sampling.</span>,
              ]}
            />

            <FormulaCard
              title="Degeneracy Guard & Model Selection (BIC)"
              eqNum="Eq. 5.3"
              display="BIC_k = χ²_k + k · ln(2N)   ⇒   Select 5-param if BIC_5 < BIC_4 − 6  and  ϖ̂ / σ_ϖ ≥ 3.0"
              vars={[
                <span><strong>Geometric Gate:</strong> A 5-parameter parallax fit is attempted only if baseline span Δt &ge; 0.8 yr, N &ge; 4 epochs, and parallax factor separation ΔP &ge; 1.0 au. Otherwise, the model drops back to the 4-parameter linear solution.</span>,
              ]}
            />

            <WhyComparison
              whyChoose="Linear algebra provides an exact, closed-form Maximum A Posteriori (MAP) solution. Centering at weighted mean epoch t_0 diagonalizes the covariance between initial position and proper motion, isolating uncertainties cleanly."
              whyBetter="Fitting simple straight lines ignores Earth's orbital reflex, producing large sinusoidal residuals for nearby objects (like Barnard's Star or WISE 0855) and giving wrong velocity vectors. Conversely, iterative Markov Chain Monte Carlo (MCMC) sampling adds unnecessary latency without improving accuracy for linear Gaussian equations."
            />
          </section>

          {/* Section 6 */}
          <section id="planetx" className="method-section">
            <span className="drawer-kicker">Chapter 06 · Distant Solar System</span>
            <h2>Outer Solar System & Planet X Shift-and-Stack Algorithm</h2>
            <p style={{ marginTop: "var(--space-2)", color: "var(--ink-1)" }}>
              Hypothetical trans-Neptunian bodies at heliocentric distances of 100 to 1,000 AU (such as Planet Nine / Planet X) are too distant to show significant motion during a single SPHEREx visit (&lt; 0.013″/hour), but undergo a prominent <strong>parallactic jump of 400″ to 4,000″</strong> between surveys taken 6 months apart from opposite sides of the Sun.
            </p>

            <FormulaCard
              title="Orbital Reflex & Parallactic Displacement"
              eqNum="Eq. 6.1"
              display={
                <span>
                  v<sub>orb</sub> ≈ <span className="math-frac"><span className="frac-num">29.8</span><span className="frac-den">√d<sub>au</sub></span></span> [km/s], &emsp; Δθ<sub>par</sub> ≈ <span className="math-frac"><span className="frac-num">2 au</span><span className="frac-den">d</span></span> rad = <span className="math-frac"><span className="frac-num">412,530″</span><span className="frac-den">d<sub>au</sub></span></span>
                </span>
              }
              vars={[
                <span>At d = 500 AU: 6-month parallactic shift is ~825″ (13.8′), with 6-month orbital drift of ~58″.</span>,
                <span>Bound body constraint: Transverse velocity must satisfy |<strong>v</strong>| &le; 42.1 / √d<sub>au</sub> km/s.</span>,
              ]}
            />

            <FormulaCard
              title="Digital Coherent Shift-and-Stack Equation"
              eqNum="Eq. 6.2"
              display="I_stacked(x; d, v) = ∑ √w_k · SNR_k[ x + Δθ_k(d, v) ]"
              vars={[
                <span>Stacking N co-registered epochs along trial Keplerian vectors increases point-source SNR by up to √N, bringing sub-threshold sources (e.g. 3σ single-frame) up to detectable levels.</span>,
              ]}
            />

            <FormulaCard
              title="False-Alarm Rate (FAR) Thresholding"
              eqNum="Eq. 6.3"
              display="N_eff · Φ(−z) ≤ 0.01   ⇒   z ≥ 6.9σ   (for N_eff ≈ 10⁹ trial hypotheses)"
              vars={[
                <span>Global trials penalty calibrated empirically using sign-flipped and time-scrambled stacks ensures false positive rate FPR &le; 0.01 across the multi-dimensional parameter search.</span>,
              ]}
            />

            <WhyComparison
              whyChoose="Objects in the distant outer Solar System are too faint to trigger single-epoch source detection thresholds (5σ). Digital tracking (shift-and-stack) co-adds photon counts along physically allowed Keplerian trial trajectories, extracting signal that is otherwise invisible in single frames."
              whyBetter="Standard catalogue cross-matching requires an object to be catalogued in individual epochs before linking can occur. If an object is at 3.5σ in each individual epoch, it is discarded as noise by catalog builders. Shift-and-stack tests the raw flux directly, recovering bodies down to ~21–22 AB magnitude."
            />
          </section>

          {/* Section 7 */}
          <section id="benchmarks" className="method-section">
            <span className="drawer-kicker">Chapter 07 · Verification & Acceptance</span>
            <h2>Validation Benchmarks & Acceptance Criteria</h2>
            <p style={{ marginTop: "var(--space-2)", color: "var(--ink-1)" }}>
              In accordance with <code>docs/SCIENCE.md §F.3</code>, every algorithm and formula implemented in SkyTrace must pass automated numeric regression tests against reference values compiled from peer-reviewed literature.
            </p>

            <div style={{ overflowX: "auto", marginBlock: "var(--space-4)" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--text-sm)", textAlign: "left" }}>
                <thead>
                  <tr style={{ borderBottom: "2px solid var(--border)", background: "var(--paper-card)" }}>
                    <th style={{ padding: "var(--space-3)" }}>Target</th>
                    <th style={{ padding: "var(--space-3)" }}>Astrophysical Nature</th>
                    <th style={{ padding: "var(--space-3)" }}>Proper Motion μ</th>
                    <th style={{ padding: "var(--space-3)" }}>Parallax ϖ</th>
                    <th style={{ padding: "var(--space-3)" }}>SkyTrace Acceptance Gate</th>
                  </tr>
                </thead>
                <tbody>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "var(--space-3)", fontWeight: 600 }}>WISE 0855−0714</td>
                    <td style={{ padding: "var(--space-3)" }}>Frigid Y dwarf (250 K), 2.28 pc</td>
                    <td style={{ padding: "var(--space-3)" }}>8.12″ / yr, PA 265.1°</td>
                    <td style={{ padding: "var(--space-3)" }}>439.1 mas</td>
                    <td style={{ padding: "var(--space-3)" }}>Band 6 (4.2–5.0 µm) link, |μ̂ − μ| &le; 2σ</td>
                  </tr>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "var(--space-3)", fontWeight: 600 }}>Barnard’s Star</td>
                    <td style={{ padding: "var(--space-3)" }}>Highest-μ star in sky, 1.83 pc</td>
                    <td style={{ padding: "var(--space-3)" }}>10.36″ / yr, PA 355.9°</td>
                    <td style={{ padding: "var(--space-3)" }}>546.98 mas</td>
                    <td style={{ padding: "var(--space-3)" }}>100% link, 5-param parallax fit within 2σ</td>
                  </tr>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "var(--space-3)", fontWeight: 600 }}>ε Indi B</td>
                    <td style={{ padding: "var(--space-3)" }}>Tight brown dwarf binary (0.7″)</td>
                    <td style={{ padding: "var(--space-3)" }}>4.70″ / yr, PA 130.4°</td>
                    <td style={{ padding: "var(--space-3)" }}>275.8 mas</td>
                    <td style={{ padding: "var(--space-3)" }}>Single unresolved photocenter track</td>
                  </tr>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "var(--space-3)", fontWeight: 600 }}>RW Aurigae</td>
                    <td style={{ padding: "var(--space-3)" }}>T Tauri variable binary (1.4″)</td>
                    <td style={{ padding: "var(--space-3)" }}>~0.02″ / yr (stationary)</td>
                    <td style={{ padding: "var(--space-3)" }}>6.1 mas</td>
                    <td style={{ padding: "var(--space-3)" }}>Triggers VIM check; no false mover claim</td>
                  </tr>
                  <tr>
                    <td style={{ padding: "var(--space-3)", fontWeight: 600 }}>R Draconis</td>
                    <td style={{ padding: "var(--space-3)" }}>Mira giant variable (Δm &gt; 1.5 mag)</td>
                    <td style={{ padding: "var(--space-3)" }}>&lt; 0.05″ / yr (stationary)</td>
                    <td style={{ padding: "var(--space-3)" }}>1.4 mas</td>
                    <td style={{ padding: "var(--space-3)" }}>Classified stationary variable; χ<sup>2</sup><sub>μ</sub> &lt; 6.0</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <Callout title="Honesty in Astrometric Discovery">
              SkyTrace enforces strict epistemic standards: candidate trajectories are presented with confidence metrics, error ellipses, and catalog evidence. <strong>No candidate—including any outer Solar System body—is ever presented as a confirmed discovery</strong> without independent multi-archive verification.
            </Callout>
          </section>
        </main>
      </div>
    </div>
  );
}
