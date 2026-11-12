import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function MetaPill({ tag, date }: { tag: string; date?: string }) {
  return (
    <div className="meta-row">
      <span className="meta-dots" aria-hidden="true">
        ···
      </span>
      <span className="meta-tag">{tag}</span>
      {date && <span className="meta-date">{date}</span>}
    </div>
  );
}

export function NewYorkerHeading({
  title,
  subtitle,
  as = "h2",
}: {
  title: string;
  subtitle?: string;
  as?: "h1" | "h2";
}) {
  const Tag = as;
  return (
    <header className="ny-header">
      <div className="ny-rule-row">
        <Tag className="ny-title">{title}</Tag>
      </div>
      {subtitle && <p className="ny-subtitle">{subtitle}</p>}
      <svg
        className="ny-vignette"
        viewBox="0 0 180 28"
        width="180"
        height="28"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.15"
        aria-hidden="true"
      >
        <path d="M12 22c28-5 54-7 78-7s50 2 78 7" opacity="0.65" />
        <path d="M90 3v18M80 14h20" />
        <circle cx="90" cy="14" r="7.5" />
        <circle cx="90" cy="14" r="1.8" fill="currentColor" stroke="none" />
        <path d="M46 19l4-4M134 19l-4-4M66 17l2-3M114 17l-2-3" opacity="0.55" />
      </svg>
    </header>
  );
}

export function ArchivalCard({
  tag,
  date,
  image,
  alt,
  provenance,
  title,
  description,
  to,
}: {
  tag: string;
  date: string;
  image: string;
  alt: string;
  provenance: string;
  title: string;
  description: string;
  to: string;
}) {
  return (
    <article className="archival-card">
      <MetaPill tag={tag} date={date} />
      <Link to={to} className="archival-media-link" aria-label={title}>
        <img src={image} alt={alt} className="archival-img" loading="lazy" />
      </Link>
      <span className="provenance-note">{provenance}</span>
      <h3 className="archival-title">
        <Link to={to}>{title}</Link>
      </h3>
      <p className="archival-desc">{description}</p>
    </article>
  );
}

export function OatCard({
  tag,
  date,
  title,
  description,
  rows,
  ctaLabel,
  to,
}: {
  tag: string;
  date: string;
  title: string;
  description: string;
  rows: ReadonlyArray<readonly [string, string]>;
  ctaLabel: string;
  to: string;
}) {
  return (
    <article className="oat-card">
      <div>
        <MetaPill tag={tag} date={date} />
        <h3 className="oat-title">{title}</h3>
        <p className="oat-desc">{description}</p>
      </div>
      <footer>
        <dl className="oat-rows">
          {rows.map(([k, v]) => (
            <div key={k} className="oat-row">
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
        <Link to={to} className="btn btn-stone">
          {ctaLabel} →
        </Link>
      </footer>
    </article>
  );
}

export function EpochPager({
  index,
  total,
  unit,
  onChange,
  meta,
}: {
  index: number;
  total: number;
  unit: string;
  onChange: (nextIndex: number) => void;
  meta?: string;
}) {
  return (
    <div className="pager-row">
      <button
        type="button"
        className="btn-icon-sq"
        aria-label={`Previous ${unit}`}
        onClick={() => onChange((index + total - 1) % total)}
      >
        ←
      </button>
      <span className="pager-count" aria-live="polite">
        0{index + 1} / 0{total}
      </span>
      <button
        type="button"
        className="btn-icon-sq"
        aria-label={`Next ${unit}`}
        onClick={() => onChange((index + 1) % total)}
      >
        →
      </button>
      {meta && (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "var(--text-xs)",
            color: "var(--ink-2)",
          }}
        >
          {meta}
        </span>
      )}
    </div>
  );
}

export function Callout({ title, children }: { title: string; children: ReactNode }) {
  return (
    <aside className="callout" aria-label={title}>
      <strong>{title}</strong>
      <p>{children}</p>
    </aside>
  );
}
