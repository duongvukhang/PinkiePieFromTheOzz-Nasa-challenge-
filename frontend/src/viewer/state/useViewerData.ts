import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  fetchScienceFrame,
  type CutoutFrame,
  type DetectionRead,
  type EphemerisPredictionResult,
  type SuggestionItem,
} from "../../api/client";
import { useAuthStore } from "../../app/authStore";
import { parseFitsWcs } from "../aladin/wcs";
import { useViewStore, MAX_TRACKED_OBJECTS } from "./viewStore";

export const isUuid = (val?: string | null): boolean =>
  Boolean(val && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(val));

export function nextTrackLabel(existing: string[]): string {
  const set = new Set(existing.map((t) => t.split(":")[0]));
  for (const ch of "ABCDEFGHIJKLMNOPQRSTUVWXYZ") {
    if (!set.has(ch)) return ch;
  }
  return `O${set.size + 1}`;
}

export function useViewerData(search: string, skipSecondary = false) {
  const user = useAuthStore((s) => s.user);
  const view = useViewStore();
  const {
    target,
    ra,
    dec,
    fov,
    from,
    to,
    release,
    bands,
    source,
    frame,
    fps,
    playing,
    a,
    b,
    overlay,
    obj,
    track,
    selectBoxMode,
    toggleSelectBox,
    addTrack,
    removeTrack,
    syncFromSearch,
    setView,
  } = view;
  const fitsId = view.fitsId;
  const fitsIds = view.fitsIds;
  const excludedFrameIds = view.excludedFrameIds;

  const allFitsIds = useMemo(() => {
    const s = new Set<string>();
    if (fitsId) s.add(fitsId);
    for (const fid of fitsIds || []) {
      if (fid) s.add(fid);
    }
    return Array.from(s);
  }, [fitsId, fitsIds]);

  const uploadsQ = useQuery({
    queryKey: ["uploads", allFitsIds],
    queryFn: () => api.listUploads(),
    enabled: allFitsIds.length > 0,
  });

  const [pausedByVis, setPausedByVis] = useState(false);
  const [trackedUuid, setTrackedUuid] = useState<string | null>(null);
  const [ephemerisResult, setEphemerisResult] = useState<EphemerisPredictionResult | null>(null);

  useEffect(() => {
    syncFromSearch(search, Boolean(user));
  }, [search, user, syncFromSearch]);

  const examplesQ = useQuery({
    queryKey: ["examples"],
    queryFn: api.getExamples,
    enabled: !skipSecondary,
  });

  const releasesQ = useQuery({
    queryKey: ["releases"],
    queryFn: api.getReleases,
    enabled: !skipSecondary,
  });

  const resolveTargetDirect = useCallback(
    async (name: string) => {
      const res = await api.resolveTarget(name);
      setView({ target: name, ra: res.ra_deg, dec: res.dec_deg, frame: 0 });
      return res;
    },
    [setView],
  );

  const resolveQ = useQuery({
    queryKey: ["resolve", target],
    queryFn: () => api.resolveTarget(target),
    enabled: ra === undefined && Boolean(target),
  });

  useEffect(() => {
    if (resolveQ.data && (ra === undefined || dec === undefined)) {
      setView({ ra: resolveQ.data.ra_deg, dec: resolveQ.data.dec_deg });
    }
  }, [resolveQ.data, ra, dec, setView]);

  const activeRa = ra ?? resolveQ.data?.ra_deg;
  const activeDec = dec ?? resolveQ.data?.dec_deg;
  const hasCoords = activeRa !== undefined && activeDec !== undefined;

  const fieldsQ = useQuery({
    queryKey: ["fields", activeRa, activeDec, fov, bands, release, from, to, source],
    queryFn: () =>
      api.getFieldFrames({
        ra: activeRa!,
        dec: activeDec!,
        fov,
        bands,
        release,
        from: from || undefined,
        to: to || undefined,
      }),
    enabled: hasCoords && allFitsIds.length === 0,
  });

  const cutoutQ = useQuery({
    queryKey: ["cutout", activeRa, activeDec, fov, bands, release, from, to, source],
    queryFn: async () => {
      const accepted = await api.createCutouts({
        ra: activeRa!,
        dec: activeDec!,
        fov_arcmin: fov,
        release,
        bands: bands === "all" ? ["all"] : [bands],
        from_utc: from ? `${from}T00:00:00Z` : undefined,
        to_utc: to ? `${to}T23:59:59Z` : undefined,
        source: source || "spherex_irsa",
      });
      let status = accepted.status;
      let delay = 150;
      for (let i = 0; i < 30 && status !== "succeeded" && status !== "failed"; i++) {
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(1000, Math.round(delay * 1.25));
        status = (await api.getJob(accepted.job_id)).status;
      }
      if (status === "failed") {
        throw new ApiError(500, `Cutout job ${accepted.job_id} failed`, "Cutout extraction failed");
      }
      if (status !== "succeeded") {
        throw new ApiError(
          504,
          `Cutout job ${accepted.job_id} timed out`,
          "Cutout extraction timed out",
        );
      }
      return api.getCutoutSet(accepted.cutout_id);
    },
    enabled: hasCoords && allFitsIds.length === 0,
  });

  const frames: CutoutFrame[] = useMemo(() => {
    const excluded = new Set(excludedFrameIds || []);
    const userUploadedList = uploadsQ.data ?? [];
    const matchedUploads = userUploadedList.filter((u) => allFitsIds.includes(u.fits_file_id));
    const matchedIds = new Set(matchedUploads.map((m) => m.fits_file_id));

    const userFrames: CutoutFrame[] = matchedUploads.map((u) => ({
      frame_id: u.fits_file_id,
      epoch: u.epoch || u.band_label || "Uploaded FITS",
      mjd_tdb_mid: u.mjd_tdb_mid,
      utc_mid: u.utc_mid,
      array_id: undefined,
      band_label: u.band_label,
      display_url: u.display_url,
      science_url: u.science_url,
      fits_url: u.fits_url,
      wcs_header: u.wcs_header || "",
      stretch: u.stretch || { vmin: 0, vmax: 1000, shape: [u.naxis2 || 128, u.naxis1 || 128] },
      flagged_fraction: 0,
    }));

    for (const fid of allFitsIds) {
      if (!matchedIds.has(fid) && !excluded.has(fid)) {
        userFrames.push({
          frame_id: fid,
          epoch: "FITS Frame",
          mjd_tdb_mid: 60000.0,
          utc_mid: "2025-01-01T00:00:00Z",
          display_url: `/api/v1/uploads/${fid}/display.webp`,
          science_url: `/api/v1/uploads/${fid}/science.bin`,
          fits_url: `/api/v1/uploads/${fid}/frame.fits`,
          wcs_header: "",
          stretch: { vmin: 0, vmax: 1000, shape: [128, 128] },
          flagged_fraction: 0,
        });
      }
    }

    if (userFrames.length > 0) {
      const seen = new Set<string>();
      return userFrames
        .filter((uf) => !excluded.has(uf.frame_id) && !seen.has(uf.frame_id) && seen.add(uf.frame_id))
        .sort((a, b) => a.mjd_tdb_mid - b.mjd_tdb_mid);
    }

    const rawCutouts = cutoutQ.data?.frames ?? [];
    const seenFrameIds = new Set<string>();
    const seenEpochs = new Set<string>();
    const combined: CutoutFrame[] = [];

    for (const cf of rawCutouts) {
      if (excluded.has(cf.frame_id) || seenFrameIds.has(cf.frame_id)) continue;
      if (!seenEpochs.has(cf.epoch.toLowerCase())) {
        combined.push(cf);
        seenFrameIds.add(cf.frame_id);
        seenEpochs.add(cf.epoch.toLowerCase());
      }
    }

    return combined.sort((a, b) => a.mjd_tdb_mid - b.mjd_tdb_mid);
  }, [cutoutQ.data, uploadsQ.data, allFitsIds, excludedFrameIds]);
  const safeIdx = frames.length ? ((frame % frames.length) + frames.length) % frames.length : 0;
  const frameA = frames.find((f) => f.frame_id === a) ?? frames[safeIdx];
  const frameB =
    frames.find((f) => f.frame_id === b) ?? frames[(safeIdx + 1) % Math.max(1, frames.length)];

  const bufAQ = useQuery({
    queryKey: ["science", frameA?.science_url],
    queryFn: () => fetchScienceFrame(frameA!.science_url),
    enabled: Boolean(frameA?.science_url),
  });

  const bufBQ = useQuery({
    queryKey: ["science", frameB?.science_url],
    queryFn: () => fetchScienceFrame(frameB!.science_url),
    enabled: Boolean(frameB?.science_url) && frameB?.frame_id !== frameA?.frame_id,
  });

  const useCatOverlay = (cat: "gaia" | "catwise") =>
    useQuery({
      queryKey: ["overlay", cat, activeRa, activeDec, fov, frameA?.epoch],
      queryFn: () =>
        api.getCatalogOverlay({ cat, ra: activeRa!, dec: activeDec!, fov, epoch: frameA?.epoch }),
      enabled: hasCoords && overlay.includes(cat),
    });
  const gaiaQ = useCatOverlay("gaia");
  const catwiseQ = useCatOverlay("catwise");

  const analysisQ = useQuery({
    queryKey: ["analysis", cutoutQ.data?.cutout_id, fitsId, activeRa, activeDec, fov, release],
    queryFn: async () => {
      const acc = fitsId
        ? await api.createAnalysis({ fits_file_ids: [fitsId] })
        : await api.createAnalysis({
            cutout_id: cutoutQ.data?.cutout_id,
            ra: cutoutQ.data ? undefined : activeRa,
            dec: cutoutQ.data ? undefined : activeDec,
            fov_arcmin: fov,
            release,
          });
      let status = acc.status;
      let delay = 150;
      for (let i = 0; i < 30 && status !== "succeeded" && status !== "failed"; i++) {
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(1000, Math.round(delay * 1.25));
        status = (await api.getJob(acc.job_id)).status;
      }
      if (status === "failed") {
        throw new ApiError(500, `Analysis job ${acc.job_id} failed`, "Analysis processing failed");
      }
      if (status !== "succeeded") {
        throw new ApiError(
          504,
          `Analysis job ${acc.job_id} timed out`,
          "Analysis processing timed out",
        );
      }
      const [sources, suggestions] = await Promise.all([
        api.getAnalysisSources(acc.analysis_id),
        api.getAnalysisSuggestions(acc.analysis_id),
      ]);
      return {
        analysisId: acc.analysis_id,
        detections: sources.items,
        suggestions: suggestions.suggestions,
      };
    },
    enabled: !skipSecondary && (Boolean(cutoutQ.data?.cutout_id) || Boolean(fitsId)),
  });

  const activeObjId = (obj && isUuid(obj)) ? obj : (trackedUuid ?? undefined);

  useEffect(() => {
    if (
      !obj &&
      !trackedUuid &&
      analysisQ.data?.analysisId &&
      analysisQ.data.suggestions.length > 0
    ) {
      const top = analysisQ.data.suggestions[0];
      if (top) {
        api
          .selectAnalysisObject(analysisQ.data.analysisId, {
            mode: "suggestion",
            detection_ids: [top.detection_id],
          })
          .then((res) => {
            if (res.objects?.[0]) {
              setTrackedUuid(res.objects[0].id);
              addTrack(res.objects[0].label, top.ra, top.dec);
            }
          })
          .catch(() => {});
      }
    }
  }, [analysisQ.data?.analysisId, analysisQ.data?.suggestions, obj, trackedUuid, addTrack]);

  const hasValidObjUuid = isUuid(activeObjId);
  const canQueryObj = !skipSecondary && Boolean(activeObjId && hasValidObjUuid);

  const objectQ = useQuery({
    queryKey: ["object", activeObjId],
    queryFn: () => api.getObjectDetail(activeObjId!),
    enabled: canQueryObj,
  });

  const associationsQ = useQuery({
    queryKey: ["associations", activeObjId],
    queryFn: () => api.getObjectAssociations(activeObjId!),
    enabled: canQueryObj,
  });

  const spectrumQ = useQuery({
    queryKey: ["spectrum", activeObjId, frameA?.epoch],
    queryFn: () => api.getObjectSpectrum(activeObjId!, frameA?.epoch),
    enabled: canQueryObj,
  });

  // Blink playback timer
  useEffect(() => {
    if (!playing || pausedByVis || frames.length < 2) return;
    const id = window.setInterval(
      () => {
        const cur = useViewStore.getState().frame;
        setView({ frame: (cur + 1) % frames.length }, false);
      },
      Math.round(1000 / Math.max(0.5, fps)),
    );
    return () => window.clearInterval(id);
  }, [playing, pausedByVis, fps, frames.length, setView]);

  const frameShape = frameA?.stretch?.shape as [number, number] | undefined;
  const imgW = frameShape?.[1] ?? bufAQ.data?.width ?? 2040;
  const imgH = frameShape?.[0] ?? bufAQ.data?.height ?? 2040;

  const wcs = parseFitsWcs(
    frameA?.wcs_header,
    activeRa ?? 276.26139,
    activeDec ?? 64.82577,
    fov,
    imgW,
    imgH,
  );

  const overrideAssociation = async (
    epoch: string,
    action: "accept" | "reject" | "reassign",
    detectionId?: number | null,
    reason = "User manual association override",
  ) => {
    if (!activeObjId || !hasValidObjUuid) return;
    await api.overrideObjectAssociation(activeObjId, epoch, {
      action,
      detection_id: detectionId,
      reason,
    });
    associationsQ.refetch();
    objectQ.refetch();
  };

  const onTrackSuggestion = async (s: SuggestionItem) => {
    if (track.length >= MAX_TRACKED_OBJECTS) return;
    if (analysisQ.data?.analysisId) {
      try {
        const res = await api.selectAnalysisObject(analysisQ.data.analysisId, {
          mode: "suggestion",
          detection_ids: [s.detection_id],
        });
        const created = res.objects?.[0];
        if (created) {
          setTrackedUuid(created.id);
          addTrack(created.label, s.ra, s.dec);
          setView({ obj: created.id, inspectorTab: "object" });
          return;
        }
      } catch {
        // fallback to client-side tracking
      }
    }
    const lbl = nextTrackLabel(track);
    addTrack(lbl, s.ra, s.dec);
    setView({ obj: String(s.detection_id), inspectorTab: "object" });
  };

  const onTrackDetection = async (d: DetectionRead) => {
    if (track.length >= MAX_TRACKED_OBJECTS) return;
    if (analysisQ.data?.analysisId) {
      try {
        const res = await api.selectAnalysisObject(analysisQ.data.analysisId, {
          mode: "box",
          detection_ids: [d.id],
        });
        const created = res.objects?.[0];
        if (created) {
          setTrackedUuid(created.id);
          addTrack(created.label, d.ra, d.dec);
          setView({ obj: created.id, inspectorTab: "object" });
          return;
        }
      } catch {
        // fallback to client-side tracking
      }
    }
    const lbl = nextTrackLabel(track);
    addTrack(lbl, d.ra, d.dec);
    setView({ obj: String(d.id), inspectorTab: "object" });
  };

  const onBoxSelect = async (box: { raMin: number; raMax: number; decMin: number; decMax: number }) => {
    setActiveBox(box);
    setView({
      activeBox: box,
      inspectorTab: "object",
      selectBoxMode: false,
    });
  };

  return {
    activeRa: activeRa ?? 133.79513,
    activeDec: activeDec ?? -7.24515,
    wcs,
    frames,
    frameA,
    frameB,
    bufA: bufAQ.data,
    bufB: bufBQ.data,
    examples: examplesQ.data?.targets ?? [],
    releases: releasesQ.data ?? [],
    warnings: [...(fieldsQ.data?.warnings ?? []), ...(cutoutQ.data?.warnings ?? [])],
    gaiaSources: gaiaQ.data?.sources ?? [],
    catwiseSources: catwiseQ.data?.sources ?? [],
    detections: analysisQ.data?.detections ?? [],
    suggestions: analysisQ.data?.suggestions ?? [],
    objectDetail: objectQ.data,
    associations: associationsQ.data?.associations ?? [],
    overrideAssociation,
    onTrackSuggestion,
    onTrackDetection,
    onBoxSelect,
    track,
    removeTrack,
    selectBoxMode,
    toggleSelectBox,
    spectrum: spectrumQ.data,
    resolveTargetDirect,
    resolving: resolveQ.isFetching,
    loadingCutouts: allFitsIds.length > 0 ? uploadsQ.isFetching : cutoutQ.isFetching,
    loadingText: allFitsIds.length > 0 ? "Loading FITS frames…" : "Loading FITS cutouts…",
    errorMsg: (() => {
      const err = resolveQ.error ?? cutoutQ.error ?? fieldsQ.error ?? bufAQ.error ?? null;
      return err instanceof Error ? err.message : typeof err === "string" ? err : undefined;
    })(),
    resolveError: (() => {
      const err = resolveQ.error ?? cutoutQ.error ?? fieldsQ.error ?? bufAQ.error ?? null;
      return err instanceof Error ? err.message : typeof err === "string" ? err : undefined;
    })(),
    setPausedByVis,
    ephemerisResult,
    setEphemerisResult,
  };
}
