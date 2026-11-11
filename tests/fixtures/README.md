# Test Fixtures Placeholder

Tiny synthetic FITS cutouts (`<= 64x64` pixels), header-only real archive samples, and recorded provider HTTP cassettes live here so `sphx_core` and backend tests execute offline and deterministically without loading large FITS files into context.

## Planned Fixtures (Stage 0 T0.3)

1. **Moving-object synthetic multi-epoch sequences (`<= 64x64` px)**
   - ICRS `TAN` WCS headers, `MJD-OBS` / `DATE-OBS`, variance/mask extensions, synthetic Gaia-like reference stars, and injected proper-motion + parallax targets matching `tests/science/reference_values.json` (WISE 0855-0714, Barnard's Star, Epsilon Indi B).
2. **Stationary variable-star sequences (`<= 64x64` px)**
   - Constant astrometric centroid with flux modulation across epochs (RW Aurigae, R Draconis) to verify variable stars are never misclassified as movers.
3. **Edge-case & artifact fixtures**
   - Missing celestial WCS, missing `DATE-OBS`, rotated/rescaled frames requiring reprojection, zero-overlap frames, blended/crowded PSFs, cosmic-ray/persistence bitmasks, and single-frame streaks.
4. **Header-only archive samples & HTTP cassettes (`cassettes/`)**
   - Header-only real samples (no pixel arrays) and recorded TAP/SIA/catalog JSON/VOTable responses for IRSA SPHEREx, Gaia DR3, WISE/CatWISE, and Sesame.
   - Note: SPHEREx Level-2 MEF extension names, flag bits, and collection IDs remain marked `[verify]` until checked against official IRSA documentation.

## Rules

- Keep every synthetic FITS image `<= 64x64` pixels; commit generator seed/parameters with fixtures.
- Never paste raw FITS arrays into prompts or docs; inspect header summaries only (`Agents.md` §6).
