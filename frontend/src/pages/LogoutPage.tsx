import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuthStore } from "../app/authStore";
import { NewYorkerHeading } from "../design/components/Primitives";

export function LogoutPage() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const [signedOutName, setSignedOutName] = useState<string | null>(null);

  useEffect(() => {
    if (user) {
      setSignedOutName(user.name);
      logout();
    }
  }, [user, logout]);

  return (
    <section className="section">
      <div className="container" style={{ maxWidth: "46rem" }}>
        <NewYorkerHeading
          as="h1"
          title="Session Closed"
          subtitle="You have safely signed out of your local SkyTrace researcher workspace."
        />

        <div
          className="auth-panel"
          style={{
            textAlign: "center",
            display: "grid",
            gap: "var(--space-4)",
            justifyItems: "center",
          }}
        >
          <p
            role="status"
            aria-live="polite"
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: "var(--text-lg)",
              color: "var(--ink-1)",
            }}
          >
            {signedOutName
              ? `Signed out from ${signedOutName}'s session. Public sky exploration remains open.`
              : "Your observatory session has ended. Public archives and coordinates remain accessible."}
          </p>

          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--space-3)",
              justifyContent: "center",
            }}
          >
            <Link to="/login" className="btn btn-dark">
              Log back in
            </Link>
            <Link to="/" className="nav-pill" style={{ minBlockSize: 38 }}>
              Return to Journal Home
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
