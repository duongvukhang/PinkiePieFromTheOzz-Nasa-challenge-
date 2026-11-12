import { useEffect } from "react";
import { BrowserRouter, Link, Route, Routes, useLocation } from "react-router-dom";
import { Kicker } from "../design/components/Primitives";
import { AuthPage } from "../pages/AuthPage";
import { Home } from "../pages/Home";
import { AppShell } from "./layout/AppShell";

function StubPage({ kicker, title, body }: { kicker: string; title: string; body: string }) {
  return (
    <section className="section">
      <div
        className="container"
        style={{ display: "grid", gap: "var(--space-4)", maxWidth: "56rem" }}
      >
        <Kicker>{kicker}</Kicker>
        <h1 id="main-heading" className="chapter-title">
          {title}
        </h1>
        <p className="prose-lead">{body}</p>
        <div>
          <Link to="/" className="btn btn-secondary">
            ← Return to Journal Home
          </Link>
        </div>
      </div>
    </section>
  );
}

function DocumentTitleSync() {
  const { pathname } = useLocation();
  useEffect(() => {
    const titles: Record<string, string> = {
      "/": "SkyTrace — SPHEREx Moving-Object & Trajectory Platform",
      "/login": "Sign in | SkyTrace",
      "/signin": "Create account | SkyTrace",
      "/signup": "Create account | SkyTrace",
      "/v": "Observatory Viewer | SkyTrace",
      "/methodology": "Methodology & Limits | SkyTrace",
      "/examples": "Calibration Targets | SkyTrace",
      "/docs": "Documentation & API | SkyTrace",
      "/credits": "Data Sources & Credits | SkyTrace",
    };
    document.title = titles[pathname] ?? "SkyTrace";
  }, [pathname]);
  return null;
}

const STUB_ROUTES = [
  {
    path: "/v",
    kicker: "Observatory Workspace",
    title: "WebGL2 & Aladin Lite Viewer (Next Task)",
    body: "The full-bleed dark scientific viewer (/v) connects next to the already-verified SPHEREx backend endpoints and astrometric science core.",
  },
  {
    path: "/methodology",
    kicker: "Methodology",
    title: "Astrometric Calibration & Uncertainty",
    body: "Detailed chaptered documentation covering native-frame astrometry, Hungarian identity matching, proper motion vs. parallax fitting, and noise rejection.",
  },
  {
    path: "/examples",
    kicker: "Validation",
    title: "Five Calibration Targets",
    body: "Preset fields for WISE 0855, Barnard's Star, ε Indi B, RW Aurigae, and R Draconis.",
  },
  {
    path: "/docs",
    kicker: "Documentation",
    title: "OpenAPI, Python & Embed Parameters",
    body: "URL query schema, /embed iframe parameters, and REST API documentation.",
  },
  {
    path: "/credits",
    kicker: "Provenance",
    title: "Archive Credits & Software Acknowledgements",
    body: "NASA / IPAC IRSA, SPHEREx, Gaia DR3, CatWISE2020, Astropy, photutils, SEP, and Aladin Lite.",
  },
  {
    path: "*",
    kicker: "404",
    title: "This patch of sky is empty.",
    body: "The requested page could not be found.",
  },
] as const;

export function AppRoutes() {
  return (
    <BrowserRouter>
      <DocumentTitleSync />
      <AppShell>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<AuthPage defaultMode="login" />} />
          <Route path="/signin" element={<AuthPage defaultMode="signin" />} />
          <Route path="/signup" element={<AuthPage defaultMode="signin" />} />
          {STUB_ROUTES.map(({ path, ...props }) => (
            <Route key={path} path={path} element={<StubPage {...props} />} />
          ))}
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
