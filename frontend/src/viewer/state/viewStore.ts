import { create } from "zustand";

export type ViewerMode = "blink" | "compare" | "planetx";
export type ImageOp = "none" | "sum" | "diff" | "ratio" | "swipe";
export type StretchMode = "asinh" | "linear" | "log" | "sqrt";
export type ColormapMode = "gray" | "sepia" | "viridis";
export type CatalogName = "gaia" | "catwise";
export type InspectorTab = "spectrum" | "flags" | "object" | "tracks" | "layers";

export const MAX_TRACKED_OBJECTS = 2;

export interface ViewState {
  target: string;
  ra?: number;
  dec?: number;
  fov: number;
  from: string;
  to: string;
  release: string;
  bands: string;
  source?: string;
  projectId?: string;
  fitsId?: string;
  fitsIds: string[];
  excludedFrameIds: string[];
  isUploadModalOpen: boolean;
  mode: ViewerMode;
  op: ImageOp;
  a?: string;
  b?: string;
  frame: number;
  fps: number;
  playing: boolean;
  stretch: StretchMode;
  colormap: ColormapMode;
  overlay: CatalogName[];
  track: string[];
  obj?: string;
  hips: boolean;
  swipe: number;
  inspectorTab: InspectorTab;
  selectBoxMode: boolean;
  activeBox?: { raMin: number; raMax: number; decMin: number; decMax: number } | null;
  isLayersOpen: boolean;
  isInspectorOpen: boolean;
  isTimelineOpen: boolean;
}

const MODES = new Set<ViewerMode>(["blink", "compare", "planetx"]);
const OPS = new Set<ImageOp>(["none", "sum", "diff", "ratio", "swipe"]);
const STRETCHES = new Set<StretchMode>(["asinh", "linear", "log", "sqrt"]);
const INSPECTOR_TABS = new Set<InspectorTab>(["spectrum", "flags", "object", "tracks", "layers"]);

