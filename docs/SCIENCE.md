# SCIENCE: Spec Section 6.4 (Scientific methodology)

> Audience: scientists (to audit) and developers (to implement). Units in brackets. Method maturity tags: **[E]** established/published method, **[A]** our adaptation of an established method, **[S]** speculative/experimental (must be validated before user-facing claims).
> **[verify]** = must be checked against the official SPHEREx Explanatory Supplement / IRSA SPHEREx documentation before implementation. Reference values for test targets must be loaded from `tests/science/reference_values.json` (pinned, with citations), never typed from memory.

---

## 0. SPHEREx facts that constrain everything

| Quantity | Value (nominal) | Consequence |
|---|---|---|
| Launch / survey start | Mar 2025 / May 2025 | By Nov 2026 ≈ 3 all-sky passes |
| Cadence | full sky every ~6 months | 2 epochs/yr for most sky; more near ecliptic poles (deep fields near NEP/SEP) [verify] |
| Aperture | 20 cm | diffraction 1.22λ/D ≈ 1.0″ (0.75 µm) – 6.3″ (5 µm) |
| Detectors | 6 × H2RG 2048², two focal planes (3 short-λ, 3 long-λ arrays) via dichroic | per-array calibration offsets |
| Pixel scale | ≈ 6.15–6.2″ | PSF undersampled ⇒ pixel-phase astrometric bias; confusion in crowded fields |
| FOV | ≈ 3.5° × 11.3° (each array 3.5° × 3.5°) [verify] | full-frame needed for fast SSOs |
| Spectral | 0.75–5.0 µm, linear variable filters (LVF); 17 channels/array, 102 total; R ≈ 39/41/41/35 (arrays 1–4), ≈ 110–130 (arrays 5–6) | **Wavelength varies across each image**; a source's 102-point spectrum is assembled from many exposures taken at different times |
| Exposure | O(100 s) per frame [verify EXPTIME] | main-belt asteroids not streaked; fast NEOs streaked |
| PSF FWHM (effective) | ≈ 1.5–2 px (≈ 9–12″) assumed [verify via PSF extension] | centroid precision formula below |
| Level-2 product | MEF: IMAGE (MJy/sr), FLAGS, VARIANCE, ZODI, PSF, WCS-WAVE (λ per pixel) [verify names/units] | we use VARIANCE for weights, FLAGS for masks, ZODI for background, PSF for fitting, WCS-WAVE for per-source λ |
| Point-source depth | per channel per survey ~AB 17–18.5 (5σ), deeper with co-adds [verify against official sensitivity curves] | WISE 0855 is marginal per channel; needs array-5/6 co-adds |

**Biggest honest limits**: (1) 6.2″ pixels ⇒ per-epoch astrometric floor ≈ 0.1–0.3″ for well-measured sources; no binaries < ~10″ resolved (ε Indi Ba/Bb, 0.7″, are one photocentre). (2) Only ~2 epochs/yr ⇒ SPHEREx-only proper motions are good to ~0.1–1″/yr level early in the mission; long-baseline anchors (2MASS 1998–2001, WISE 2010–2024, Gaia 2016) are essential. (3) Spectra of moving or variable sources are temporally smeared.

---

## A. Image and data handling

### A.1 FITS structure & validation [E]
- Open with `astropy.io.fits.open(path, memmap=True, lazy_load_hdus=True)`. Record every HDU: name, type, shape, BUNIT.
- Required for astrometry: celestial WCS (CTYPE `RA---TAN(-SIP)`/`DEC--TAN(-SIP)`), time (`DATE-OBS`/`MJD-OBS`/`DATE-BEG`/`DATE-END`, `TIMESYS`), exposure (`EXPTIME`/`XPOSURE`). Missing time ⇒ user must supply; measurement flagged `USER_EPOCH`.
- **Compatibility report** for a set of uploaded files (all must be satisfied for "fully compatible"):
  - overlap fraction $f_{ov} = \mathrm{Area}(\cap_i F_i)/\mathrm{Area}(F_1)$ via polygon intersection of WCS footprints (`wcs.calc_footprint()` → spherical polygons). ≥ 0.8 OK, 0.2–0.8 partial (analysis restricted), < 0.2 incompatible.
  - pixel scale ratio $s_i/s_1$ from `proj_plane_pixel_scales`; rotation from CD matrix: $\rho = \mathrm{atan2}(CD_{2,1}, CD_{1,1})$; if $|s_i/s_1-1|>1\%$ or $|\Delta\rho|>0.1°$ ⇒ "reprojection required" with stated consequence (correlated noise, §A.4; ~5–15% S/N loss in apertures, astrometry done on native frames instead).
  - wavelength/band match (same channel/array or overlapping λ); different bands ⇒ colour-dependent flux changes are expected (do not flag as variability).
  - epoch separation $\Delta t$; same-epoch ⇒ no motion info.

### A.2 WCS: pixel ↔ sky [E]
With $u=p_1-\mathrm{CRPIX1}$, $v=p_2-\mathrm{CRPIX2}$ (1-based FITS pixels):
$$u' = u + \sum_{p+q\le A\_ORDER} A_{pq}u^pv^q,\qquad v' = v + \sum_{p+q\le B\_ORDER} B_{pq}u^pv^q$$
$$\begin{pmatrix}x\\y\end{pmatrix} = \mathbf{CD}\begin{pmatrix}u'\\v'\end{pmatrix}\ [\mathrm{deg}]$$
Gnomonic (TAN) deprojection: $R_\theta=\sqrt{x^2+y^2}$, $\phi=\mathrm{atan2}(x,-y)$, $\theta=\arctan(180°/(\pi R_\theta))$, then spherical rotation with $(\alpha_0,\delta_0)=$(CRVAL1, CRVAL2), $\phi_p=180°$:
$$\sin\delta=\sin\theta\sin\delta_0+\cos\theta\cos\delta_0\cos(\phi-\phi_p)$$
$$\alpha=\alpha_0+\mathrm{atan2}\!\big(-\cos\theta\sin(\phi-\phi_p),\ \sin\theta\cos\delta_0-\cos\theta\sin\delta_0\cos(\phi-\phi_p)\big)$$
Implementation: `astropy.wcs.WCS.all_pix2world/all_world2pix` (handles SIP); never hand-roll. Astrometry is done in **tangent-plane (standard) coordinates** $(\xi,\eta)$ about a per-object reference point to avoid $\cos\delta$ singularities:
$$\xi=\frac{\cos\delta\sin(\alpha-\alpha_r)}{D},\ \eta=\frac{\sin\delta\cos\delta_r-\cos\delta\sin\delta_r\cos(\alpha-\alpha_r)}{D},\ D=\sin\delta\sin\delta_r+\cos\delta\cos\delta_r\cos(\alpha-\alpha_r).$$

### A.3 Frames, time, observer position [E]
- Frame: ICRS (Gaia-CRF3). Reference catalog epochs: Gaia DR3 = J2016.0 (TCB-based; difference negligible here), CatWISE2020 ≈ MJD 57170, 2MASS per-source JD.
- Time: UTC → TAI (+37 s) → TT (+32.184 s) → TDB (periodic, ≤ 1.7 ms). Use `astropy.time.Time(date_obs, scale="utc").tdb`. Exposure mid-time $t_{mid}=t_{beg}+\tfrac12 T_{exp}$; for co-adds use flux-weighted mean epoch $\bar t=\sum w_it_i/\sum w_i$ and keep per-frame epochs available.
- Observer barycentric position $\mathbf b(t)=\mathbf b_\oplus(t)+\mathbf r_{sc}(t)$ [au, ICRS]. $\mathbf b_\oplus$ from `astropy.coordinates.get_body_barycentric("earth", t, ephemeris="de440")`; $\mathbf r_{sc}$ (≈ 7000 km, LEO ~650 km altitude, polar sun-synchronous) from FITS header keys if present [verify] or JPL Horizons for the SPHEREx spacecraft [verify Horizons ID].
  - Spacecraft offset matters for solar-system objects: parallax $\approx r_{sc}/\Delta = 7000\,\mathrm{km}/(1.496\times10^8\,\mathrm{km}) = 4.7\times10^{-5}\,\mathrm{rad}\approx 9.6''$ at $\Delta=1$ au (≫ astrometric floor). For stars it is negligible ($\sim10^{-5}$ mas at 1 pc).

### A.4 Reprojection & correlated noise [E/A]
- Common grid only for **display, differencing, stacking**; **astrometry and photometry are measured on native frames** (no resampling noise).
- Flux-conserving: `reproject.reproject_exact` (spherical-polygon overlap, flux-conserving for surface brightness) or `reproject_adaptive(conserve_flux=True)` (Lanczos-like anti-aliased). Image units MJy/sr ⇒ surface brightness conserved; for point-source flux, multiply by pixel solid angle $\Omega_{pix}$ [sr].
- Variance: reproject VARIANCE with the same weights squared (`reproject_exact` of variance is approximate; we propagate $V_{out}=\sum_i w_i^2V_i/(\sum w_i)^2$ using the overlap matrix from our own sparse implementation for small cutouts).
- Correlated noise: resampling correlates neighbouring pixels so per-pixel variance underestimates aperture noise. Correction factor measured empirically: place $N\ge 200$ random apertures (radius $r$) on source-masked regions; $F_{corr}(r)=\mathrm{std}(f_{ap})/\sqrt{\sum_{ap}V}$. Store $F_{corr}(r)$ in product header (`CORRNOIS`), apply to all aperture errors and to difference-image significance. Typical 1.1–1.5.

---

## B. Noise and artifact rejection

### B.1 Noise model & S/N [E]
Per pixel (electrons): $\sigma_{pix}^2 = S\,t + (B_{zodi}+B_{other})\,t + D\,t + \sigma_{RN,eff}^2$, with up-the-ramp read noise $\sigma_{RN,eff}^2 \approx \frac{12\,\sigma_{RN}^2}{N_r}\cdot\frac{N_r-1}{N_r+1}$ (Rauscher et al. 2007, read-noise-limited regime; $N_r$ = reads). In practice use the **VARIANCE** extension $V_i$ [(MJy/sr)²] which already includes these terms [verify]. Zodiacal light dominates background at 1–5 µm away from the Galactic plane.

Optimal (PSF-weighted) photometry, PSF $P_i$ normalised ($\sum P_i=1$), background-subtracted data $D_i$:
$$\hat F=\frac{\sum_i P_iD_i/V_i}{\sum_i P_i^2/V_i},\qquad \sigma_F^2=\frac{1}{\sum_i P_i^2/V_i},\qquad \mathrm{S/N}=\hat F/\sigma_F.$$
Aperture photometry: $\sigma_F^2=F_{corr}^2\big(\sum_{ap}V_i + N_{ap}^2\sigma_{\bar B}^2\big)$.

