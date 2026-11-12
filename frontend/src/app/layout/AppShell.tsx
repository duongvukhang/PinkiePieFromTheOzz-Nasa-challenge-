import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { Kicker } from "../../design/components/Primitives";
import { PRIMARY_NAV, Sidebar } from "./Sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const [isDesktop, setIsDesktop] = useState(
    () => typeof window !== "undefined" && window.matchMedia?.("(min-width: 1024px)").matches,
  );
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(true);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia?.("(min-width: 1024px)");
    if (!mq) return;
    const onChange = () => {
      setIsDesktop(mq.matches);
      if (mq.matches) setMobileDrawerOpen(false);
    };
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    setMobileDrawerOpen(false);
  }, [pathname]);

  const toggleMenu = () => {
    if (isDesktop) setDesktopSidebarOpen((v) => !v);
    else setMobileDrawerOpen((v) => !v);
  };

  const isMenuExpanded = isDesktop ? desktopSidebarOpen : mobileDrawerOpen;

  return (
    <div className="app-shell">
      <a className="skip-link visually-hidden" href="#main-content">
        Skip to main content
      </a>

      <header className="site-header">
        <div className="container header-row">
          <div className="header-left">
            <button
              type="button"
              className="btn btn-icon"
              aria-label="Toggle navigation sidebar"
              aria-expanded={isMenuExpanded}
              aria-controls={isDesktop ? "skytrace-sidebar" : "skytrace-drawer"}
              onClick={toggleMenu}
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 18 18"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                aria-hidden="true"
              >
                <path d="M2 4.5h14M2 9h14M2 13.5h14" />
              </svg>
            </button>
            <Link className="wordmark" to="/">
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                aria-hidden="true"
              >
                <circle cx="12" cy="12" r="9" />
                <path d="M12 2v5M12 17v5M2 12h5M17 12h5" />
                <circle cx="14.5" cy="9.5" r="1.5" fill="var(--accent-terracotta)" stroke="none" />
              </svg>
              <span>SkyTrace</span>
            </Link>
          </div>

          <nav className="primary-nav" aria-label="Primary">
            <ul>
              {PRIMARY_NAV.map((item) => (
                <li key={item.to}>
                  <Link
                    to={item.to}
                    className="nav-link"
                    aria-current={pathname === item.to ? "page" : undefined}
                  >
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>

          <div className="header-actions">
            <Link
              to="/login"
              className="btn btn-ghost"
              aria-current={pathname === "/login" ? "page" : undefined}
            >
              Sign in
            </Link>
            <Link
              to="/signin"
              className="btn btn-primary"
              aria-current={pathname === "/signin" || pathname === "/signup" ? "page" : undefined}
            >
              Create account
            </Link>
          </div>
        </div>
      </header>

      <div className={`app-body${desktopSidebarOpen ? " has-sidebar" : ""}`}>
        <Sidebar
          desktopOpen={desktopSidebarOpen}
          drawerOpen={mobileDrawerOpen}
          onCloseDrawer={() => setMobileDrawerOpen(false)}
        />
        <main id="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>

      <footer className="site-footer">
        <div className="container">
          <div className="footer-grid">
            <div>
              <span className="wordmark" style={{ color: "var(--sky-text)" }}>
                SkyTrace
              </span>
              <p
                style={{
                  marginTop: "var(--space-3)",
                  fontSize: "var(--text-sm)",
                  color: "#c8cdd6",
                  maxWidth: "38ch",
                }}
              >
                An independent astronomical journal and SPHEREx multi-epoch astrometric observatory.
                Every position carries uncertainty; every candidate remains unconfirmed until
                independently verified.
              </p>
            </div>
            <div>
              <Kicker>Observatory</Kicker>
              <ul className="footer-links">
                <li>
                  <Link to="/">Journal Home</Link>
                </li>
                <li>
                  <Link to="/methodology">Methodology &amp; Limits</Link>
                </li>
                <li>
                  <Link to="/examples">Five Calibration Targets</Link>
                </li>
                <li>
                  <Link to="/v">Launch Sky Viewer</Link>
                </li>
              </ul>
            </div>
            <div>
              <Kicker>Access &amp; Provenance</Kicker>
              <ul className="footer-links">
                <li>
                  <Link to="/login">Sign in</Link>
                </li>
                <li>
                  <Link to="/signin">Create account</Link>
                </li>
                <li>
                  <Link to="/docs">API &amp; Embed Guide</Link>
                </li>
                <li>
                  <Link to="/credits">IRSA / SPHEREx Credits</Link>
                </li>
              </ul>
            </div>
          </div>
          <div className="footer-meta">
            <span>DATA: NASA / IPAC IRSA · SPHEREX · GAIA DR3 · CATWISE2020</span>
            <span>FRAME: ICRS J2016.0 · TIME: MJD / TDB · SOFTWARE: ASTROPY · ALADIN LITE</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
