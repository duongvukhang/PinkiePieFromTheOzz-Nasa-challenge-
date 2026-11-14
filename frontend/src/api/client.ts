import type { components } from "./schema";
import { useAuthStore } from "../app/authStore";

export type Schema<K extends keyof components["schemas"]> = components["schemas"][K];
export type ProblemDetail = Schema<"ProblemDetail">;
export type CutoutFrame = Schema<"CutoutFrame">;
export type DataReleaseRead = Schema<"DataReleaseRead">;
export type ExampleTargetSummary = Schema<"ExampleTargetSummary">;
export type CatalogOverlaySource = Schema<"CatalogOverlaySource">;
export type SuggestionItem = Schema<"SuggestionItem">;
export type DetectionRead = Schema<"DetectionRead">;
export type TrackedObjectDetailResponse = Schema<"TrackedObjectDetailResponse">;
export type SpectrumResponse = Schema<"SpectrumResponse">;
export type ObjectAssociationsResponse = Schema<"ObjectAssociationsResponse">;
export type AssociationHypothesis = Schema<"AssociationHypothesis">;
export type AssociationOverrideRequest = Schema<"AssociationOverrideRequest">;
export type AssociationOverrideResponse = Schema<"AssociationOverrideResponse">;
export type CompareRequest = Schema<"CompareRequest">;
export type CompareAcceptedResponse = Schema<"CompareAcceptedResponse">;
export type CompareResultResponse = Schema<"CompareResultResponse">;
export type EpochMeasurementRead = Schema<"EpochMeasurementRead">;
export type MotionFitRead = Schema<"MotionFitRead">;
export type ClassificationSummary = Schema<"ClassificationSummary">;
export type UploadResponse = Schema<"UploadResponse">;
export type UploadCompatibilityReport = Schema<"UploadCompatibilityReport"> & {
  ra_center_deg?: number | null;
  dec_center_deg?: number | null;
};

export type ProjectCreate = Schema<"ProjectCreate">;
export type ProjectRead = Schema<"ProjectRead">;
export type ProjectUpdate = Schema<"ProjectUpdate">;
export type NoteCreate = Schema<"NoteCreate">;
export type NoteRead = Schema<"NoteRead">;
export type NoteUpdate = Schema<"NoteUpdate">;
export type FindingCreate = Schema<"FindingCreate">;
export type FindingRead = Schema<"FindingRead">;
export type FindingUpdate = Schema<"FindingUpdate">;
export type SavedViewCreate = Schema<"SavedViewCreate">;
export type SavedViewRead = Schema<"SavedViewRead">;
export type ProjectVersionCreate = Schema<"ProjectVersionCreate">;
export type ProjectVersionRead = Schema<"ProjectVersionRead">;
export type ProjectShareResponse = Schema<"ProjectShareResponse">;
export type ExportRequest = Schema<"ExportRequest">;
export interface PaginatedProjects {
  items: ProjectRead[];
  next_cursor?: string | null;
}

export type ArchiveLoadRequest = Schema<"ArchiveLoadRequest">;
export type ArchiveSourceId = NonNullable<ArchiveLoadRequest["source"]>;

export interface UploadedFitsSummary {
  fits_file_id: string;
  filename: string;
  obs_id?: string;
  origin: string;
  epoch: string;
  mjd_tdb_mid: number;
  utc_mid: string;
  ra_center?: number;
  dec_center?: number;
  naxis1?: number;
  naxis2?: number;
  wcs_header: string;
  display_url: string;
  science_url: string;
  fits_url: string;
  band_label?: string;
  stretch?: { vmin?: number; vmax?: number; shape?: [number, number] };
}

export interface ArchiveLoadResponse {
  obs_id: string;
  storage_key: string;
  status: string;
  source: string;
  fits_file_id?: string;
  mjd_tdb_mid?: number;
  utc_mid?: string;
  ra_center?: number;
  dec_center?: number;
  naxis1?: number;
  naxis2?: number;
  wcs_header?: string;
  band_label?: string;
  display_url?: string;
  science_url?: string;
  fits_url?: string;
}
export type EpochProduct = Schema<"EpochProduct">;
export type ObservationEpoch = Schema<"ObservationEpoch">;
export type ArchiveRecord = Schema<"ArchiveRecord">;
export type ArchiveSearchResponse = Schema<"ArchiveSearchResponse">;

export interface ArchiveSearchParams {
  ra: number;
  dec: number;
  radius?: number;
  radius_unit?: "arcsec" | "arcmin" | "deg";
  source?: ArchiveSourceId;
  from?: string;
  to?: string;
  band?: string;
  max_records?: number;
}

export interface ScienceBuffer {
  data: Float32Array;
  width: number;
  height: number;
}