### B.2 Background [E]
1. Subtract ZODI model (`ZODI` extension, if provided) [verify].
2. Residual background via `photutils.background.Background2D`: mesh 32×32 px (≈ 3.3′), filter 3×3, `SigmaClip(sigma=3, maxiters=5)`, estimator `SExtractorBackground` (2.5·median − 1.5·mean), sources masked by `detect_sources` (2σ, npix ≥ 3) dilated by 2 px. **LVF guard**: because $\lambda(x,y)$ varies continuously across the detector, fit the background only along the iso-wavelength axis (or subtract a smooth 1D column/row profile after zodiacal subtraction) so spectral-edge steps are not absorbed into the background mesh. Cutouts < 64 px: global sigma-clipped median plane + gradient fit.
3. Background RMS map → empirical noise floor check against `sqrt(VARIANCE)`.

### B.3 Masks & artifacts [E/A]
| Artifact | Detection/mitigation |
|---|---|
| Cosmic rays | In L2 already rejected by ramp fitting [verify]; residuals: L.A.Cosmic (`astroscrappy`) only on single-frame uploads; multi-frame: per-pixel 3σ-clip against inverse-variance median in co-add |
| Hot/bad pixels | FLAGS bits (`SATURATED`, `NONLINEAR`, `BADPIX`, `CR`, `PERSIST`, `GHOST`); also pixels flagged in > 30% of frames at the same `(detector_id, x_det, y_det)` |
| Persistence | Source present within $r < 1.5\,\mathrm{px}$ at the same **detector** `(detector_id, x_det, y_det)` within $\Delta t_{\mathrm{persist}} \le 3600\,\mathrm{s}$ after a pixel exceeded $50\%$ full-well ($>10^4\,\mathrm{MJy/sr}$) ⇒ flag `PERSIST`; checking detector coordinates rather than sky coordinates cleanly separates H2RG latent charge from real sky movers |
| Ghosts & Dichroic glints | Optical reflections at deterministic offsets from bright stars across the beamsplitter (arrays 1–3 ↔ 4–6) [verify geometry]; mask templates from release profile |
| Diffraction spikes | Mask along spider spike angles around stars with $K < 10$; length $\propto 10^{-0.2(m-m_0)}$ |
| Scattered light / moon / earth-shine | Low-order structure in background residual; flag frame (`SCATTERED_LIGHT`) if RMS of mesh map $> 3\times$ median |
| Saturation/nonlinearity | FLAGS; PSF fit to wings with core ($r \le 1.5\,\mathrm{px}$) masked, $\sigma_{pos}$ inflated $\times 2$, flag `SATURATED` |
| Array / LVF boundaries | Mask within $2\,\mathrm{FWHM}$ ($4\,\mathrm{px}$) of physical H2RG edges $[0, 2047]$ and inter-strip LVF boundaries; flag detections within $6\,\mathrm{px}$ as `EDGE` |

Quality mask $Q_i\in\{0,1\}$; masked pixels get $w_i=0$.

### B.4 Per-array calibration and co-addition [A]
- **Six-array architecture**: SPHEREx uses 6 H2RG $2048^2$ detectors split by a dichroic into short-$\lambda$ (arrays 1–3: $0.75\text{--}2.42\,\mu\mathrm{m}$, $R\approx 39\text{--}41$) and long-$\lambda$ (array 4: $2.42\text{--}3.82\,\mu\mathrm{m}$, $R\approx 35$; arrays 5–6: $3.82\text{--}5.00\,\mu\mathrm{m}$, $R\approx 110\text{--}130$). Each exposure has a linear variable filter (LVF) mapping pixel $(x,y) \mapsto \lambda(x,y)$ via `WCS-WAVE`.
- **Per-array photometric zero-point**: fit $ZP_a$ per array $a\in\{1..6\}$ by matching uncrowded reference stars to synthetic photometry interpolated to exact pixel $\lambda_k$: $ZP_a = \mathrm{median}(m_{ref}-m_{inst})$, $\sigma_{ZP} = 1.4826\,\mathrm{MAD}/\sqrt{N}$.
- **Band-grouped co-addition (never blind full-array co-adds)**: because a source's spectrum varies strongly across $0.75\text{--}5.0\,\mu\mathrm{m}$ (e.g., WISE 0855 is undetectable at $<3.5\,\mu\mathrm{m}$ and peaks at $4.5\text{--}5.0\,\mu\mathrm{m}$ in arrays 5–6), frames are co-added on a common tangent-plane grid only within **6 fixed narrow/medium band groups** $b \in \{B_1:[0.75,1.1], B_2:[1.1,1.6], B_3:[1.6,2.42], B_4:[2.42,3.5], B_5:[3.5,4.2], B_6:[4.2,5.0]\}\,\mu\mathrm{m}$ per epoch visit:
$$I_{co,b}(p)=\frac{\sum_{k\in b} w_k(p) I_k(p)}{\sum_{k\in b} w_k(p)},\quad w_k=\frac{Q_k}{V_k},\quad V_{co,b}=\frac{1}{\sum_{k\in b} w_k},\quad \lambda_{eff,b}(p)=\frac{\sum_{k\in b} w_k\lambda_k}{\sum_{k\in b} w_k}$$
  with 1-pass $3\sigma$ clipping on $|I_k-\tilde I_b|/\sqrt{V_k}$.
- **Astrometric & moving-object rule [A]**: Resampled co-adds introduce correlated noise and smear fast objects over multi-day visits. **All precision astrometry (§C) is measured on native un-resampled L2 exposures** (or evaluated jointly across native frames); band-grouped co-adds are used only for deep candidate seeding (e.g., WISE 0855 in $B_6$), visualisation, and image differencing.

### B.5 Image differencing [E]
After registration and reprojection of reference $R_{b}$ onto science $N_{b}$ **restricted to matching band group $b$ ($|\Delta\lambda_{eff}/\lambda_{eff}| < 0.05$)**:
- **ZOGY (Zackay, Ofek & Gal-Yam 2016)** (sole MVP differencing algorithm; Alard–Lupton dropped to avoid kernel-basis overfitting on undersampled $6.2''$ pixels): in Fourier space
$$\hat D=\frac{F_r\hat P_r\hat N-F_n\hat P_n\hat R}{\sqrt{\sigma_n^2F_r^2|\hat P_r|^2+\sigma_r^2F_n^2|\hat P_n|^2}},\quad \hat P_D=\frac{F_rF_n\hat P_r\hat P_n}{F_D\sqrt{\sigma_n^2F_r^2|\hat P_r|^2+\sigma_r^2F_n^2|\hat P_n|^2}},\quad S=D\otimes\overleftarrow{P_D}$$
  Score image $S_{corr}=S/\sqrt{V(S_N)+V(S_R)+V_{ast}}$ including source-noise and astrometric-registration variance $V_{ast}=\sigma_{reg,x}^2(\partial_x S)^2+\sigma_{reg,y}^2(\partial_y S)^2$ **plus chromatic differential-refraction/LVF-gradient variance** $V_{chrom}=(\Delta\lambda\cdot\partial_\lambda P\star F)^2$.
- Detection: local extrema with $|S_{corr}|\ge 5$ (both positive and negative lobes retained for dipole pairing).

### B.6 Matched filter & false-alarm rate [E]
Detection map $\mathrm{SNR}(p)=\dfrac{(P\star D/V)(p)}{\sqrt{(P^2\star 1/V)(p)}}$. Gaussian FAR at threshold $z$: per independent beam $p_{FA}=\Phi(-z)$; $N_{beam}\approx N_{pix}/(1.133\,\mathrm{FWHM_{px}}^2)$. Real H2RG noise is non-Gaussian, so **thresholds are calibrated empirically**: run detection on sign-inverted difference images $S_{corr} \to -S_{corr}$ and choose $z=\max(5.0, z_{emp})$ so the empirical false-alarm rate is $\le 0.01$ per $10'\times 10'$ field.

### B.7 Real–bogus vetting [A]
**MVP deterministic rule-based score** (`real_bogus` $\in [0,1]$, replacing an uncalibrated ML classifier until labelled SPHEREx release training sets exist; `# PHASE2: HistGradientBoostingClassifier`):
Compute penalty product from: (1) reduced PSF fit $\chi^2_\nu \in [0.5, 2.5]$, (2) sharpness $0.2 \le \mathrm{sharp} \le 1.0$ (rejects single-pixel unflagged hot pixels and flat ghosts), (3) roundness $|\mathrm{round}| \le 0.6$ (unless consistent with known SSO rate), (4) unflagged aperture weight fraction $\ge 0.85$, (5) not flagged `PERSIST`, `GHOST`, or `SPIKE`, and (6) detection present in $\ge 2$ dithered exposures within the visit (for slow movers). Candidate passes vetting if `real_bogus >= 0.70` (achieving $\mathrm{TPR}\ge 0.95, \mathrm{FPR}\le 0.01$ on synthetic injection tests).

### B.8 Movers vs variables (critical) [A]
On the band-matched difference stamp $D_b(x)$ ($11\times 11\,\mathrm{px}$) and native-frame centroids across epochs, apply a 3-stage discriminator:
1. **Single-pair difference stamp test**: fit by weighted least squares ($n = 121\,\mathrm{px}$):
   - $\mathcal M_V$ (stationary variable, $k_V=3$: $\Delta F, x_0, y_0$): $D(x)=\Delta F\,P(x-x_0)$ at fixed reference position $x_0$.
   - $\mathcal M_M$ (moving source, $k_M=5$: $F_0, F_1, x_0, x_1, y_1$): $D(x)=F_1 P(x-x_1) - F_0 P(x-x_0)$.
   Compute $\Delta\mathrm{BIC}_{MV} = (\chi^2_V - \chi^2_M) - (k_M - k_V)\ln n$. Require $\Delta\mathrm{BIC}_{MV} > 10$ **and** $|\mathbf x_1 - \mathbf x_0| > 3\sigma_{\Delta x}$ to prefer motion over pure flux change.
