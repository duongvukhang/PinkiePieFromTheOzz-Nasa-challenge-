import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("Unhandled client error in Observatory view:", error, errorInfo);
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          style={{
            minHeight: "100vh",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "#030712",
            color: "#f3f4f6",
            padding: "2rem",
            textAlign: "center",
            fontFamily: "system-ui, sans-serif",
          }}
        >
          <h1 style={{ fontSize: "1.75rem", marginBottom: "0.5rem", color: "#38bdf8" }}>
            Observatory Interface Error
          </h1>
          <p style={{ maxWidth: "480px", color: "#9ca3af", marginBottom: "1.5rem" }}>
            An unexpected error occurred while rendering the celestial viewer.
          </p>
          {this.state.error?.message && (
            <pre
              style={{
                backgroundColor: "#111827",
                border: "1px solid #1f2937",
                padding: "0.75rem 1rem",
                borderRadius: "6px",
                fontSize: "0.85rem",
                color: "#f87171",
                maxWidth: "600px",
                overflowX: "auto",
                marginBottom: "1.5rem",
              }}
            >
              {this.state.error.message}
            </pre>
          )}
          <button
            type="button"
            onClick={() => window.location.reload()}
            style={{
              backgroundColor: "#0284c7",
              color: "#ffffff",
              border: "none",
              padding: "0.5rem 1.25rem",
              borderRadius: "4px",
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            Reload Observatory
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