export class ApiError extends Error {
  status: number;
  scienceImpact?: string;
  constructor(status: number, message: string, scienceImpact?: string) {
    super(message);
    this.status = status;
    this.scienceImpact = scienceImpact;
  }
}

const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined) ??
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "";

function buildUrl(path: string, params?: Record<string, string | number | undefined>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return `${API_BASE}${path}${s ? `?${s}` : ""}`;
}

function getAuthHeaders(shareToken?: string): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = useAuthStore.getState().user?.token;
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  if (shareToken) {
    headers["X-Share-Token"] = shareToken;
  }
  return headers;
}

export async function apiJson<T>(path: string, init?: RequestInit, shareToken?: string): Promise<T> {
  const authHeaders = getAuthHeaders(shareToken);
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders, ...init?.headers },
  });
  const text = await res.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    const isHtml = /^\s*<(!doctype|html)/i.test(text);
    throw new ApiError(
      res.ok ? 502 : res.status,
      isHtml
        ? "Backend API unavailable (received HTML instead of JSON). Start the API server on port 8000."
        : res.statusText || `Invalid JSON response (${res.status})`,
    );
  }
  if (!res.ok) {
    const err = (data ?? {}) as Partial<ProblemDetail>;
    throw new ApiError(
      res.status,
      err.detail ?? err.title ?? res.statusText,
      err.science_impact ?? undefined,
    );
  }
  return data as T;
}

const postJson = <T>(path: string, body: unknown) =>
  apiJson<T>(path, { method: "POST", body: JSON.stringify(body) });

export async function fetchScienceFrame(url: string): Promise<ScienceBuffer> {
  const res = await fetch(url.startsWith("http") ? url : `${API_BASE}${url}`);
  if (!res.ok || (res.headers.get("content-type") ?? "").includes("text/html")) {
    throw new ApiError(res.ok ? 502 : res.status, `Science frame load failed (${res.status})`);
  }
  const shape = (res.headers.get("X-Shape") ?? "")
    .split(",")
    .map((n) => Number.parseInt(n.trim(), 10))
    .filter((n) => Number.isFinite(n) && n > 0);
  const data = new Float32Array(await res.arrayBuffer());
  const side = Math.max(1, Math.round(Math.sqrt(data.length)));
  return { data, width: shape[1] ?? side, height: shape[0] ?? side };
}

export interface EphemerisTrajectoryPoint {
  time_mjd: number;
  date_iso: string;
  ra_deg: number;
  dec_deg: number;
  uncertainty_semi_major_arcsec: number;
  uncertainty_semi_minor_arcsec: number;
  pa_deg: number;
  is_extrapolated: boolean;
  method: string;
}

export interface EphemerisPredictionResult {
  object_id: string;
  trajectory: EphemerisTrajectoryPoint[];
  preferred_model?: string;
  baseline_days: number;
}

