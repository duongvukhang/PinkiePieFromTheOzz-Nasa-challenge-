import type { SpectrumResponse } from "../../api/client";

const ARRAY_BANDS = [
  [0.75, 1.32, "A1"],
  [1.32, 2.34, "A2"],
  [2.34, 3.12, "A3"],
  [3.12, 3.83, "A4"],
  [3.83, 4.42, "A5"],
  [4.42, 5.0, "A6"],
] as const;

export function SpectrumChart({ spectrum, title }: { spectrum?: SpectrumResponse; title: string }) {
  if (!spectrum || !spectrum.wavelength_um.length) {
    return (
      <p className="viewer-muted">
        Select a tracked source or run analysis to inspect its 102-channel SPHEREx spectral energy
        distribution.
      </p>
    );
  }

  const w = 320;
  const h = 175;
  const pad = { l: 34, r: 10, t: 18, b: 26 };
  const pw = w - pad.l - pad.r;
  const ph = h - pad.t - pad.b;

  const minL = 0.75;
  const maxL = 5.0;
  const fluxes = spectrum.flux_ujy;
  const maxF = Math.max(1e-3, ...fluxes.map((v, i) => v + (spectrum.flux_err_ujy[i] ?? 0)));
  const minF = Math.min(0, ...fluxes);
  const fSpan = Math.max(1e-3, maxF - minF);

  const xOf = (lam: number) => pad.l + ((lam - minL) / (maxL - minL)) * pw;
  const yOf = (f: number) => pad.t + ph - ((f - minF) / fSpan) * ph;

  return (
    <div className="spectrum-box">
      <div className="spectrum-head">
        <strong>{title}</strong>
        <span className="viewer-mono">
          {spectrum.wavelength_um.length} ch · {spectrum.method ?? "forced_psf"}
        </span>
      </div>

      <svg
        className="spectrum-svg"
        viewBox={`0 0 ${w} ${h}`}
        role="img"
        aria-label={`SED chart for ${title}`}
      >
        {ARRAY_BANDS.map(([l0, l1, name], idx) => (
          <g key={name}>
            <rect
              x={xOf(l0)}
              y={pad.t}
              width={xOf(l1) - xOf(l0)}
              height={ph}
              fill={idx % 2 === 0 ? "rgba(255,255,255,0.03)" : "transparent"}
            />
            <text
              x={(xOf(l0) + xOf(l1)) / 2}
              y={11}
              textAnchor="middle"
              fill="#8e8a80"
              fontSize="8"
            >
              {name}
            </text>
          </g>
        ))}

        <line x1={pad.l} y1={pad.t + ph} x2={w - pad.r} y2={pad.t + ph} stroke="#504e48" />
        <line x1={pad.l} y1={pad.t} x2={pad.l} y2={pad.t + ph} stroke="#504e48" />

        {spectrum.wavelength_um.map((lam, i) => {
          const flux = spectrum.flux_ujy[i] ?? 0;
          const err = spectrum.flux_err_ujy[i] ?? 0;
          const flagged = (spectrum.flags[i] ?? 0) !== 0;
          const cx = xOf(lam);
          const cy = yOf(flux);
          return (
            <g key={i} className="sed-point">
              <line
                x1={cx}
                y1={yOf(flux - err)}
                x2={cx}
                y2={yOf(flux + err)}
                stroke={flagged ? "#75726a" : "rgba(156,196,228,0.45)"}
                strokeWidth="1"
              />
              <circle
                cx={cx}
                cy={cy}
                r={flagged ? 1.8 : 2.3}
                fill={flagged ? "#75726a" : "var(--oi-orange)"}
              />
            </g>
          );
        })}

        <text x={pad.l} y={h - 6} fill="#b0ada5" fontSize="9">
          0.75 µm
        </text>
        <text x={w - pad.r} y={h - 6} textAnchor="end" fill="#b0ada5" fontSize="9">
          5.0 µm
        </text>
        <text x={4} y={pad.t + 6} fill="#b0ada5" fontSize="8">
          {maxF.toFixed(0)} µJy
        </text>
      </svg>

      <table className="visually-hidden" aria-label="Spectral flux samples">
        <thead>
          <tr>
            <th>Wavelength (um)</th>
            <th>Flux (uJy)</th>
            <th>Uncertainty (uJy)</th>
          </tr>
        </thead>
        <tbody>
          {spectrum.wavelength_um.slice(0, 12).map((lam, i) => (
            <tr key={i}>
              <td>{lam.toFixed(3)}</td>
              <td>{spectrum.flux_ujy[i]?.toFixed(2)}</td>
              <td>{spectrum.flux_err_ujy[i]?.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
