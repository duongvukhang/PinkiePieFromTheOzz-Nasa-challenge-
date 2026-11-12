import { useEffect, useRef } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuthStore } from "../authStore";

export const PRIMARY_NAV = [
  { label: "Journal", to: "/" },
  { label: "About", to: "/about" },
  { label: "Methodology", to: "/methodology" },
  { label: "Observatory", to: "/v" },
] as const;

export const MANDATORY_TARGETS = [
  { name: "WISE 0855", to: "/v?target=WISE%200855" },
  { name: "Barnard's Star", to: "/v?target=Barnard%27s%20Star" },
  { name: "ε Indi B", to: "/v?target=eps%20Ind%20B" },
  { name: "RW Aurigae", to: "/v?target=RW%20Aurigae" },
  { name: "R Draconis", to: "/v?target=R%20Draconis" },
] as const;

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { pathname } = useLocation();
  const user = useAuthStore((s) => s.user);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  const linkProps = (to: string) => ({
    to,
    className: "drawer-link",
    "aria-current": pathname === to ? ("page" as const) : undefined,
    onClick: onClose,
  });

  return (
    <dialog
      ref={dialogRef}
      id="mobile-nav-drawer"
      className="drawer-dialog"
      aria-label="Mobile site navigation"
      onClose={onClose}
      onClick={(e) => {
        if (e.target === dialogRef.current) onClose();
      }}
    >
      <div className="drawer-sheet">
        <div className="drawer-top">
          <span className="masthead" style={{ fontSize: "1.15rem" }}>
            SkyTrace
          </span>
          <button
            type="button"
            className="btn btn-icon-sq"
            autoFocus
            aria-label="Close navigation menu"
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <nav aria-label="Mobile menu">
          <div className="drawer-group">
            <span className="drawer-kicker">Pages</span>
            <ul className="drawer-links">
              {PRIMARY_NAV.map((item) => (
                <li key={item.to}>
                  <Link {...linkProps(item.to)}>{item.label}</Link>
                </li>
              ))}
            </ul>
          </div>

          <div className="drawer-group">
            <span className="drawer-kicker">Calibration Fields</span>
            <ul className="drawer-links">
              {MANDATORY_TARGETS.map((t) => (
                <li key={t.name}>
                  <Link className="drawer-link" to={t.to} onClick={onClose}>
                    <span>{t.name}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>

          <div className="drawer-group">
            <span className="drawer-kicker">Researcher Access</span>
            <ul className="drawer-links">
              {user ? (
                <>
                  <li className="drawer-link" style={{ color: "var(--ink-2)" }}>
                    Signed in as {user.name}
                  </li>
                  <li>
                    <Link {...linkProps("/logout")}>Log out</Link>
                  </li>
                </>
              ) : (
                <>
                  <li>
                    <Link {...linkProps("/login")}>Log in</Link>
                  </li>
                  <li>
                    <Link {...linkProps("/signin")}>Create account</Link>
                  </li>
                </>
              )}
            </ul>
          </div>
        </nav>
      </div>
    </dialog>
  );
}
