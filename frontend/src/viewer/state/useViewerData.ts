import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, fetchScienceFrame, type CutoutFrame, type SuggestionItem } from "../../api/client";
import { useAuthStore } from "../../app/authStore";
import { parseFitsWcs } from "../aladin/wcs";
import { useViewStore } from "./viewStore";

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

  const [pausedByVis, setPausedByVis] = useState(false);

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

  const resolveQ = useQuery({
    queryKey: ["resolve", target],
    queryFn: () => api.resolveTarget(target),
    enabled: ra === undefined && Boolean(target),
  });

  useEffect(() => {
    if (resolveQ.data && ra === undefined) {
      setView({ ra: resolveQ.data.ra_deg, dec: resolveQ.data.dec_deg });
    }
  }, [resolveQ.data, ra, setView]);

  const activeRa = ra ?? resolveQ.data?.ra_deg;
  const activeDec = dec ?? resolveQ.data?.dec_deg;
  const hasCoords = activeRa !== undefined && activeDec !== undefined;

  const fieldsQ = useQuery({
    queryKey: ["fields", activeRa, activeDec, fov, bands, release, from, to],
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
    enabled: hasCoords,
  });

  const cutoutQ = useQuery({
    queryKey: ["cutout", activeRa, activeDec, fov, bands, release, from, to],
    queryFn: async () => {
      const accepted = await api.createCutouts({
        ra: activeRa!,
        dec: activeDec!,
        fov_arcmin: fov,
        release,
        bands: bands === "all" ? ["all"] : [bands],
        from_utc: from ? `${from}T00:00:00Z` : undefined,
        to_utc: to ? `${to}T23:59:59Z` : undefined,
      });
      let status = accepted.status;
      for (let i = 0; i < 8 && status !== "succeeded" && status !== "failed"; i++) {
        await new Promise((r) => setTimeout(r, 120));
        status = (await api.getJob(accepted.job_id)).status;
      }
      return api.getCutoutSet(accepted.cutout_id);
    },
    enabled: hasCoords,
  });

  const frames: CutoutFrame[] = cutoutQ.data?.frames ?? [];
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
    queryKey: ["analysis", cutoutQ.data?.cutout_id, activeRa, activeDec, fov, release],
    queryFn: async () => {
      const acc = await api.createAnalysis({
        cutout_id: cutoutQ.data?.cutout_id,
        ra: cutoutQ.data ? undefined : activeRa,
        dec: cutoutQ.data ? undefined : activeDec,
        fov_arcmin: fov,
        release,
      });
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
    enabled: !skipSecondary && Boolean(cutoutQ.data?.cutout_id),
  });

  const activeObjId =
    obj ??
    (analysisQ.data?.suggestions[0]
      ? String(analysisQ.data.suggestions[0].detection_id)
      : undefined);

  const objectQ = useQuery({
    queryKey: ["object", activeObjId],
    queryFn: () => api.getObjectDetail(activeObjId!),
    enabled: !skipSecondary && Boolean(activeObjId),
  });

  const associationsQ = useQuery({
    queryKey: ["associations", activeObjId],
    queryFn: () => api.getObjectAssociations(activeObjId!),
    enabled: !skipSecondary && Boolean(activeObjId),
  });

  const spectrumQ = useQuery({
    queryKey: ["spectrum", activeObjId, frameA?.epoch],
    queryFn: () => api.getObjectSpectrum(activeObjId!, frameA?.epoch),
    enabled: !skipSecondary && Boolean(activeObjId),
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

  const wcs = parseFitsWcs(
    frameA?.wcs_header,
    activeRa ?? 133.79513,
    activeDec ?? -7.24515,
    fov,
    bufAQ.data?.width ?? 128,
    bufAQ.data?.height ?? 128,
  );

  const overrideAssociation = async (
    epoch: string,
    action: "accept" | "reject" | "reassign",
    detectionId?: number | null,
    reason = "User manual association override",
  ) => {
    if (!activeObjId) return;
    await api.overrideObjectAssociation(activeObjId, epoch, {
      action,
      detection_id: detectionId,
      reason,
    });
    associationsQ.refetch();
    objectQ.refetch();
  };

  const nextLabel = (existing: string[]): string => {
    const set = new Set(existing.map((t) => t.split(":")[0]));
    for (const ch of "ABCDEFGHIJKLMNOPQRSTUVWXYZ") {
      if (!set.has(ch)) return ch;
    }
    return `O${set.size + 1}`;
  };

  const onTrackSuggestion = (s: SuggestionItem) => {
    const lbl = nextLabel(track);
    addTrack(lbl, s.ra, s.dec);
    setView({ obj: String(s.detection_id), inspectorTab: "object" });
  };

  const onBoxSelect = (box: { raMin: number; raMax: number; decMin: number; decMax: number }) => {
    const inBox = (analysisQ.data?.detections ?? []).filter(
      (d) => d.ra >= box.raMin && d.ra <= box.raMax && d.dec >= box.decMin && d.dec <= box.decMax,
    );
    if (inBox.length > 0) {
      let cur = track;
      inBox.forEach((d) => {
        const lbl = nextLabel(cur);
        cur = [...cur, `${lbl}:${d.ra.toFixed(5)},${d.dec.toFixed(5)}`];
        addTrack(lbl, d.ra, d.dec);
      });
      const first = inBox[0];
      if (first) setView({ obj: String(first.id), inspectorTab: "object" });
    } else {
      const midRa = 0.5 * (box.raMin + box.raMax);
      const midDec = 0.5 * (box.decMin + box.decMax);
      addTrack(nextLabel(track), midRa, midDec);
      setView({ inspectorTab: "object" });
    }
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
    onBoxSelect,
    track,
    removeTrack,
    selectBoxMode,
    toggleSelectBox,
    spectrum: spectrumQ.data,
    resolving: resolveQ.isFetching,
    resolveError: resolveQ.error instanceof Error ? resolveQ.error.message : undefined,
    setPausedByVis,
  };
}