function hmsToDeg(token: string, isHours: boolean): number | undefined {
  const clean = token
    .trim()
    .replace(/[hmsd°′″'"]+/gi, ":")
    .replace(/:+$/g, "");
  const parts = clean.split(/[:\s]+/).map(Number);
  if (!parts.length || parts.some(Number.isNaN)) return undefined;
  const sign = token.trim().startsWith("-") ? -1 : 1;
  const [a = 0, b = 0, c = 0] = parts.map(Math.abs);
  const deg = (a + b / 60 + c / 3600) * (isHours ? 15 : 1);
  return sign * deg;
}

/** Parse decimal or sexagesimal sky coordinates (degrees, ICRS). */
export function parseCoordsInput(raw: string): { ra: number; dec: number } | null {
  const s = raw.trim();
  const decMatch = s.match(/^(-?\d+(?:\.\d+)?)[,\s]+([+-]?\d+(?:\.\d+)?)$/);
  if (decMatch) {
    const ra = ((Number(decMatch[1]) % 360) + 360) % 360;
    const dec = Number(decMatch[2]);
    return Number.isFinite(ra) && Math.abs(dec) <= 90 ? { ra, dec } : null;
  }
  const sexa = s.match(
    /^([+-]?\d+(?::|\s|h)\d+(?::|\s|m)\d+(?:\.\d+)?s?)[,\s]+([+-]?\d+(?::|\s|d|°)\d+(?::|\s|m|′|')\d+(?:\.\d+)?(?:s|″|")?)$/i,
  );
  if (sexa?.[1] && sexa[2]) {
    const ra = hmsToDeg(sexa[1], true);
    const dec = hmsToDeg(sexa[2], false);
    if (ra !== undefined && dec !== undefined && Math.abs(dec) <= 90) {
      return { ra: ((ra % 360) + 360) % 360, dec };
    }
  }
  return null;
}

export function clampFov(fov: number, authenticated: boolean): number {
  const max = authenticated ? 60 : 15;
  return Number.isFinite(fov) ? Math.min(max, Math.max(2, fov)) : 10;
}

export function parseViewParams(search: string, authenticated = false): Partial<ViewState> {
  const q = new URLSearchParams(search);
  const raRaw = q.get("ra");
  const decRaw = q.get("dec");
  const coords = raRaw && decRaw ? parseCoordsInput(`${raRaw} ${decRaw}`) : null;
  const mode = q.get("mode") as ViewerMode | null;
  const op = q.get("op") as ImageOp | null;
  const stretch = q.get("stretch") as StretchMode | null;
  const overlay = (q.get("overlay") ?? "")
    .split(",")
    .filter((v): v is CatalogName => v === "gaia" || v === "catwise");
  const track = (q.get("track") ?? "")
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean);
  const frameNum = Number(q.get("frame"));
  const fpsNum = Number(q.get("fps"));
  const singleFits = q.get("fitsId");
  const multiFits = q.get("fitsIds");
  const parsedFitsIds = multiFits
    ? multiFits
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
    : singleFits
      ? [singleFits]
      : [];
  const excluded = (q.get("excluded") ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  return {
    target: q.get("target") ?? "",
    ra: coords?.ra,
    dec: coords?.dec,
    fov: q.has("fov") ? clampFov(Number(q.get("fov")), authenticated) : 10,
    from: q.get("from") ?? "",
    to: q.get("to") ?? "",
    release: q.get("release") ?? "latest",
    bands: q.get("bands") ?? "all",
    source: q.get("source") ?? undefined,
    projectId: q.get("projectId") ?? undefined,
    fitsId: parsedFitsIds[0] ?? singleFits ?? undefined,
    fitsIds: parsedFitsIds,
    excludedFrameIds: excluded,
    isUploadModalOpen: false,
    mode: mode && MODES.has(mode) ? mode : "blink",
    op: op && OPS.has(op) ? op : "none",
    a: q.get("a") ?? undefined,
    b: q.get("b") ?? undefined,
    frame: Number.isInteger(frameNum) && frameNum >= 0 ? frameNum : 0,
    fps: Number.isFinite(fpsNum) && fpsNum > 0 ? Math.min(10, Math.max(0.5, fpsNum)) : 2,
    stretch: stretch && STRETCHES.has(stretch) ? stretch : "asinh",
    overlay,
    track,
    obj: q.get("obj") ?? undefined,
    inspectorTab: (() => {
      const t = (q.get("inspectorTab") || q.get("tab")) as InspectorTab;
      return t && INSPECTOR_TABS.has(t) ? t : "spectrum";
    })(),
  };
}

export function toViewQuery(s: Partial<ViewState>): string {
  const q = new URLSearchParams();
  if (s.ra !== undefined && s.dec !== undefined) {
    q.set("ra", s.ra.toFixed(5));
    q.set("dec", s.dec.toFixed(5));
  }
  if (s.target) q.set("target", s.target);
  if (s.fov !== undefined && s.fov !== 10) q.set("fov", String(s.fov));
  if (s.from) q.set("from", s.from);
  if (s.to) q.set("to", s.to);
  if (s.release && s.release !== "latest") q.set("release", s.release);
  if (s.bands && s.bands !== "all") q.set("bands", s.bands);
  if (s.source && s.source !== "spherex_irsa") q.set("source", s.source);
  if (s.projectId) q.set("projectId", s.projectId);
  if (s.fitsIds && s.fitsIds.length > 0) {
    if (s.fitsIds[0]) q.set("fitsId", s.fitsIds[0]);
    if (s.fitsIds.length > 1) q.set("fitsIds", s.fitsIds.join(","));
  } else if (s.fitsId) {
    q.set("fitsId", s.fitsId);
  }
  if (s.excludedFrameIds && s.excludedFrameIds.length > 0) {
    q.set("excluded", s.excludedFrameIds.join(","));
  }
  if (s.mode && s.mode !== "blink") q.set("mode", s.mode);
  if (s.op && s.op !== "none") q.set("op", s.op);
  if (s.a) q.set("a", s.a);
  if (s.b) q.set("b", s.b);
  if (s.frame !== undefined && s.frame > 0) q.set("frame", String(s.frame));
  if (s.fps !== undefined && s.fps !== 2) q.set("fps", String(s.fps));
  if (s.stretch && s.stretch !== "asinh") q.set("stretch", s.stretch);
  if (s.overlay && s.overlay.length) q.set("overlay", s.overlay.join(","));
  if (s.track && s.track.length) q.set("track", s.track.join(";"));
  if (s.obj) q.set("obj", s.obj);
  if (s.inspectorTab && s.inspectorTab !== "spectrum") q.set("inspectorTab", s.inspectorTab);
  const str = q.toString();
  return str ? `?${str}` : "";
}

const DEFAULT_STATE: ViewState = {
  target: "SPHEREx NEP Deep Field",
  ra: 276.26139,
  dec: 64.82577,
  fov: 15,
  from: "",
  to: "",
  release: "latest",
  bands: "all",
  source: "spherex_irsa",
  fitsIds: [],
  excludedFrameIds: [],
  isUploadModalOpen: false,
  mode: "blink",
  op: "none",
  frame: 0,
  fps: 2,
  playing: false,
  stretch: "asinh",
  colormap: "gray",
  overlay: [],
  track: [],
  hips: false,
  swipe: 0.5,
  inspectorTab: "spectrum",
  selectBoxMode: false,
  activeBox: null,
  isLayersOpen: false,
  isInspectorOpen: false,
  isTimelineOpen: false,
};

interface ViewStore extends ViewState {
  syncFromSearch: (search: string, authenticated: boolean) => void;
  setView: (patch: Partial<ViewState>, syncUrl?: boolean) => void;
  toggleOverlay: (cat: CatalogName) => void;
  toggleSelectBox: () => void;
  setActiveBox: (box: { raMin: number; raMax: number; decMin: number; decMax: number } | null) => void;
  toggleLayers: () => void;
  toggleInspector: () => void;
  toggleTimeline: () => void;
  openInspector: (tab?: InspectorTab) => void;
  addTrack: (label: string, ra: number, dec: number) => void;
  removeTrack: (label: string) => void;
  addFitsId: (id: string) => void;
  removeFitsId: (id: string) => void;
  removeFrame: (frameId: string) => void;
  setUploadModalOpen: (open: boolean) => void;
}

function syncReplaceUrl(next: ViewState) {
  if (typeof window === "undefined") return;
  const qs = toViewQuery(next);
  if (window.location.search !== qs) {
    window.history.replaceState(null, "", `${window.location.pathname}${qs}`);
  }
}

export const useViewStore = create<ViewStore>((set) => ({
  ...DEFAULT_STATE,
  syncFromSearch: (search, authenticated) =>
    set(() => {
      const parsed = parseViewParams(search, authenticated);
      return {
        ...DEFAULT_STATE,
        ...parsed,
        target: parsed.target || (parsed.ra === undefined ? "WISE 0855" : ""),
      };
    }),
  setView: (patch, syncUrl = true) =>
    set((prev) => {
      const updated = { ...patch };
      if (patch.inspectorTab === "tracks") {
        updated.inspectorTab = "object";
        updated.isInspectorOpen = true;
      } else if (patch.inspectorTab === "layers") {
        updated.isLayersOpen = true;
      } else if (patch.inspectorTab) {
        updated.isInspectorOpen = true;
      }
      if (patch.obj !== undefined && patch.obj !== "") {
        updated.isInspectorOpen = true;
      }
      const next: ViewState = { ...prev, ...updated };
      if (syncUrl) syncReplaceUrl(next);
      return next;
    }),
  toggleOverlay: (cat) =>
    set((prev) => {
      const overlay = prev.overlay.includes(cat)
        ? prev.overlay.filter((c) => c !== cat)
        : [...prev.overlay, cat];
      const next = { ...prev, overlay };
      syncReplaceUrl(next);
      return next;
    }),
  toggleSelectBox: () => set((prev) => ({ selectBoxMode: !prev.selectBoxMode })),
  setActiveBox: (box) => set(() => ({ activeBox: box })),
  toggleLayers: () => set((prev) => ({ isLayersOpen: !prev.isLayersOpen })),
  toggleInspector: () => set((prev) => ({ isInspectorOpen: !prev.isInspectorOpen })),
  toggleTimeline: () => set((prev) => ({ isTimelineOpen: !prev.isTimelineOpen })),
  openInspector: (tab) =>
    set((prev) => {
      const next = {
        ...prev,
        isInspectorOpen: true,
        inspectorTab: tab === "tracks" ? "object" : (tab ?? prev.inspectorTab),
      };
      syncReplaceUrl(next);
      return next;
    }),
  addTrack: (label, ra, dec) =>
    set((prev) => {
      const entry = `${label}:${ra.toFixed(5)},${dec.toFixed(5)}`;
      const next = {
        ...prev,
        track: [...prev.track.filter((t) => !t.startsWith(`${label}:`)), entry],
      };
      syncReplaceUrl(next);
      return next;
    }),
  removeTrack: (label) =>
    set((prev) => {
      const next = {
        ...prev,
        track: prev.track.filter((t) => !t.startsWith(`${label}:`)),
        obj: prev.obj === label ? undefined : prev.obj,
      };
      syncReplaceUrl(next);
      return next;
    }),
  addFitsId: (id) =>
    set((prev) => {
      const nextFitsIds = Array.from(new Set([...prev.fitsIds, id]));
      const next = {
        ...prev,
        fitsIds: nextFitsIds,
        fitsId: prev.fitsId || id,
        excludedFrameIds: prev.excludedFrameIds.filter((f) => f !== id),
      };
      syncReplaceUrl(next);
      return next;
    }),
  removeFitsId: (id) =>
    set((prev) => {
      const nextFitsIds = prev.fitsIds.filter((f) => f !== id);
      const next = {
        ...prev,
        fitsIds: nextFitsIds,
        fitsId: prev.fitsId === id ? (nextFitsIds[0] ?? undefined) : prev.fitsId,
        excludedFrameIds: Array.from(new Set([...prev.excludedFrameIds, id])),
      };
      syncReplaceUrl(next);
      return next;
    }),
  removeFrame: (frameId) =>
    set((prev) => {
      const nextFitsIds = prev.fitsIds.filter((f) => f !== frameId);
      const nextExcluded = Array.from(new Set([...prev.excludedFrameIds, frameId]));
      const next = {
        ...prev,
        fitsIds: nextFitsIds,
        fitsId: prev.fitsId === frameId ? (nextFitsIds[0] ?? undefined) : prev.fitsId,
        excludedFrameIds: nextExcluded,
      };
      syncReplaceUrl(next);
      return next;
    }),
  setUploadModalOpen: (open) => set(() => ({ isUploadModalOpen: open })),
}));