2. **Multi-epoch trajectory vs stationary variable**: across $N \ge 2$ epochs, compare stationary model $\mathcal M_{stat}$ ($\boldsymbol\mu = \mathbf 0$, $k=2$) against linear proper-motion model $\mathcal M_{lin}$ ($\boldsymbol\mu \ne \mathbf 0$, $k=4$) on native-frame sky coordinates: require $\chi^2_\mu = \hat{\boldsymbol\mu}^T \mathrm{Cov}(\hat{\boldsymbol\mu})^{-1}\hat{\boldsymbol\mu} > 25.0$ ($5\sigma$ in 2D) and $\Delta\mathrm{BIC}_{stat\to lin} > 10$, whilst flux variability significances $\chi^2_{var} = \sum_i (m_i - \bar m)^2/\sigma_{m,i}^2$ are tracked independently. Pure variables (R Dra, Mira, AGN) have $\chi^2_{var} \gg 50$ but $\chi^2_\mu < 6.0$ ($<2\sigma$).
3. **Variability-induced motion (VIM) check [A]** (mandatory for unresolved variable binaries like RW Aur, $1.4''$ separation $\approx 0.23\,\mathrm{px}$): a flux change in one component shifts the blended photocentre along the binary axis by $\Delta\mathbf x_{pc} = \mathbf s\,\Delta\big(\frac{F_B}{F_A+F_B}\big)$ up to $\sim 0.3''$. When $N \ge 4$ epochs show significant flux variation ($\chi^2_{var}/(N-1) > 3$), regress centroid offset $\Delta\mathbf x_i$ against normalised flux change $\Delta F_i/\bar F$ ($\mathcal M_{VIM}: \Delta\mathbf x_i = \mathbf c\,(\Delta F_i/\bar F)$) vs linear time ($\mathcal M_{lin}: \Delta\mathbf x_i = \boldsymbol\mu(t_i - t_0)$). Flag `VIM_CANDIDATE` (and suppress mover classification) if $|r(\Delta\mathbf x_\parallel, \Delta F)| > 0.8$ with $p < 0.01$ or $\mathrm{BIC}_{VIM} < \mathrm{BIC}_{lin} - 6$.

---

## C. Astrometry

### C.1 Centroiding [E]
PSF fitting **strictly on native un-resampled L2 frames** using `photutils.psf.PSFPhotometry` with the wavelength-indexed `GriddedPSFModel` built from the `PSF` extension at pixel $(x,y)$ and wavelength $\lambda(x,y)$ [verify PSF oversampling factor, nominal $4\times$]. Fallbacks: elliptical Moffat ($\beta = 3.0$) if `PSF` extension is absent; windowed centroid (`SEP` `winpos`) as cross-check (flag `CENTROID_MISMATCH` if $|\mathbf x_{PSF} - \mathbf x_{win}| > 0.5\,\mathrm{px}$).
Per-axis statistical error:
$$\sigma_{x,stat}\approx\frac{\mathrm{FWHM}}{2.355\,\mathrm{S/N}}$$
Full $2\times 2$ detector covariance $\mathbf C_{xy,stat}$ from the WLS Gauss–Newton inverse Hessian $\mathbf H^{-1}$ scaled by $\max(1, \chi^2_\nu)$. Total per-frame covariance on the tangent plane ($\mathbf J = \partial(\xi,\eta)/\partial(x,y)$ from refined WCS):
$$\mathbf C_{\xi\eta,tot} = \mathbf J\,\big(\mathbf C_{xy,stat} + (\sigma_{phase}^2 + \sigma_{psf}^2)\mathbf I_2\big)\,\mathbf J^T + (\sigma_{reg}^2 + \sigma_{sys}^2)\mathbf I_2$$

| Term | Origin | Default / Typical |
|---|---|---|
| $\sigma_{stat}$ | photon + zodi + read noise (from `VARIANCE`) | $0.21''$ at S/N 20; $0.04''$ at S/N 100 ($\mathrm{FWHM}=10''$) |
| $\sigma_{reg}$ | per-array WCS tie to Gaia DR3 | $\mathrm{RMS}_{ref}/\sqrt{N_{ref}} \approx 0.02\text{--}0.05''$ |
| $\sigma_{phase}$ | undersampled $6.2''$ pixel-phase bias after lookup correction | $0.05''$ ($0.12''$ uncorrected) |
| $\sigma_{psf}$ | monochromatic PSF vs LVF spectral slope across stamp | $0.03''$ |
| $\sigma_{sys}$ | per-array systematic floor (calibrated from bright Gaia stars) | $0.10''$ default |

### C.2 Per-array plate solution refinement against Gaia DR3 [E]
1. **Per-array registration**: SPHEREx's 6 H2RG arrays sit on two focal planes separated by a dichroic with thermally drifting inter-array alignment. **Every detector array $a\in\{1..6\}$ is registered independently.**
2. **Gaia DR3 reference selection**: cone query over array footprint $+1'$; select `ruwe < 1.4`, `astrometric_params_solved >= 31`, $12 < G < 17.5$, isolated (no neighbour with $\Delta G < 3$ within $2\,\mathrm{FWHM} = 20''$), and unflagged (`FLAGS == 0`) in our frame.
3. **Epoch & topocentric propagation**: propagate each Gaia star from `J2016.0` (TCB) to frame mid-exposure `MJD_TDB` and spacecraft barycentric position $\mathbf b(t_{obs})$ [au]:
   $$\Delta\alpha\cos\delta = \mu_{\alpha*}(t - 2016.0) + \varpi\,P_\alpha(t),\qquad \Delta\delta = \mu_\delta(t - 2016.0) + \varpi\,P_\delta(t)$$
   with epoch error $\sigma_{Gaia}^2(t) = \sigma_0^2 + (\Delta t\,\sigma_\mu)^2 + \sigma_\varpi^2 (P_\alpha^2+P_\delta^2)$ ($<2\,\mathrm{mas}$).
4. **Adaptive distortion model (preventing overfitting)**:
   - $N_{ref} \ge 80$ (full $3.5^\circ$ array): refit `TAN-SIP` polynomial degree 3 ($2\times 10$ coefficients) via `fit_wcs_from_points(..., sip_degree=3)` with 3-iteration $3\sigma$ MAD clipping.
   - $15 \le N_{ref} < 80$ (sub-array or user cutout): fit **6-parameter affine** transformation ($\Delta\xi_0, \Delta\eta_0$, scale, rotation, shear) around existing SIP polynomials.
   - $5 \le N_{ref} < 15$ (tiny cutout $\le 64\times 64\,\mathrm{px}$): fit **2-parameter shift** ($\Delta\xi_0, \Delta\eta_0$) only.
   - $N_{ref} < 5$: do not refit; keep header WCS, set `WCS_UNREFINED`, and inflate $\sigma_{reg} = 0.50''$.
5. **Pixel-phase bias correction**: bin Gaia residuals $(\Delta x, \Delta y)$ in $5\times 5$ sub-pixel phase bins $(\phi_x, \phi_y) = (x - \lfloor x+0.5\rfloor, y - \lfloor y+0.5\rfloor) \in [-0.5, 0.5)^2$ per array; subtract bicubic-spline lookup $\delta_{phase}(\phi_x, \phi_y)$ before final WCS fit.
6. **Acceptance gate**: require $\mathrm{RMS}_{2D} \le 0.35''$ and $N_{ref} \ge 5$; otherwise flag `ASTROMETRY_POOR`.

### C.3 Proper motion (linear model) [E]
Project sky coordinates $(\alpha_i, \delta_i)$ onto the gnomonic tangent plane $(\xi_i, \eta_i)$ [arcsec] centred at reference position $(\alpha_0, \delta_0)$ at weighted mean epoch $t_0 = \big(\sum_i w_i t_i\big)/\sum_i w_i$ (which diagonalises the position–proper-motion cross-covariance):
$$\xi_i = \xi_0 + \mu_{\alpha*}(t_i - t_0) + \epsilon_{\xi,i},\qquad \eta_i = \eta_0 + \mu_\delta(t_i - t_0) + \epsilon_{\eta,i}$$
Solve via weighted least squares with full $2\times 2$ per-epoch inverse covariance $\mathbf W_i = (\mathbf C_{\xi\eta,tot,i} + \sigma_{cat,i}^2\mathbf I_2)^{-1}$, where $\sigma_{cat,i} \in \{0.0\text{ (SPHEREx/Gaia)}, 0.03''\text{ (CatWISE)}, 0.08''\text{ (2MASS)}\}$ accounts for external catalogue frame-tie uncertainty:
$$\hat{\boldsymbol\theta} = \Big(\sum_{i=1}^N \mathbf A_i^T \mathbf W_i \mathbf A_i\Big)^{-1} \sum_{i=1}^N \mathbf A_i^T \mathbf W_i \mathbf y_i,\qquad \mathrm{Cov}(\hat{\boldsymbol\theta}) = \Big(\sum_{i=1}^N \mathbf A_i^T \mathbf W_i \mathbf A_i\Big)^{-1}$$
where $\mathbf A_i = \begin{bmatrix} 1 & 0 & t_i-t_0 & 0 \\ 0 & 1 & 0 & t_i-t_0 \end{bmatrix}$ and $\boldsymbol\theta = [\xi_0, \eta_0, \mu_{\alpha*}, \mu_\delta]^T$.
Total proper motion and Rice-bias-free significance:
$$\mu = \sqrt{\mu_{\alpha*}^2 + \mu_\delta^2},\quad \sigma_\mu^2 = \frac{\boldsymbol\mu^T \mathrm{Cov}(\boldsymbol\mu)\boldsymbol\mu}{\mu^2},\quad \chi^2_\mu = \boldsymbol\mu^T \mathrm{Cov}(\boldsymbol\mu)^{-1}\boldsymbol\mu\ (\sim\chi^2_2),\quad \mathrm{PA} = \mathrm{atan2}(\mu_{\alpha*}, \mu_\delta)\bmod 360^\circ$$
**Outlier rejection**: if $N \ge 4$, compute leave-one-out studentised residual $r_i^2 = \mathbf e_{i}^T (\mathbf C_i - \mathbf A_i \mathrm{Cov}(\hat{\boldsymbol\theta})\mathbf A_i^T)^{-1}\mathbf e_i$; iteratively drop at most 1 epoch per pass with $r_i > 4.0$ (never dropping below $N=3$) and flag the epoch detection `PM_OUTLIER`.

