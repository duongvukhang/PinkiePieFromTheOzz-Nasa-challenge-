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

export interface EpochWaypoint {
  epoch: string;
  mjd: number;
  ra: number;
  dec: number;
  x?: number;
  y?: number;
  isDetected: boolean;
  detectionId?: number | null;
  snr?: number | null;
  flux?: number | null;
  flags?: number | null;
  sigRaMas?: number | null;
  sigDecMas?: number | null;
}

export interface TrackedTrajectory {
  label: string;
  color: string;
  waypoints: EpochWaypoint[];
  currentWaypoint?: EpochWaypoint;
  previousWaypoint?: EpochWaypoint;
  motionVector?: {
    from: EpochWaypoint;
    to: EpochWaypoint;
    deltaArcsec: number;
    deltaTimeYears: number;
    pmTotalMasYr: number;
    paDeg: number;
    isMoving: boolean;
  } | null;
  pmVector?: {
    pmTotalMasYr: number;
    paDeg: number;
  } | null;
}

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
    setActiveBox,
    addTrack,
    removeTrack,
    syncFromSearch,
    setView,
    mode,
    op,
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
      for (let i = 0; i < 45 && status !== "succeeded" && status !== "failed"; i++) {
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

    const cachedMeta = view.loadedFramesMeta || {};
    for (const fid of allFitsIds) {
      if (!matchedIds.has(fid) && !excluded.has(fid)) {
        const meta = cachedMeta[fid] || {};
        userFrames.push({
          frame_id: fid,
          epoch: meta.epoch || meta.band_label || "Uploaded FITS",
          mjd_tdb_mid: meta.mjd_tdb_mid ?? 60000.0,
          utc_mid:
            meta.utc_mid ||
            (meta.mjd_tdb_mid
              ? new Date((meta.mjd_tdb_mid - 40587) * 86400000).toISOString()
              : "2025-01-01T00:00:00Z"),
          array_id: undefined,
          band_label: meta.band_label,
          display_url: meta.display_url || `/api/v1/uploads/${fid}/display.webp`,
          science_url: meta.science_url || `/api/v1/uploads/${fid}/science.bin`,
          fits_url: meta.fits_url || `/api/v1/uploads/${fid}/frame.fits`,
          wcs_header: meta.wcs_header || "",
          stretch: meta.stretch || { vmin: 0, vmax: 1000, shape: [128, 128] },
          flagged_fraction: 0,
        });
      }
    }

    if (userFrames.length > 0) {
      let filteredUserFrames = userFrames;
      if (bands === "all" && userFrames.length > 1) {
        const bandCounts = new Map<string, number>();
        for (const uf of userFrames) {
          const b = uf.band_label || "";
          if (b) bandCounts.set(b, (bandCounts.get(b) ?? 0) + 1);
        }
        if (bandCounts.size > 1) {
          const sorted = [...bandCounts.entries()].sort((a, b) => b[1] - a[1]);
          const dominantUserBand = sorted[0]?.[0];
          if (dominantUserBand) {
            filteredUserFrames = userFrames.filter((uf) => !uf.band_label || uf.band_label === dominantUserBand);
          }
        }
      }
      const seen = new Set<string>();
      return filteredUserFrames
        .filter((uf) => !excluded.has(uf.frame_id) && !seen.has(uf.frame_id) && seen.add(uf.frame_id))
        .sort((a, b) => a.mjd_tdb_mid - b.mjd_tdb_mid);
    }

    const rawCutouts = cutoutQ.data?.frames ?? [];
    const seenFrameIds = new Set<string>();
    const seenEpochs = new Set<string>();
    const combined: CutoutFrame[] = [];

    // Consistent infrared band filtering when bands === "all"
    let targetBand: string | null = null;
    if (bands === "all" && rawCutouts.length > 0) {
      const bandCounts = new Map<string, number>();
      for (const cf of rawCutouts) {
        const b = String(cf.array_id || cf.band_label || "");
        if (b) bandCounts.set(b, (bandCounts.get(b) ?? 0) + 1);
      }
      if (bandCounts.size > 0) {
        const sorted = [...bandCounts.entries()].sort((a, b) => b[1] - a[1]);
        targetBand = sorted[0]?.[0] ?? null;
      }
    }

    for (const cf of rawCutouts) {
      if (excluded.has(cf.frame_id) || seenFrameIds.has(cf.frame_id)) continue;
      const frameBand = String(cf.array_id ?? cf.band_label ?? "");
      if (targetBand && frameBand && frameBand !== targetBand) {
        continue;
      }
      if (!seenEpochs.has(cf.epoch.toLowerCase())) {
        combined.push(cf);
        seenFrameIds.add(cf.frame_id);
        seenEpochs.add(cf.epoch.toLowerCase());
      }
    }

    return combined.sort((a, b) => a.mjd_tdb_mid - b.mjd_tdb_mid);
  }, [cutoutQ.data, uploadsQ.data, allFitsIds, excludedFrameIds, bands, view.loadedFramesMeta]);
  const safeIdx = frames.length ? ((frame % frames.length) + frames.length) % frames.length : 0;
  const frameA = frames.find((f) => f.frame_id === a) ?? frames[safeIdx];
  const frameB =
    frames.find((f) => f.frame_id === b) ?? frames[(safeIdx + 1) % Math.max(1, frames.length)];

  const bufAQ = useQuery({
    queryKey: ["science", frameA?.science_url],
    queryFn: () => fetchScienceFrame(frameA!.science_url),
    enabled: Boolean(frameA?.science_url && (op !== "none" || mode === "compare")),
  });

  const bufBQ = useQuery({
    queryKey: ["science", frameB?.science_url],
    queryFn: () => fetchScienceFrame(frameB!.science_url),
    enabled:
      Boolean(frameB?.science_url && (op !== "none" || mode === "compare")) &&
      frameB?.frame_id !== frameA?.frame_id,
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
    queryKey: ["analysis", cutoutQ.data?.cutout_id, allFitsIds.join(","), activeRa, activeDec, fov, release],
    queryFn: async () => {
      const acc = allFitsIds.length > 0
        ? await api.createAnalysis({ fits_file_ids: allFitsIds })
        : await api.createAnalysis({
            cutout_id: cutoutQ.data?.cutout_id,
            ra: cutoutQ.data ? undefined : activeRa,
            dec: cutoutQ.data ? undefined : activeDec,
            fov_arcmin: fov,
            release,
          });
      let status = acc.status;
      let delay = 150;
      for (let i = 0; i < 45 && status !== "succeeded" && status !== "failed"; i++) {
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
    enabled: !skipSecondary && (Boolean(cutoutQ.data?.cutout_id) || allFitsIds.length > 0),
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
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data || (data.measurements && data.measurements.length < 2)) {
        return 1500;
      }
      return false;
    },
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
  const imgW = frameShape?.[1] ?? bufAQ.data?.width ?? 128;
  const imgH = frameShape?.[0] ?? bufAQ.data?.height ?? 128;

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
          setView({ obj: created.id, inspectorTab: "object", isTimelineOpen: true });
          return;
        }
      } catch {
        // fallback to client-side tracking
      }
    }
    const lbl = nextTrackLabel(track);
    addTrack(lbl, d.ra, d.dec);
    setView({ obj: String(d.id), inspectorTab: "object", isTimelineOpen: true });
  };

  const onBoxSelect = async (box: { raMin: number; raMax: number; decMin: number; decMax: number }) => {
    setActiveBox(box);
    setView(
      {
        activeBox: box,
        inspectorTab: "object",
        selectBoxMode: false,
      },
      false,
    );

    if (track.length < MAX_TRACKED_OBJECTS) {
      const inRa = (r: number) =>
        box.raMin <= box.raMax ? r >= box.raMin && r <= box.raMax : r >= box.raMin || r <= box.raMax;
      const inDec = (d: number) =>
        box.decMin <= box.decMax ? d >= box.decMin && d <= box.decMax : d >= box.decMax && d <= box.decMin;
      const inBoxDets = (detections || []).filter((d) => inRa(d.ra) && inDec(d.dec));
      if (inBoxDets.length > 0 && inBoxDets[0]) {
        await onTrackDetection(inBoxDets[0]);
        return;
      }
      if (analysisQ.data?.analysisId) {
        try {
          const res = await api.selectAnalysisObject(analysisQ.data.analysisId, {
            mode: "box",
            box: {
              ra_min: box.raMin,
              ra_max: box.raMax,
              dec_min: box.decMin,
              dec_max: box.decMax,
            },
            epoch: frameA?.epoch,
          });
          const created = res.objects?.[0];
          if (created) {
            setTrackedUuid(created.id);
            addTrack(created.label, created.ra, created.dec);
            setView({ obj: created.id, inspectorTab: "object", isTimelineOpen: true });
            return;
          }
        } catch {
          // fallback
        }
      }
      const midRa =
        box.raMin <= box.raMax ? 0.5 * (box.raMin + box.raMax) : (0.5 * (box.raMin + box.raMax + 360.0)) % 360.0;
      const midDec = 0.5 * (box.decMin + box.decMax);
      const lbl = nextTrackLabel(track);
      addTrack(lbl, midRa, midDec);
      setView({ obj: lbl, inspectorTab: "object", isTimelineOpen: true });
    }
  };

  const trackedTrajectories: TrackedTrajectory[] = useMemo(() => {
    if (!track.length || !frames.length) return [];
    const allDets = analysisQ.data?.detections ?? [];
    const objDetail = objectQ.data;

    return track.map((t, tIdx) => {
      const [lbl, coords] = t.split(":");
      const [rStr, dStr] = (coords ?? "").split(",");
      const initRa = Number(rStr);
      const initDec = Number(dStr);
      const label = lbl || (tIdx === 0 ? "A" : "B");
      const color = label === "B" ? "#f97316" : label === "A" ? "#38bdf8" : "#22c55e";

      const matchesObj =
        objDetail &&
        (objDetail.label === label ||
          String(objDetail.id) === obj ||
          (tIdx === 0 && !objDetail.label));
      const backendMeasurements = matchesObj ? objDetail.measurements ?? [] : [];

      const waypoints: EpochWaypoint[] = frames.map((f, fIdx) => {
        const ep = f.epoch || `E${fIdx + 1}`;
        const fMjd = f.mjd_tdb_mid;

        // 1. Check if backend measurement exists for this epoch
        const bMeas = backendMeasurements.find(
          (m: any) =>
            (m.epoch && m.epoch.toLowerCase() === ep.toLowerCase()) ||
            Math.abs(m.mjd_tdb - fMjd) < 0.5,
        );
        if (bMeas && bMeas.ra != null && bMeas.dec != null) {
          return {
            epoch: ep,
            mjd: fMjd,
            ra: bMeas.ra,
            dec: bMeas.dec,
            isDetected: !bMeas.is_upper_limit,
            detectionId: undefined,
            snr: undefined,
            flux: bMeas.flux_ujy,
            flags: bMeas.flags,
            sigRaMas: bMeas.sig_ra_mas,
            sigDecMas: bMeas.sig_dec_mas,
          };
        }

        // 2. Search frame detections around initial / projected position
        const frameDets = allDets.filter(
          (d) => String(d.fits_file_id) === String(f.frame_id) || (d as any).epoch === ep,
        );
        let bestDet: DetectionRead | null = null;
        let bestSep = 1e9;
        for (const fd of frameDets) {
          const sep = Math.hypot(
            (fd.ra - initRa) * Math.cos((initDec * Math.PI) / 180),
            fd.dec - initDec,
          );
          if (sep < 0.005 && sep < bestSep) {
            bestSep = sep;
            bestDet = fd;
          }
        }

        if (bestDet) {
          return {
            epoch: ep,
            mjd: fMjd,
            ra: bestDet.ra,
            dec: bestDet.dec,
            isDetected: true,
            detectionId: bestDet.id,
            snr: bestDet.snr ?? undefined,
            flux: bestDet.flux_ujy ?? undefined,
            flags: bestDet.flags ?? 0,
            sigRaMas: bestDet.sig_ra_mas ?? undefined,
            sigDecMas: bestDet.sig_dec_mas ?? undefined,
          };
        }

        // 3. Fallback position across epochs
        return {
          epoch: ep,
          mjd: fMjd,
          ra: initRa,
          dec: initDec,
          isDetected: false,
        };
      });

      const curEp = frameA?.epoch;
      const curIdx = waypoints.findIndex((w) => w.epoch === curEp);
      const activeIdx = curIdx >= 0 ? curIdx : Math.min(frame, waypoints.length - 1);
      const currentWaypoint = waypoints[activeIdx];
      const prevIdx =
        activeIdx > 0 ? activeIdx - 1 : waypoints.length > 1 ? waypoints.length - 1 : -1;
      const previousWaypoint = prevIdx >= 0 ? waypoints[prevIdx] : undefined;

      let motionVector = null;
      if (previousWaypoint && currentWaypoint && previousWaypoint !== currentWaypoint) {
        const cosDec = Math.cos((currentWaypoint.dec * Math.PI) / 180);
        const dRa = (currentWaypoint.ra - previousWaypoint.ra) * cosDec;
        const dDec = currentWaypoint.dec - previousWaypoint.dec;
        const deltaArcsec = Math.hypot(dRa, dDec) * 3600;
        const dtYr = Math.abs(currentWaypoint.mjd - previousWaypoint.mjd) / 365.25;
        const pmTotalMasYr = dtYr > 0.0001 ? (deltaArcsec / dtYr) * 1000 : 0;
        const paDeg = ((Math.atan2(dRa, dDec) * 180) / Math.PI + 360) % 360;
        motionVector = {
          from: previousWaypoint,
          to: currentWaypoint,
          deltaArcsec,
          deltaTimeYears: dtYr,
          pmTotalMasYr,
          paDeg,
          isMoving: deltaArcsec > 0.05,
        };
      }

      return {
        label,
        color,
        waypoints,
        currentWaypoint,
        previousWaypoint,
        motionVector,
        pmVector:
          matchesObj && objDetail?.motion_fit?.pm_total_masyr != null
            ? {
                pmTotalMasYr: objDetail.motion_fit.pm_total_masyr,
                paDeg: objDetail.motion_fit.pa_deg ?? 0,
              }
            : null,
      };
    });
  }, [track, frames, frameA?.epoch, frame, analysisQ.data?.detections, objectQ.data, obj]);

  const rawDetections = analysisQ.data?.detections ?? [];
  const detections: DetectionRead[] = useMemo(() => {
    if (rawDetections.length > 0) {
      if (frameA?.frame_id) {
        const frameMatches = rawDetections.filter(
          (d) => String(d.fits_file_id) === String(frameA.frame_id),
        );
        if (frameMatches.length > 0) return frameMatches;
      }
      const seen = new Set<number>();
      const deduped: DetectionRead[] = [];
      for (const d of rawDetections) {
        if (!seen.has(d.id)) {
          seen.add(d.id);
          deduped.push(d);
        }
      }
      return deduped;
    }
    const gaiaList = gaiaQ.data?.sources ?? [];
    if (gaiaList.length > 0) {
      return gaiaList.map((g, idx) => ({
        id: idx + 1,
        fits_file_id: frameA?.frame_id || "",
        ra: g.ra,
        dec: g.dec,
        x_pix: null,
        y_pix: null,
        flux_ujy: g.mag ? Math.pow(10, (23.9 - g.mag) / 2.5) : 1000,
        mag_ab: g.mag,
        fwhm_arcsec: 6.2,
        flags: 0,
        real_bogus: 0.99,
        is_difference: false,
      }));
    }
    const catwiseList = catwiseQ.data?.sources ?? [];
    if (catwiseList.length > 0) {
      return catwiseList.map((c, idx) => ({
        id: idx + 1,
        fits_file_id: frameA?.frame_id || "",
        ra: c.ra,
        dec: c.dec,
        x_pix: null,
        y_pix: null,
        flux_ujy: c.mag ? Math.pow(10, (23.9 - c.mag) / 2.5) : 800,
        mag_ab: c.mag,
        fwhm_arcsec: 6.2,
        flags: 0,
        real_bogus: 0.99,
        is_difference: false,
      }));
    }
    const sugs = analysisQ.data?.suggestions ?? [];
    if (sugs.length > 0) {
      return sugs.map((s, idx) => ({
        id: s.detection_id || idx + 1,
        fits_file_id: frameA?.frame_id || "",
        ra: s.ra,
        dec: s.dec,
        x_pix: null,
        y_pix: null,
        flux_ujy: 1000,
        mag_ab: undefined,
        fwhm_arcsec: 6.2,
        flags: 0,
        real_bogus: 0.95,
        is_difference: false,
      }));
    }
    return [];
  }, [rawDetections, frameA?.frame_id, gaiaQ.data?.sources, catwiseQ.data?.sources, analysisQ.data?.suggestions]);

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
    warnings: cutoutQ.data?.warnings ?? [],
    gaiaSources: gaiaQ.data?.sources ?? [],
    catwiseSources: catwiseQ.data?.sources ?? [],
    detections,
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
    trackedTrajectories,
    resolveTargetDirect,
    resolving: resolveQ.isFetching,
    loadingCutouts:
      (allFitsIds.length > 0 ? uploadsQ.isFetching : cutoutQ.isFetching) && frames.length === 0,
    loadingText: analysisQ.isFetching
      ? "Extracting sources & candidates…"
      : allFitsIds.length > 0
        ? (uploadsQ.isFetching ? "Loading FITS frames…" : "Processing frames…")
        : (cutoutQ.isFetching ? "Loading FITS cutouts…" : "Processing field…"),
    errorMsg: (() => {
      const err = resolveQ.error ?? cutoutQ.error ?? bufAQ.error ?? null;
      return err instanceof Error ? err.message : typeof err === "string" ? err : undefined;
    })(),
    resolveError: (() => {
      const err = resolveQ.error ?? cutoutQ.error ?? bufAQ.error ?? null;
      return err instanceof Error ? err.message : typeof err === "string" ? err : undefined;
    })(),
    setPausedByVis,
    ephemerisResult,
    setEphemerisResult,
  };
}
