import type { CutoutFrame } from "../../api/client";
import { decodeFlagBits, FLAG_BITS } from "../aladin/wcs";

interface FlagsMetaPanelProps {
  frame?: CutoutFrame;
  release: string;
  warnings: string[];
  activeMask?: number;
}

export function FlagsMetaPanel({ frame, release, warnings, activeMask = 0 }: FlagsMetaPanelProps) {
  const activeNames = new Set(decodeFlagBits(activeMask));
  const metaRows: Array<[string, string]> = frame
    ? [
        ["Release", release],
        ["Epoch", frame.epoch],
        ["UTC Mid", frame.utc_mid],
        ["MJD TDB", frame.mjd_tdb_mid.toFixed(4)],
        ["Array / Band", frame.band_label ?? (frame.array_id ? `Arr ${frame.array_id}` : "Co-add")],
        ["Flagged Fraction", `${(frame.flagged_fraction * 100).toFixed(2)}%`],
      ]
    : [
        ["Release", release],
        ["Frame", "Awaiting cutout metadata…"],
      ];

  return (
    <div className="meta-panel">
      <dl className="viewer-dl">
        {metaRows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>

      {warnings.length > 0 && (
        <aside className="viewer-warn" aria-label="Science warnings">
          {warnings.map((w, i) => (
            <p key={i}>⚠ {w}</p>
          ))}
        </aside>
      )}

      <h3 className="viewer-sec-title" style={{ marginTop: "var(--space-4)" }}>
        Detector &amp; Source Flag Legend
      </h3>
      <ul className="flag-list">
        {FLAG_BITS.map(([bit, name, desc]) => {
          const on = activeNames.has(name);
          return (
            <li key={name} className={`flag-row${on ? " is-active" : ""}`}>
              <span className="flag-badge">{on ? "● ON" : `b${bit}`}</span>
              <div>
                <strong>{name}</strong>
                <span className="viewer-muted"> — {desc}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
