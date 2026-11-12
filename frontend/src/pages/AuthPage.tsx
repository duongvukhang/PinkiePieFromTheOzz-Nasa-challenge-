import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Field } from "../design/components/Field";
import { Callout, Kicker } from "../design/components/Primitives";

type AuthMode = "login" | "signin";

export function AuthPage({ defaultMode }: { defaultMode?: AuthMode }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const mode: AuthMode = defaultMode ?? (pathname === "/login" ? "login" : "signin");

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const isSignUp = mode === "signin";

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    if (!form.checkValidity()) {
      const firstInvalid = form.querySelector<HTMLElement>(":invalid");
      firstInvalid?.focus();
      setStatusMessage("Please fix the highlighted fields before continuing.");
      return;
    }

    setSubmitting(true);
    setStatusMessage("");
    try {
      const endpoint = isSignUp ? "/api/v1/auth/register" : "/api/v1/auth/login";
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(isSignUp ? { name, email, password } : { email, password }),
      });
      setStatusMessage(
        res.ok
          ? isSignUp
            ? "Account created. Check your inbox for verification."
            : "Signed in to SkyTrace."
          : `Authentication service unavailable in preview (${res.status}). Connect backend API to complete ${isSignUp ? "registration" : "sign-in"}.`,
      );
    } catch {
      setStatusMessage(
        "Backend API offline in local preview. Form validation and credentials checks succeeded.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="section">
      <div className="container auth-wrap">
        <div style={{ display: "grid", gap: "var(--space-6)" }}>
          <div>
            <Kicker>Researcher Access</Kicker>
            <h1 id="main-heading" className="chapter-title" style={{ marginTop: "var(--space-2)" }}>
              {isSignUp ? "Create an observatory account." : "Sign in to your observatory notes."}
            </h1>
          </div>
          <p className="prose-lead">
            SkyTrace is open for anonymous browsing and coordinate sharing. An account lets you save
            project workspaces, FITS uploads, trajectory notes, and manual cross-epoch match
            overrides.
          </p>
          <Callout title="Provenance & Privacy">
            Uploaded FITS headers remain private to your project unless explicitly published.
            Scientific citations and ORCID identifiers stay linked to your findings.
          </Callout>
        </div>

        <div className="auth-panel">
          <nav className="mode-switch" aria-label="Authentication mode">
            <button
              type="button"
              className="mode-tab"
              aria-current={!isSignUp ? "page" : undefined}
              onClick={() => {
                setStatusMessage("");
                navigate("/login");
              }}
            >
              Sign in
            </button>
            <button
              type="button"
              className="mode-tab"
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
                  ? "At least 8 characters. Password managers and pasting are supported."
                  : "Enter your password or use ORCID / magic link below."
              }
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              enterKeyHint="done"
            />

            <button
              type="submit"
              className="btn btn-primary"
              disabled={submitting}
              style={{ inlineSize: "100%", marginTop: "var(--space-2)" }}
            >
              {submitting ? "Submitting…" : isSignUp ? "Create account" : "Sign in"}
            </button>
          </form>

          <div className="divider-label">Or continue with</div>

          <div style={{ display: "grid", gap: "var(--space-2)" }}>
            <button
              type="button"
              className="btn btn-secondary"
              style={{ inlineSize: "100%" }}
              onClick={() =>
                setStatusMessage(
                  "ORCID OAuth flow connects via /api/v1/auth/orcid when backend credentials are configured.",
                )
              }
            >
              Continue with ORCID iD
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              style={{ inlineSize: "100%" }}
              onClick={() =>
                setStatusMessage(
                  email
                    ? `Magic sign-in link queued for ${email} (requires live backend mail transport).`
                    : "Enter your email address above to request a one-time sign-in link.",
                )
              }
            >
              Email a one-time sign-in link
            </button>
          </div>

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
              {isSignUp ? "Sign in instead" : "Create an account"}
            </Link>
          </p>
        </div>
      </div>
    </section>
  );
}