export const api = {
  resolveTarget: (name: string) =>
    apiJson<Schema<"ResolveResponse">>(buildUrl("/api/v1/resolve", { name })),
  getExamples: () => apiJson<Schema<"ExamplesResponse">>("/api/v1/examples"),
  getReleases: () => apiJson<DataReleaseRead[]>("/api/v1/releases"),
  getFieldFrames: (q: {
    ra: number;
    dec: number;
    fov: number;
    bands?: string;
    release?: string;
    from?: string;
    to?: string;
  }) => apiJson<Schema<"FieldFramesResponse">>(buildUrl("/api/v1/fields/frames", q)),
  createCutouts: (body: Partial<Schema<"CutoutRequest">> & { ra: number; dec: number }) =>
    postJson<Schema<"CutoutAcceptedResponse">>("/api/v1/cutouts", body),
  getJob: (jobId: string) => apiJson<Schema<"JobStatusResponse">>(`/api/v1/jobs/${jobId}`),
  getCutoutSet: (cutoutId: string) =>
    apiJson<Schema<"CutoutSetResponse">>(`/api/v1/cutouts/${cutoutId}`),
  getCatalogOverlay: (q: {
    cat: "gaia" | "catwise";
    ra: number;
    dec: number;
    fov: number;
    epoch?: string;
  }) => apiJson<Schema<"CatalogOverlayResponse">>(buildUrl("/api/v1/catalogs/overlay", q)),
  createAnalysis: (body: {
    ra?: number;
    dec?: number;
    fov_arcmin?: number;
    release?: string;
    cutout_id?: string;
    fits_file_ids?: string[];
  }) => postJson<Schema<"AnalysisAcceptedResponse">>("/api/v1/analyses", body),
  getAnalysisSources: (analysisId: string) =>
    apiJson<{ items: DetectionRead[]; next_cursor: string | null }>(
      `/api/v1/analyses/${analysisId}/sources?limit=200`,
    ),
  getAnalysisSuggestions: (analysisId: string) =>
    apiJson<Schema<"SuggestionsResponse">>(`/api/v1/analyses/${analysisId}/suggestions`),
  selectAnalysisObject: (
    analysisId: string,
    body: {
      mode: "box" | "suggestion" | "catalog" | "manual";
      detection_ids?: number[];
      box?: { ra_min: number; dec_min: number; ra_max: number; dec_max: number };
      epoch?: string;
    },
  ) =>
    postJson<{ objects: Array<{ id: string; label: string; ra: number; dec: number }> }>(
      `/api/v1/analyses/${analysisId}/select`,
      body,
    ),
  getObjectDetail: (objectId: string) =>
    apiJson<TrackedObjectDetailResponse>(`/api/v1/objects/${objectId}`),
  getObjectAssociations: (objectId: string) =>
    apiJson<ObjectAssociationsResponse>(`/api/v1/objects/${objectId}/associations`),
  overrideObjectAssociation: (objectId: string, epoch: string, body: AssociationOverrideRequest) =>
    apiJson<AssociationOverrideResponse>(`/api/v1/objects/${objectId}/associations/${epoch}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  createComparison: (body: CompareRequest) =>
    postJson<CompareAcceptedResponse>("/api/v1/compare", body),
  getComparison: (compareId: string) =>
    apiJson<CompareResultResponse>(`/api/v1/compare/${compareId}`),
  getObjectSpectrum: (objectId: string, epoch?: string) =>
    apiJson<SpectrumResponse>(buildUrl(`/api/v1/objects/${objectId}/spectrum`, { epoch })),
  recomputeTrajectory: (
    objectId: string,
    body: { excluded_detection_ids?: number[]; models?: string[] },
  ) =>
    postJson<{ object_id: string; refit_job_id: string; status: string }>(
      `/api/v1/objects/${objectId}/recompute`,
      body,
    ),
  predictEphemeris: (
    objectId: string,
    body: { extrapolation_days?: number; steps?: number; start_mjd?: number },
  ) => postJson<EphemerisPredictionResult>(`/api/v1/objects/${objectId}/ephemeris`, body),
  searchArchive: (params: ArchiveSearchParams) =>
    apiJson<ArchiveSearchResponse>(
      buildUrl("/api/v1/archive/search", {
        ra: params.ra,
        dec: params.dec,
        radius: params.radius,
        radius_unit: params.radius_unit,
        source: params.source,
        from: params.from,
        to: params.to,
        band: params.band,
        max_records: params.max_records,
      }),
    ),
  loadArchiveProduct: (body: ArchiveLoadRequest) =>
    postJson<ArchiveLoadResponse>("/api/v1/archive/load", body),
  listUploads: () => apiJson<UploadedFitsSummary[]>("/api/v1/uploads"),
  deleteUpload: async (fitsId: string) => {
    const res = await fetch(`${API_BASE}/api/v1/uploads/${fitsId}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError(res.status, err.detail || "Delete upload failed", err.title);
    }
  },
  uploadFits: async (
    file: File,
    userEpoch?: string,
    refRa?: number,
    refDec?: number,
    projectId?: string,
  ) => {
    const form = new FormData();
    form.append("file", file);
    if (userEpoch) form.append("user_epoch", userEpoch);
    if (refRa !== undefined) form.append("reference_ra_deg", String(refRa));
    if (refDec !== undefined) form.append("reference_dec_deg", String(refDec));
    if (projectId) form.append("project_id", projectId);
    const headers = getAuthHeaders();
    const res = await fetch(`${API_BASE}/api/v1/uploads`, {
      method: "POST",
      credentials: "include",
      headers,
      body: form,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError(res.status, err.detail || "FITS Upload failed", err.title);
    }
    return (await res.json()) as UploadResponse;
  },
  getUploadReport: (uploadId: string) =>
    apiJson<UploadCompatibilityReport>(`/api/v1/uploads/${uploadId}/report`),
  listProjects: (params?: { cursor?: string; limit?: number }) =>
    apiJson<PaginatedProjects>(buildUrl("/api/v1/projects", params)),
  createProject: (body: ProjectCreate) =>
    postJson<ProjectRead>("/api/v1/projects", body),
  getProject: (projectId: string, shareToken?: string) =>
    apiJson<ProjectRead>(
      buildUrl(`/api/v1/projects/${projectId}`, { share_token: shareToken }),
      undefined,
      shareToken,
    ),
  updateProject: (projectId: string, body: ProjectUpdate) =>
    apiJson<ProjectRead>(`/api/v1/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteProject: async (projectId: string) => {
    const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}`, {
      method: "DELETE",
      credentials: "include",
      headers: getAuthHeaders(),
    });
    if (!res.ok && res.status !== 404) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError(res.status, err.detail || "Delete project failed");
    }
  },
  listProjectFiles: (projectId: string, shareToken?: string) =>
    apiJson<UploadedFitsSummary[]>(
      buildUrl(`/api/v1/projects/${projectId}/files`, { share_token: shareToken }),
      undefined,
      shareToken,
    ),
  linkProjectFile: (projectId: string, fitsFileId: string) =>
    postJson<UploadedFitsSummary>(`/api/v1/projects/${projectId}/files`, {
      fits_file_id: fitsFileId,
    }),
  unlinkProjectFile: async (projectId: string, fitsId: string) => {
    const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/files/${fitsId}`, {
      method: "DELETE",
      credentials: "include",
      headers: getAuthHeaders(),
    });
    if (!res.ok && res.status !== 404) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError(res.status, err.detail || "Unlink file failed");
    }
  },
  listNotes: (projectId: string, shareToken?: string) =>
    apiJson<NoteRead[]>(
      buildUrl(`/api/v1/projects/${projectId}/notes`, { share_token: shareToken }),
      undefined,
      shareToken,
    ),
  createNote: (projectId: string, body: NoteCreate) =>
    postJson<NoteRead>(`/api/v1/projects/${projectId}/notes`, body),
  updateNote: (projectId: string, noteId: string, body: NoteUpdate) =>
    apiJson<NoteRead>(`/api/v1/projects/${projectId}/notes/${noteId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteNote: async (projectId: string, noteId: string) => {
    const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/notes/${noteId}`, {
      method: "DELETE",
      credentials: "include",
      headers: getAuthHeaders(),
    });
    if (!res.ok && res.status !== 404) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError(res.status, err.detail || "Delete note failed");
    }
  },
  listFindings: (projectId: string, shareToken?: string) =>
    apiJson<FindingRead[]>(
      buildUrl(`/api/v1/projects/${projectId}/findings`, { share_token: shareToken }),
      undefined,
      shareToken,
    ),
  createFinding: (projectId: string, body: FindingCreate) =>
    postJson<FindingRead>(`/api/v1/projects/${projectId}/findings`, body),
  updateFinding: (projectId: string, findingId: string, body: FindingUpdate) =>
    apiJson<FindingRead>(`/api/v1/projects/${projectId}/findings/${findingId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteFinding: async (projectId: string, findingId: string) => {
    const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/findings/${findingId}`, {
      method: "DELETE",
      credentials: "include",
      headers: getAuthHeaders(),
    });
    if (!res.ok && res.status !== 404) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError(res.status, err.detail || "Delete finding failed");
    }
  },
  listSavedViews: (projectId: string, shareToken?: string) =>
    apiJson<SavedViewRead[]>(
      buildUrl(`/api/v1/projects/${projectId}/views`, { share_token: shareToken }),
      undefined,
      shareToken,
    ),
  createSavedView: (projectId: string, body: SavedViewCreate) =>
    postJson<SavedViewRead>(`/api/v1/projects/${projectId}/views`, body),
  deleteSavedView: async (projectId: string, viewId: string) => {
    const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/views/${viewId}`, {
      method: "DELETE",
      credentials: "include",
      headers: getAuthHeaders(),
    });
    if (!res.ok && res.status !== 404) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError(res.status, err.detail || "Delete saved view failed");
    }
  },
  listVersions: (projectId: string, shareToken?: string) =>
    apiJson<ProjectVersionRead[]>(
      buildUrl(`/api/v1/projects/${projectId}/versions`, { share_token: shareToken }),
      undefined,
      shareToken,
    ),
  createVersion: (projectId: string, body: ProjectVersionCreate) =>
    postJson<ProjectVersionRead>(`/api/v1/projects/${projectId}/versions`, body),
  restoreVersion: (projectId: string, version: number) =>
    postJson<ProjectRead>(`/api/v1/projects/${projectId}/versions/${version}/restore`, {}),
  createShareLink: (projectId: string) =>
    postJson<ProjectShareResponse>(`/api/v1/projects/${projectId}/share`, {}),
  revokeShareLink: async (projectId: string) => {
    const res = await fetch(`${API_BASE}/api/v1/projects/${projectId}/share`, {
      method: "DELETE",
      credentials: "include",
      headers: getAuthHeaders(),
    });
    if (!res.ok && res.status !== 404) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError(res.status, err.detail || "Revoke share link failed");
    }
  },
  exportProject: (projectId: string, body: ExportRequest) =>
    postJson<{ job_id: string; status: string }>(`/api/v1/projects/${projectId}/exports`, body),
  downloadProjectExportUrl: (projectId: string, exportId: string, shareToken?: string) =>
    buildUrl(`/api/v1/projects/${projectId}/exports/${exportId}`, { share_token: shareToken }),
};
