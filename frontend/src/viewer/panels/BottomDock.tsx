interface BottomDockProps {
  showSources: boolean;
  onToggleSources: () => void;
  showLabels: boolean;
  onToggleLabels: () => void;
  showGrid: boolean;
  onToggleGrid: () => void;
  showTrajectories: boolean;
  onToggleTrajectories: () => void;
  showUncertainty: boolean;
  onToggleUncertainty: () => void;
  isTracksOpen?: boolean;
  onToggleTracks?: () => void;
  isSpectrumOpen?: boolean;
  onToggleSpectrum?: () => void;
  isTimelineOpen?: boolean;
  onToggleTimeline?: () => void;
  isSettingsOpen?: boolean;
  onToggleSettings?: () => void;
  tracksCount?: number;
}

export function BottomDock({
  showSources,
  onToggleSources,
  showLabels,
  onToggleLabels,
  showGrid,
  onToggleGrid,
  showTrajectories,
  onToggleTrajectories,
  showUncertainty,
  onToggleUncertainty,
  isTracksOpen,
  onToggleTracks,
  isSpectrumOpen,
  onToggleSpectrum,
  isTimelineOpen,
  onToggleTimeline,
  isSettingsOpen,
  onToggleSettings,
  tracksCount = 0,
}: BottomDockProps) {
  const tools = [
    {
      id: "showSources",
      label: "Source Reticles",
      active: showSources,
      onClick: onToggleSources,
      icon: (
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <circle cx="12" cy="12" r="9" />
          <line x1="12" y1="3" x2="12" y2="7" />
          <line x1="12" y1="17" x2="12" y2="21" />
          <line x1="3" y1="12" x2="7" y2="12" />
          <line x1="17" y1="12" x2="21" y2="12" />
        </svg>
      ),
    },
    {
      id: "showLabels",
      label: "Source Labels",
      active: showLabels,
      onClick: onToggleLabels,
      icon: (
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
          <line x1="7" y1="7" x2="7.01" y2="7" />
        </svg>
      ),
    },
    {
      id: "showGrid",
      label: "Celestial Coordinate Grid",
      active: showGrid,
      onClick: onToggleGrid,
      icon: (
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <line x1="3" y1="9" x2="21" y2="9" />
          <line x1="3" y1="15" x2="21" y2="15" />
          <line x1="9" y1="3" x2="9" y2="21" />
          <line x1="15" y1="3" x2="15" y2="21" />
        </svg>
      ),
    },
    {
      id: "showTrajectories",
      label: "Multi-Epoch Tracking Trajectories",
      active: showTrajectories,
      onClick: onToggleTrajectories,
      icon: (
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <circle cx="12" cy="12" r="10" />
          <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" />
        </svg>
      ),
    },
    {
      id: "tracksList",
      label: `Active Multi-Epoch Tracks (${tracksCount})`,
      active: Boolean(isTracksOpen),
      onClick: onToggleTracks,
      icon: (
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polygon points="12 2 2 7 12 12 22 7 12 2" />
          <polyline points="2 17 12 22 22 17" />
          <polyline points="2 12 12 17 22 12" />
        </svg>
      ),
    },
    {
      id: "showUncertainty",
      label: "Positional Uncertainty Ellipses",
      active: showUncertainty,
      onClick: onToggleUncertainty,
      icon: (
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <circle cx="12" cy="12" r="10" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      ),
    },
    {
      id: "spectrum",
      label: "Infrared LVF Spectral Mode",
      active: Boolean(isSpectrumOpen),
      onClick: onToggleSpectrum,
      icon: (
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
        </svg>
      ),
    },
    {
      id: "timeline",
      label: "Observation Epoch Controls",
      active: Boolean(isTimelineOpen),
      onClick: onToggleTimeline,
      icon: (
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
      ),
    },
    {
      id: "sliders",
      label: "Contrast & Brightness Controls",
      active: Boolean(isSettingsOpen),
      onClick: onToggleSettings,
      icon: (
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <line x1="4" y1="21" x2="4" y2="14" />
          <line x1="4" y1="10" x2="4" y2="3" />
          <line x1="12" y1="21" x2="12" y2="12" />
          <line x1="12" y1="8" x2="12" y2="3" />
          <line x1="20" y1="21" x2="20" y2="16" />
          <line x1="20" y1="12" x2="20" y2="3" />
          <line x1="1" y1="14" x2="7" y2="14" />
          <line x1="9" y1="8" x2="15" y2="8" />
          <line x1="17" y1="16" x2="23" y2="16" />
        </svg>
      ),
    },
  ];

  return (
    <div className="bottom-dock-wrapper" role="toolbar" aria-label="Observatory view toggles">
      <div className="bottom-dock-pill stellarium-glass">
        {tools.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={t.onClick}
            className={`dock-tool-btn ${t.active ? "is-active" : ""}`}
            title={t.label}
            aria-label={t.label}
          >
            {t.icon}
            {t.active && <span className="dock-active-pip" />}
          </button>
        ))}
      </div>
    </div>
  );
}
