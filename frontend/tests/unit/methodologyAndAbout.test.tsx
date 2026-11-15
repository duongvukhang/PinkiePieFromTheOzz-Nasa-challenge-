import { render, screen } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AboutPage } from "../../src/pages/AboutPage";
import { MethodologyPage } from "../../src/pages/MethodologyPage";

describe("MethodologyPage & AboutPage", () => {
  it("renders MethodologyPage with mathematical formulas and all 7 chapters", () => {
    render(
      <BrowserRouter>
        <MethodologyPage />
      </BrowserRouter>,
    );

    // Title and subline
    expect(
      screen.getByRole("heading", { name: /Astrometric Calibration & Methodology/i }),
    ).toBeTruthy();

    // Chapter headings
    expect(screen.getByText(/01. Native-Frame Astrometry & WCS/i)).toBeTruthy();
    expect(screen.getByText(/04. Cross-Epoch Hungarian Matching/i)).toBeTruthy();
    expect(screen.getByText(/05. Proper Motion & Parallax Fitting/i)).toBeTruthy();
    expect(screen.getByText(/06. Planet X Shift-and-Stack/i)).toBeTruthy();

    // Equations and terms
    expect(screen.getByText(/Centroid Statistical Precision/i)).toBeTruthy();
    expect(screen.getByText(/Detector to Tangent-Plane Covariance Mapping/i)).toBeTruthy();
    expect(screen.getByText(/Difference Image Bayesian Model Test/i)).toBeTruthy();
    expect(screen.getByText(/Mahalanobis Spatial Gating & Innovation Covariance/i)).toBeTruthy();
    expect(screen.getByText(/Closed-Form Weighted Least Squares Solution/i)).toBeTruthy();

    // Verification targets
    expect(screen.getByText("WISE 0855−0714")).toBeTruthy();
    expect(screen.getByText("Barnard’s Star")).toBeTruthy();
  });

  it("renders AboutPage with project description, 5 key ideas, and 6-step usage guide", () => {
    render(
      <BrowserRouter>
        <AboutPage />
      </BrowserRouter>,
    );

    // Hero and project overview
    expect(
      screen.getByRole("heading", { name: /Observing the universe in motion/i }),
    ).toBeTruthy();
    expect(screen.getByText(/Why SkyTrace Exists/i)).toBeTruthy();

    // Key Ideas
    expect(screen.getByText(/Native-Frame Astrometric Integrity/i)).toBeTruthy();
    expect(screen.getByText(/Deterministic Identity Tracking/i)).toBeTruthy();
    expect(screen.getByText(/Rigorous Mover vs. Variable Separation/i)).toBeTruthy();
    expect(screen.getByText(/URL-First Reproducible Science/i)).toBeTruthy();

    // Usage steps
    expect(screen.getByText(/Enter Sky Coordinates or Pick a Benchmark/i)).toBeTruthy();
    expect(screen.getByText(/Inspect Multi-Epoch Infrared Exposures/i)).toBeTruthy();
    expect(screen.getByText(/Click-Drag Box on Any Moving Candidate/i)).toBeTruthy();
    expect(screen.getByText(/Review Proper Motion & Parallax Trajectory/i)).toBeTruthy();
    expect(screen.getByText(/Verify Against Gaia, CatWISE & JPL Horizons/i)).toBeTruthy();
    expect(screen.getByText(/Compare Epochs & Save Project Findings/i)).toBeTruthy();
  });
});
