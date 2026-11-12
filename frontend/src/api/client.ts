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
  getObjectSpectrum: (objectId: string, epoch?: string) =>
    apiJson<SpectrumResponse>(buildUrl(`/api/v1/objects/${objectId}/spectrum`, { epoch })),
};
