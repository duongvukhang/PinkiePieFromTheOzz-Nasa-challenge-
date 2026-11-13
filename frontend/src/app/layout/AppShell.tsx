import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuthStore } from "../authStore";
import { PRIMARY_NAV, Sidebar } from "./Sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const user = useAuthStore((s) => s.user);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  const isObservatory = pathname === "/v" || pathname.startsWith("/v/");

  useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const mq = window.matchMedia?.("(min-width: 1024px)");
    if (!mq) return;
    const closeOnDesktop = () => {
      if (mq.matches) setDrawerOpen(false);
    };
    mq.addEventListener("change", closeOnDesktop);
    return () => mq.removeEventListener("change", closeOnDesktop);
  }, []);

  return (
    <div className={`app-shell${isObservatory ? " is-observatory" : ""}`}>
      <a className="skip-link visually-hidden" href="#main-content">
        Skip to main content
      </a>

      {!isObservatory && (
        <header className={`site-header${scrolled ? " is-scrolled" : ""}`}>
          <div className="container header-inner">
            <nav className="primary-pills" aria-label="Primary">
              {PRIMARY_NAV.map((item) => (
                <Link
                  key={item.to}
                  to={item.to}
                  className="nav-pill"
                  aria-current={pathname === item.to ? "page" : undefined}
                >
                  {item.label}
                </Link>
              ))}
            </nav>

            <Link className="masthead" to="/">
              SkyTrace
            </Link>

            <div className="header-right">
              {user ? (
                <>
                  <span className="user-chip">{user.name}</span>
                  <Link
                    to="/logout"
                    className="nav-pill"
                    aria-current={pathname === "/logout" ? "page" : undefined}
                  >
                    Log out
                  </Link>
                </>
              ) : (
                <>
                  <Link
                    to="/login"
                    className="nav-pill desktop-auth-secondary"
                    aria-current={pathname === "/login" ? "page" : undefined}
                  >
                    Log in
                  </Link>
                  <Link
                    to="/signin"
                    className="btn btn-dark"
                    aria-current={
                      pathname === "/signin" || pathname === "/signup" ? "page" : undefined
                    }
                  >
                    Create account
                  </Link>
                </>
              )}

              <button
                type="button"
                className="btn-icon-sq mobile-only"
                aria-label="Open mobile navigation"
                aria-expanded={drawerOpen}
                aria-controls="mobile-nav-drawer"
                onClick={() => setDrawerOpen(true)}
              >
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 18 18"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  aria-hidden="true"
                >
                  <path d="M2 4.5h14M2 9h14M2 13.5h14" />
                </svg>
              </button>
            </div>
          </div>
        </header>
      )}

      {!isObservatory && <Sidebar open={drawerOpen} onClose={() => setDrawerOpen(false)} />}

      <main id="main-content" tabIndex={-1}>
        {children}
      </main>

      {!isObservatory && (
        <footer className="site-footer">
          <div className="container">
            <div className="footer-grid">
              <div>
                <span className="masthead" style={{ color: "var(--sky-text)" }}>
                  SkyTrace
                </span>
                <p
                  style={{
                    marginTop: "var(--space-3)",
                    fontSize: "var(--text-sm)",
                    color: "#c8c5bc",
                    maxWidth: "38ch",
                  }}
                >
                  An independent astronomical journal and SPHEREx multi-epoch observatory. Every
                  measurement carries uncertainty; every candidate stays open to doubt.
                </p>
              </div>
              <div>
                <span className="drawer-kicker" style={{ color: "var(--sky-muted)" }}>
                  Journal
                </span>
                <ul className="footer-links">
                  <li>
                    <Link to="/">Home</Link>
                  </li>
                  <li>
                    <Link to="/about">About Us</Link>
                  </li>
                  <li>
                    <Link to="/methodology">Methodology &amp; Limits</Link>
                  </li>
                  <li>
                    <Link to="/v">Sky Viewer</Link>
                  </li>
                </ul>
              </div>
              <div>
                <span className="drawer-kicker" style={{ color: "var(--sky-muted)" }}>
                  Account &amp; Sources
                </span>
                <ul className="footer-links">
                  {user ? (
                    <li>
                      <Link to="/logout">Log out ({user.name})</Link>
                    </li>
                  ) : (
                    <>
                      <li>
                        <Link to="/login">Log in</Link>
                      </li>
                      <li>
                        <Link to="/signin">Create account</Link>
                      </li>
                    </>
                  )}
                  <li>
                    <Link to="/credits">Archive Credits</Link>
                  </li>
                </ul>
              </div>
            </div>
            <div className="footer-meta">
              <span>DATA: NASA / IPAC IRSA · SPHEREX · GAIA DR3 · CATWISE2020</span>
              <span>FRAME: ICRS J2016.0 · TIME: MJD / TDB</span>
            </div>
          </div>
        </footer>
      )}
    </div>
  );
}
