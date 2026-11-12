import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuthStore } from "../app/authStore";
import { Field } from "../design/components/Field";
import { Callout } from "../design/components/Primitives";

type AuthMode = "login" | "signin";

export function AuthPage({ defaultMode }: { defaultMode?: AuthMode }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const mode: AuthMode = defaultMode ?? (pathname === "/login" ? "login" : "signin");
  const isSignUp = mode === "signin";

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    if (!form.checkValidity()) {
      form.querySelector<HTMLElement>(":invalid")?.focus();
      setStatusMessage("Please fix the highlighted fields before continuing.");
      return;
    }

    setSubmitting(true);
    setStatusMessage("");
    const displayName = name.trim() || email.split("@")[0] || "Researcher";

    const apiBase = import.meta.env.VITE_API_URL;
    if (apiBase) {
      try {
        const endpoint = isSignUp ? "/api/v1/auth/register" : "/api/v1/auth/login";
        await fetch(`${apiBase}${endpoint}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            isSignUp ? { name: displayName, email, password } : { email, password },
          ),
        });
      } catch {
        /* backend optional during frontend preview */
      }
    }
    login({ name: displayName, email });
    setSubmitting(false);
    navigate("/");
  };

  return (
    <section className="section">
      <div className="container auth-wrap">
        <div style={{ display: "grid", gap: "var(--space-6)" }}>
          <h1 id="main-heading" style={{ fontSize: "var(--text-3xl)" }}>
            {isSignUp ? "Create an observatory account." : "Log in to your field notes."}
          </h1>
          <p
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: "var(--text-lg)",
              color: "var(--ink-1)",
            }}
          >
            SkyTrace stays open for anonymous reading and coordinate sharing. Signing in lets you
            save project workspaces, custom FITS comparisons, and manual match overrides.
          </p>
          <img
            src="/images/plate-flamsteed-1729.jpg"
            alt="John Flamsteed Atlas Coelestis archival frontispiece from 1729"
            style={{
              inlineSize: "100%",
              maxInlineSize: "24rem",
              borderRadius: "var(--radius-image)",
              border: "1px solid var(--border)",
            }}
          />
          <Callout title="Provenance & Privacy">
            Uploaded FITS headers remain private to your project unless you choose to publish a
            link.
          </Callout>
        </div>

        <div className="auth-panel">
          <nav className="mode-switch" aria-label="Authentication mode">
            <button
              type="button"
              className="nav-pill"
              aria-current={!isSignUp ? "page" : undefined}
              onClick={() => {
                setStatusMessage("");
                navigate("/login");
              }}
            >
              Log in
            </button>
            <button
              type="button"
              className="nav-pill"
              aria-current={isSignUp ? "page" : undefined}
              onClick={() => {
                setStatusMessage("");
                navigate("/signin");
              }}
            >
              Create account
            </button>
          </nav>

          <form onSubmit={handleSubmit} noValidate aria-labelledby="main-heading">
            {isSignUp && (
              <Field
                label="Full name"
                name="name"
                type="text"
                autoComplete="name"
                required
                hint="Display name for saved project notes and citations."
                value={name}
                onChange={(e) => setName(e.target.value)}
                enterKeyHint="next"
              />
            )}

            <Field
              label="Email address"
              name="email"
              type="email"
              inputMode="email"
              autoComplete="username"
              required
              hint="Format: researcher@observatory.org"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              spellCheck={false}
              autoCorrect="off"
              autoCapitalize="none"
              enterKeyHint="next"
            />

            <Field
              label="Password"
              name="password"
              type="password"
              passwordToggle
              required
              minLength={8}
              autoComplete={isSignUp ? "new-password" : "current-password"}
              hint={
                isSignUp
                  ? "At least 8 characters."
                  : "Enter your password to open your researcher session."
              }
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              enterKeyHint="done"
            />

            <button
              type="submit"
              className="btn btn-dark"
              disabled={submitting}
              style={{ inlineSize: "100%", minBlockSize: 46, marginTop: "var(--space-2)" }}
            >
              {submitting ? "Signing in…" : isSignUp ? "Create account" : "Log in"}
            </button>
          </form>

          <div
            role="status"
            aria-live="polite"
            style={{
              marginTop: "var(--space-4)",
              fontSize: "var(--text-sm)",
              color: "var(--ink-1)",
            }}
          >
            {statusMessage}
          </div>

          <p
            style={{
              marginTop: "var(--space-4)",
              fontSize: "var(--text-xs)",
              color: "var(--ink-2)",
            }}
          >
            {isSignUp ? "Already registered? " : "New to SkyTrace? "}
            <Link to={isSignUp ? "/login" : "/signin"}>
              {isSignUp ? "Log in instead" : "Create an account"}
            </Link>
          </p>
        </div>
      </div>
    </section>
  );
}
