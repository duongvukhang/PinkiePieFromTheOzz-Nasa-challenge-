import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  api,
  type ExportRequest,
  type FindingCreate,
  type FindingRead,
  type NoteRead,
  type ProjectRead,
  type ProjectVersionRead,
  type SavedViewRead,
  type UploadedFitsSummary,
} from "../api/client";
import { useAuthStore } from "../app/authStore";

type TabId = "overview" | "files" | "notes" | "findings" | "views" | "history" | "export";

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const user = useAuthStore((s) => s.user);

  const shareToken = searchParams.get("share_token") ?? undefined;

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [files, setFiles] = useState<UploadedFitsSummary[]>([]);
  const [notes, setNotes] = useState<NoteRead[]>([]);
  const [findings, setFindings] = useState<FindingRead[]>([]);
  const [views, setViews] = useState<SavedViewRead[]>([]);
  const [versions, setVersions] = useState<ProjectVersionRead[]>([]);

  const [activeTab, setActiveTab] = useState<TabId>("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modals & form state
  const [isShareModalOpen, setIsShareModalOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [shareLoading, setShareLoading] = useState(false);

  const [isSnapshotModalOpen, setIsSnapshotModalOpen] = useState(false);
  const [snapshotMessage, setSnapshotMessage] = useState("");
  const [snapshotLoading, setSnapshotLoading] = useState(false);

  // New Note state
  const [newNoteBody, setNewNoteBody] = useState("");
  const [addingNote, setAddingNote] = useState(false);

  // New Finding state
  const [isFindingModalOpen, setIsFindingModalOpen] = useState(false);
  const [findingTitle, setFindingTitle] = useState("");
  const [findingStatus, setFindingStatus] = useState<
    "draft" | "candidate" | "submitted" | "known" | "rejected"
  >("draft");
  const [findingSummary, setFindingSummary] = useState("");
  const [addingFinding, setAddingFinding] = useState(false);

  // File Upload state
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadEpoch, setUploadEpoch] = useState("");
  const [uploadingFile, setUploadingFile] = useState(false);

  // Export state
  const [exportFormat, setExportFormat] = useState<ExportRequest["format"]>("json");
  const [exportJobId, setExportJobId] = useState<string | null>(null);
  const [exportStatus, setExportStatus] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const loadAll = async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const p = await api.getProject(id, shareToken);
      setProject(p);

      const [fList, nList, fdList, vList, verList] = await Promise.all([
        api.listProjectFiles(id, shareToken).catch(() => []),
        api.listNotes(id, shareToken).catch(() => []),
        api.listFindings(id, shareToken).catch(() => []),
        api.listSavedViews(id, shareToken).catch(() => []),
        api.listVersions(id, shareToken).catch(() => []),
      ]);

      setFiles(fList);
      setNotes(nList);
      setFindings(fdList);
      setViews(vList);
      setVersions(verList);
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to load project workspace. Check permissions.",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAll();
  }, [id, shareToken, user]);

  const handleShare = async () => {
    if (!id) return;
    setShareLoading(true);
    try {
      const res = await api.createShareLink(id);
      const fullUrl = `${window.location.origin}${res.share_url}`;
      setShareUrl(fullUrl);
      setIsShareModalOpen(true);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to generate share link");
    } finally {
      setShareLoading(false);
    }
  };

  const handleRevokeShare = async () => {
    if (!id) return;
    if (!window.confirm("Revoke link sharing? Previous links will cease to work.")) return;
    try {
      await api.revokeShareLink(id);
      setShareUrl(null);
      setIsShareModalOpen(false);
      void loadAll();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to revoke share link");
    }
  };

  const handleCreateSnapshot = async (e: FormEvent) => {
    e.preventDefault();
    if (!id) return;
    setSnapshotLoading(true);
    try {
      await api.createVersion(id, { message: snapshotMessage.trim() || undefined });
      setIsSnapshotModalOpen(false);
      setSnapshotMessage("");
      void loadAll();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to create snapshot");
    } finally {
      setSnapshotLoading(false);
    }
  };

  const handleRestoreVersion = async (verNum: number) => {
    if (!id) return;
    if (
      !window.confirm(
        `Restore project to version ${verNum}? Current changes will be rolled back to that snapshot.`,
      )
    )
      return;
    try {
      await api.restoreVersion(id, verNum);
      alert(`Restored to version ${verNum}`);
      void loadAll();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to restore version");
    }
  };

  const handleAddNote = async (e: FormEvent) => {
    e.preventDefault();
    if (!id || !newNoteBody.trim()) return;
    setAddingNote(true);
    try {
      const created = await api.createNote(id, { body_md: newNoteBody.trim() });
      setNotes((prev) => [created, ...prev]);
      setNewNoteBody("");
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to add note");
    } finally {
      setAddingNote(false);
    }
  };

  const handleDeleteNote = async (noteId: string) => {
    if (!id || !window.confirm("Delete this note?")) return;
    try {
      await api.deleteNote(id, noteId);
      setNotes((prev) => prev.filter((n) => n.id !== noteId));
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to delete note");
    }
  };

  const handleAddFinding = async (e: FormEvent) => {
    e.preventDefault();
    if (!id || !findingTitle.trim()) return;
    setAddingFinding(true);
    try {
      const payload: FindingCreate = {
        title: findingTitle.trim(),
        status: findingStatus,
        summary_md: findingSummary.trim() || undefined,
      };
      const created = await api.createFinding(id, payload);
      setFindings((prev) => [created, ...prev]);
      setIsFindingModalOpen(false);
      setFindingTitle("");
      setFindingSummary("");
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to create finding");
    } finally {
      setAddingFinding(false);
    }
  };

  const handleDeleteFinding = async (findingId: string) => {
    if (!id || !window.confirm("Delete this scientific finding?")) return;
    try {
      await api.deleteFinding(id, findingId);
      setFindings((prev) => prev.filter((f) => f.id !== findingId));
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to delete finding");
    }
  };

  const handleDeleteView = async (viewId: string) => {
    if (!id || !window.confirm("Delete this saved view bookmark?")) return;
    try {
      await api.deleteSavedView(id, viewId);
      setViews((prev) => prev.filter((v) => v.id !== viewId));
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to delete saved view");
    }
  };

  const handleUnlinkFile = async (fitsId: string) => {
    if (!id || !window.confirm("Unlink this FITS file from this workspace?")) return;
    try {
      await api.unlinkProjectFile(id, fitsId);
      setFiles((prev) => prev.filter((f) => f.fits_file_id !== fitsId));
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to unlink file");
    }
  };

  const handleUploadFile = async (e: FormEvent) => {
    e.preventDefault();
    if (!id || !uploadFile) return;
    setUploadingFile(true);
    try {
      const res = await api.uploadFits(
        uploadFile,
        uploadEpoch.trim() || undefined,
        project?.center_ra ?? undefined,
        project?.center_dec ?? undefined,
        id,
      );
      // Wait for job completion
      let status = res.status;
      let delay = 200;
      for (let i = 0; i < 20 && status !== "succeeded" && status !== "failed"; i++) {
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(1000, Math.round(delay * 1.25));
        const job = await api.getJob(res.job_id);
        status = job.status as typeof status;
      }
      setUploadFile(null);
      setUploadEpoch("");
      void loadAll();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to upload FITS file");
    } finally {
      setUploadingFile(false);
    }
  };

  const handleExport = async (e: FormEvent) => {
    e.preventDefault();
    if (!id) return;
    setExporting(true);
    setExportJobId(null);
    setExportStatus("queued");

    try {
      const res = await api.exportProject(id, { format: exportFormat });
      setExportJobId(res.job_id);
      setExportStatus(res.status);

      // Poll until succeeded
      let status = res.status;
      for (let i = 0; i < 30 && status !== "succeeded" && status !== "failed"; i++) {
        await new Promise((r) => setTimeout(r, 800));
        const job = await api.getJob(res.job_id);
        status = job.status as typeof status;
        setExportStatus(status);
      }
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Export job failed");
      setExportStatus("failed");
    } finally {
      setExporting(false);
    }
  };

  if (loading) {
    return (
      <section className="section">
        <div className="container" style={{ textAlign: "center", color: "var(--ink-2)" }}>
          Loading workspace details…
        </div>
      </section>
    );
  }

  if (error || !project) {
    return (
      <section className="section">
        <div
          className="container"
          style={{
            maxWidth: "40rem",
            background: "var(--paper-card)",
            padding: "var(--space-8)",
            borderRadius: "var(--radius-card)",
            textAlign: "center",
          }}
        >
          <h2 style={{ color: "var(--error-light)", marginBottom: "var(--space-4)" }}>
            Workspace Access
          </h2>
          <p style={{ color: "var(--ink-1)", marginBottom: "var(--space-6)" }}>
            {error || "Project workspace not found."}
          </p>
          <div style={{ display: "flex", gap: "var(--space-4)", justifyContent: "center" }}>
            <Link to="/projects" className="nav-pill">
              ← Return to Workspaces
            </Link>
            {!user && (
              <Link to="/login" className="btn btn-dark">
                Log In
              </Link>
            )}
          </div>
        </div>
      </section>
    );
  }

  const hasCoords = project.center_ra !== null && project.center_dec !== null;
  const obsUrl = hasCoords
    ? `/v?projectId=${project.id}&ra=${project.center_ra}&dec=${project.center_dec}&fov=${project.fov_arcmin ?? 10}`
    : `/v?projectId=${project.id}`;

  return (
    <section className="section">
      <div className="container" style={{ display: "grid", gap: "var(--space-6)" }}>
        {/* Breadcrumb & Navigation */}
        <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.88rem" }}>
          <Link to="/projects" className="nav-pill" style={{ padding: "3px 10px" }}>
            ← All Workspaces
          </Link>
          <span style={{ color: "var(--rule)" }}>/</span>
          <span style={{ color: "var(--ink-2)" }}>{project.name}</span>
        </div>

        {/* Header */}
        <header
          style={{
            background: "var(--paper-card)",
            border: "1px solid var(--rule)",
            borderRadius: "var(--radius-card)",
            padding: "var(--space-6)",
            display: "grid",
            gap: "var(--space-4)",
          }}
        >
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
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                <span
                  style={{
                    fontSize: "0.72rem",
                    letterSpacing: "0.06em",
                    textTransform: "uppercase",
                    padding: "2px 8px",
                    borderRadius: "var(--radius-pill)",
                    background:
                      project.visibility === "public"
                        ? "var(--valid-light)"
                        : project.visibility === "link"
                          ? "var(--oi-sky)"
                          : "var(--paper-pill)",
                    color:
                      project.visibility === "private" ? "var(--ink-1)" : "var(--paper-0)",
                  }}
                >
                  {project.visibility}
                </span>
                <span style={{ fontSize: "0.8rem", color: "var(--ink-2)" }}>
                  Snapshot v{project.current_version}
                </span>
              </div>
              <h1 id="main-heading" style={{ fontSize: "var(--text-3xl)", margin: "6px 0 4px" }}>
                {project.name}
              </h1>
              {project.description && (
                <p
                  style={{
                    fontFamily: "var(--font-serif)",
                    fontSize: "var(--text-base)",
                    color: "var(--ink-1)",
                    margin: 0,
                    maxWidth: "46rem",
                  }}
                >
                  {project.description}
                </p>
              )}
            </div>

            <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
              <Link
                to={obsUrl}
                className="btn btn-dark"
                title="Launch full Observatory Blink/Compare viewer for this field"
                style={{ display: "flex", alignItems: "center", gap: "6px" }}
              >
                🔭 Open in Observatory
              </Link>

              <button
                type="button"
                className="nav-pill"
                onClick={() => void handleShare()}
                disabled={shareLoading}
                title="Share project with revocable link"
              >
                🔗 Share
              </button>

              <button
                type="button"
                className="nav-pill"
                onClick={() => setIsSnapshotModalOpen(true)}
                title="Create immutable version snapshot"
              >
                📸 Snapshot
              </button>
            </div>
          </div>

          {hasCoords && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-4)",
                flexWrap: "wrap",
                fontSize: "0.85rem",
                color: "var(--ink-1)",
                fontFamily: "var(--font-mono)",
                background: "var(--paper-0)",
                padding: "8px 14px",
                borderRadius: "var(--radius-btn)",
                border: "1px solid var(--rule)",
              }}
            >
              <span>RA {project.center_ra?.toFixed(5)}°</span>
              <span>Dec {project.center_dec?.toFixed(5)}°</span>
              <span>FOV {project.fov_arcmin}′</span>
              <span style={{ color: "var(--ink-2)", fontFamily: "var(--font-sans)" }}>
                ICRS Celestial Frame
              </span>
            </div>
          )}
        </header>

        {/* Tab Navigation */}
        <nav
          aria-label="Workspace tabs"
          style={{
            display: "flex",
            gap: "var(--space-2)",
            borderBottom: "1px solid var(--rule)",
            paddingBottom: "var(--space-2)",
            overflowX: "auto",
          }}
        >
          {(
            [
              { id: "overview", label: "Overview" },
              { id: "files", label: `FITS Files (${files.length})` },
              { id: "notes", label: `Notes (${notes.length})` },
              { id: "findings", label: `Findings (${findings.length})` },
              { id: "views", label: `Saved Views (${views.length})` },
              { id: "history", label: `History (${versions.length})` },
              { id: "export", label: "Export" },
            ] as const
          ).map((t) => (
            <button
              key={t.id}
              type="button"
              className="nav-pill"
              aria-current={activeTab === t.id ? "page" : undefined}
              onClick={() => setActiveTab(t.id)}
              style={{
                background: activeTab === t.id ? "var(--ink-0)" : undefined,
                color: activeTab === t.id ? "var(--paper-0)" : undefined,
                fontWeight: activeTab === t.id ? 600 : 400,
                fontSize: "0.88rem",
              }}
            >
              {t.label}
            </button>
          ))}
        </nav>

        {/* TAB 1: OVERVIEW */}
        {activeTab === "overview" && (
          <div style={{ display: "grid", gap: "var(--space-6)" }}>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                gap: "var(--space-4)",
              }}
            >
              <div
                style={{
                  background: "var(--paper-card)",
                  padding: "var(--space-4)",
                  borderRadius: "var(--radius-card)",
                  border: "1px solid var(--rule)",
                }}
              >
                <span className="drawer-kicker">Field Coordinates</span>
                <p
                  style={{
                    fontSize: "var(--text-lg)",
                    fontFamily: "var(--font-mono)",
                    margin: "4px 0",
                  }}
                >
                  {project.center_ra?.toFixed(3)}°, {project.center_dec?.toFixed(3)}°
                </p>
                <span style={{ fontSize: "0.8rem", color: "var(--ink-2)" }}>
                  FOV: {project.fov_arcmin} arcmin
                </span>
              </div>

              <div
                style={{
                  background: "var(--paper-card)",
                  padding: "var(--space-4)",
                  borderRadius: "var(--radius-card)",
                  border: "1px solid var(--rule)",
                }}
              >
                <span className="drawer-kicker">Observational Epochs</span>
                <p style={{ fontSize: "var(--text-2xl)", margin: "4px 0" }}>{files.length}</p>
                <span style={{ fontSize: "0.8rem", color: "var(--ink-2)" }}>Linked FITS frames</span>
              </div>

              <div
                style={{
                  background: "var(--paper-card)",
                  padding: "var(--space-4)",
                  borderRadius: "var(--radius-card)",
                  border: "1px solid var(--rule)",
                }}
              >
                <span className="drawer-kicker">Field Notes</span>
                <p style={{ fontSize: "var(--text-2xl)", margin: "4px 0" }}>{notes.length}</p>
                <span style={{ fontSize: "0.8rem", color: "var(--ink-2)" }}>Markdown entries</span>
              </div>

              <div
                style={{
                  background: "var(--paper-card)",
                  padding: "var(--space-4)",
                  borderRadius: "var(--radius-card)",
                  border: "1px solid var(--rule)",
                }}
              >
                <span className="drawer-kicker">Candidate Findings</span>
                <p style={{ fontSize: "var(--text-2xl)", margin: "4px 0" }}>{findings.length}</p>
                <span style={{ fontSize: "0.8rem", color: "var(--ink-2)" }}>
                  Tracked scientific findings
                </span>
              </div>
            </div>

            {/* Launch Banner */}
            <div
              style={{
                background: "var(--sky-0)",
                color: "var(--sky-text)",
                borderRadius: "var(--radius-hero)",
                padding: "var(--space-8)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: "var(--space-6)",
              }}
            >
              <div>
                <span
                  style={{
                    color: "var(--oi-orange)",
                    fontSize: "0.78rem",
                    letterSpacing: "0.08em",
                    textTransform: "uppercase",
                  }}
                >
                  Observatory Ready
                </span>
                <h2 style={{ fontSize: "var(--text-2xl)", margin: "4px 0 8px" }}>
                  Analyze Trajectories on the Sky
                </h2>
                <p style={{ color: "var(--sky-muted)", maxWidth: "34rem", margin: 0 }}>
                  Launch the WebGL2 blink comparator and Hungarian association engine with all
                  workspace epochs and coordinates automatically pre-loaded.
                </p>
              </div>
              <Link
                to={obsUrl}
                className="btn btn-dark"
                style={{
                  background: "var(--paper-0)",
                  color: "var(--ink-0)",
                  padding: "10px 22px",
                  fontSize: "1rem",
                }}
              >
                Launch Observatory →
              </Link>
            </div>
          </div>
        )}

        {/* TAB 2: FITS FILES */}
        {activeTab === "files" && (
          <div style={{ display: "grid", gap: "var(--space-6)" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: "var(--space-4)",
              }}
            >
              <div>
                <h2 style={{ fontSize: "var(--text-xl)", margin: 0 }}>Linked FITS Files</h2>
                <p style={{ fontSize: "0.88rem", color: "var(--ink-2)", margin: 0 }}>
                  Observational epochs and calibration frames curated for this workspace (1 frame
                  per epoch).
                </p>
              </div>
            </div>

            {files.length === 0 ? (
              <div
                style={{
                  padding: "var(--space-8)",
                  background: "var(--paper-card)",
                  borderRadius: "var(--radius-card)",
                  textAlign: "center",
                  border: "1px dashed var(--rule)",
                }}
              >
                <p style={{ color: "var(--ink-2)" }}>No FITS files linked to this workspace yet.</p>
              </div>
            ) : (
              <div
                style={{
                  display: "grid",
                  gap: "var(--space-3)",
                }}
              >
                {files.map((f) => (
                  <div
                    key={f.fits_file_id}
                    style={{
                      background: "var(--paper-card)",
                      border: "1px solid var(--rule)",
                      borderRadius: "var(--radius-btn)",
                      padding: "var(--space-4)",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      flexWrap: "wrap",
                      gap: "var(--space-3)",
                    }}
                  >
                    <div>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "8px",
                          marginBottom: "4px",
                        }}
                      >
                        <span
                          style={{
                            fontSize: "0.72rem",
                            padding: "2px 6px",
                            borderRadius: "var(--radius-pill)",
                            background: "var(--paper-pill)",
                            color: "var(--ink-1)",
                            textTransform: "uppercase",
                          }}
                        >
                          {f.origin}
                        </span>
                        <strong style={{ fontSize: "0.95rem" }}>{f.filename}</strong>
                        {f.band_label && (
                          <span style={{ fontSize: "0.8rem", color: "var(--ink-2)" }}>
                            ({f.band_label})
                          </span>
                        )}
                      </div>
                      <div
                        style={{
                          fontSize: "0.8rem",
                          color: "var(--ink-2)",
                          fontFamily: "var(--font-mono)",
                        }}
                      >
                        Date: {f.utc_mid.slice(0, 10)} · MJD {f.mjd_tdb_mid.toFixed(2)}
                        {f.naxis1 && f.naxis2 && ` · ${f.naxis1}×${f.naxis2} px`}
                      </div>
                    </div>

                    <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
                      <Link
                        to={`/v?projectId=${project.id}&fitsId=${f.fits_file_id}`}
                        className="nav-pill"
                        style={{ fontSize: "0.82rem", padding: "4px 10px" }}
                      >
                        🔭 View
                      </Link>

                      <a
                        href={f.fits_url}
                        download={`${f.filename}.fits`}
                        className="nav-pill"
                        style={{ fontSize: "0.82rem", padding: "4px 10px" }}
                      >
                        ⬇ Download
                      </a>

                      <button
                        type="button"
                        className="nav-pill"
                        style={{
                          fontSize: "0.82rem",
                          padding: "4px 8px",
                          color: "var(--error-light)",
                        }}
                        onClick={() => void handleUnlinkFile(f.fits_file_id)}
                        title="Unlink file from workspace"
                      >
                        Unlink
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Direct Upload Section */}
            <div
              style={{
                background: "var(--paper-card)",
                border: "1px solid var(--rule)",
                borderRadius: "var(--radius-card)",
                padding: "var(--space-6)",
                display: "grid",
                gap: "var(--space-4)",
              }}
            >
              <h3 style={{ fontSize: "var(--text-lg)", margin: 0 }}>
                Attach New FITS Epoch to Workspace
              </h3>
              <form
                onSubmit={(e) => void handleUploadFile(e)}
                style={{
                  display: "flex",
                  gap: "var(--space-3)",
                  alignItems: "center",
                  flexWrap: "wrap",
                }}
              >
                <input
                  type="file"
                  accept=".fits,.fit,.fts,.fits.gz,.fz"
                  required
                  onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
                  style={{ fontSize: "0.85rem" }}
                />

                <input
                  type="text"
                  placeholder="User Epoch (e.g. 2025.5)"
                  value={uploadEpoch}
                  onChange={(e) => setUploadEpoch(e.target.value)}
                  style={{
                    padding: "6px 10px",
                    borderRadius: "var(--radius-btn)",
                    border: "1px solid var(--rule)",
                    background: "var(--paper-0)",
                    fontSize: "0.85rem",
                  }}
                />

                <button
                  type="submit"
                  className="btn btn-dark"
                  disabled={uploadingFile || !uploadFile}
                  style={{ padding: "6px 14px", fontSize: "0.85rem" }}
                >
                  {uploadingFile ? "Uploading…" : "Upload to Workspace"}
                </button>
              </form>
            </div>
          </div>
        )}

        {/* TAB 3: NOTES */}
        {activeTab === "notes" && (
          <div style={{ display: "grid", gap: "var(--space-6)" }}>
            <div
              style={{
                background: "var(--paper-card)",
                border: "1px solid var(--rule)",
                borderRadius: "var(--radius-card)",
                padding: "var(--space-6)",
                display: "grid",
                gap: "var(--space-3)",
              }}
            >
              <h3 style={{ fontSize: "var(--text-lg)", margin: 0 }}>Add Observational Note</h3>
              <form onSubmit={(e) => void handleAddNote(e)} style={{ display: "grid", gap: "8px" }}>
                <textarea
                  rows={4}
                  required
                  placeholder="Record observational notes, astrometric vetting comments, or instrument quirks in Markdown…"
                  value={newNoteBody}
                  onChange={(e) => setNewNoteBody(e.target.value)}
                  style={{
                    padding: "10px 14px",
                    borderRadius: "var(--radius-btn)",
                    border: "1px solid var(--rule)",
                    background: "var(--paper-0)",
                    fontSize: "0.9rem",
                    fontFamily: "var(--font-sans)",
                    color: "var(--ink-0)",
                  }}
                />
                <div style={{ display: "flex", justifyContent: "flex-end" }}>
                  <button
                    type="submit"
                    className="btn btn-dark"
                    disabled={addingNote || !newNoteBody.trim()}
                    style={{ padding: "6px 16px", fontSize: "0.88rem" }}
                  >
                    {addingNote ? "Saving…" : "Save Note"}
                  </button>
                </div>
              </form>
            </div>

            {notes.length === 0 ? (
              <p style={{ textAlign: "center", color: "var(--ink-2)", padding: "var(--space-6)" }}>
                No notes recorded yet for this workspace.
              </p>
            ) : (
              <div style={{ display: "grid", gap: "var(--space-4)" }}>
                {notes.map((n) => (
                  <article
                    key={n.id}
                    style={{
                      background: "var(--paper-card)",
                      border: "1px solid var(--rule)",
                      borderRadius: "var(--radius-btn)",
                      padding: "var(--space-4)",
                      display: "grid",
                      gap: "var(--space-2)",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                      }}
                    >
                      <span style={{ fontSize: "0.75rem", color: "var(--ink-2)" }}>
                        {n.created_at ? new Date(n.created_at).toLocaleString() : "Recently logged"}
                      </span>
                      <button
                        type="button"
                        className="nav-pill"
                        style={{
                          fontSize: "0.75rem",
                          padding: "2px 8px",
                          color: "var(--error-light)",
                        }}
                        onClick={() => void handleDeleteNote(n.id)}
                      >
                        Delete
                      </button>
                    </div>
                    <div
                      style={{
                        fontSize: "0.92rem",
                        color: "var(--ink-0)",
                        lineHeight: 1.5,
                        whiteSpace: "pre-wrap",
                      }}
                    >
                      {n.body_md}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        )}

        {/* TAB 4: FINDINGS */}
        {activeTab === "findings" && (
          <div style={{ display: "grid", gap: "var(--space-6)" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: "var(--space-4)",
              }}
            >
              <div>
                <h2 style={{ fontSize: "var(--text-xl)", margin: 0 }}>Scientific Findings</h2>
                <p style={{ fontSize: "0.88rem", color: "var(--ink-2)", margin: 0 }}>
                  Verified motion tracks, candidate brown dwarfs, and solar system objects.
                </p>
              </div>

              <button
                type="button"
                className="btn btn-dark"
                onClick={() => setIsFindingModalOpen(true)}
                style={{ padding: "6px 14px", fontSize: "0.85rem" }}
              >
                + New Finding
              </button>
            </div>

            {findings.length === 0 ? (
              <div
                style={{
                  padding: "var(--space-8)",
                  background: "var(--paper-card)",
                  borderRadius: "var(--radius-card)",
                  textAlign: "center",
                  border: "1px dashed var(--rule)",
                }}
              >
                <p style={{ color: "var(--ink-2)" }}>No findings logged yet.</p>
                <button
                  type="button"
                  className="btn btn-dark"
                  onClick={() => setIsFindingModalOpen(true)}
                  style={{ marginTop: "var(--space-2)" }}
                >
                  Log First Finding
                </button>
              </div>
            ) : (
              <div style={{ display: "grid", gap: "var(--space-3)" }}>
                {findings.map((f) => (
                  <div
                    key={f.id}
                    style={{
                      background: "var(--paper-card)",
                      border: "1px solid var(--rule)",
                      borderRadius: "var(--radius-btn)",
                      padding: "var(--space-4)",
                      display: "grid",
                      gap: "var(--space-2)",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: "var(--space-2)",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <span
                          style={{
                            fontSize: "0.72rem",
                            padding: "2px 8px",
                            borderRadius: "var(--radius-pill)",
                            background:
                              f.status === "candidate"
                                ? "var(--oi-orange)"
                                : f.status === "submitted"
                                  ? "var(--valid-light)"
                                  : "var(--paper-pill)",
                            color:
                              f.status === "draft" ? "var(--ink-1)" : "var(--paper-0)",
                            textTransform: "uppercase",
                          }}
                        >
                          {f.status}
                        </span>
                        <strong style={{ fontSize: "1rem" }}>{f.title}</strong>
                      </div>

                      <button
                        type="button"
                        className="nav-pill"
                        style={{
                          fontSize: "0.75rem",
                          padding: "2px 8px",
                          color: "var(--error-light)",
                        }}
                        onClick={() => void handleDeleteFinding(f.id)}
                      >
                        Delete
                      </button>
                    </div>

                    {f.summary_md && (
                      <p style={{ fontSize: "0.9rem", color: "var(--ink-1)", margin: "4px 0" }}>
                        {f.summary_md}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* TAB 5: SAVED VIEWS */}
        {activeTab === "views" && (
          <div style={{ display: "grid", gap: "var(--space-6)" }}>
            <div>
              <h2 style={{ fontSize: "var(--text-xl)", margin: 0 }}>Saved Viewer Bookmarks</h2>
              <p style={{ fontSize: "0.88rem", color: "var(--ink-2)", margin: 0 }}>
                Exact observatory states (coordinates, stretch, layers, and active frame) bookmarked
                from the viewer.
              </p>
            </div>

            {views.length === 0 ? (
              <div
                style={{
                  padding: "var(--space-8)",
                  background: "var(--paper-card)",
                  borderRadius: "var(--radius-card)",
                  textAlign: "center",
                  border: "1px dashed var(--rule)",
                }}
              >
                <p style={{ color: "var(--ink-2)" }}>No views saved yet.</p>
                <p style={{ fontSize: "0.85rem", color: "var(--ink-2)" }}>
                  Open the Observatory viewer while working on this workspace and click "💾 Bookmark
                  View" in the top bar to save a view here.
                </p>
              </div>
            ) : (
              <div style={{ display: "grid", gap: "var(--space-3)" }}>
                {views.map((v) => (
                  <div
                    key={v.id}
                    style={{
                      background: "var(--paper-card)",
                      border: "1px solid var(--rule)",
                      borderRadius: "var(--radius-btn)",
                      padding: "var(--space-4)",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: "var(--space-3)",
                      flexWrap: "wrap",
                    }}
                  >
                    <div>
                      <strong style={{ fontSize: "0.95rem" }}>{v.name || "Untitled View"}</strong>
                      <div
                        style={{
                          fontSize: "0.8rem",
                          color: "var(--ink-2)",
                          fontFamily: "var(--font-mono)",
                          marginTop: "2px",
                        }}
                      >
                        ?{v.url_state}
                      </div>
                    </div>

                    <div style={{ display: "flex", gap: "var(--space-2)" }}>
                      <Link
                        to={`/v?${v.url_state}&projectId=${project.id}`}
                        className="btn btn-dark"
                        style={{ fontSize: "0.82rem", padding: "4px 12px" }}
                      >
                        Open View →
                      </Link>

                      <button
                        type="button"
                        className="nav-pill"
                        style={{
                          fontSize: "0.82rem",
                          padding: "4px 8px",
                          color: "var(--error-light)",
                        }}
                        onClick={() => void handleDeleteView(v.id)}
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* TAB 6: HISTORY & VERSIONS */}
        {activeTab === "history" && (
          <div style={{ display: "grid", gap: "var(--space-6)" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: "var(--space-4)",
              }}
            >
              <div>
                <h2 style={{ fontSize: "var(--text-xl)", margin: 0 }}>Version History</h2>
                <p style={{ fontSize: "0.88rem", color: "var(--ink-2)", margin: 0 }}>
                  Immutable snapshots of workspace states, notes, findings, and FITS files.
                </p>
              </div>

              <button
                type="button"
                className="btn btn-dark"
                onClick={() => setIsSnapshotModalOpen(true)}
                style={{ padding: "6px 14px", fontSize: "0.85rem" }}
              >
                + Snapshot Version
              </button>
            </div>

            {versions.length === 0 ? (
              <p style={{ textAlign: "center", color: "var(--ink-2)", padding: "var(--space-6)" }}>
                No version snapshots recorded yet.
              </p>
            ) : (
              <div style={{ display: "grid", gap: "var(--space-3)" }}>
                {versions.map((ver) => (
                  <div
                    key={ver.version}
                    style={{
                      background: "var(--paper-card)",
                      border: "1px solid var(--rule)",
                      borderRadius: "var(--radius-btn)",
                      padding: "var(--space-4)",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: "var(--space-3)",
                      flexWrap: "wrap",
                    }}
                  >
                    <div>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "8px",
                          marginBottom: "4px",
                        }}
                      >
                        <span
                          style={{
                            fontWeight: 600,
                            fontFamily: "var(--font-mono)",
                            fontSize: "0.95rem",
                          }}
                        >
                          Version {ver.version}
                        </span>
                        {ver.message && (
                          <span style={{ fontSize: "0.9rem", color: "var(--ink-1)" }}>
                            — {ver.message}
                          </span>
                        )}
                      </div>
                      <span style={{ fontSize: "0.8rem", color: "var(--ink-2)" }}>
                        {ver.created_at ? new Date(ver.created_at).toLocaleString() : ""}
                      </span>
                    </div>

                    <button
                      type="button"
                      className="nav-pill"
                      style={{ fontSize: "0.82rem", padding: "4px 12px" }}
                      onClick={() => void handleRestoreVersion(ver.version)}
                    >
                      Restore to v{ver.version}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* TAB 7: EXPORT */}
        {activeTab === "export" && (
          <div style={{ display: "grid", gap: "var(--space-6)", maxWidth: "44rem" }}>
            <div>
              <h2 style={{ fontSize: "var(--text-xl)", margin: 0 }}>Export Workspace Data</h2>
              <p style={{ fontSize: "0.88rem", color: "var(--ink-2)", margin: 0 }}>
                Generate publication-ready packages (CSV tables, FITS BINTABLE, JSON bundles, or
                animated GIFs).
              </p>
            </div>

            <form
              onSubmit={(e) => void handleExport(e)}
              style={{
                background: "var(--paper-card)",
                padding: "var(--space-6)",
                border: "1px solid var(--rule)",
                borderRadius: "var(--radius-card)",
                display: "grid",
                gap: "var(--space-4)",
              }}
            >
              <div style={{ display: "grid", gap: "6px" }}>
                <label htmlFor="export-fmt" style={{ fontSize: "0.88rem", fontWeight: 600 }}>
                  Export Format
                </label>
                <select
                  id="export-fmt"
                  value={exportFormat}
                  onChange={(e) => setExportFormat(e.target.value as ExportRequest["format"])}
                  style={{
                    padding: "8px 12px",
                    borderRadius: "var(--radius-btn)",
                    border: "1px solid var(--rule)",
                    background: "var(--paper-0)",
                    color: "var(--ink-0)",
                  }}
                >
                  <option value="json">JSON (Full Workspace Bundle)</option>
                  <option value="csv">CSV (Astrometric Measurements Table)</option>
                  <option value="fits">FITS (Binary Table HDU)</option>
                  <option value="gif">GIF (Animated Multi-Epoch Movie)</option>
                  <option value="png">PNG (Composite Summary Plot)</option>
                  <option value="ades">ADES XML (IAU Minor Planet Center Format)</option>
                </select>
              </div>

              <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center" }}>
                <button
                  type="submit"
                  className="btn btn-dark"
                  disabled={exporting}
                  style={{ padding: "8px 20px" }}
                >
                  {exporting ? "Generating Export…" : "Export Project"}
                </button>

                {exportStatus && (
                  <span
                    style={{
                      fontSize: "0.85rem",
                      fontFamily: "var(--font-mono)",
                      color: exportStatus === "succeeded" ? "var(--valid-light)" : "var(--ink-2)",
                    }}
                  >
                    Status: {exportStatus}
                  </span>
                )}
              </div>

              {exportJobId && exportStatus === "succeeded" && (
                <div
                  style={{
                    marginTop: "var(--space-2)",
                    padding: "10px 14px",
                    background: "var(--paper-0)",
                    border: "1px solid var(--valid-light)",
                    borderRadius: "var(--radius-btn)",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <span style={{ fontSize: "0.88rem" }}>
                    Export generated successfully ({exportFormat.toUpperCase()}).
                  </span>
                  <a
                    href={api.downloadProjectExportUrl(project.id, exportJobId, shareToken)}
                    download={`project_${project.id}_export.${exportFormat}`}
                    className="btn btn-dark"
                    style={{ fontSize: "0.82rem", padding: "4px 12px" }}
                  >
                    ⬇ Download Artifact
                  </a>
                </div>
              )}
            </form>

            {/* Citation Snippet */}
            <div
              style={{
                background: "var(--paper-card)",
                padding: "var(--space-6)",
                border: "1px solid var(--rule)",
                borderRadius: "var(--radius-card)",
                display: "grid",
                gap: "var(--space-2)",
              }}
            >
              <h3 style={{ fontSize: "var(--text-base)", margin: 0 }}>Cite this Workspace</h3>
              <p style={{ fontSize: "0.82rem", color: "var(--ink-2)", margin: 0 }}>
                Copyable BibTeX snippet for publication manuscripts:
              </p>
              <pre
                style={{
                  background: "var(--paper-0)",
                  padding: "10px",
                  borderRadius: "var(--radius-btn)",
                  border: "1px solid var(--rule)",
                  fontSize: "0.78rem",
                  fontFamily: "var(--font-mono)",
                  overflowX: "auto",
                }}
              >
                {`@misc{skytrace_${project.id.slice(0, 8)},
  title = {${project.name}},
  author = {SkyTrace Observational Platform},
  year = {${new Date().getFullYear()}},
  url = {${window.location.href}}
}`}
              </pre>
            </div>
          </div>
        )}

        {/* MODAL: SHARE PROJECT */}
        {isShareModalOpen && (
          <div
            className="archive-modal-backdrop"
            role="dialog"
            aria-modal="true"
            aria-label="Share Project"
            onClick={(e) => {
              if (e.target === e.currentTarget) setIsShareModalOpen(false);
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
                <h2 style={{ fontSize: "var(--text-xl)", margin: 0 }}>Share Project Workspace</h2>
                <button
                  type="button"
                  className="btn btn-icon-sq"
                  onClick={() => setIsShareModalOpen(false)}
                >
                  ✕
                </button>
              </div>

              <div style={{ display: "grid", gap: "var(--space-4)", marginTop: "var(--space-4)" }}>
                <p style={{ fontSize: "0.9rem", color: "var(--ink-1)", margin: 0 }}>
                  Anyone with this link can view this workspace, inspect FITS epochs, read notes, and
                  open the field in the Observatory:
                </p>

                {shareUrl && (
                  <div style={{ display: "flex", gap: "8px" }}>
                    <input
                      type="text"
                      readOnly
                      value={shareUrl}
                      style={{
                        flex: 1,
                        padding: "8px 12px",
                        borderRadius: "var(--radius-btn)",
                        border: "1px solid var(--rule)",
                        background: "var(--paper-card)",
                        fontFamily: "var(--font-mono)",
                        fontSize: "0.82rem",
                      }}
                      onFocus={(e) => e.target.select()}
                    />
                    <button
                      type="button"
                      className="btn btn-dark"
                      onClick={() => {
                        void navigator.clipboard.writeText(shareUrl);
                        alert("Share link copied to clipboard!");
                      }}
                    >
                      Copy
                    </button>
                  </div>
                )}

                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    borderTop: "1px solid var(--rule)",
                    paddingTop: "var(--space-4)",
                  }}
                >
                  <button
                    type="button"
                    className="nav-pill"
                    style={{ color: "var(--error-light)" }}
                    onClick={() => void handleRevokeShare()}
                  >
                    Revoke Share Link
                  </button>

                  <button
                    type="button"
                    className="btn btn-dark"
                    onClick={() => setIsShareModalOpen(false)}
                  >
                    Done
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* MODAL: CREATE SNAPSHOT */}
        {isSnapshotModalOpen && (
          <div
            className="archive-modal-backdrop"
            role="dialog"
            aria-modal="true"
            aria-label="Snapshot Version"
            onClick={(e) => {
              if (e.target === e.currentTarget) setIsSnapshotModalOpen(false);
            }}
          >
            <div
              className="archive-modal-content"
              style={{
                background: "var(--paper-0)",
                color: "var(--ink-0)",
                border: "1px solid var(--rule)",
                maxWidth: "32rem",
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
                <h2 style={{ fontSize: "var(--text-xl)", margin: 0 }}>Create Version Snapshot</h2>
                <button
                  type="button"
                  className="btn btn-icon-sq"
                  onClick={() => setIsSnapshotModalOpen(false)}
                >
                  ✕
                </button>
              </div>

              <form
                onSubmit={(e) => void handleCreateSnapshot(e)}
                style={{ display: "grid", gap: "var(--space-4)", marginTop: "var(--space-4)" }}
              >
                <div style={{ display: "grid", gap: "4px" }}>
                  <label htmlFor="ver-msg" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                    Snapshot Commit Message
                  </label>
                  <input
                    id="ver-msg"
                    type="text"
                    placeholder="e.g. Added 2025 FITS epoch and confirmed proper motion"
                    value={snapshotMessage}
                    onChange={(e) => setSnapshotMessage(e.target.value)}
                    style={{
                      padding: "8px 12px",
                      borderRadius: "var(--radius-btn)",
                      border: "1px solid var(--rule)",
                      background: "var(--paper-card)",
                    }}
                  />
                </div>

                <div
                  style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-3)" }}
                >
                  <button
                    type="button"
                    className="nav-pill"
                    onClick={() => setIsSnapshotModalOpen(false)}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-dark" disabled={snapshotLoading}>
                    {snapshotLoading ? "Saving…" : "Save Snapshot"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* MODAL: CREATE FINDING */}
        {isFindingModalOpen && (
          <div
            className="archive-modal-backdrop"
            role="dialog"
            aria-modal="true"
            aria-label="New Finding"
            onClick={(e) => {
              if (e.target === e.currentTarget) setIsFindingModalOpen(false);
            }}
          >
            <div
              className="archive-modal-content"
              style={{
                background: "var(--paper-0)",
                color: "var(--ink-0)",
                border: "1px solid var(--rule)",
                maxWidth: "34rem",
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
                <h2 style={{ fontSize: "var(--text-xl)", margin: 0 }}>Log Scientific Finding</h2>
                <button
                  type="button"
                  className="btn btn-icon-sq"
                  onClick={() => setIsFindingModalOpen(false)}
                >
                  ✕
                </button>
              </div>

              <form
                onSubmit={(e) => void handleAddFinding(e)}
                style={{ display: "grid", gap: "var(--space-4)", marginTop: "var(--space-4)" }}
              >
                <div style={{ display: "grid", gap: "4px" }}>
                  <label htmlFor="find-title" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                    Finding Title *
                  </label>
                  <input
                    id="find-title"
                    type="text"
                    required
                    placeholder="e.g. Candidate High-Proper-Motion Brown Dwarf"
                    value={findingTitle}
                    onChange={(e) => setFindingTitle(e.target.value)}
                    style={{
                      padding: "8px 12px",
                      borderRadius: "var(--radius-btn)",
                      border: "1px solid var(--rule)",
                      background: "var(--paper-card)",
                    }}
                  />
                </div>

                <div style={{ display: "grid", gap: "4px" }}>
                  <label htmlFor="find-status" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                    Status
                  </label>
                  <select
                    id="find-status"
                    value={findingStatus}
                    onChange={(e) =>
                      setFindingStatus(
                        e.target.value as "draft" | "candidate" | "submitted" | "known" | "rejected",
                      )
                    }
                    style={{
                      padding: "8px 12px",
                      borderRadius: "var(--radius-btn)",
                      border: "1px solid var(--rule)",
                      background: "var(--paper-card)",
                    }}
                  >
                    <option value="draft">Draft (Preliminary inspection)</option>
                    <option value="candidate">Candidate (Verified motion fit)</option>
                    <option value="submitted">Submitted (External catalogue/MPC report)</option>
                    <option value="known">Known (Archival cross-match)</option>
                    <option value="rejected">Rejected (Artifact / Variable false-positive)</option>
                  </select>
                </div>

                <div style={{ display: "grid", gap: "4px" }}>
                  <label htmlFor="find-summary" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                    Summary / Astrometric Evidence
                  </label>
                  <textarea
                    id="find-summary"
                    rows={4}
                    placeholder="Notes on proper motion vectors, parallax significance, band colors, and noise checks…"
                    value={findingSummary}
                    onChange={(e) => setFindingSummary(e.target.value)}
                    style={{
                      padding: "8px 12px",
                      borderRadius: "var(--radius-btn)",
                      border: "1px solid var(--rule)",
                      background: "var(--paper-card)",
                      fontFamily: "var(--font-sans)",
                    }}
                  />
                </div>

                <div
                  style={{ display: "flex", justifyContent: "flex-end", gap: "var(--space-3)" }}
                >
                  <button
                    type="button"
                    className="nav-pill"
                    onClick={() => setIsFindingModalOpen(false)}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-dark" disabled={addingFinding}>
                    {addingFinding ? "Saving…" : "Save Finding"}
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
