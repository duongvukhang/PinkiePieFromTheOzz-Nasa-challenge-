import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type ProjectCreate, type ProjectRead } from "../api/client";
import { useAuthStore } from "../app/authStore";

const TARGET_PRESETS = [
  { name: "WISE 0855", ra: 133.79513, dec: -7.24515, fov: 10 },
  { name: "Barnard's Star", ra: 269.45208, dec: 4.69339, fov: 10 },
  { name: "ε Indi B", ra: 330.84042, dec: -56.78611, fov: 10 },
  { name: "RW Aurigae", ra: 77.45667, dec: 30.40111, fov: 10 },
  { name: "R Draconis", ra: 248.33083, dec: 66.86861, fov: 10 },
] as const;

export function ProjectsPage() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);

  const [projects, setProjects] = useState<ProjectRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [ra, setRa] = useState<number>(133.79513);
  const [dec, setDec] = useState<number>(-7.24515);
  const [fov, setFov] = useState<number>(10);
  const [visibility, setVisibility] = useState<"private" | "link" | "public">("private");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const fetchProjects = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listProjects({ limit: 100 });
      setProjects(res.items);
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to load projects. You may need to log in.",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchProjects();
  }, [user]);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setCreateError(null);

    const payload: ProjectCreate = {
      name: name.trim(),
      description: description.trim() || undefined,
      center_ra: ra,
      center_dec: dec,
      fov_arcmin: fov,
      visibility,
    };

    try {
      const created = await api.createProject(payload);
      setIsModalOpen(false);
      setName("");
      setDescription("");
      navigate(`/projects/${created.id}`);
    } catch (err: unknown) {
      setCreateError(err instanceof Error ? err.message : "Failed to create project workspace");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string, projName: string) => {
    if (!window.confirm(`Are you sure you want to delete workspace "${projName}"?`)) return;
    try {
      await api.deleteProject(id);
      setProjects((prev) => prev.filter((p) => p.id !== id));
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to delete project");
    }
  };

  const filtered = projects.filter((p) => {
    const q = search.toLowerCase().trim();
    if (!q) return true;
    return (
      p.name.toLowerCase().includes(q) ||
      (p.description && p.description.toLowerCase().includes(q)) ||
      (p.center_ra !== null && p.center_ra !== undefined && String(p.center_ra).includes(q)) ||
      (p.center_dec !== null && p.center_dec !== undefined && String(p.center_dec).includes(q))
    );
  });

  return (
    <section className="section">
      <div className="container" style={{ display: "grid", gap: "var(--space-8)" }}>
        <header style={{ display: "grid", gap: "var(--space-2)" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "flex-start",
              flexWrap: "wrap",
              gap: "var(--space-4)",
            }}
          >
            <div>
              <span className="drawer-kicker">Workspace Archive</span>
              <h1 id="main-heading" style={{ fontSize: "var(--text-3xl)", margin: "4px 0" }}>
                Research Workspaces
              </h1>
              <p
                style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: "var(--text-lg)",
                  color: "var(--ink-1)",
                  maxWidth: "42rem",
                }}
              >
                Organize sky fields, log candidate findings, persist markdown notebooks, and manage
                multi-epoch FITS files across observational campaigns.
              </p>
            </div>

            <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
              <button
                type="button"
                className="btn btn-dark"
                onClick={() => setIsModalOpen(true)}
                style={{ padding: "8px 18px", fontSize: "0.95rem" }}
              >
                + New Workspace
              </button>
            </div>
          </div>

          {!user && (
            <div
              style={{
                marginTop: "var(--space-2)",
                padding: "12px 16px",
                background: "var(--paper-card)",
                border: "1px solid var(--rule)",
                borderRadius: "var(--radius-card)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: "var(--space-2)",
              }}
            >
              <span style={{ fontSize: "0.9rem", color: "var(--ink-1)" }}>
                You are currently viewing anonymously. Log in to persist workspaces permanently and
                share findings.
              </span>
              <Link to="/login" className="nav-pill" style={{ fontSize: "0.85rem" }}>
                Log in →
              </Link>
            </div>
          )}
        </header>

        {/* Filter bar */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "var(--space-4)",
            flexWrap: "wrap",
          }}
        >
          <input
            type="search"
            placeholder="Search workspaces by name or coordinates…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              padding: "8px 14px",
              borderRadius: "var(--radius-btn)",
              border: "1px solid var(--rule)",
              background: "var(--paper-0)",
              fontSize: "0.9rem",
              minWidth: "280px",
              maxWidth: "400px",
              color: "var(--ink-0)",
            }}
          />
          <span style={{ fontSize: "0.85rem", color: "var(--ink-2)" }}>
            Showing {filtered.length} of {projects.length} workspace
            {projects.length === 1 ? "" : "s"}
          </span>
        </div>

        {/* Workspace list */}
        {loading ? (
          <div style={{ padding: "var(--space-8)", textAlign: "center", color: "var(--ink-2)" }}>
            Loading workspaces…
          </div>
        ) : error ? (
          <div
            style={{
              padding: "var(--space-6)",
              background: "var(--paper-card)",
              border: "1px solid var(--rule)",
              borderRadius: "var(--radius-card)",
              textAlign: "center",
            }}
          >
            <p style={{ color: "var(--error-light)", marginBottom: "var(--space-4)" }}>{error}</p>
            {!user ? (
              <Link to="/login" className="btn btn-dark">
                Log in to view your workspaces
              </Link>
            ) : (
              <button type="button" className="btn btn-dark" onClick={() => void fetchProjects()}>
                Retry
              </button>
            )}
          </div>
        ) : filtered.length === 0 ? (
          <div
            style={{
              padding: "var(--space-12) var(--space-6)",
              background: "var(--paper-card)",
              border: "1px dashed var(--rule)",
              borderRadius: "var(--radius-card)",
              textAlign: "center",
              display: "grid",
              gap: "var(--space-4)",
              justifyItems: "center",
            }}
          >
            <h3 style={{ fontSize: "var(--text-xl)" }}>No workspaces found</h3>
            <p style={{ color: "var(--ink-2)", maxWidth: "30rem" }}>
              {search
                ? "No workspaces match your query. Try clearing your search filter."
                : "Create your first workspace to track target fields, curate FITS epochs, and record astrometric findings."}
            </p>
            <div style={{ display: "flex", gap: "var(--space-3)" }}>
              {search ? (
                <button type="button" className="nav-pill" onClick={() => setSearch("")}>
                  Clear filter
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    className="btn btn-dark"
                    onClick={() => setIsModalOpen(true)}
                  >
                    + Create Workspace
                  </button>
                  <Link to="/v" className="nav-pill">
                    Launch Observatory
                  </Link>
                </>
              )}
            </div>
          </div>
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
              gap: "var(--space-6)",
            }}
          >
            {filtered.map((proj) => {
              const hasCoords = proj.center_ra !== null && proj.center_dec !== null;
              const obsUrl = hasCoords
                ? `/v?projectId=${proj.id}&ra=${proj.center_ra}&dec=${proj.center_dec}&fov=${proj.fov_arcmin ?? 10}`
                : `/v?projectId=${proj.id}`;

              return (
                <article
                  key={proj.id}
                  style={{
                    background: "var(--paper-card)",
                    border: "1px solid var(--rule)",
                    borderRadius: "var(--radius-card)",
                    padding: "var(--space-6)",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "space-between",
                    gap: "var(--space-4)",
                    transition: "border-color 0.2s ease, transform 0.2s ease",
                  }}
                >
                  <div style={{ display: "grid", gap: "var(--space-2)" }}>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: "var(--space-2)",
                      }}
                    >
                      <span
                        style={{
                          fontSize: "0.72rem",
                          letterSpacing: "0.06em",
                          textTransform: "uppercase",
                          padding: "2px 8px",
                          borderRadius: "var(--radius-pill)",
                          background:
                            proj.visibility === "public"
                              ? "var(--valid-light)"
                              : proj.visibility === "link"
                                ? "var(--oi-sky)"
                                : "var(--paper-pill)",
                          color:
                            proj.visibility === "private" ? "var(--ink-1)" : "var(--paper-0)",
                        }}
                      >
                        {proj.visibility}
                      </span>
                      <span style={{ fontSize: "0.75rem", color: "var(--ink-2)" }}>
                        v{proj.current_version}
                      </span>
                    </div>

                    <h2 style={{ fontSize: "var(--text-xl)", margin: 0 }}>
                      <Link
                        to={`/projects/${proj.id}`}
                        style={{ textDecoration: "none", color: "var(--ink-0)" }}
                      >
                        {proj.name}
                      </Link>
                    </h2>

                    {proj.description && (
                      <p
                        style={{
                          fontSize: "0.9rem",
                          color: "var(--ink-2)",
                          margin: 0,
                          lineHeight: 1.4,
                          display: "-webkit-box",
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                        }}
                      >
                        {proj.description}
                      </p>
                    )}

                    {hasCoords && (
                      <div
                        style={{
                          marginTop: "var(--space-2)",
                          fontSize: "0.82rem",
                          fontFamily: "var(--font-mono)",
                          background: "var(--paper-0)",
                          padding: "4px 8px",
                          borderRadius: "var(--radius-btn)",
                          border: "1px solid var(--rule)",
                          color: "var(--ink-1)",
                        }}
                      >
                        RA {proj.center_ra?.toFixed(4)}° · Dec {proj.center_dec?.toFixed(4)}° (
                        {proj.fov_arcmin}′)
                      </div>
                    )}
                  </div>

                  <div
                    style={{
                      borderTop: "1px solid var(--rule)",
                      paddingTop: "var(--space-4)",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: "var(--space-2)",
                    }}
                  >
                    <div style={{ display: "flex", gap: "var(--space-2)" }}>
                      <Link
                        to={obsUrl}
                        className="nav-pill"
                        title="Open in Observatory viewer"
                        style={{ fontSize: "0.82rem", padding: "4px 10px" }}
                      >
                        🔭 Viewer
                      </Link>
                      <Link
                        to={`/projects/${proj.id}`}
                        className="btn btn-dark"
                        style={{ fontSize: "0.82rem", padding: "4px 12px" }}
                      >
                        Workspace →
                      </Link>
                    </div>

                    <button
                      type="button"
                      className="nav-pill"
                      style={{
                        fontSize: "0.8rem",
                        padding: "4px 8px",
                        color: "var(--error-light)",
                      }}
                      onClick={() => void handleDelete(proj.id, proj.name)}
                      title="Delete workspace"
                    >
                      ✕
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}

        {/* Create Project Modal */}
        {isModalOpen && (
          <div
            className="archive-modal-backdrop"
            role="dialog"
            aria-modal="true"
            aria-label="Create New Workspace"
            onClick={(e) => {
              if (e.target === e.currentTarget) setIsModalOpen(false);
            }}
          >
            <div
              className="archive-modal-content"
              style={{
                background: "var(--paper-0)",
                color: "var(--ink-0)",
                border: "1px solid var(--rule)",
                maxWidth: "36rem",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  borderBottom: "1px solid var(--rule)",
                  paddingBottom: "var(--space-3)",
                }}
              >
                <h2 style={{ fontSize: "var(--text-xl)", margin: 0 }}>Create Research Workspace</h2>
                <button
                  type="button"
                  className="btn btn-icon-sq"
                  onClick={() => setIsModalOpen(false)}
                  aria-label="Close dialog"
                >
                  ✕
                </button>
              </div>

              {createError && (
                <div
                  style={{
                    padding: "8px 12px",
                    background: "var(--error-bg-light)",
                    color: "var(--error-light)",
                    borderRadius: "var(--radius-btn)",
                    fontSize: "0.88rem",
                    marginTop: "var(--space-3)",
                  }}
                >
                  {createError}
                </div>
              )}

              <form
                onSubmit={(e) => void handleCreate(e)}
                style={{ display: "grid", gap: "var(--space-4)", marginTop: "var(--space-4)" }}
              >
                <div style={{ display: "grid", gap: "4px" }}>
                  <label htmlFor="proj-name" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                    Workspace Name *
                  </label>
                  <input
                    id="proj-name"
                    type="text"
                    required
                    placeholder="e.g. WISE 0855 Proper Motion Campaign"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    style={{
                      padding: "8px 12px",
                      borderRadius: "var(--radius-btn)",
                      border: "1px solid var(--rule)",
                      background: "var(--paper-card)",
                      color: "var(--ink-0)",
                    }}
                  />
                </div>

                <div style={{ display: "grid", gap: "4px" }}>
                  <label htmlFor="proj-desc" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                    Description
                  </label>
                  <textarea
                    id="proj-desc"
                    rows={3}
                    placeholder="Brief objective, target notes, or observational plan…"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    style={{
                      padding: "8px 12px",
                      borderRadius: "var(--radius-btn)",
                      border: "1px solid var(--rule)",
                      background: "var(--paper-card)",
                      color: "var(--ink-0)",
                      fontFamily: "var(--font-sans)",
                    }}
                  />
                </div>

                <div style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>Target Field Presets</span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                    {TARGET_PRESETS.map((t) => (
                      <button
                        key={t.name}
                        type="button"
                        className="nav-pill"
                        style={{ fontSize: "0.8rem", padding: "3px 8px" }}
                        onClick={() => {
                          setRa(t.ra);
                          setDec(t.dec);
                          setFov(t.fov);
                          if (!name) setName(`${t.name} Field`);
                        }}
                      >
                        {t.name}
                      </button>
                    ))}
                  </div>
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr 1fr",
                    gap: "var(--space-3)",
                  }}
                >
                  <div style={{ display: "grid", gap: "4px" }}>
                    <label htmlFor="proj-ra" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                      Center RA (°)
                    </label>
                    <input
                      id="proj-ra"
                      type="number"
                      step="any"
                      min={0}
                      max={360}
                      value={ra}
                      onChange={(e) => setRa(Number(e.target.value))}
                      style={{
                        padding: "8px 12px",
                        borderRadius: "var(--radius-btn)",
                        border: "1px solid var(--rule)",
                        background: "var(--paper-card)",
                        color: "var(--ink-0)",
                      }}
                    />
                  </div>

                  <div style={{ display: "grid", gap: "4px" }}>
                    <label htmlFor="proj-dec" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                      Center Dec (°)
                    </label>
                    <input
                      id="proj-dec"
                      type="number"
                      step="any"
                      min={-90}
                      max={90}
                      value={dec}
                      onChange={(e) => setDec(Number(e.target.value))}
                      style={{
                        padding: "8px 12px",
                        borderRadius: "var(--radius-btn)",
                        border: "1px solid var(--rule)",
                        background: "var(--paper-card)",
                        color: "var(--ink-0)",
                      }}
                    />
                  </div>

                  <div style={{ display: "grid", gap: "4px" }}>
                    <label htmlFor="proj-fov" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                      FOV (arcmin)
                    </label>
                    <input
                      id="proj-fov"
                      type="number"
                      step="any"
                      min={1}
                      max={60}
                      value={fov}
                      onChange={(e) => setFov(Number(e.target.value))}
                      style={{
                        padding: "8px 12px",
                        borderRadius: "var(--radius-btn)",
                        border: "1px solid var(--rule)",
                        background: "var(--paper-card)",
                        color: "var(--ink-0)",
                      }}
                    />
                  </div>
                </div>

                <div style={{ display: "grid", gap: "4px" }}>
                  <label htmlFor="proj-vis" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                    Visibility
                  </label>
                  <select
                    id="proj-vis"
                    value={visibility}
                    onChange={(e) =>
                      setVisibility(e.target.value as "private" | "link" | "public")
                    }
                    style={{
                      padding: "8px 12px",
                      borderRadius: "var(--radius-btn)",
                      border: "1px solid var(--rule)",
                      background: "var(--paper-card)",
                      color: "var(--ink-0)",
                    }}
                  >
                    <option value="private">Private (Only you)</option>
                    <option value="link">Link Shareable (Anyone with token link)</option>
                    <option value="public">Public (Indexed in open archive)</option>
                  </select>
                </div>

                <div
                  style={{
                    display: "flex",
                    justifyContent: "flex-end",
                    gap: "var(--space-3)",
                    marginTop: "var(--space-4)",
                  }}
                >
                  <button
                    type="button"
                    className="nav-pill"
                    onClick={() => setIsModalOpen(false)}
                    disabled={creating}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="btn btn-dark"
                    disabled={creating || !name.trim()}
                  >
                    {creating ? "Creating…" : "Create Workspace"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
