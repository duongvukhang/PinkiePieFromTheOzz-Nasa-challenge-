import type { CutoutFrame } from "../../api/client";

interface TimeControllerProps {
  frames: CutoutFrame[];
  currentFrame?: CutoutFrame;
  currentIndex: number;
  playing: boolean;
  fps: number;
  onSelectFrame: (index: number) => void;
  onTogglePlay: () => void;
  onChangeSpeed: (fps: number) => void;
}

export function TimeController({
  frames,
  currentFrame,
  currentIndex,
  playing,
  fps,
  onSelectFrame,
  onTogglePlay,
  onChangeSpeed,
}: TimeControllerProps) {
  if (!frames.length || !currentFrame) return null;

  const total = frames.length;
  const safeIdx = Math.max(0, Math.min(currentIndex, total - 1));
  const dateStr = currentFrame.utc_mid ? currentFrame.utc_mid.slice(0, 10) : "2025-04-28";
  const timeStr =
    currentFrame.utc_mid && currentFrame.utc_mid.length >= 19
      ? currentFrame.utc_mid.slice(11, 19)
      : "13:00:15";
  const mjdStr = currentFrame.mjd_tdb_mid ? currentFrame.mjd_tdb_mid.toFixed(2) : "60793.54";

  // Interval gap computation relative to previous epoch
  let intervalGapText = "Reference Epoch (t₀)";
  if (safeIdx > 0 && frames[safeIdx - 1]) {
    const prevMjd = frames[safeIdx - 1]!.mjd_tdb_mid;
    const currMjd = currentFrame.mjd_tdb_mid;
    const dtDays = currMjd - prevMjd;
    intervalGapText = `Δt: ${dtDays >= 0 ? "+" : ""}${dtDays.toFixed(1)} days`;
  }

  const handlePrev = () => {
    onSelectFrame((safeIdx - 1 + total) % total);
  };

  const handleNext = () => {
    onSelectFrame((safeIdx + 1) % total);
  };

  return (
    <div
      className="time-controller-dock stellarium-glass"
      role="region"
      aria-label="Observation epoch timeline and playback"
    >
      <div className="tc-header">
        <div className="tc-date-group">
          <span className="tc-clock-icon" aria-hidden="true">
            ⏱
          </span>
          <span className="tc-date-label">{dateStr}</span>
        </div>
        <div className="tc-badge-group">
          <span className="tc-badge tc-badge-irsa">IRSA</span>
          <span className="tc-badge tc-badge-epoch">
            Epoch {safeIdx + 1} / {total}
          </span>
        </div>
      </div>

      <div className="tc-meta-row">
        <span className="tc-meta-item">
          <span className="tc-meta-k">UTC: </span>
          <span className="tc-meta-v">{timeStr}</span>
        </span>
        <span className="tc-meta-item">
          <span className="tc-meta-k">MJD: </span>
          <span className="tc-meta-v">{mjdStr}</span>
        </span>
      </div>

      <div className="tc-gap-row">
        <span className="tc-gap-label">INTERVAL GAP:</span>
        <span className="tc-gap-val">{intervalGapText}</span>
      </div>

      <div className="tc-controls-row">
        <div className="tc-btn-group">
          <button
            type="button"
            className="tc-btn"
            onClick={handlePrev}
            title="Previous Epoch"
            aria-label="Previous Epoch"
          >
            |◀
          </button>
          <button
            type="button"
            className="tc-btn tc-btn-play"
            onClick={onTogglePlay}
            title={playing ? "Pause Multi-Epoch Blink (Space)" : "Play Multi-Epoch Blink (Space)"}
            aria-label={playing ? "Pause Multi-Epoch Blink" : "Play Multi-Epoch Blink"}
          >
            {playing ? "⏸" : "▶"}
          </button>
          <button
            type="button"
            className="tc-btn"
            onClick={handleNext}
            title="Next Epoch"
            aria-label="Next Epoch"
          >
            ▶|
          </button>
        </div>

        <div className="tc-speed-group">
          <span className="tc-speed-label">Speed:</span>
          {[0.5, 1, 2].map((s) => (
            <button
              key={s}
              type="button"
              className={`tc-speed-btn ${fps === s ? "is-active" : ""}`}
              onClick={() => onChangeSpeed(s)}
            >
              {s}x
            </button>
          ))}
        </div>
      </div>

      <div className="tc-scrubber-track">
        {frames.map((f, i) => (
          <button
            key={f.frame_id}
            type="button"
            className={`tc-scrubber-segment ${i === safeIdx ? "is-active" : ""}`}
            onClick={() => onSelectFrame(i)}
            title={`Epoch ${i + 1} (${f.utc_mid.slice(0, 10)})`}
          >
            <span className="tc-seg-dot" />
            <span className="tc-seg-label">
              Epoch {i + 1} ({f.utc_mid.slice(0, 10)})
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
