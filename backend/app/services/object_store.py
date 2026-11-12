"""Object, observation, association, motion-fit, classification, and spectrum persistence."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.flags import DetectionFlag
from app.db.models import (
    Association,
    AuditLog,
    Classification,
    Crossmatch,
    DataRelease,
    Detection,
    EpochMeasurement,
    FitsFile,
    MotionFit,
    Observation,
    Project,
    Provenance,
    SkyField,
    Spectrum,
    TrackedObject,
    User,
)
from app.schemas.objects import (
    AssociationHypothesis,
    ClassificationSummary,
    EpochMeasurementRead,
    MotionFitRead,
    SpectrumResponse,
    TrackedObjectSummary,
)
from app.services.tracking import resolve_epoch_observation
from sphx_core.identity import format_public_id
from sphx_core.wcs import healpix9_nested


def _session_label(index: int) -> str:
    n = index
    chars: list[str] = []
    while True:
        chars.append(chr(ord("A") + (n % 26)))
        n = n // 26 - 1
        if n < 0:
            break
    return "".join(reversed(chars))


def _next_label(used: set[str]) -> str:
    idx = 0
    while (lbl := _session_label(idx)) in used:
        idx += 1
    used.add(lbl)
    return lbl


def _obs_dict(
    oid: UUID,
    epoch_label: str,
    mjd_tdb_mean: float,
    fits_file_id: UUID | None,
    date_obs_utc: Any,
    release_id: Any,
) -> dict[str, Any]:
    return {
        "id": oid,
        "epoch_label": epoch_label,
        "mjd_tdb_mean": mjd_tdb_mean,
        "fits_file_id": fits_file_id,
        "date_obs_utc": date_obs_utc,
        "release_id": release_id,
    }


def _summary_from_cand(
    oid: UUID,
    label: str,
    ra: float,
    dec: float,
    cand: Mapping[str, Any],
    selection_mode: str,
) -> TrackedObjectSummary:
    band, flux = cand.get("band_group"), cand.get("flux_ujy")
    return TrackedObjectSummary(
        id=oid,
        label=label,
        ra=ra,
        dec=dec,
        crossmatch=cand.get("crossmatch"),
        flux_ujy={str(band): float(flux)} if band and flux is not None else {},
        selection_mode=selection_mode,
    )


def _assoc_payload(
    obs_id: UUID,
    det_id: int | None,
    pred_ra: Any,
    pred_dec: Any,
    *,
    is_chosen: bool,
    decided_by: str,
    user_id: UUID | None,
    reason: str,
    flags: int,
    cost: float | None = 0.0,
) -> dict[str, Any]:
    return {
        "observation_id": obs_id,
        "detection_id": det_id,
        "pred_ra": pred_ra,
        "pred_dec": pred_dec,
        "cost": cost,
        "confidence": 1.0,
        "rank": 1,
        "is_chosen": is_chosen,
        "decided_by": decided_by,
        "user_id": user_id,
        "reason": reason,
        "flags": flags,
    }


def _latest_provenance(session: Session, entity_table: str, entity_id: str) -> Provenance | None:
    return session.scalar(
        select(Provenance)
        .where(Provenance.entity_table == entity_table, Provenance.entity_id == entity_id)
        .order_by(Provenance.id.desc())
        .limit(1)
    )


class InMemoryObjectMixin:
    """In-memory persistence methods for tracked objects, tracks, associations, and spectra."""

    _lock: threading.Lock

    def _init_object_store(self) -> None:
        self._observations: dict[UUID, dict[str, Any]] = {}
        self._analysis_obs: dict[UUID, list[UUID]] = {}
        self._objects: dict[UUID, dict[str, Any]] = {}
        self._project_objects: dict[UUID, list[UUID]] = {}
        self._associations: dict[UUID, list[dict[str, Any]]] = {}
        self._next_assoc_id: int = 1
        self._measurements: dict[UUID, list[dict[str, Any]]] = {}
        self._motion_fits: dict[UUID, list[dict[str, Any]]] = {}
        self._classifications: dict[UUID, dict[str, Any]] = {}
        self._crossmatches: dict[UUID, list[dict[str, Any]]] = {}
        self._spectra: dict[tuple[UUID, UUID | None], SpectrumResponse] = {}
        self._audit_log: list[dict[str, Any]] = []

    def save_analysis_observations(
        self, analysis_id: UUID, frames: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        with self._lock:
            obs_ids: list[UUID] = []
            out: list[dict[str, Any]] = []
            for f in frames:
                oid = UUID(str(f["id"]))
                rec = _obs_dict(
                    oid,
                    str(f["epoch_label"]),
                    float(f["mjd_tdb_mean"]),
                    UUID(str(f["fits_file_id"])),
                    f.get("date_obs_utc"),
                    f.get("release_id"),
                )
                self._observations[oid] = rec
                obs_ids.append(oid)
                out.append(dict(rec))
            self._analysis_obs[analysis_id] = obs_ids
            return out

    def get_analysis_observations(self, analysis_id: UUID) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(self._observations[oid])
                for oid in self._analysis_obs.get(analysis_id, [])
                if oid in self._observations
            ]

    def select_tracked_objects(
        self,
        *,
        project_id: UUID,
        analysis_id: UUID,
        fits_file_ids: Sequence[UUID],
        candidates: Sequence[Mapping[str, Any]],
        selection_mode: str,
    ) -> list[TrackedObjectSummary]:
        with self._lock:
            proj_ids = self._project_objects.setdefault(project_id, [])
            used_labels = {
                str(self._objects[oid]["label"]) for oid in proj_ids if oid in self._objects
            }
            by_seed = {
                self._objects[oid]["seed_detection_id"]: self._objects[oid]
                for oid in proj_ids
                if oid in self._objects and self._objects[oid].get("seed_detection_id") is not None
            }
            summaries: list[TrackedObjectSummary] = []

            for cand in candidates:
                det_id = cand.get("detection_id")
                if det_id is not None and det_id in by_seed:
                    existing = by_seed[det_id]
                    if str(analysis_id) not in existing["analysis_ids"]:
                        existing["analysis_ids"].append(str(analysis_id))
                    for fid in fits_file_ids:
                        if fid not in existing["fits_file_ids"]:
                            existing["fits_file_ids"].append(fid)
                    summaries.append(TrackedObjectSummary.model_validate(existing["summary"]))
                    continue

                oid = uuid4()
                label = _next_label(used_labels)
                ra, dec, mjd = float(cand["ra"]), float(cand["dec"]), float(cand["mjd_tdb"])
                summary = _summary_from_cand(oid, label, ra, dec, cand, selection_mode)
                self._objects[oid] = {
                    "id": oid,
                    "project_id": project_id,
                    "label": label,
                    "public_id": format_public_id(ra, dec),
                    "selection_mode": selection_mode,
                    "ref_ra": ra,
                    "ref_dec": dec,
                    "ref_mjd_tdb": mjd,
                    "status": "active",
                    "identity_confidence": 1.0 if det_id is not None else None,
                    "seed_detection_id": det_id,
                    "analysis_ids": [str(analysis_id)],
                    "fits_file_ids": list(fits_file_ids),
                    "caveats": [],
                    "override_rev": 0,
                    "summary": summary.model_dump(),
                }
                proj_ids.append(oid)
                if det_id is not None:
                    by_seed[det_id] = self._objects[oid]

                obs_id = cand.get("observation_id")
                self._measurements[oid] = [
                    {
                        "epoch": cand.get("epoch_label"),
                        "observation_id": obs_id,
                        "source": "spherex" if det_id is not None else "manual",
                        "mjd_tdb": mjd,
                        "ra": ra,
                        "dec": dec,
                        "sig_ra_mas": cand.get("sig_ra_mas"),
                        "sig_dec_mas": cand.get("sig_dec_mas"),
                        "corr_radec": cand.get("corr_radec"),
                        "band_group": cand.get("band_group"),
                        "flux_ujy": cand.get("flux_ujy"),
                        "flux_err_ujy": cand.get("flux_err_ujy"),
                        "is_upper_limit": False,
                        "flags": int(cand.get("flags") or 0),
                        "association_confidence": 1.0 if det_id is not None else None,
                        "used_in_fit": True,
                    }
                ]
                self._associations[oid] = []
                if obs_id is not None and det_id is not None:
                    aid = self._next_assoc_id
                    self._next_assoc_id += 1
                    self._associations[oid].append(
                        {
                            "id": aid,
                            **_assoc_payload(
                                obs_id,
                                int(det_id),
                                ra,
                                dec,
                                is_chosen=True,
                                decided_by="auto",
                                user_id=None,
                                reason="initial selection",
                                flags=int(cand.get("flags") or 0),
                            ),
                        }
                    )
                self._motion_fits[oid] = []
                self._crossmatches[oid] = []
                summaries.append(summary)
            return summaries

    def list_project_objects(
        self, project_id: UUID, object_ids: Sequence[UUID] | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            proj_ids = self._project_objects.get(project_id, [])
            proj_set = set(proj_ids)
            targets = list(object_ids) if object_ids is not None else proj_ids
            out: list[dict[str, Any]] = []
            for oid in targets:
                if oid not in proj_set or oid not in self._objects:
                    if object_ids is not None:
                        raise KeyError(f"Tracked object {oid} not found in project {project_id}.")
                    continue
                out.append(dict(self._objects[oid]))
            return out

    def get_tracked_object_bundle(self, object_id: UUID) -> dict[str, Any] | None:
        with self._lock:
            obj = self._objects.get(object_id)
            if obj is None:
                return None
            obs_list: list[dict[str, Any]] = []
            seen_obs: set[UUID] = set()
            for aid_str in obj.get("analysis_ids", []):
                for obs_id in self._analysis_obs.get(UUID(aid_str), []):
                    if obs_id not in seen_obs and obs_id in self._observations:
                        seen_obs.add(obs_id)
                        obs_list.append(dict(self._observations[obs_id]))
            obs_list.sort(key=lambda o: float(o["mjd_tdb_mean"]))
            cls_raw = self._classifications.get(object_id)
            return {
                **obj,
                "observations": obs_list,
                "measurements": [
                    EpochMeasurementRead.model_validate(m)
                    for m in self._measurements.get(object_id, [])
                ],
                "motion_fits": [dict(f) for f in self._motion_fits.get(object_id, [])],
                "classification": (
                    ClassificationSummary.model_validate(cls_raw) if cls_raw is not None else None
                ),
                "associations": [
                    AssociationHypothesis.model_validate(a)
                    for a in self._associations.get(object_id, [])
                ],
                "raw_associations": [dict(a) for a in self._associations.get(object_id, [])],
                "crossmatches": [dict(c) for c in self._crossmatches.get(object_id, [])],
            }

    def save_track_results(
        self,
        object_id: UUID,
        *,
        obj_updates: Mapping[str, Any],
        measurements: Sequence[Mapping[str, Any]],
        associations: Sequence[Mapping[str, Any]],
        motion_fits: Sequence[Mapping[str, Any]],
        classification: Mapping[str, Any] | None,
        crossmatches: Sequence[Mapping[str, Any]],
        caveats: Sequence[str],
    ) -> None:
        with self._lock:
            obj = self._objects[object_id]
            obj.update(obj_updates)
            obj["caveats"] = list(caveats)
            self._measurements[object_id] = [dict(m) for m in measurements]

            existing_assocs = self._associations.get(object_id, [])
            locked_obs = {
                a["observation_id"] for a in existing_assocs if a.get("decided_by") == "user"
            }
            kept = [a for a in existing_assocs if a["observation_id"] in locked_obs]
            for a in associations:
                if UUID(str(a["observation_id"])) in locked_obs:
                    continue
                aid = self._next_assoc_id
                self._next_assoc_id += 1
                kept.append({**dict(a), "id": aid})
            obs_mjd = {oid: o["mjd_tdb_mean"] for oid, o in self._observations.items()}
            kept.sort(key=lambda a: (obs_mjd.get(a["observation_id"], 0.0), int(a["rank"])))
            self._associations[object_id] = kept

            self._motion_fits[object_id] = [dict(f) for f in motion_fits]
            if classification is not None:
                self._classifications[object_id] = dict(classification)
            self._crossmatches[object_id] = [dict(c) for c in crossmatches]
            for key in [k for k in self._spectra if k[0] == object_id]:
                self._spectra.pop(key, None)

    def apply_association_override(
        self,
        object_id: UUID,
        epoch: str,
        *,
        action: str,
        detection_id: int | None,
        reason: str,
        user_id: UUID | None,
    ) -> dict[str, Any]:
        with self._lock:
            obj = self._objects.get(object_id)
            if obj is None:
                raise KeyError(f"Tracked object not found: {object_id}")
            obs_list = [
                self._observations[oid]
                for aid in obj.get("analysis_ids", [])
                for oid in self._analysis_obs.get(UUID(aid), [])
                if oid in self._observations
            ]
            obs_list.sort(key=lambda o: float(o["mjd_tdb_mean"]))
            obs = resolve_epoch_observation(obs_list, epoch)
            if obs is None:
                raise LookupError(f"Epoch {epoch!r} not found for object {object_id}.")
            obs_id = UUID(str(obs["id"]))
            assocs = self._associations.setdefault(object_id, [])
            before = [dict(a) for a in assocs if a["observation_id"] == obs_id]

            override_flag = int(DetectionFlag.USER_OVERRIDE)
            epoch_rows = [a for a in assocs if a["observation_id"] == obs_id]
            for a in epoch_rows:
                a["is_chosen"] = False
                a["flags"] = int(a.get("flags") or 0) | override_flag

            if action in {"accept", "reassign"}:
                if action == "accept" and detection_id is None:
                    target = next(
                        (a for a in epoch_rows if a.get("rank") == 1),
                        epoch_rows[0] if epoch_rows else None,
                    )
                    if target is None:
                        raise LookupError(f"No association candidate at epoch {epoch!r} to accept.")
                else:
                    target = next(
                        (a for a in epoch_rows if a.get("detection_id") == detection_id), None
                    )
                if target is None:
                    aid = self._next_assoc_id
                    self._next_assoc_id += 1
                    target = {
                        "id": aid,
                        **_assoc_payload(
                            obs_id,
                            detection_id,
                            obj.get("ref_ra"),
                            obj.get("ref_dec"),
                            is_chosen=True,
                            decided_by="user",
                            user_id=user_id,
                            reason=reason,
                            flags=override_flag,
                        ),
                    }
                    assocs.append(target)
                else:
                    target.update(
                        {
                            "is_chosen": True,
                            "confidence": 1.0,
                            "decided_by": "user",
                            "user_id": user_id,
                            "reason": reason,
                            "flags": int(target.get("flags") or 0) | override_flag,
                        }
                    )
                chosen_det = target.get("detection_id")
            elif action == "reject":
                if not epoch_rows:
                    aid = self._next_assoc_id
                    self._next_assoc_id += 1
                    assocs.append(
                        {
                            "id": aid,
                            **_assoc_payload(
                                obs_id,
                                None,
                                obj.get("ref_ra"),
                                obj.get("ref_dec"),
                                is_chosen=False,
                                decided_by="user",
                                user_id=user_id,
                                reason=reason,
                                flags=override_flag,
                                cost=None,
                            ),
                        }
                    )
                else:
                    for a in epoch_rows:
                        a.update(
                            {
                                "is_chosen": False,
                                "decided_by": "user",
                                "user_id": user_id,
                                "reason": reason,
                            }
                        )
                chosen_det = None
            else:
                raise ValueError(f"Unsupported override action: {action!r}")

            obj["override_rev"] = int(obj.get("override_rev", 0)) + 1
            after = [dict(a) for a in assocs if a["observation_id"] == obs_id]
            self._audit_log.append(
                {
                    "user_id": user_id,
                    "action": f"association.{action}",
                    "entity_table": "associations",
                    "entity_id": f"{object_id}:{obs_id}",
                    "before": {"rows": before},
                    "after": {"rows": after},
                }
            )
            return {
                "observation_id": obs_id,
                "epoch_label": obs["epoch_label"],
                "detection_id": chosen_det,
                "action": action,
                "override_rev": obj["override_rev"],
            }

    def get_cached_spectrum(
        self, object_id: UUID, observation_id: UUID | None = None
    ) -> SpectrumResponse | None:
        with self._lock:
            return self._spectra.get((object_id, observation_id))

    def save_spectrum(self, spectrum: SpectrumResponse) -> None:
        with self._lock:
            self._spectra[(spectrum.object_id, spectrum.observation_id)] = spectrum


class SqlAlchemyObjectMixin:
    """PostgreSQL persistence for tracked objects, tracks, associations, and spectra."""

    _session_factory: sessionmaker[Session]

    @staticmethod
    def _obs_to_dict(obs: Observation) -> dict[str, Any]:
        q = dict(obs.quality or {})
        fid = UUID(str(q["fits_file_id"])) if q.get("fits_file_id") else None
        return _obs_dict(
            obs.id,
            obs.epoch_label or "S1",
            float(obs.mjd_tdb_mean),
            fid,
            q.get("date_obs_utc"),
            obs.release_id,
        )

    def save_analysis_observations(
        self, analysis_id: UUID, frames: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        if not frames:
            return []
        with self._session_factory() as session:
            ra_c = float(sum(float(f.get("ra_center") or 0.0) for f in frames) / len(frames)) % 360
            dec_c = float(sum(float(f.get("dec_center") or 0.0) for f in frames) / len(frames))
            field = SkyField(
                center_ra=ra_c,
                center_dec=dec_c,
                radius_deg=0.5,
                healpix9=healpix9_nested(ra_c, dec_c),
            )
            session.add(field)
            session.flush()

            obs_ids: list[str] = []
            out: list[dict[str, Any]] = []
            for f in frames:
                oid, fid = UUID(str(f["id"])), UUID(str(f["fits_file_id"]))
                rel = f.get("release_id")
                valid_rel = str(rel) if rel and session.get(DataRelease, str(rel)) else None
                if session.get(Observation, oid) is None:
                    session.add(
                        Observation(
                            id=oid,
                            sky_field_id=field.id,
                            release_id=valid_rel,
                            epoch_label=str(f["epoch_label"]),
                            mjd_tdb_mean=float(f["mjd_tdb_mean"]),
                            n_frames=1,
                            quality={
                                "fits_file_id": str(fid),
                                "analysis_id": str(analysis_id),
                                "date_obs_utc": f.get("date_obs_utc"),
                            },
                        )
                    )
                fits_row = session.get(FitsFile, fid)
                if fits_row is not None and fits_row.observation_id is None:
                    fits_row.observation_id = oid
                obs_ids.append(str(oid))
                out.append(
                    _obs_dict(
                        oid,
                        str(f["epoch_label"]),
                        float(f["mjd_tdb_mean"]),
                        fid,
                        f.get("date_obs_utc"),
                        valid_rel,
                    )
                )
            session.add(
                Provenance(
                    entity_table="observations",
                    entity_id=str(analysis_id),
                    inputs={"observation_ids": obs_ids},
                    code_version="0.1.0",
                    lib_versions={},
                )
            )
            session.commit()
            return out

    def get_analysis_observations(self, analysis_id: UUID) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            prov = _latest_provenance(session, "observations", str(analysis_id))
            if prov is None or not isinstance(prov.inputs, dict):
                return []
            ids = [UUID(str(i)) for i in prov.inputs.get("observation_ids", [])]
            if not ids:
                return []
            rows = session.scalars(
                select(Observation)
                .where(Observation.id.in_(ids))
                .order_by(Observation.mjd_tdb_mean)
            ).all()
            return [self._obs_to_dict(r) for r in rows]

    def select_tracked_objects(
        self,
        *,
        project_id: UUID,
        analysis_id: UUID,
        fits_file_ids: Sequence[UUID],
        candidates: Sequence[Mapping[str, Any]],
        selection_mode: str,
    ) -> list[TrackedObjectSummary]:
        with self._session_factory() as session:
            if session.get(Project, project_id) is None:
                raise KeyError(f"Project not found: {project_id}")
            existing = session.scalars(
                select(TrackedObject).where(TrackedObject.project_id == project_id)
            ).all()
            used_labels = {o.label for o in existing}
            existing_ids = [o.id for o in existing]

            seed_map: dict[int, TrackedObject] = {}
            if existing_ids:
                provs = session.scalars(
                    select(Provenance).where(
                        Provenance.entity_table == "tracked_objects",
                        Provenance.entity_id.in_([str(oid) for oid in existing_ids]),
                    )
                ).all()
                obj_by_id = {o.id: o for o in existing}
                for p in provs:
                    if isinstance(p.inputs, dict) and p.inputs.get("seed_detection_id") is not None:
                        oid = UUID(p.entity_id)
                        if oid in obj_by_id:
                            seed_map[int(p.inputs["seed_detection_id"])] = obj_by_id[oid]

            summaries: list[TrackedObjectSummary] = []
            for cand in candidates:
                det_id = cand.get("detection_id")
                if det_id is not None and int(det_id) in seed_map:
                    obj = seed_map[int(det_id)]
                    summaries.append(
                        _summary_from_cand(
                            obj.id,
                            obj.label,
                            float(obj.ref_ra if obj.ref_ra is not None else cand["ra"]),
                            float(obj.ref_dec if obj.ref_dec is not None else cand["dec"]),
                            cand,
                            obj.selection_mode or selection_mode,
                        )
                    )
                    continue

                oid = uuid4()
                label = _next_label(used_labels)
                ra, dec, mjd = float(cand["ra"]), float(cand["dec"]), float(cand["mjd_tdb"])
                obj = TrackedObject(
                    id=oid,
                    project_id=project_id,
                    label=label,
                    public_id=format_public_id(ra, dec),
                    selection_mode=selection_mode,
                    ref_ra=ra,
                    ref_dec=dec,
                    ref_mjd_tdb=mjd,
                    status="active",
                    identity_confidence=1.0 if det_id is not None else None,
                )
                session.add(obj)
                session.flush()

                obs_id = cand.get("observation_id")
                session.add(
                    EpochMeasurement(
                        tracked_object_id=oid,
                        observation_id=obs_id,
                        source="spherex" if det_id is not None else "manual",
                        mjd_tdb=mjd,
                        ra=ra,
                        dec=dec,
                        sig_ra_mas=cand.get("sig_ra_mas"),
                        sig_dec_mas=cand.get("sig_dec_mas"),
                        corr_radec=cand.get("corr_radec"),
                        band_group=cand.get("band_group"),
                        flux_ujy=cand.get("flux_ujy"),
                        flux_err_ujy=cand.get("flux_err_ujy"),
                        is_upper_limit=False,
                        flags=int(cand.get("flags") or 0),
                        used_in_fit=True,
                    )
                )
                if obs_id is not None and det_id is not None:
                    session.add(
                        Association(
                            tracked_object_id=oid,
                            **_assoc_payload(
                                obs_id,
                                int(det_id),
                                ra,
                                dec,
                                is_chosen=True,
                                decided_by="auto",
                                user_id=None,
                                reason="initial selection",
                                flags=int(cand.get("flags") or 0),
                            ),
                        )
                    )
                session.add(
                    Provenance(
                        entity_table="tracked_objects",
                        entity_id=str(oid),
                        inputs={
                            "analysis_ids": [str(analysis_id)],
                            "fits_file_ids": [str(f) for f in fits_file_ids],
                            "seed_detection_id": det_id,
                        },
                        params={"caveats": [], "override_rev": 0},
                        code_version="0.1.0",
                        lib_versions={},
                    )
                )
                summaries.append(_summary_from_cand(oid, label, ra, dec, cand, selection_mode))
            session.commit()
            return summaries

    def list_project_objects(
        self, project_id: UUID, object_ids: Sequence[UUID] | None = None
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            stmt = select(TrackedObject).where(TrackedObject.project_id == project_id)
            if object_ids is not None:
                stmt = stmt.where(TrackedObject.id.in_(list(object_ids)))
            rows = session.scalars(stmt.order_by(TrackedObject.created_at)).all()
            if object_ids is not None:
                found = {r.id for r in rows}
                missing = [oid for oid in object_ids if oid not in found]
                if missing:
                    raise KeyError(
                        f"Tracked object {missing[0]} not found in project {project_id}."
                    )
            out: list[dict[str, Any]] = []
            for r in rows:
                prov = _latest_provenance(session, "tracked_objects", str(r.id))
                p_params = dict(prov.params or {}) if prov else {}
                out.append(
                    {
                        "id": r.id,
                        "project_id": r.project_id,
                        "label": r.label,
                        "public_id": r.public_id,
                        "status": r.status or "active",
                        "override_rev": int(p_params.get("override_rev", 0)),
                    }
                )
            return out

    def get_tracked_object_bundle(self, object_id: UUID) -> dict[str, Any] | None:
        with self._session_factory() as session:
            obj = session.get(TrackedObject, object_id)
            if obj is None:
                return None
            prov = _latest_provenance(session, "tracked_objects", str(object_id))
            p_in = dict(prov.inputs or {}) if prov and isinstance(prov.inputs, dict) else {}
            p_par = dict(prov.params or {}) if prov and isinstance(prov.params, dict) else {}

            obs_ids_set: set[UUID] = set()
            for aid_str in p_in.get("analysis_ids", []):
                oprov = _latest_provenance(session, "observations", str(aid_str))
                if oprov and isinstance(oprov.inputs, dict):
                    for oid_s in oprov.inputs.get("observation_ids", []):
                        obs_ids_set.add(UUID(str(oid_s)))

            assoc_rows = session.scalars(
                select(Association)
                .where(Association.tracked_object_id == object_id)
                .order_by(Association.id)
            ).all()
            for a in assoc_rows:
                obs_ids_set.add(a.observation_id)

            obs_rows = (
                session.scalars(
                    select(Observation)
                    .where(Observation.id.in_(list(obs_ids_set)))
                    .order_by(Observation.mjd_tdb_mean)
                ).all()
                if obs_ids_set
                else []
            )
            obs_by_id = {o.id: o for o in obs_rows}
            chosen_conf = {a.observation_id: float(a.confidence) for a in assoc_rows if a.is_chosen}

            meas_rows = session.scalars(
                select(EpochMeasurement)
                .where(EpochMeasurement.tracked_object_id == object_id)
                .order_by(EpochMeasurement.mjd_tdb)
            ).all()
            measurements = [
                EpochMeasurementRead(
                    epoch=obs_by_id[m.observation_id].epoch_label
                    if m.observation_id and m.observation_id in obs_by_id
                    else None,
                    source=m.source,
                    mjd_tdb=float(m.mjd_tdb),
                    ra=m.ra,
                    dec=m.dec,
                    sig_ra_mas=m.sig_ra_mas,
                    sig_dec_mas=m.sig_dec_mas,
                    corr_radec=m.corr_radec,
                    band_group=m.band_group,
                    flux_ujy=m.flux_ujy,
                    flux_err_ujy=m.flux_err_ujy,
                    is_upper_limit=bool(m.is_upper_limit),
                    flags=int(m.flags or 0),
                    association_confidence=chosen_conf.get(m.observation_id)
                    if m.observation_id
                    else None,
                )
                for m in meas_rows
            ]

            fit_rows = session.scalars(
                select(MotionFit)
                .where(MotionFit.tracked_object_id == object_id)
                .order_by(MotionFit.is_preferred.desc(), MotionFit.created_at.desc())
            ).all()
            motion_fits = [MotionFitRead.model_validate(f).model_dump() for f in fit_rows]

            cls_row = session.scalar(
                select(Classification)
                .where(Classification.tracked_object_id == object_id)
                .order_by(Classification.created_at.desc())
                .limit(1)
            )
            cls_summary: ClassificationSummary | None = None
            if cls_row is not None:
                ev_dict = dict(cls_row.evidence or {})
                cls_summary = ClassificationSummary(
                    top_class=cls_row.top_class,
                    top_prob=float(cls_row.top_prob),
                    probabilities=dict(cls_row.probabilities or {}),
                    evidence=ev_dict.get("items", ev_dict),
                    consensus=ev_dict.get("consensus"),
                )

            assoc_sorted = sorted(
                assoc_rows,
                key=lambda a: (
                    float(obs_by_id[a.observation_id].mjd_tdb_mean)
                    if a.observation_id in obs_by_id
                    else 0.0,
                    int(a.rank),
                ),
            )
            raw_assocs = [
                {
                    "id": a.id,
                    "observation_id": a.observation_id,
                    "detection_id": a.detection_id,
                    "pred_ra": a.pred_ra,
                    "pred_dec": a.pred_dec,
                    "cost": a.cost,
                    "confidence": float(a.confidence),
                    "rank": int(a.rank),
                    "is_chosen": bool(a.is_chosen),
                    "decided_by": a.decided_by,
                    "user_id": a.user_id,
                    "reason": a.reason,
                    "flags": int(a.flags or 0),
                }
                for a in assoc_sorted
            ]
            xm_rows = session.scalars(
                select(Crossmatch).where(Crossmatch.tracked_object_id == object_id)
            ).all()

            return {
                "id": obj.id,
                "project_id": obj.project_id,
                "label": obj.label,
                "public_id": obj.public_id,
                "selection_mode": obj.selection_mode,
                "ref_ra": obj.ref_ra,
                "ref_dec": obj.ref_dec,
                "ref_mjd_tdb": obj.ref_mjd_tdb,
                "status": obj.status or "active",
                "identity_confidence": obj.identity_confidence,
                "seed_detection_id": p_in.get("seed_detection_id"),
                "analysis_ids": list(p_in.get("analysis_ids", [])),
                "fits_file_ids": [UUID(str(f)) for f in p_in.get("fits_file_ids", [])],
                "caveats": list(p_par.get("caveats", [])),
                "override_rev": int(p_par.get("override_rev", 0)),
                "observations": [self._obs_to_dict(o) for o in obs_rows],
                "measurements": measurements,
                "motion_fits": motion_fits,
                "classification": cls_summary,
                "associations": [AssociationHypothesis.model_validate(a) for a in raw_assocs],
                "raw_associations": raw_assocs,
                "crossmatches": [
                    {
                        "catalog": x.catalog,
                        "catalog_id": x.catalog_id,
                        "sep_arcsec": x.sep_arcsec,
                        "match_prob": x.match_prob,
                    }
                    for x in xm_rows
                ],
            }

    def save_track_results(
        self,
        object_id: UUID,
        *,
        obj_updates: Mapping[str, Any],
        measurements: Sequence[Mapping[str, Any]],
        associations: Sequence[Mapping[str, Any]],
        motion_fits: Sequence[Mapping[str, Any]],
        classification: Mapping[str, Any] | None,
        crossmatches: Sequence[Mapping[str, Any]],
        caveats: Sequence[str],
    ) -> None:
        with self._session_factory() as session:
            obj = session.get(TrackedObject, object_id)
            if obj is None:
                raise KeyError(f"Tracked object not found: {object_id}")
            for k in (
                "public_id",
                "ref_ra",
                "ref_dec",
                "ref_mjd_tdb",
                "status",
                "identity_confidence",
            ):
                if k in obj_updates:
                    setattr(obj, k, obj_updates[k])

            session.execute(
                delete(EpochMeasurement).where(EpochMeasurement.tracked_object_id == object_id)
            )
            for m in measurements:
                session.add(
                    EpochMeasurement(
                        tracked_object_id=object_id,
                        observation_id=m.get("observation_id"),
                        source=str(m["source"]),
                        mjd_tdb=float(m["mjd_tdb"]),
                        ra=m.get("ra"),
                        dec=m.get("dec"),
                        sig_ra_mas=m.get("sig_ra_mas"),
                        sig_dec_mas=m.get("sig_dec_mas"),
                        corr_radec=m.get("corr_radec"),
                        band_group=m.get("band_group"),
                        flux_ujy=m.get("flux_ujy"),
                        flux_err_ujy=m.get("flux_err_ujy"),
                        is_upper_limit=bool(m.get("is_upper_limit", False)),
                        flags=int(m.get("flags") or 0),
                        used_in_fit=bool(m.get("used_in_fit", True)),
                    )
                )

            locked_obs = set(
                session.scalars(
                    select(Association.observation_id).where(
                        Association.tracked_object_id == object_id,
                        Association.decided_by == "user",
                    )
                ).all()
            )
            del_assoc = delete(Association).where(Association.tracked_object_id == object_id)
            if locked_obs:
                del_assoc = del_assoc.where(Association.observation_id.notin_(list(locked_obs)))
            session.execute(del_assoc)
            for a in associations:
                obs_id = UUID(str(a["observation_id"]))
                if obs_id in locked_obs:
                    continue
                session.add(
                    Association(
                        tracked_object_id=object_id,
                        observation_id=obs_id,
                        detection_id=a.get("detection_id"),
                        pred_ra=a.get("pred_ra"),
                        pred_dec=a.get("pred_dec"),
                        cost=a.get("cost"),
                        confidence=float(a["confidence"]),
                        rank=int(a["rank"]),
                        is_chosen=bool(a["is_chosen"]),
                        decided_by=str(a.get("decided_by", "auto")),
                        reason=a.get("reason"),
                        flags=int(a.get("flags") or 0),
                    )
                )

            session.execute(delete(MotionFit).where(MotionFit.tracked_object_id == object_id))
            for f in motion_fits:
                session.add(
                    MotionFit(
                        tracked_object_id=object_id,
                        model=str(f["model"]),
                        ref_epoch_mjd_tdb=float(f["ref_epoch_mjd_tdb"]),
                        params=dict(f["params"]),
                        covariance=list(f.get("covariance") or []),
                        pm_total_masyr=f.get("pm_total_masyr"),
                        pm_total_err=f.get("pm_total_err"),
                        pa_deg=f.get("pa_deg"),
                        pa_err=f.get("pa_err"),
                        plx_mas=f.get("plx_mas"),
                        plx_err=f.get("plx_err"),
                        chi2=f.get("chi2"),
                        dof=f.get("dof"),
                        chi2_red=f.get("chi2_red"),
                        bic=f.get("bic"),
                        n_epochs=f.get("n_epochs"),
                        baseline_yr=f.get("baseline_yr"),
                        is_preferred=bool(f.get("is_preferred", False)),
                    )
                )

            if classification is not None:
                session.execute(
                    delete(Classification).where(Classification.tracked_object_id == object_id)
                )
                session.add(
                    Classification(
                        tracked_object_id=object_id,
                        method="bayesian_combiner",
                        probabilities=dict(classification["probabilities"]),
                        top_class=str(classification["top_class"]),
                        top_prob=float(classification["top_prob"]),
                        evidence={
                            "items": list(classification.get("evidence") or []),
                            "consensus": classification.get("consensus"),
                        },
                        disagreements={
                            "items": (classification.get("consensus") or {}).get(
                                "disagreements", []
                            )
                        },
                    )
                )

            session.execute(delete(Crossmatch).where(Crossmatch.tracked_object_id == object_id))
            for xm in crossmatches:
                session.add(
                    Crossmatch(
                        tracked_object_id=object_id,
                        catalog=str(xm["catalog"]),
                        catalog_id=xm.get("catalog_id"),
                        sep_arcsec=xm.get("sep_arcsec"),
                        match_prob=xm.get("match_prob"),
                        payload=xm.get("payload"),
                    )
                )

            session.execute(delete(Spectrum).where(Spectrum.tracked_object_id == object_id))

            prov = _latest_provenance(session, "tracked_objects", str(object_id))
            if prov is not None:
                params = dict(prov.params or {})
                params["caveats"] = list(caveats)
                prov.params = params
            session.commit()

    def apply_association_override(
        self,
        object_id: UUID,
        epoch: str,
        *,
        action: str,
        detection_id: int | None,
        reason: str,
        user_id: UUID | None,
    ) -> dict[str, Any]:
        bundle = self.get_tracked_object_bundle(object_id)
        if bundle is None:
            raise KeyError(f"Tracked object not found: {object_id}")
        obs = resolve_epoch_observation(bundle["observations"], epoch)
        if obs is None:
            raise LookupError(f"Epoch {epoch!r} not found for object {object_id}.")
        obs_id = UUID(str(obs["id"]))
        override_flag = int(DetectionFlag.USER_OVERRIDE)

        with self._session_factory() as session:
            valid_user = user_id if user_id and session.get(User, user_id) else None
            rows = session.scalars(
                select(Association).where(
                    Association.tracked_object_id == object_id,
                    Association.observation_id == obs_id,
                )
            ).all()
            before = [
                {
                    "id": r.id,
                    "detection_id": r.detection_id,
                    "is_chosen": r.is_chosen,
                    "decided_by": r.decided_by,
                }
                for r in rows
            ]
            for r in rows:
                r.is_chosen = False
                r.flags = int(r.flags or 0) | override_flag

            chosen_det: int | None = None
            if action in {"accept", "reassign"}:
                if action == "reassign":
                    assert detection_id is not None
                    if session.get(Detection, detection_id) is None:
                        raise LookupError(f"Detection not found: {detection_id}")
                if action == "accept" and detection_id is None:
                    target = next((r for r in rows if r.rank == 1), rows[0] if rows else None)
                    if target is None:
                        raise LookupError(f"No association candidate at epoch {epoch!r} to accept.")
                else:
                    target = next((r for r in rows if r.detection_id == detection_id), None)
                if target is None:
                    target = Association(
                        tracked_object_id=object_id,
                        **_assoc_payload(
                            obs_id,
                            detection_id,
                            bundle.get("ref_ra"),
                            bundle.get("ref_dec"),
                            is_chosen=True,
                            decided_by="user",
                            user_id=valid_user,
                            reason=reason,
                            flags=override_flag,
                        ),
                    )
                    session.add(target)
                else:
                    target.is_chosen = True
                    target.confidence = 1.0
                    target.decided_by = "user"
                    target.user_id = valid_user
                    target.reason = reason
                    target.flags = int(target.flags or 0) | override_flag
                chosen_det = target.detection_id
            elif action == "reject":
                if not rows:
                    session.add(
                        Association(
                            tracked_object_id=object_id,
                            **_assoc_payload(
                                obs_id,
                                None,
                                bundle.get("ref_ra"),
                                bundle.get("ref_dec"),
                                is_chosen=False,
                                decided_by="user",
                                user_id=valid_user,
                                reason=reason,
                                flags=override_flag,
                                cost=None,
                            ),
                        )
                    )
                else:
                    for r in rows:
                        r.decided_by = "user"
                        r.user_id = valid_user
                        r.reason = reason
            else:
                raise ValueError(f"Unsupported override action: {action!r}")

            prov = _latest_provenance(session, "tracked_objects", str(object_id))
            rev = 1
            if prov is not None:
                par = dict(prov.params or {})
                rev = int(par.get("override_rev", 0)) + 1
                par["override_rev"] = rev
                prov.params = par

            session.flush()
            session.add(
                AuditLog(
                    user_id=valid_user,
                    action=f"association.{action}",
                    entity_table="associations",
                    entity_id=f"{object_id}:{obs_id}",
                    before={"rows": before},
                    after={"detection_id": chosen_det, "action": action},
                )
            )
            session.commit()
            return {
                "observation_id": obs_id,
                "epoch_label": obs["epoch_label"],
                "detection_id": chosen_det,
                "action": action,
                "override_rev": rev,
            }

    def get_cached_spectrum(
        self, object_id: UUID, observation_id: UUID | None = None
    ) -> SpectrumResponse | None:
        with self._session_factory() as session:
            stmt = select(Spectrum).where(Spectrum.tracked_object_id == object_id)
            if observation_id is None:
                stmt = stmt.where(Spectrum.observation_id.is_(None))
            else:
                stmt = stmt.where(Spectrum.observation_id == observation_id)
            row = session.scalar(stmt.order_by(Spectrum.created_at.desc()).limit(1))
            if row is None:
                return None
            return SpectrumResponse(
                object_id=object_id,
                observation_id=row.observation_id,
                wavelength_um=[float(v) for v in row.wavelength_um],
                flux_ujy=[float(v) for v in row.flux_ujy],
                flux_err_ujy=[float(v) for v in row.flux_err_ujy],
                mjd_tdb=[float(v) for v in row.mjd_tdb],
                flags=[int(v) for v in row.flags],
                method=row.method,
            )

    def save_spectrum(self, spectrum: SpectrumResponse) -> None:
        with self._session_factory() as session:
            session.add(
                Spectrum(
                    tracked_object_id=spectrum.object_id,
                    observation_id=spectrum.observation_id,
                    wavelength_um=list(spectrum.wavelength_um),
                    flux_ujy=list(spectrum.flux_ujy),
                    flux_err_ujy=list(spectrum.flux_err_ujy),
                    mjd_tdb=list(spectrum.mjd_tdb),
                    flags=list(spectrum.flags),
                    method=spectrum.method,
                )
            )
            session.commit()
