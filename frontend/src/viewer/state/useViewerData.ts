import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  fetchScienceFrame,
  type CutoutFrame,
  type EphemerisPredictionResult,
  type SuggestionItem,
} from "../../api/client";
import { useAuthStore } from "../../app/authStore";
import { parseFitsWcs } from "../aladin/wcs";
import { useViewStore } from "./viewStore";

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
    enabled: hasCoords,
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
    enabled: hasCoords,
  });

  const frames: CutoutFrame[] = useMemo(() => {
    const raw = cutoutQ.data?.frames ?? [];
    if (raw.length > 0) {
      return [...raw].sort((a, b) => a.mjd_tdb_mid - b.mjd_tdb_mid);
    }
    if (fitsId) {
      return [
        {
          frame_id: fitsId,
          epoch: "Uploaded FITS",
          mjd_tdb_mid: 60000.0,
          utc_mid: "2025-01-01T00:00:00Z",
          display_url: `/api/v1/uploads/${fitsId}/display.webp`,
          science_url: `/api/v1/uploads/${fitsId}/science.bin`,
          fits_url: `/api/v1/uploads/${fitsId}/report`,
          wcs_header: "",
          stretch: { vmin: 0, vmax: 1000 },
          flagged_fraction: 0,
        },
      ];
    }
    return [];
  }, [cutoutQ.data?.frames, fitsId]);
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

  const onBoxSelect = async (box: { raMin: number; raMax: number; decMin: number; decMax: number }) => {
    if (analysisQ.data?.analysisId) {
      try {
        const res = await api.selectAnalysisObject(analysisQ.data.analysisId, {
          mode: "box",
          box: {
            ra_min: Math.min(box.raMin, box.raMax),
            dec_min: Math.min(box.decMin, box.decMax),
            ra_max: Math.max(box.raMin, box.raMax),
            dec_max: Math.max(box.decMin, box.decMax),
          },
        });
        if (res.objects && res.objects.length > 0) {
          res.objects.forEach((o) => {
            addTrack(o.label, o.ra, o.dec);
          });
          const first = res.objects[0];
          if (first) {
            setTrackedUuid(first.id);
            setView({
              obj: first.id,
              inspectorTab: "object",
              selectBoxMode: false,
            });
            return;
          }
        }
      } catch {
        // fallback to client-side tracking
      }
    }

    const inRa = (ra: number) =>
      box.raMin <= box.raMax
        ? ra >= box.raMin && ra <= box.raMax
        : ra >= box.raMin || ra <= box.raMax;
    const inDec = (dec: number) =>
      box.decMin <= box.decMax
        ? dec >= box.decMin && dec <= box.decMax
        : dec >= box.decMax && dec <= box.decMin;

    const inBoxDetections = (analysisQ.data?.detections ?? []).filter(
      (d) => inRa(d.ra) && inDec(d.dec),
    );
    const inBoxGaia = (gaiaQ.data?.sources ?? []).filter((s) => inRa(s.ra) && inDec(s.dec));
    const inBoxCatwise = (catwiseQ.data?.sources ?? []).filter((s) => inRa(s.ra) && inDec(s.dec));

    if (inBoxDetections.length > 0) {
      let cur = track;
      inBoxDetections.forEach((d) => {
        const lbl = nextTrackLabel(cur);
        cur = [...cur, `${lbl}:${d.ra.toFixed(5)},${d.dec.toFixed(5)}`];
        addTrack(lbl, d.ra, d.dec);
      });
      const first = inBoxDetections[0];
      setView({
        obj: first ? String(first.id) : undefined,
        inspectorTab: "object",
        selectBoxMode: false,
      });
    } else if (inBoxGaia.length > 0 || inBoxCatwise.length > 0) {
      const catSources = [...inBoxGaia, ...inBoxCatwise];
      let cur = track;
      catSources.forEach((s) => {
        const lbl = nextTrackLabel(cur);
        cur = [...cur, `${lbl}:${s.ra.toFixed(5)},${s.dec.toFixed(5)}`];
        addTrack(lbl, s.ra, s.dec);
      });
      const firstLbl = cur[track.length]?.split(":")[0];
      setView({
        obj: firstLbl,
        inspectorTab: "object",
        selectBoxMode: false,
      });
    } else {
      // Empty box: no detections or catalog sources; do not create empty track
      setView({ selectBoxMode: false });
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
    resolveTargetDirect,
    resolving: resolveQ.isFetching,
    loadingCutouts: cutoutQ.isFetching,
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
