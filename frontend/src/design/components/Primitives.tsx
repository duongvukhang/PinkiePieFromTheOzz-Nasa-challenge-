import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function Kicker({ children }: { children: ReactNode }) {
  return <span className="kicker">{children}</span>;
}

export function Badge({ children, warn = false }: { children: ReactNode; warn?: boolean }) {
  return (
    <span className={warn ? "badge badge-warn" : "badge"}>
      {warn && <span aria-hidden="true">⚠</span>}
      {children}
    </span>
  );
}

export function Callout({ title, children }: { title: string; children: ReactNode }) {
  return (
    <aside className="callout" aria-label={title}>
      <strong style={{ display: "block", marginBottom: 4 }}>{title}</strong>
      {children}
    </aside>
  );
}

export function CircleLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link className="circle-link" to={to}>
      <span className="circle-glyph" aria-hidden="true">
        →
      </span>
      <span>{children}</span>
    </Link>
  );
}

export function GalleryMat({
  caption,
  provenance,
  children,
}: {
  caption: string;
  provenance: string;
  children: ReactNode;
}) {
  return (
    <figure className="gallery-mat">
      {children}
      <figcaption>
        <span>{caption}</span>
        <span>{provenance}</span>
      </figcaption>
    </figure>
  );
}

export function SpotMark({ kind }: { kind: "astrolabe" | "plate" | "blink" | "cryo" }) {
  const paths: Record<typeof kind, ReactNode> = {
    astrolabe: (
      <>
        <circle cx="24" cy="24" r="16" />
        <path d="M24 4v40M4 24h40M13 35L35 13" />
      </>
    ),
    plate: (
      <>
        <rect x="6" y="6" width="36" height="36" />
        <path d="M6 24h36M24 6v36" />
        <circle cx="30" cy="18" r="2.5" fill="currentColor" />
      </>
    ),
    blink: (
      <>
        <circle cx="18" cy="24" r="11" />
        <circle cx="30" cy="24" r="11" strokeDasharray="3 3" />
        <path d="M18 24h12" />
      </>
    ),
    cryo: (
      <>
        <path d="M10 38L16 12h16l6 26z" />
        <path d="M16 12c4-5 12-5 16 0M24 18v14" />
      </>
    ),
  };
  return (
    <svg
      width="40"
      height="40"
      viewBox="0 0 48 48"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      aria-hidden="true"
    >
      {paths[kind]}
    </svg>
  );
}