### C.4 Parallax and 5-parameter fit [E]
Given spacecraft/observer barycentric ICRS position $\mathbf b(t) = (X, Y, Z)$ [au] from `FITS` ephemeris keywords (`OBSGEO-X/Y/Z` or SPHEREx orbit table via `astropy.coordinates.get_body_barycentric('earth')` + L2 LEO offset), the parallax factors are:
$$P_\alpha(t) = X\sin\alpha_0 - Y\cos\alpha_0,\qquad P_\delta(t) = X\cos\alpha_0\sin\delta_0 + Y\sin\alpha_0\sin\delta_0 - Z\cos\delta_0$$
$$\xi(t) = \xi_0 + \mu_{\alpha*}(t - t_0) + \varpi P_\alpha(t),\qquad \eta(t) = \eta_0 + \mu_\delta(t - t_0) + \varpi P_\delta(t)$$
1. **Closed-form MAP / WLS estimator (no MCMC in MVP)**: because $\xi(t), \eta(t)$ are strictly linear in $\boldsymbol\theta_5 = [\xi_0, \eta_0, \mu_{\alpha*}, \mu_\delta, \varpi]^T$ with Gaussian errors, the exact posterior is analytical! To prevent unphysical negative parallaxes at low S/N without running slow `emcee` chains (`# PHASE2: optional Bailer-Jones distance sampling`), solve the regularised normal equations with weak zero-mean positive-support ridge prior $\sigma_{\varpi,prior} = 2.0''$ and enforce physical positivity $\hat\varpi \ge 0$ via active-set WLS when $\hat\varpi_{free}/\sigma_\varpi < -2$.
2. **Degeneracy guard**: compute the condition number $\kappa$ of the correlation matrix of $\mathbf A^T\mathbf W\mathbf A$ and the parallax baseline span $\Delta P = \max_{i,j}\sqrt{(P_\alpha(t_i)-P_\alpha(t_j))^2 + (P_\delta(t_i)-P_\delta(t_j))^2}$. Fit 5 parameters **only if** $N \ge 4$ epochs, $\Delta t \ge 0.8\,\mathrm{yr}$, and $\Delta P \ge 1.0\,\mathrm{au}$ (satisfied by SPHEREx's $90^\circ$ solar-elongation cadence every 6 months where $P_\alpha$ flips sign). Otherwise fix $\varpi$ to the external catalogue prior (or $\varpi = 0$) and solve the 4-parameter linear model (§C.3).
3. **Model selection**: choose between stationary ($k=2$: $\xi_0, \eta_0$), 4-param linear ($k=4$), and 5-param parallax ($k=5$) using $\mathrm{BIC}_k = \chi^2_k + k\ln(2N)$. Select 5-param over 4-param iff $\mathrm{BIC}_5 < \mathrm{BIC}_4 - 6$ and $\hat\varpi/\sigma_\varpi > 3.0$. Report distance $d = 1/\hat\varpi$ [pc] directly only when $\hat\varpi/\sigma_\varpi \ge 5.0$; otherwise report the $68\%$ confidence interval on $\varpi$.

### C.5 Detection limits vs baseline [A]
For $N$ epochs with times $t_i$: $\sigma_\mu=\sigma_{pos}/\sqrt{\sum_i(t_i-\bar t)^2}$ (per axis). 5σ detectable μ:

| Configuration | $\sum(t-\bar t)^2$ [yr²] | σ_μ (σ_pos 0.3″) | 5σ μ_min | σ_μ (σ_pos 0.1″) | 5σ μ_min |
|---|---|---|---|---|---|
| 2 epochs, 0.5 yr | 0.125 | 0.85″/yr | 4.2″/yr | 0.28″/yr | 1.4″/yr |
| 3 epochs, 1.0 yr | 0.5 | 0.42 | 2.1 | 0.14 | 0.71 |
| 4 epochs, 1.5 yr | 1.25 | 0.27 | 1.3 | 0.09 | 0.45 |
| 8 epochs, 3.5 yr (extended mission) | 10.5 | 0.093 | 0.46 | 0.031 | 0.15 |
| + WISE anchor (2010, σ 0.2″), T ≈ 16 yr | — | ≈ 0.022 | 0.11 | ≈ 0.014 | 0.07 |
| + 2MASS anchor (≈1999, σ 0.08″), T ≈ 27 yr | — | ≈ 0.012 | 0.06 | ≈ 0.005 | 0.025 |

(Ignoring parallax; 5-param fits inflate σ_μ by ~1.2–2.) Brown-dwarf discoveries by Backyard Worlds typically have μ ≳ 0.1–1″/yr; SPHEREx-only detection is realistic for μ ≳ 1–2″/yr in the first ~1.5 yr, much better with WISE/2MASS anchors.

### C.6 Max trackable rate vs FOV [A]
Object must stay in the field between consecutive used epochs: $\mu_{max}\approx\frac{\mathrm{FOV}/2}{\Delta t}$. 10′ FOV, Δt = 0.5 yr ⇒ 600″/yr (any star). For SSOs between surveys (MBA ~0.25°/day) impossible ⇒ SSOs are tracked **within a survey visit** (minutes–days) using full 3.5° frames: 3.5° / (0.25°/day) ≈ 14 days crossing time.

---

## D. Object classification

### D.1 Classes
`star_pm`, `star_static`, `brown_dwarf` (subtype M/L/T/Y), `white_dwarf`, `galaxy`, `quasar`, `asteroid`, `comet`, `variable_star` (subtypes YSO/Mira/eclipsing/other), `transient` (SN etc.), `artifact`, `candidate_new_mover`, `candidate_planet_x`.

### D.2 SED from 102 channels (on-the-fly spectra) [A]
Forced PSF photometry at the (motion-predicted) position in every frame covering the source; each point gets $(\lambda_{c},\Delta\lambda,F_\nu,\sigma,t,\mathrm{flags})$ where $\lambda_c$ from WCS-WAVE at the source pixel. Output: spectrum table + plot, epochs shown as colour, and a "temporal spread" diagnostic (max−min $t$). Systematic floor 3% added in quadrature per point.

Diagnostic features in 0.75–5.0 µm:

| Class | Features |
|---|---|
| Normal stars | Rayleigh–Jeans fall-off; H⁻ bump at 1.6 µm (cool stars); CO 2.29 µm (strong in giants), CO 4.6 µm |
| M/L dwarfs | H₂O 1.4, 1.9, 2.7 µm; CO 2.3 µm; L: very red 1–2.5 µm (dust) |
| T dwarfs | CH₄ 1.6, 2.2, **3.3 µm** strong; H₂O; flux peak 4–4.5 µm; CO 4.6, **CO₂ 4.3 µm** |
| Y dwarfs (WISE 0855) | almost all flux at 4–5 µm; $W1-W2\gtrsim3.5$ (Vega); CH₄ 3.3, H₂O, NH₃ ≈3.0, CO₂ 4.3, CO 4.6 µm |
| White dwarfs | blue, faint, steep RJ; very high reduced PM |
| Galaxies | 1.6 µm (rest) stellar bump at $1.6(1+z)$; PAH 3.3 µm (rest) emission; extended |
| Quasars | power law $F_\nu\propto\nu^{-\alpha}$, hot-dust 1–3 µm (rest) bump; redshifted Hα/Paβ/Paα; W1−W2 ≥ 0.8 (Stern+2012) |
| Asteroids | solar spectrum × reflectance (S: 1 & 2 µm pyroxene/olivine; C: flat + 3 µm hydration) + thermal tail rising ≳3.5 µm (NEATM, T ∼ 200–300 K at 1–2.5 au) |
| Comets | extended coma; emission H₂O 2.7–2.9 µm, **CO₂ 4.26 µm**, **CO 4.67 µm**; water-ice absorption 1.5/2.0/3.0 µm in grains |
| Mira (R Dra) | very red, deep H₂O bands, CO; large-amplitude variable |
| YSO / T Tau (RW Aur) | near-IR excess (disk), Br γ 2.17, Br α 4.05 µm emission, CO; irregular variability |
| Supernova | new source, host-offset, blue→red evolution, fading |

**Template fitting**: libraries: Pickles/IRTF stars, SpeX + **Sonora Bobcat/Elf Owl**, ATMO-2020 models for BDs (cover to 5 µm), JWST/AKARI Y-dwarf spectra, Brown+2014 galaxy SEDs (with z grid), Glikman+2006 QSO composite, Bus–DeMeo reflectance × solar + NEATM for asteroids, parametric coma (band strengths free) for comets. For each template $k$: $\chi_k^2=\sum_i(F_i-s_kT_{k,i})^2/\sigma_i^2$ with analytic scale $s_k=\sum F_iT_{k,i}/\sigma_i^2\big/\sum T_{k,i}^2/\sigma_i^2$; class likelihood $L_c^{SED}=\sum_{k\in c}\pi_k e^{-\chi^2_k/2}$. Index shortcuts (robust at low S/N): CH₄ index $=F(3.25\text{–}3.40)/F(3.9\text{–}4.1)$; CO₂ index $=F(4.2\text{–}4.35)/F(4.0\text{–}4.15)$; "4.5 µm excess" $=F(4.4\text{–}5.0)/F(1.0\text{–}2.4)$.

### D.3 Motion as discriminator [E]
- Reduced proper motion: $H_m=m+5\log_{10}\mu+5 = M+5\log_{10}v_t-3.379$ (μ in ″/yr). Use $H_{W2}$ vs $W1-W2$ (or SPHEREx synthetic W1/W2-like bands): Y dwarfs $H_{W2}\gtrsim18$, T dwarfs 15–18; white dwarfs separate in $H_G$ vs colour.
- High μ + red ⇒ nearby cool dwarf; μ ≈ 0 (consistent with Gaia CRF quasars) + extended/power-law ⇒ galaxy/QSO; rate ≫ stellar (″/hr) ⇒ SSO; parallactic loop size gives distance for slow SSOs (§E.3).

### D.4 Variability [E]
Per band group, fluxes $F_e\pm\sigma_e$ over epochs: $\chi^2_{var}=\sum_e(F_e-\bar F_w)^2/\sigma_e^2$ (variable if $p<10^{-3}$ and amplitude $>5\sigma$); Stetson J (correlated multi-band); amplitude $\Delta m$; Lomb–Scargle (`astropy.timeseries.LombScargle`) when ≥ 8 epochs (usually not until extended mission; deep fields near ecliptic poles may allow earlier) with false-alarm probability. Intra-survey channel repeats (same λ observed twice) give short-timescale variability checks.

### D.5 Cross-match [E]
Catalogs: Gaia DR3 (and DR4 when released, Dec 2026 expected [verify]), CatWISE2020, AllWISE, unWISE, 2MASS PSC, Pan-STARRS DR2, SIMBAD, VizieR (Backyard Worlds discoveries, UltracoolSheet), MPC/SkyBoT/Horizons. Propagate catalog positions to our epoch (μ, ϖ, observer). Bayesian positional match (Budavári & Szalay 2008), angles in radians:
$$B=\frac{2}{\sigma_1^2+\sigma_2^2}\exp\!\Big(-\frac{\psi^2}{2(\sigma_1^2+\sigma_2^2)}\Big),\qquad P(\text{match})=\Big[1+\frac{1-P_0}{B\,P_0}\Big]^{-1},\ P_0\approx\frac{\rho\,\Omega}{\ldots}$$
($P_0$ from source density ρ). Accept if $P>0.9$; ambiguous 0.5–0.9 flagged.

### D.6 Bayesian combiner [A]
$$P(c\mid\mathcal D)\propto\pi(c\mid l,b,m)\prod_{m\in\{\mathrm{SED,motion,var,morph,xmatch}\}}L_m(\mathcal D_m\mid c)^{w_m}$$
Temperatures $w_m\in(0,1]$ correct for non-independence; fitted on a labelled validation set (SIMBAD/Gaia-classified objects in SPHEREx fields) to minimise log-loss; calibration checked by reliability diagram (ECE < 0.05). Output: probability vector, top class, and **evidence** = per-method $w_m\ln L_m$ contributions + the key features (e.g. "μ = 8.1±0.4″/yr; 4.5 µm excess 140±20; no Gaia counterpart"). `candidate_new_mover` assigned when motion significant (>5σ) and no catalog counterpart after propagation; `candidate_planet_x` only from §I pipeline. Never "confirmed".

---

## E. Orbital / dynamical methods

### E.1 Solar-system objects [E]
- Apparent rate: $\omega=\frac{|\mathbf v_{rel}-(\mathbf v_{rel}\cdot\hat{\mathbf n})\hat{\mathbf n}|}{\Delta}$, $\mathbf v_{rel}=\mathbf v_{obj}-\mathbf v_{obs}$. Handy: 1 au/day ≙ 206265″/day per au. At quadrature (SPHEREx's typical geometry) Earth's velocity is nearly along the line of sight, so for distant objects $\omega\approx 148''\,\mathrm{h^{-1}}\,d^{-3/2}\times(\text{orbit-orientation factor})$ (from $v_{orb}=29.8\,d^{-1/2}$ km/s).
- Known-object check: SkyBoT cone (IMCCE) per frame epoch and observer = SPHEREx (or geocentre + correction) → Horizons ephemeris for each candidate (`astroquery.jplhorizons.Horizons(id, location=<spacecraft or '500'>, epochs=...)`); residual $\Delta=\|\mathbf x_{obs}-\mathbf x_{eph}\|$, known if $\Delta<3\sqrt{\sigma_{obs}^2+\sigma_{eph}^2}$ (σ_eph from Horizons `RSS_3sigma`/3).
- Short-arc orbit determination:
  - **Bernstein & Khushalani (2000)** [E] (default for linking & TNO arcs): parameters $(\alpha,\beta,\gamma=1/d,\dot\alpha,\dot\beta,\dot\gamma)$ in a projection frame centred on the first detection:
  $$\theta_x(t)=\frac{\alpha+\dot\alpha t+g_x(t)-\gamma x_E(t)}{1+\dot\gamma t+g_z(t)-\gamma z_E(t)},\qquad \theta_y(t)=\frac{\beta+\dot\beta t+g_y(t)-\gamma y_E(t)}{1+\dot\gamma t+g_z(t)-\gamma z_E(t)}$$
  $(x_E,y_E,z_E)$ observer position in the projection frame [au], $g$ ≈ solar-gravity perturbation $\approx-\tfrac12GM_\odot\gamma^3t^2\,\hat{\mathbf r}$. With short arcs fix $\dot\gamma=0$ (prior) and constrain $\dot\alpha^2+\dot\beta^2+\dot\gamma^2\le 2GM_\odot\gamma^3$ (bound orbit).
  - **Väisälä** [E]: 2 observations; assume perihelion ($\dot r=0$) → circular-ish solution family; used for preliminary orbits of new NEO/MBA candidates.
  - **Gauss** [E]: 3 observations; Lagrange equation $r_2^8-(A^2+2AE+R_2^2)r_2^6-2\mu B(A+E)r_2^3-\mu^2B^2=0$ solved for $r_2$, then $f,g$ series; refined by differential correction (least squares on RA/Dec residuals).
  - **Kepler's equation** $M=E-e\sin E$ by Newton: $E_{k+1}=E_k-\frac{E_k-e\sin E_k-M}{1-e\cos E_k}$, $E_0=M+e\sin M$ ($E_0=\pi$ if $e>0.8$), tolerance $10^{-12}$; hyperbolic $M=e\sinh H-H$ analogous. Position $\mathbf r=a(\cos E-e,\ \sqrt{1-e^2}\sin E)$ rotated by $(\omega,i,\Omega)$.
- Streak/rate within one frame (§H.1) seeds the linking.

### E.2 Stellar kinematics [E]
- Space velocity: with ϖ, μ, RV (SIMBAD/Gaia if available), `SkyCoord(..., radial_velocity=...).galactic.velocity` → (U,V,W) (U toward GC), corrected to LSR (Schönrich+2010: (11.1, 12.24, 7.25) km/s). Without RV: report $v_t$ only, or (U,V,W) marginalised over RV prior.
- Population hint: $v_{tot}>180$ km/s ⇒ halo; Toomre diagram. **Barnard's Star**: v_t ≈ 90 km/s (thick-disk-like) expected.
- Binary/common proper motion: for pair A, B: $\Delta\mu=|\boldsymbol\mu_A-\boldsymbol\mu_B|$; common-PM if $\Delta\mu<\max(3\sigma,\ 0.1\mu)$ and orbital motion bound $\Delta\mu\lesssim 2\pi\sqrt{M_{tot}/s_{au}}\,\varpi$ [″/yr; masses in M_⊙, s in au]. **ε Indi**: B (Ba+Bb, T1+T6, separation ≈ 0.7″ ≈ 2.4 au, P ≈ 11 yr) is ≈ 402″ from A; we test common PM of B's photocentre with A. Photocentre wobble amplitude $a_{pc}=a\,|f_M-\beta|$ is ≲ 0.1″ and wavelength-dependent (Bb relatively brighter at 4 µm): **below our floor, stated as non-measurable**.

### E.3 Planet X dynamics: see §I.

---

## F. Multi-method cross-validation

### F.1 Independent estimators
| ID | Estimator | Output | Independent of |
|---|---|---|---|
| E1 | Pixel-level difference/dipole (§B.5, B.8) | motion yes/no, Δx vector | centroid catalogs |
| E2 | Catalog-prediction check (Gaia/CatWISE μ,ϖ propagated) | predicted vs measured residual | SPHEREx motion fit |
| E3 | Centroid motion fit (WLS/MCMC, §C.3–4) | μ, PA, ϖ, χ²ν | — |
| E4 | Long-baseline anchor fit (2MASS/WISE/Gaia positions only) | μ | SPHEREx data |
| E5 | Shift-and-stack peak (stack along trial (μ, PA)) | μ that maximises S/N | centroid fits |
| E6 | SED/spectral classification (§D.2) | class probabilities | motion |
| E7 | Ephemeris match (Horizons/SkyBoT) | known SSO id, residual | — |
| E8 | Morphology/streak (§H.1) | rate, PA (mod 180°) | multi-epoch |

### F.2 Consensus [A]
- Motion vectors from estimators $i,j$: consistency $\chi^2_{ij}=\Delta\boldsymbol\mu^T(\mathbf C_i+\mathbf C_j)^{-1}\Delta\boldsymbol\mu$ (2 dof); disagreement if $p<0.01$. Consensus = inverse-covariance weighted mean of mutually consistent estimators, $\mathbf C_{cons}=(\sum\mathbf C_i^{-1})^{-1}$; estimators sharing data (E3 and E5) are not combined, only compared.
- Classification: per-method top class + agreement score $A=\max_c\#\{m: \hat c_m=c\}/M$.
- Verdict: **Consistent** (no disagreements), **Tension** (1 disagreement, explained, e.g. saturation), **Conflict** (≥ 2 or unexplained) ⇒ UI warning, object cannot be exported as a "finding" without user note.

### F.3 Validation plan: mandatory targets

Reference values must be fetched from SIMBAD / Gaia DR3 / literature at test-build time and pinned in `tests/science/reference_values.json` with citation and retrieval date. Values below are *approximate, for orientation*.

| Target | Expected (approx.) | SPHEREx-specific notes | Acceptance criteria (numeric) |
|---|---|---|---|
| **Barnard's Star** (Gaia DR3 4472832130942575872) | μα* ≈ −0.80″/yr, μδ ≈ +10.36″/yr, μ ≈ 10.39″/yr, PA ≈ 355.6°, ϖ ≈ 547 mas, Ks ≈ 4.5 | moves ≈ 5.2″ (0.84 px) per 6 months: visible in blink; **likely saturated in short-λ arrays** [verify] ⇒ use unsaturated channels/wings | identity: one object across all epochs, conf ≥ 0.99, Gaia ID attached; SPHEREx-only (≥ 1 yr): \|μ−μ_ref\| < max(3σ, 0.3″/yr), \|ΔPA\| < max(3σ, 3°); with 2MASS/WISE anchors: \|Δμ\| < 3σ and < 50 mas/yr; ϖ (if fitted) within 3σ; class `star_pm` P ≥ 0.8; mover verdict from E1, E2, E3 all agree |
| **ε Indi B (Ba/Bb)** | μ ≈ 4.7″/yr toward PA ≈ 122° (common with ε Indi A: μα* ≈ +3.97, μδ ≈ −2.54″/yr); ϖ ≈ 275 mas | **unresolved** (0.7″); T-dwarf CH₄ 3.3 µm; ε Indi A (K≈2) saturated ⇒ excluded from precise astrometry | must NOT split into two tracked objects; μ within max(3σ, 0.5″/yr) of reference (SPHEREx-only ≥ 1 yr); common-PM test with A passes (Δμ/μ < 10%, ΔPA < 5°); class `brown_dwarf` P ≥ 0.6 with CH₄ evidence; output explicitly states "binary unresolved; orbit not measurable" |
| **WISE 0855−0714** | μ ≈ 8.1″/yr (μα* ≈ −8.1, μδ ≈ +0.7), ϖ ≈ 439 mas; W2 ≈ 13.9 (Vega) ≈ AB 17.2; invisible < 3.5 µm | Challenge coordinates have **no stated epoch**; SIMBAD lists ≈ these values at J2000 [verify] ⇒ by 2026 the source is up to ~3.5′ W of the input point ⇒ preset propagates position and uses FOV ≥ 6′. Detection only in arrays 5–6 co-adds; 0.66 px shift per 6 months | detected ≥ 5σ in array-5/6 co-add in ≥ 2 epochs (else test reports non-detection + upper limit and FAILS loudly, not silently); < 3σ in arrays 1–3 (consistent with Y dwarf); μ within max(3σ, 1″/yr) of reference; class `brown_dwarf` (Y) P ≥ 0.7; no Gaia counterpart; identity not hijacked by background sources along path (check conf ≥ 0.9 per epoch or flagged) |
| **RW Aurigae** | μ ≈ few × 10 mas/yr (≈0); 1.4″ binary; T Tauri with large irregular variability (optical ΔV up to ~3 mag; IR ~0.3–1 mag) | may be saturated in some channels [verify]; VIM risk | motion: \|μ_SPHEREx\| consistent with Gaia within 3σ (i.e. no significant motion); `mover` probability < 0.05; difference images show monopole (ΔBIC favours $\mathcal M_V$); any centroid shift flagged as VIM if correlated with flux; if Δflux > 5σ: class `variable_star` (YSO) P ≥ 0.7; A-vs-B compare lists it as "changed brightness", never "moved" |
| **R Draconis** | Mira, P ≈ 245.6 d, μ small; very bright in IR (K ≈ 0–1) | likely saturated across most channels [verify]; field ~9° from NEP ⇒ possibly higher cadence [verify] | no false motion (mover P < 0.05; \|μ\| within 3σ of Gaia/Hipparcos); if saturated in all channels the output **must** carry `SATURATED` and "photometry unreliable" (honesty test); if unsaturated channels exist and Δflux > 5σ: `variable_star` (Mira) P ≥ 0.7; if ≥ 8 epochs: Lomb–Scargle period within 10% of 245.6 d |

Additional automated science tests:
- **Injection–recovery**: synthetic PSF movers injected into real SPHEREx cutouts (μ 0.2–20″/yr, S/N 5–100, random PA), and synthetic variables (static, Δm 0.1–2): recall ≥ 0.90 for S/N ≥ 10 and μ ≥ 5σ_μ; mover false-positive rate ≤ 1 per 100 fields; variables misclassified as movers ≤ 1%.
- **Astrometric QA**: Gaia stars (16 < G < 18) per release: median |residual| at S/N > 50 ≤ 0.15″ (to be refined once measured).
- **Identity**: simulated crowded fields (density up to 1 src per 10 beams) with known truth: match accuracy ≥ 99% for S/N ≥ 10, ≥ 95% overall; all errors with conf < 0.9 flagged (no silent errors > 0.5%).
- **SSO**: known asteroids from SkyBoT in SPHEREx frames: residual vs Horizons < 3σ for ≥ 95%.
- **Planet X mode** (stretch test): recover Pluto (V≈14.5), Makemake & Haumea (V≈17) in reflected light at correct parallactic offsets [verify detectability].

---

## G. Cross-epoch identity pipeline (Requirement 4.3)

### G.1 Steps (Iterative WLS + Hungarian + Exact 2nd-Best Margin)
> **Efficiency & architecture note**: With SPHEREx's 6-month survey cadence ($N \approx 2\text{--}8$ epochs) and linear astrometric observation equations, sequential Kalman filtering ($q=(1\,\mathrm{mas/yr})^2/\mathrm{yr}$), Murty's $K=20$ best assignment enumeration, and $N$-scan track-oriented MHT are unnecessary over-engineering. They are replaced by **direct analytical 4/5-parameter WLS projection (§C.3/C.4) + 1-to-1 Hungarian assignment + single-link burn-out margin ($\Delta C_{2nd}$)** (`# PHASE2: Murty K-best / MHT for dense SSO linkers`).

1. **Per-array registration** (§C.2) of every native L2 exposure; store refined WCS and $\sigma_{reg}$.
2. **Dual-tier extraction**:
   - **Seeding tier**: run `SEP` (`thresh=1.5\sigma`, `minarea=3`, `deblend_nthresh=32`, `deblend_cont=0.005`) on the 6 band-grouped visit co-adds (§B.4) and ZOGY difference images (§B.5) to detect faint/red movers (e.g., WISE 0855 in $B_6$: $4.2\text{--}5.0\,\mu\mathrm{m}$).
   - **Astrometric tier**: measure PSF centroids $(\alpha_{j}, \delta_{j})$, full $2\times 2$ sky covariances $\mathbf C_{j}$, band-group fluxes $F_{j,b} \pm \sigma_{F,j,b}$, and quality flags **on the native un-resampled L2 exposures** (or variance-weighted epoch mean of native exposures within a visit for slow movers, $\mu < 20''/\mathrm{yr}$).
3. **Initial state per tracked object $k$** at reference epoch $t_0$: tangent-plane state $\hat{\boldsymbol\theta}_k = [\xi_0, \eta_0, \mu_{\alpha*}, \mu_\delta, \varpi]^T$ and $5\times 5$ covariance $\mathbf P_k$, plus band-group magnitude vector $\hat{\mathbf m}_k$, variability allowance $\sigma_{var,k}$, and optional external catalogue ID (`gaia_id`, `wise_id`):
   - If matched to Gaia DR3 / CatWISE2020: initialise $(\mu_{\alpha*}, \mu_\delta, \varpi)$ and $\mathbf P_k$ from catalogue.
   - If seeded from a ZOGY dipole or 2-epoch pair: initialise $\boldsymbol\mu$ from the pair displacement.
   - If seeded at a single epoch with no prior: set $\boldsymbol\mu = \mathbf 0$, $\varpi = 0$, and broad isotropic rate prior $\mathbf P_{\mu\mu} = \sigma_{\mu,0}^2 \mathbf I_2$ with $\sigma_{\mu,0} = 2.0''/\mathrm{yr}$ (expanded to $12.0''/\mathrm{yr}$ in high-proper-motion search mode so Barnard's Star at $10.36''/\mathrm{yr}$ is captured).
4. **Analytical WLS prediction** to target epoch $t_e$ (with spacecraft barycentric position $\mathbf b(t_e)$):
   $$\hat{\mathbf x}_k(t_e) = \mathbf H(t_e)\hat{\boldsymbol\theta}_k,\qquad \mathbf S_{kj} = \mathbf H(t_e)\mathbf P_k\mathbf H(t_e)^T + \mathbf C_j,\qquad \mathbf H(t_e) = \begin{bmatrix} 1 & 0 & t_e-t_0 & 0 & P_\alpha(t_e) \\ 0 & 1 & 0 & t_e-t_0 & P_\delta(t_e) \end{bmatrix}$$
5. **Footprint check & spatial gating**:
   - Evaluate predicted detector coordinates $(x_{det}, y_{det})$ on each active array $a\in\{1..6\}$ at epoch $t_e$. If $(x_{det}, y_{det})$ lies outside $[4, 2043]^2$ on all arrays or falls on a dead/masked region ($Q=0$), mark epoch status `OUT_OF_FIELD` (or `MASKED`) with **zero miss penalty** and skip assignment for object $k$ at $t_e$.
   - Otherwise gate candidate detections $j$ by Mahalanobis distance $d^2_{M,kj} = (\mathbf x_j - \hat{\mathbf x}_k)^T \mathbf S_{kj}^{-1} (\mathbf x_j - \hat{\mathbf x}_k) \le \gamma_{gate} = 18.42$ ($\chi^2_2$ at $p = 0.9999$).
6. **Log-likelihood ratio cost matrix**: for gated pairs $(k, j)$, compute negative log-likelihood ratio against local background clutter density $\rho_{local}$ [$\mathrm{arcsec}^{-2}$, measured in a $3'$ annulus]:
   $$C_{kj} = \frac{1}{2} d^2_{M,kj} + \frac{1}{2}\ln\det(2\pi\mathbf S_{kj}) + \ln\rho_{local} + C_{phot,kj} + C_{id,kj}$$
   - **Band-interpolated & variability-tolerant photometric cost**: compare only matching band groups $b$ (or log-linearly interpolate $F(\lambda)$ across adjacent channels when LVF wavelength shifts between visits):
     $$C_{phot,kj} = \frac{1}{2}\frac{(m_{j,b^*} - \hat m_{k,b^*})^2}{\sigma_{m,j}^2 + \sigma_{var,k}^2} + \frac{1}{2}\sum_{b\ne b^*} \frac{\big((m_{j,b}-m_{j,b^*}) - (\hat m_{k,b}-\hat m_{k,b^*})\big)^2}{\sigma_{col,jb}^2 + (0.15\,\mathrm{mag})^2}$$
     where $b^*$ is the highest-S/N band group (e.g., $B_6$ for Y dwarfs) and $\sigma_{var,k}$ is $0.10\,\mathrm{mag}$ default, or inflated to $\max(0.10, 1.4826\,\mathrm{MAD}(m_k))$ (up to $2.5\,\mathrm{mag}$ for Mira variables like R Dra). Colour indices $m_b - m_{b^*}$ are invariant to grey flux changes.
   - **Catalogue ID cost**: $C_{id,kj} = -\ln(20) \approx -3.00$ if $j$ and $k$ share a deterministic cross-match ID; $+\ln(100) \approx +4.61$ if both have high-confidence conflicting IDs; $0$ otherwise.
   - Ungated pairs ($d^2_{M,kj} > 18.42$) or candidates flagged `PERSIST`/`GHOST` receive $C_{kj} = +10^6$.
7. **Global 1-to-1 assignment (Hungarian)**: form the $K \times (J + K)$ augmented cost matrix $[\,\mathbf C \mid \mathrm{diag}(C_{miss,1}, \dots, C_{miss,K})\,]$ with miss cost $C_{miss,k} = -\ln\big(1 - P_D(\hat m_{k,b^*})\big)$ where $P_D = \Phi\big((\mathrm{SNR}_{pred} - 5.0)/1.0\big) \in [0.01, 0.99]$, and solve in $O((K+J)^3)$ via `scipy.optimize.linear_sum_assignment`.
8. **Exact 2nd-best margin & match probability (replacing Murty/MHT)**: for each assigned link $(k, j^*)$, set $C_{k,j^*} \leftarrow +10^6$, re-run `linear_sum_assignment` on the active connected component, and record the cost increase $\Delta C_{2nd,k} = C_{alt} - C_{opt} \ge 0$. The posterior match confidence is:
   $$P(k \leftrightarrow j^*) = \frac{1}{1 + e^{-\Delta C_{2nd,k}}}$$
   Flag `AMBIGUOUS_MATCH` if $P(k \leftrightarrow j^*) < 0.90$ ($\Delta C_{2nd,k} < 2.20$).
9. **Global trajectory refit & EM loop**: after linking across all $N$ epochs, refit every track's 4- or 5-parameter trajectory (§C.3/C.4) using all linked native-frame epochs, update $\hat{\boldsymbol\theta}_k, \mathbf P_k, \sigma_{var,k}$, and repeat Steps 4–8 (max 3 iterations; stop early when all assignments are unchanged).

### G.2 Special cases & failure modes
| Failure mode | Deterministic handling |
|---|---|
| **Crowding & blends** (two predictions within $1.5\,\mathrm{FWHM} \approx 15''$, e.g., high-$\mu$ star crossing a background star, or `SEP` deblend flag) | Jointly fit a 2-PSF model (`PSFPhotometry` group) with centres constrained to predicted trajectories $\hat{\mathbf x}_{k_1}(t_e), \hat{\mathbf x}_{k_2}(t_e)$ (or subtract the stationary background star's reference-epoch flux template before centroiding the mover); flag `BLENDED`, inflate $\mathbf C_j \leftarrow 2\mathbf C_j$. |
| **Unresolved binaries** ($s < 0.8\,\mathrm{FWHM} \approx 8''$, e.g., $\varepsilon$ Indi Ba/Bb at $0.7''$, RW Aur at $1.4''$) | Tracked as a single `UNRESOLVED_PHOTOCENTER` identity; run the §B.8 VIM regression check on residuals to flag photocentre shifts driven by component variability (`VIM_CANDIDATE`). |
| **Object leaves FOV / array gap / dichroic mismatch** | Predicted $(x_{det}, y_{det})$ tested against each array's active polygon $[4, 2043]^2$ and `WCS-WAVE` coverage; epochs outside coverage get status `OUT_OF_FIELD` (or `BAND_UNCOVERED` for $T_{\mathrm{eff}} < 500\,\mathrm{K}$ sources on arrays 1–4) with zero miss penalty; identity resumes automatically when re-entering coverage. |
| **Fades below limit / marginal S/N** | When assigned to the miss column ($j^* = \text{miss}$) while inside the active footprint, execute **forced PSF photometry** at $\hat{\mathbf x}_k(t_e)$ on the native frame: if $2.5 \le \mathrm{S/N}_{forced} < 5.0$, record forced flux + centroid with flag `LOW_SNR_FORCED`; if $\mathrm{S/N}_{forced} < 2.5$, record $3\sigma$ upper limit with status `UPPER_LIMIT`. Mark track `LOST` only after $\ge 2$ consecutive inexplicable misses when predicted $\mathrm{S/N}_{pred} \ge 7$. |
| **Large-amplitude variables** (R Dra, Mira, YSOs) | $\sigma_{var,k}$ dynamically adapts to observed MAD($m_k$); colour difference vector and astrometric gate remain tight; §B.8 prevents classifying monopole flux changes as motion. |
| **H2RG persistence / optical ghosts** | Candidates within $1.5\,\mathrm{px}$ of a prior bright-source detector pixel `(detector_id, x_det, y_det)` within $3600\,\mathrm{s}$ are flagged `PERSIST` (§B.3) and excluded from the Hungarian cost matrix ($C_{kj} = +10^6$). |
| **False match / outlier epoch** | Leave-one-out studentised residual $r_i > 4.0$ in the §C.3/C.4 trajectory fit drops the offending link, marks it `PM_OUTLIER`, and re-runs Hungarian assignment for that epoch excluding the rejected pair. |
| **Duplicate / split / user override** | Tracks sharing $\ge 80\%$ of epoch detections are flagged `DUPLICATE_PROPOSAL`; user decisions (`Accept`/`Reject`/`Reassign`) set `decided_by='user'` and lock $C_{kj} = -10^6$ (or $+10^6$) across all subsequent refits. |

Persistent IDs: session label assigned at creation (`A`, `B`, …, `AA`…), never reused within a project; stable tracks ($\ge 3$ epochs, $P_{match} \ge 0.90$) receive deterministic `public_id` `SPHX Jhhmmss.ss±ddmmss.s` at J2026.0.

### G.3 Data structures (Pydantic / typed contracts)
- `EpochDetection`: `(detection_id: str, epoch_id: str, mjd_tdb: float, detector_id: int, x_det: float, y_det: float, ra_deg: float, dec_deg: float, cov_sky_arcsec2: tuple[float, float, float], flux_band_mjy: dict[str, tuple[float, float]], snr: float, real_bogus: float, flags: int, catalog_id: str | None)`
- `TrackState`: `(object_id: str, label: str, ref_epoch_jyear: float, ra0_deg: float, dec0_deg: float, theta_5: tuple[float, float, float, float, float], cov_5x5: list[list[float]], mag_bands: dict[str, float], sigma_var_mag: float, model_type: Literal['STATIC', 'LINEAR_4P', 'PARALLAX_5P'], chi2_nu: float, chi2_mu: float, delta_bic: float, vim_r: float | None, flags: int)`
- `EpochLink`: `(object_id: str, epoch_id: str, detection_id: str | None, status: Literal['LINKED', 'LOW_SNR_FORCED', 'UPPER_LIMIT', 'OUT_OF_FIELD', 'BAND_UNCOVERED', 'MASKED', 'AMBIGUOUS_MATCH', 'PM_OUTLIER'], cost_ll: float, delta_c_2nd: float, match_prob: float, residual_arcsec: tuple[float, float], decided_by: Literal['auto', 'user'])`

### G.4 Exact numeric acceptance criteria (verified against `tests/science/reference_values.json`)
1. **Per-array WCS registration (§C.2)**: On synthetic & real L2 frames with $N_{ref} \ge 20$ Gaia DR3 stars, recovered shift/rotation error $\le 0.05''$, 2D Gaia residual $\mathrm{RMS}_{2D} \le 0.25''$, and pixel-phase bias reduced by $\ge 40\%$.
2. **Barnard's Star (fast mover + parallax)**: Linked across $100\%$ of in-field epochs with $P(k\leftrightarrow j^*) \ge 0.99$; recovered $|\hat\mu_{\alpha*} - \mu_{\alpha*,ref}| \le 2\sigma_{\mu}$ and $|\hat\mu_\delta - \mu_{\delta,ref}| \le 2\sigma_{\mu}$ (with $\sigma_\mu \le 0.15''/\mathrm{yr}$ over a 1.5-yr SPHEREx baseline or $\le 0.015''/\mathrm{yr}$ with WISE/2MASS anchor); 5-param parallax $\hat\varpi$ within $2\sigma_\varpi$ of $546.98\,\mathrm{mas}$ when $N \ge 4$ and $\Delta P \ge 1.5\,\mathrm{au}$.
3. **WISE J085510.83−071442.5 (faint $T_{\mathrm{eff}}\approx 250\,\mathrm{K}$ mover, $B_6$-only)**: Detected at $\mathrm{S/N} \ge 5.0$ in $B_6$ ($4.2\text{--}5.0\,\mu\mathrm{m}$) co-adds while arrays 1–4 are marked `BAND_UNCOVERED` / `UPPER_LIMIT` without breaking track continuity; recovered $|\hat\mu - 8.12''/\mathrm{yr}| \le 2\sigma_\mu$ ($\le 0.20''/\mathrm{yr}$ with WISE anchor) and $\mathrm{PA} = 265.1^\circ \pm 2.0^\circ$.
4. **$\varepsilon$ Indi Ba/Bb ($0.7''$ binary mover)**: Linked as single `UNRESOLVED_PHOTOCENTER` across all epochs with $P_{match} \ge 0.99$; recovered $|\hat\mu - 4.70''/\mathrm{yr}| \le 2\sigma_\mu$.
5. **R Draconis (Mira variable, $\Delta m > 1.5\,\mathrm{mag}$, stationary)**: Linked as a single persistent identity across all epochs ($P_{match} \ge 0.95$) despite $1.5\,\mathrm{mag}$ flux changes; classified as stationary variable with $\hat\mu < 0.10''/\mathrm{yr}$, $\chi^2_\mu < 6.0$ ($<2\sigma$), and $\Delta\mathrm{BIC}_{MV} < 0$.
6. **RW Aurigae ($1.4''$ variable binary, VIM test)**: Linked as single persistent identity; when synthetic/observed component flux ratio varies across $N \ge 4$ epochs, centroid shift correlates with flux ($|r| > 0.80, p < 0.01$), triggering `VIM_CANDIDATE` and suppressing false proper-motion classification.
7. **H2RG persistence & FOV boundary immunity**: Injected detector-fixed persistence spike at `(detector_id=5, x_det=1024, y_det=1024)` across dithered frames is $100\%$ flagged `PERSIST` and never linked into a moving track ($\mathrm{FPR} = 0.0$); a synthetic mover exiting array 5 at epoch 3 and entering array 6 at epoch 4 receives `OUT_OF_FIELD` on array 5 and preserves a single unbroken `object_id`.

---

## H. Trajectory checking (Requirement 4.4)

### H.1 Within a single FITS / single survey visit [A]
1. **Streaks** (fast movers within one exposure): length $\ell=\omega T_{exp}$. Example: NEO at 10°/day, T=100 s ⇒ 42″ ≈ 7 px. Detect via second moments (elongation $a/b>1.5$ relative to PSF), matched streak filter (PSF ⊗ line segment kernel over (ℓ, θ) grid; Radon transform for long trails); fit trailed-PSF model (Vereš+2012) ⇒ ω, PA (mod 180°), midpoint (= position at $t_{mid}$).
2. **Multi-HDU / cube uploads** with distinct times: treated as a sequence (tracklets via §H.2 with intra-file times).
3. **SPHEREx survey visit**: a sky point is observed in multiple exposures (stepped across the LVF) over hours–days [verify sequence timing]. Link single-frame detections not matching static sources into tracklets: ≥ 3 detections, linear motion χ²ν < 3, rate > 5σ. Known SSO check (E7).
4. **Spectral-temporal coupling [A]**: because each channel is observed at a different time, a "single-epoch spectrum" of a mover has positions $(\lambda_i,t_i,\mathbf x_i)$: fitting $\mathbf x(t)$ over the visit yields intra-visit motion even for slow movers (e.g. Barnard 0.2″/week, sub-floor; but NEOs/comets obvious). The spectrum extraction itself must use moving forced photometry.

### H.2 Between FITS files (multi-epoch)
Identity (§G) → motion models in order of complexity: static → linear → 5-param astrometric → (SSO) Bernstein–Khushalani/Keplerian; choose by BIC with physical priors (rate ≫ 1″/hr ⇒ SSO family).

### H.3 Outputs per tracked object
Positions per epoch with σ and covariance; preferred model params with covariance; μ [mas/yr] or rate [″/hr]; PA [deg]; ϖ [mas] and d [pc] when significant; $v_t$; χ², dof, χ²ν, residuals (per epoch, in σ); classification probabilities + evidence; cross-validation verdict; identity confidence per epoch.

### H.4 Drawing
Measured positions + 1σ/2σ ellipses (eigen-decomposition of 2×2 covariance, semi-axes $\sqrt{\chi^2_2(p)\lambda_{1,2}}$ with $\chi^2_2=2.30$ (1σ-equiv 39%) / 6.18 (2σ 95.4%)); fitted path sampled densely (shows parallactic wiggle); predicted past/future (±N yr) with growing uncertainty band from $\mathbf J\,\mathrm{Cov}\,\mathbf J^T$.

---

## I. Planet X search mode (Requirement 4.6)

### I.1 Expected signature [A]
- Distant bound body, heliocentric distance $d$ [au]: orbital speed $v\approx29.8\,d^{-1/2}$ km/s.
- **Within a survey visit** (days), SPHEREx looks near quadrature ⇒ Earth's reflex motion is mostly along line of sight ⇒ rate $\lesssim148''\mathrm{h^{-1}}d^{-3/2}$; d = 500 au ⇒ ≲ 0.013″/h ≈ 0.3″/day ⇒ **quasi-static** (< 1 px per week).
- **Between surveys** (6 months, opposite sides of the Sun, baseline ≈ 2 au ⊥ line of sight) ⇒ parallactic jump $\Delta\theta\approx2/d$ rad $=412{,}530''/d$: 825″ (13.8′) at 500 au; 4125″ (69′) at 100 au; plus orbital drift ≈ 58″ per 6 months at 500 au. Direction ≈ along ecliptic longitude, computable from $\mathbf b(t)$.
- ⇒ Signature: a source present in survey k, absent at that position in surveys k±1 (difference "orphan"), and reappearing at the parallax-predicted offset for some $d$; surveys k and k+2 nearly coincide (shift = 1 yr orbital drift).

### I.2 Pipeline
1. **Per-survey deep co-add** (all frames, optionally all channels or a λ subset) of a sky tile (one array ≈ 3.5°).
2. **Static-sky removal**: subtract median of other surveys (or ZOGY against multi-survey template) ⇒ orphans; remove Gaia/CatWISE stars (with μ), galaxies (extended, present in all surveys), persistence (detector-coordinate check), ghosts, known SSOs (SkyBoT/MPC/Horizons at each epoch, V limit 24).
3. **Linking orphans** across surveys over hypothesis grid $(d,\dot\theta_x,\dot\theta_y)$: predicted offset $\Delta\boldsymbol\theta_{kl}(d,\mathbf v)=\mathrm{parallax}(\mathbf b_k,\mathbf b_l,d)+\mathbf v_\perp(t_l-t_k)/d$ (B&K model, §E.1); bound-orbit constraint $|\mathbf v|\le42.1\,d^{-1/2}$ km/s.
4. **Shift-and-stack** for sub-threshold bodies [E, KBMOD-style digital tracking]: for each hypothesis, sum S/N maps $\sum_k \mathrm{SNR}_k(\mathbf x+\Delta\boldsymbol\theta_k)\cdot\sqrt{w_k}$; grid in $\gamma=1/d$ uniform with step such that max mismatch < FWHM/2 over the arc ($\Delta\gamma\lesssim \mathrm{FWHM}/(2\cdot 2\,\mathrm{au})$ in rad ⇒ ~1.2×10⁻⁵ ⇒ ~80 steps for 70–2000 au), velocity grid similar. GPU optional (CuPy).
5. **Significance & FAR**: number of effective trials $N_{eff}$ (pixels × hypotheses, de-duplicated); threshold $z$ with $N_{eff}\Phi(-z)\le0.01$ (e.g. $N_{eff}=10^{9}$ ⇒ z ≈ 6.9); empirically calibrated with sign-flipped and time-scrambled stacks.
6. **Vetting**: per-survey stamps (must show a PSF-like source in each), consistency of λ-dependent flux (reflected solar ± CH₄ absorption at 1.6/2.2/3.3 µm for icy giants), not on artifact masks, not in persistence history, real-bogus ≥ 0.9.
7. **Orbit constraints** from limited arcs: B&K fit with MCMC over $(\alpha,\beta,\gamma,\dot\alpha,\dot\beta)$, $\dot\gamma$ prior 0 ± bound; report $d$ (σ_d/d ≈ σ_Δθ/Δθ), sky-plane velocity, families of $(a,e,i)$ compatible (sampled), and the explicit degeneracy: with 2–3 surveys over 1 yr, $a$ and $e$ are essentially unconstrained; $i$, Ω weakly.
8. **Known-body cross-check**: MPC (MPChecker), Horizons, SkyBoT, lists of TNOs (MPC distant objects), planets/dwarf planets.
9. **Injection–recovery** per run: inject synthetic distant bodies over magnitude × d grid ⇒ efficiency curve $\eta(m,d)$, stored with the run.

### I.3 Honest detection limits [A/S]
- Planet Nine predictions (Batygin & Brown) suggest V ≈ 19–24 at 400–800 au. SPHEREx per-survey all-channel co-add depth is roughly AB ~19–20 (5σ) [verify] and degraded by confusion in crowded fields (6.2″ pixels). ⇒ SPHEREx can test only the bright end (e.g. Neptune-size bodies at a few hundred au, or Mars/Earth-size at ≲ 100–200 au); faint P9 predictions are out of reach. **[S]**: an internally heated giant could be relatively bright in the 4–5 µm window (Neptune analogue) but this is model-dependent.
- UI must show: depth achieved, efficiency curve, expected false positives, and the banner "Candidates are not confirmed; follow-up and MPC reporting required."

---

## J. FITS comparison A vs B (Requirement 4.5)

1. Validate (A.1); report overlap, band, epoch, scale, rotation diffs.
2. Reproject B → A grid (flux-conserving, §A.4), PSF-match (Alard–Lupton or Gaussian-kernel when PSFs known), photometric scale κ from common unsaturated Gaia stars: $\kappa=\mathrm{median}(F_A/F_B)$ (robust, ≥ 10 stars).
3. Products: blink pair (common stretch), side-by-side, swipe, $D=A-\kappa B\otimes K$ with variance $V_D=V_A+\kappa^2V_{B\otimes K}$ and $S_{corr}$; sum $A+\kappa B$ (inverse-variance weighted); ratio $A/(\kappa B)$ masked where S/N < 3.
4. Header/metadata diff: per HDU, grouped (Time: DATE-OBS/MJD/EXPTIME; WCS: CRVAL/CD/SIP order; Instrument: array/band/λ; Calibration: BUNIT/zero point; Quality: flag fractions), unit-aware tolerance.
5. Source change lists (extract in A and B, match by §D.5 + motion allowance $\mu_{max}\Delta t$):
   - **appeared**: S/N ≥ 5 in B, forced flux in A < 3σ; **disappeared**: inverse;
   - **moved**: matched pair with offset > 3σ_comb and dipole test (§B.8) prefers $\mathcal M_M$; VIM-checked;
   - **changed brightness**: |Δm| > 5σ_comb with centroid unchanged (offset < 2σ).
6. Photometric difference table with Δm, σ, significance.

---

## K. Pseudocode (key algorithms)

```python
# --- Registration ---
def register_frame(frame, gaia, obs_bary, t):
    stars = select_ref(gaia, ruwe<1.4, 12<G<18, isolated=True)
    sky_t = propagate(stars, t, obs_bary)                  # μ, ϖ, observer parallax
    meas = psf_centroids(frame.image, frame.var, frame.psf, guess=frame.wcs.world_to_pixel(sky_t))
    good = meas.ok & ~frame.flags_at(meas)
    for _ in range(3):
        wcs = fit_wcs_from_points(meas.xy[good], sky_t[good],
                                  sip_degree=3 if good.sum() >= 100 else None)
        r = residuals(wcs, meas, sky_t); good &= abs(r) < 3*robust_std(r)
    wcs = apply_pixel_phase_correction(wcs, release_table)
    return wcs, qa(r[good], n=good.sum())
```

```python
# --- Detection (multi-epoch) ---
def detect(epoch_coadds):
    for e in epochs:
        img = coadd[e] - background(coadd[e]); cat[e] = sep_extract(img, var, thresh=1.5)
        cat[e] = psf_photometry(img, var, psf, cat[e])          # centroids + cov
    tmpl = median_coadd(all_epochs)
    for e in epochs:
        S = zogy(coadd[e], tmpl)                                # S_corr
        diff[e] = peaks(abs(S) >= z_cal)                        # empirically calibrated z
        diff[e] = real_bogus(diff[e]) >= 0.5
    suggestions = rank(dipoles(diff), high_pm_candidates(cat), red_movers(cat), sso_matches)
    return cat, diff, suggestions
```

```python
# --- Identity association ---
def associate(objects, epochs):
    for e in sorted(epochs, key=time):
        pred = [kalman_predict(o, e.t, e.obs_bary) for o in objects]
        C = full((len(objects), len(e.dets) + len(objects)), BIG)
        for k, p in enumerate(pred):
            for j in gate(e.dets, p, chi2=18.4):
                C[k, j] = -log(L_pos(j,p) * L_flux(j,k) * L_col(j,k) * L_shape(j,k) * L_id(j,k)
                               / L_clutter(j))
            C[k, len(e.dets)+k] = -log(1 - P_detect(objects[k], e))
        rows, cols = linear_sum_assignment(C)
        conf = murty_marginals(C, K=20)
        for k, j in zip(rows, cols):
            record(objects[k], e, j, conf[k, j], ambiguous=conf[k, j] < 0.9)
            if j < len(e.dets): kalman_update(objects[k], e.dets[j])
            else: forced_phot_upper_limit(objects[k], e)
    mht_resolve(ambiguous_clusters, M=10, nscan=2)
    refit_and_reassociate(objects, max_iter=3)
```

```python
# --- Motion fit ---
def fit_motion(meas, t0):
    A, y, W = [], [], []
    for m in meas:                                           # tangent-plane offsets
        Pa, Pd = parallax_factors(m.obs_bary, ref_ra, ref_dec)
        A += [[1,0,m.t-t0,0,Pa],[0,1,0,m.t-t0,Pd]]; y += [m.xi, m.eta]; W.append(inv(m.cov))
    W = block_diag(*W); A = array(A); y = array(y)
    models = {"static": [0,1], "linear": [0,1,2,3], "astrometric5": [0,1,2,3,4]}
    fits = {n: wls(A[:, c], y, W) for n, c in models.items()}
    best = min(fits, key=lambda n: fits[n].bic)              # ΔBIC rule, outlier loop inside wls
    return fits[best], fits
```

```python
# --- Classification ---
def classify(obj):
    L = {"sed": sed_likelihoods(obj.spectrum, TEMPLATES),
         "motion": motion_likelihoods(obj.fit, reduced_pm(obj)),
         "var": variability_likelihoods(obj.lightcurve),
         "morph": morphology_likelihoods(obj.shape),
         "xmatch": xmatch_likelihoods(obj.crossmatches)}
    logp = log_prior(obj.l, obj.b, obj.mag) + sum(w[m] * log(L[m]) for m in L)
    p = softmax(logp)
    return p, evidence(L, w), disagreements(L)
```

```python
# --- Planet X ---
def planetx(tile, surveys, d_grid, v_grid):
    co = {s: deep_coadd(tile, s) for s in surveys}
    orph = {s: remove_known(zogy(co[s], median([co[o] for o in surveys if o != s]))) for s in surveys}
    best = []
    for d in d_grid:                                         # uniform in 1/d
        for v in v_grid(d):                                  # bound-orbit constrained
            shifts = {s: bk_offset(d, v, surveys[s].t, surveys[s].obs_bary) for s in surveys}
            stack = sum(shift(snr_map(orph[s]), shifts[s]) * sqrt(w[s]) for s in surveys) / norm
            best += peaks(stack, z=z_threshold(n_eff))
    cands = vet(dedupe(best))                                # stamps, masks, persistence, real-bogus
    for c in cands: c.orbits = bk_mcmc(c); c.known = mpc_horizons_skybot(c)
    eff = injection_recovery(tile, surveys, d_grid)
    return cands, eff                                        # never 'confirmed'
```

---

## L. Parameter defaults (summary)
| Parameter | Default |
|---|---|
| Detection threshold (catalog) | 1.5σ pixel, 5σ source S/N for use |
| Difference threshold | |S_corr| ≥ 5 (empirically recalibrated per release) |
| Gate | χ²₂ = 18.4 |
| Ambiguity | P < 0.9 or ΔC < 2 |
| Outlier clip | 4σ, iterative |
| Systematic floor | 0.10″ (update from QA) |
| Flux systematic | 3% |
| Variability allowance | 0.1 mag (or learned amplitude) |
| Model selection | ΔBIC ≥ 6 to prefer more complex; mover ΔBIC ≥ 10 |
| Classification calibration | ECE < 0.05 |
