import { useEffect } from "react";
import { BrowserRouter, Link, Route, Routes, useLocation } from "react-router-dom";
import { AboutPage } from "../pages/AboutPage";
import { AuthPage } from "../pages/AuthPage";
import { Home } from "../pages/Home";
import { LogoutPage } from "../pages/LogoutPage";
import { AppShell } from "./layout/AppShell";

function StubPage({ kicker, title, body }: { kicker: string; title: string; body: string }) {
  return (
    <section className="section">
      <div
        className="container"
        style={{ display: "grid", gap: "var(--space-4)", maxWidth: "48rem" }}
      >
        <span className="drawer-kicker">{kicker}</span>
        <h1 id="main-heading" style={{ fontSize: "var(--text-3xl)" }}>
          {title}
        </h1>
        <p
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "var(--text-lg)",
            color: "var(--ink-1)",
          }}
        >
          {body}
        </p>
        <div>
          <Link to="/" className="nav-pill">
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
      "/": "SkyTrace — Observational Journal & SPHEREx Trajectory Archive",
      "/about": "About Us | SkyTrace",
      "/company": "About Us | SkyTrace",
      "/login": "Log in | SkyTrace",
      "/signin": "Create account | SkyTrace",
      "/signup": "Create account | SkyTrace",
      "/logout": "Log out | SkyTrace",
      "/v": "Observatory Viewer | SkyTrace",
      "/methodology": "Methodology & Limits | SkyTrace",
      "/credits": "Archive Credits | SkyTrace",
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
    path: "/credits",
    kicker: "Provenance",
    title: "Archive Credits & Acknowledgements",
    body: "NASA / IPAC IRSA, SPHEREx, Gaia DR3, CatWISE2020, Astropy, photutils, SEP, Aladin Lite, and archival celestial atlas engravings via Wikimedia Commons & NASA Image Library.",
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
          <Route path="/about" element={<AboutPage />} />
          <Route path="/company" element={<AboutPage />} />
          <Route path="/login" element={<AuthPage defaultMode="login" />} />
          <Route path="/signin" element={<AuthPage defaultMode="signin" />} />
          <Route path="/signup" element={<AuthPage defaultMode="signin" />} />
          <Route path="/logout" element={<LogoutPage />} />
          {STUB_ROUTES.map(({ path, ...props }) => (
            <Route key={path} path={path} element={<StubPage {...props} />} />
          ))}
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
