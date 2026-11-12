import { useEffect, useRef } from "react";
import { Link, useLocation } from "react-router-dom";
import { Kicker } from "../../design/components/Primitives";

export const PRIMARY_NAV = [
  { label: "Journal", to: "/" },
  { label: "Methodology", to: "/methodology" },
  { label: "Examples", to: "/examples" },
  { label: "Observatory", to: "/v" },
  { label: "Docs", to: "/docs" },
] as const;

export const MANDATORY_TARGETS = [
  { name: "WISE 0855", note: "Y dwarf · ~8″/yr", to: "/v?target=WISE%200855" },
  { name: "Barnard's Star", note: "10.3″/yr M dwarf", to: "/v?target=Barnard%27s%20Star" },
  { name: "ε Indi B", note: "T dwarf binary · 4.7″/yr", to: "/v?target=eps%20Ind%20B" },
  { name: "RW Aurigae", note: "Variable · μ ≈ 0", to: "/v?target=RW%20Aurigae" },
  { name: "R Draconis", note: "Mira variable · μ ≈ 0", to: "/v?target=R%20Draconis" },
] as const;

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { pathname } = useLocation();
  const linkProps = (to: string) => ({
    to,
    className: "sidebar-link",
    "aria-current": pathname === to ? ("page" as const) : undefined,
    onClick: onNavigate,
  });

  return (
    <nav aria-label="Observatory index">
      <div className="sidebar-group">
        <Kicker>Navigation</Kicker>
        <ul className="sidebar-list">
          {PRIMARY_NAV.map((item) => (
            <li key={item.to}>
              <Link {...linkProps(item.to)}>{item.label}</Link>
            </li>
          ))}
        </ul>
      </div>

      <div className="sidebar-group">
        <Kicker>Calibration Targets</Kicker>
        <ul className="sidebar-list">
          {MANDATORY_TARGETS.map((t) => (
            <li key={t.name}>
              <Link className="sidebar-link" to={t.to} onClick={onNavigate}>
                <span>{t.name}</span>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "0.7rem",
                    color: "var(--ink-2)",
                  }}
                >
                  {t.note.split("·")[0]}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </div>

      <div className="sidebar-group">
        <Kicker>Account</Kicker>
        <ul className="sidebar-list">
          <li>
            <Link {...linkProps("/login")}>Sign in</Link>
          </li>
          <li>
            <Link {...linkProps("/signin")}>Create account</Link>
          </li>
        </ul>
      </div>
    </nav>
  );
}

export function Sidebar({
  desktopOpen,
  drawerOpen,
  onCloseDrawer,
}: {
  desktopOpen: boolean;
  drawerOpen: boolean;
  onCloseDrawer: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (drawerOpen && !dialog.open) dialog.showModal();
    else if (!drawerOpen && dialog.open) dialog.close();
  }, [drawerOpen]);

  return (
    <>
      <aside
        id="skytrace-sidebar"
        className="sidebar-rail"
        aria-label="Observatory index"
        hidden={!desktopOpen}
      >
        <SidebarContent />
      </aside>
      <dialog
        ref={dialogRef}
        id="skytrace-drawer"
        className="drawer-dialog"
        aria-label="Site navigation drawer"
        onClose={onCloseDrawer}
        onClick={(e) => {
          if (e.target === dialogRef.current) onCloseDrawer();
        }}
      >
        <div className="drawer-sheet">
          <div className="drawer-top">
            <span className="wordmark" style={{ fontSize: "1.2rem" }}>
              SkyTrace
            </span>
            <button
              type="button"
              className="btn btn-secondary"
              autoFocus
              aria-label="Close navigation menu"
              onClick={onCloseDrawer}
            >
              Close
            </button>
          </div>
          <SidebarContent onNavigate={onCloseDrawer} />
        </div>
      </dialog>
    </>
  );
}
