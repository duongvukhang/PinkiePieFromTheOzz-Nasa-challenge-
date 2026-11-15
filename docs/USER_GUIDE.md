# SPHEREx Moving-Object & Trajectory Platform — User Guide

## 1. Quick Tour & Primary Controls

### 1.1 Sky Navigation & Viewport
- **Pan**: Click and drag on the focal plane viewport with the mouse, or use arrow keys.
- **Zoom**: Scroll mouse wheel over the viewport, or use the `+` / `−` zoom buttons in the bottom-right corner.
- **Reset / Fit to Screen**: Double-click anywhere on the focal plane, or click the `⟲` button in the zoom controls.
- **Focal Plane HUD**: Real-time bottom-left overlay displays cursor pixel coordinates ($X, Y$), celestial ICRS coordinates ($\mathrm{RA}, \mathrm{Dec}$ in degrees), and SPHEREx detector wavelength ($\lambda$ in $\mu\mathrm{m}$).

### 1.2 Multi-Epoch Timeline & Blink Controls
- **Blink Playback**: Press **Play** / **Pause** or hit Spacebar to blink through epochs sequentially at adjustable frame rates (0.5 to 10 FPS).
- **Step Frames**: Use `◀` / `▶` buttons or Left/Right arrow keys to step forward/backward through loaded sky epochs.
- **Epoch Pills**: Bottom timeline displays each loaded epoch with its observation date (UTC), time delta $\Delta t$ relative to baseline epoch $t_0$, and Julian Date (MJD). Click any pill to jump to that epoch; click the `✕` badge to remove an epoch from the viewer.
- **Add Epoch**: Click `+ Add FITS` in the timeline to load additional custom FITS observations into the active stack.

### 1.3 Detection & Track Selection
- **Detected Objects**: All detected astronomical sources are highlighted with green bounding squares sized to their fitted FWHM.
- **Track Persistent Targets**:
  - Click on any green bounding square to assign it a persistent track identity (**Track A** in cyan, **Track B** in orange).
  - Alternatively, use the **Box Select** tool (drag a rectangle or Shift+Arrow keys) to designate an area of interest.
  - Chosen objects retain their identity across all epochs, with trajectory points and proper motion vectors projected on the sky.
- **Inspector Panel**: Switch to the **Inspector** tab to view astrometric motion fits, proper motion ($\mu_\alpha \cos\delta, \mu_\delta$), parallax estimates, SED spectral curves across SPHEREx bands, and photometric light curves.

---

## 2. Modes of Operation

### 2.1 Blink Mode (Default)
Sequentially cycles through aligned FITS images to visually detect moving solar system objects, high proper-motion stars, and transient phenomena against fixed background stars.

### 2.2 Compare Mode (A-vs-B Analysis)
Allows pixel-level mathematical operations between Epoch A and Epoch B:
- **Difference (`diff`)**: Highlights motion and flux variability while cancelling static background stars.
- **Ratio (`ratio`)**: Identifies color/spectral temperature variations.
- **Swipe (`swipe`)**: Interactive split-screen curtain to wipe between Epoch A and Epoch B.

### 2.3 Planet X Search Mode
Shift-and-stack trajectory search across multi-epoch frames to uncover faint, slow-moving outer solar system bodies below single-frame detection thresholds.

---

## 3. NASA Archive Integration & FITS Uploads

### 3.1 Searching NASA Archives
1. Click **🔍 Search NASA Archives** in the top navigation bar.
2. Enter target coordinates ($\mathrm{RA}, \mathrm{Dec}$) or target name (e.g. `WISE 0855-0714`).
3. Select cone search radius (arcsec, arcmin, or deg) and observation date range.
4. Click **Search IRSA / SPHEREx** to query IPAC IRSA ObsCore and load all matching calibrated cutouts directly into the viewer.

### 3.2 Uploading Custom FITS Files
1. Click **📁 Upload FITS** in the top toolbar or `+ Add FITS` in the timeline.
2. Drag and drop a FITS image (`.fits`, `.fit`, `.fits.gz`, `.fz`) up to 200 MB.
3. If the header lacks `DATE-OBS`, optionally provide a manual observation epoch or MJD.
4. The system validates celestial WCS plate solutions (TAN, TAN-SIP), checks coordinate alignment with existing frames, and automatically generates WebP display images and float32 science arrays.

---

## 4. Benchmark Validation Targets

Use the top preset chips to jump directly to mandatory astronomical test cases:

| Target | Coordinates (J2000) | Type | Key Scientific Verification |
|---|---|---|---|
| **Barnard's Star** | RA 17h57m48.5s, Dec +04°41′36″ | High Proper Motion Star | $\mu \approx 10.3\,\text{arcsec/yr}$, rapid positional shift across epochs. |
| **WISE 0855** | RA 08h55m10.83s, Dec −07°14′42.5″ | Cold Sub-stellar Brown Dwarf | High proper motion brown dwarf benchmark; detected in mid-IR SPHEREx bands D4–D6. |
| **Epsilon Indi B** | RA 22h04m10.5s, Dec −56°46′58″ | Nearby Brown Dwarf Binary | Distinct proper motion and binary orbital motion. |
| **RW Aurigae** | RA 05h07m49.5s, Dec +30°24′05″ | Variable T Tauri Star | Photometric flux variation without astrometric positional shift. |
| **R Draconis** | RA 16h32m40.2s, Dec +66°45′18″ | Mira Variable | Pulsational brightness changes; coordinates remain fixed. |
