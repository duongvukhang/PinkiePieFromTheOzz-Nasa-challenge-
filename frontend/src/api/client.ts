import type { components } from "./schema";

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

export type ArchiveLoadRequest = Schema<"ArchiveLoadRequest">;
export type ArchiveSourceId = NonNullable<ArchiveLoadRequest["source"]>;
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

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
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
  }) => postJson<Schema<"AnalysisAcceptedResponse">>("/api/v1/analyses", body),
  getAnalysisSources: (analysisId: string) =>
    apiJson<{ items: DetectionRead[]; next_cursor: string | null }>(
      `/api/v1/analyses/${analysisId}/sources?limit=200`,
    ),
  getAnalysisSuggestions: (analysisId: string) =>
    apiJson<Schema<"SuggestionsResponse">>(`/api/v1/analyses/${analysisId}/suggestions`),
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
  ) =>
    postJson<{
      object_id: string;
      trajectory: Array<{
        time_mjd: number;
        date_iso: string;
        ra_deg: number;
        dec_deg: number;
        uncertainty_semi_major_arcsec: number;
        uncertainty_semi_minor_arcsec: number;
        pa_deg: number;
        is_extrapolated: boolean;
        method: string;
      }>;
      preferred_model?: string;
      baseline_days: number;
    }>(`/api/v1/objects/${objectId}/ephemeris`, body),
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
    postJson<{ obs_id: string; storage_key: string; status: string; source: string }>(
      "/api/v1/archive/load",
      body,
    ),
  uploadFits: async (file: File, userEpoch?: string, refRa?: number, refDec?: number) => {
    const form = new FormData();
    form.append("file", file);
    if (userEpoch) form.append("user_epoch", userEpoch);
    if (refRa !== undefined) form.append("reference_ra_deg", String(refRa));
    if (refDec !== undefined) form.append("reference_dec_deg", String(refDec));
    const res = await fetch(`${API_BASE}/api/v1/uploads`, {
      method: "POST",
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
};
