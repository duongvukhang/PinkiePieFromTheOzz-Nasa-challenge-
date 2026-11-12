"""User, API token, project workspace, version snapshot, and audit persistence."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    ApiToken,
    AuditLog,
    Finding,
    Note,
    ProcessingJob,
    Project,
    ProjectFile,
    ProjectMember,
    ProjectVersion,
    SavedView,
    TrackedObject,
    User,
)
from app.schemas.auth import ApiTokenRead, UserRead
from app.schemas.projects import (
    FindingCreate,
    FindingRead,
    NoteCreate,
    NoteRead,
    ProjectCreate,
    ProjectRead,
    ProjectVersionRead,
    SavedViewCreate,
    SavedViewRead,
)

_PROJECT_META_KEYS = (
    "name",
    "description",
    "center_ra",
    "center_dec",
    "fov_arcmin",
    "visibility",
)
_T = TypeVar("_T", bound=BaseModel)


def _build_snapshot(
    project_meta: Mapping[str, Any],
    object_ids: Sequence[UUID],
    file_ids: Sequence[UUID],
    notes: Sequence[Any],
    findings: Sequence[Any],
    views: Sequence[Any],
) -> dict[str, Any]:
    return {
        "project": dict(project_meta),
        "object_ids": [str(oid) for oid in object_ids],
        "fits_file_ids": [str(fid) for fid in file_ids],
        "notes": [NoteRead.model_validate(n).model_dump(mode="json") for n in notes],
        "findings": [FindingRead.model_validate(f).model_dump(mode="json") for f in findings],
        "saved_views": [SavedViewRead.model_validate(v).model_dump(mode="json") for v in views],
    }


class WorkspaceStore(ABC):
    """Abstract store methods for auth accounts, API tokens, and project workspaces."""

    @abstractmethod
    def create_user(
        self,
        email: str,
        password_hash: str,
        *,
        display_name: str | None = None,
        role: str = "user",
    ) -> UserRead: ...

    @abstractmethod
    def get_user_by_email(self, email: str) -> tuple[UserRead, str | None] | None: ...

    @abstractmethod
    def get_user(self, user_id: UUID) -> UserRead | None: ...

    @abstractmethod
    def create_api_token(
        self, user_id: UUID, name: str | None, scopes: Sequence[str], token_hash: bytes
    ) -> ApiTokenRead: ...

    @abstractmethod
    def list_api_tokens(self, user_id: UUID) -> list[ApiTokenRead]: ...

    @abstractmethod
    def revoke_api_token(self, user_id: UUID, token_id: UUID) -> bool: ...

    @abstractmethod
    def authenticate_api_token(
        self, token_hash: bytes
    ) -> tuple[UserRead, list[str], UUID] | None: ...

    @abstractmethod
    def create_project(self, owner_id: UUID, data: ProjectCreate) -> ProjectRead: ...

    @abstractmethod
    def list_projects_for_user(self, user_id: UUID) -> list[ProjectRead]: ...

    @abstractmethod
    def get_project(self, project_id: UUID) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_project_role(self, project_id: UUID, user_id: UUID) -> str | None: ...

    @abstractmethod
    def update_project(self, project_id: UUID, patch: Mapping[str, Any]) -> ProjectRead | None: ...

    @abstractmethod
    def delete_project(self, project_id: UUID) -> bool: ...

    @abstractmethod
    def set_project_share(
        self, project_id: UUID, *, visibility: str, share_token_hash: bytes | None
    ) -> ProjectRead | None: ...

    @abstractmethod
    def list_notes(self, project_id: UUID) -> list[NoteRead]: ...

    @abstractmethod
    def create_note(self, project_id: UUID, author_id: UUID, data: NoteCreate) -> NoteRead: ...

    @abstractmethod
    def update_note(
        self, project_id: UUID, note_id: UUID, patch: Mapping[str, Any]
    ) -> NoteRead | None: ...

    @abstractmethod
    def delete_note(self, project_id: UUID, note_id: UUID) -> bool: ...

    @abstractmethod
    def list_findings(self, project_id: UUID) -> list[FindingRead]: ...

    @abstractmethod
    def create_finding(self, project_id: UUID, data: FindingCreate) -> FindingRead: ...

    @abstractmethod
    def update_finding(
        self, project_id: UUID, finding_id: UUID, patch: Mapping[str, Any]
    ) -> FindingRead | None: ...

    @abstractmethod
    def delete_finding(self, project_id: UUID, finding_id: UUID) -> bool: ...

    @abstractmethod
    def list_saved_views(self, project_id: UUID) -> list[SavedViewRead]: ...

    @abstractmethod
    def create_saved_view(self, project_id: UUID, data: SavedViewCreate) -> SavedViewRead: ...

    @abstractmethod
    def delete_saved_view(self, project_id: UUID, view_id: UUID) -> bool: ...

    @abstractmethod
    def list_project_versions(self, project_id: UUID) -> list[ProjectVersionRead]: ...

    @abstractmethod
    def create_project_version(
        self, project_id: UUID, created_by: UUID, message: str | None
    ) -> ProjectVersionRead: ...

    @abstractmethod
    def restore_project_version(
        self, project_id: UUID, version: int, user_id: UUID
    ) -> ProjectRead | None: ...

    @abstractmethod
    def list_project_file_ids(self, project_id: UUID) -> list[UUID]: ...

    @abstractmethod
    def record_audit(
        self,
        user_id: UUID | None,
        action: str,
        *,
        entity_table: str | None = None,
        entity_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip: str | None = None,
    ) -> None: ...


class InMemoryWorkspaceMixin:
    """In-memory persistence for users, API tokens, projects, and workspace entities."""

    _lock: threading.Lock
    _fits_files: dict[UUID, dict[str, Any]]
    _objects: dict[UUID, dict[str, Any]]
    _project_objects: dict[UUID, list[UUID]]
    _audit_log: list[dict[str, Any]]

    def _init_workspace_store(self) -> None:
        self._users: dict[UUID, dict[str, Any]] = {}
        self._users_by_email: dict[str, UUID] = {}
        self._api_tokens: dict[UUID, dict[str, Any]] = {}
        self._api_tokens_by_hash: dict[bytes, UUID] = {}
        self._projects: dict[UUID, dict[str, Any]] = {}
        self._project_members: dict[tuple[UUID, UUID], str] = {}
        self._notes: dict[UUID, dict[str, Any]] = {}
        self._findings: dict[UUID, dict[str, Any]] = {}
        self._saved_views: dict[UUID, dict[str, Any]] = {}
        self._project_versions: dict[tuple[UUID, int], dict[str, Any]] = {}

    def create_user(
        self,
        email: str,
        password_hash: str,
        *,
        display_name: str | None = None,
        role: str = "user",
    ) -> UserRead:
        norm = email.strip().lower()
        with self._lock:
            if norm in self._users_by_email:
                raise ValueError("email_exists")
            uid = uuid4()
            rec = {
                "id": uid,
                "email": norm,
                "password_hash": password_hash,
                "display_name": display_name,
                "role": role,
                "settings": {},
                "created_at": datetime.now(UTC),
                "deleted_at": None,
            }
            self._users[uid] = rec
            self._users_by_email[norm] = uid
            return UserRead.model_validate(rec)

    def get_user_by_email(self, email: str) -> tuple[UserRead, str | None] | None:
        with self._lock:
            uid = self._users_by_email.get(email.strip().lower())
            rec = self._users.get(uid) if uid is not None else None
            if rec is None or rec.get("deleted_at") is not None:
                return None
            return UserRead.model_validate(rec), rec.get("password_hash")

    def get_user(self, user_id: UUID) -> UserRead | None:
        with self._lock:
            rec = self._users.get(user_id)
            if rec is None or rec.get("deleted_at") is not None:
                return None
            return UserRead.model_validate(rec)

    def create_api_token(
        self, user_id: UUID, name: str | None, scopes: Sequence[str], token_hash: bytes
    ) -> ApiTokenRead:
        with self._lock:
            tid = uuid4()
            rec = {
                "id": tid,
                "user_id": user_id,
                "token_hash": token_hash,
                "scopes": list(scopes),
                "name": name,
                "created_at": datetime.now(UTC),
                "last_used_at": None,
                "revoked_at": None,
            }
            self._api_tokens[tid] = rec
            self._api_tokens_by_hash[token_hash] = tid
            return ApiTokenRead.model_validate(rec)

    def list_api_tokens(self, user_id: UUID) -> list[ApiTokenRead]:
        with self._lock:
            rows = [t for t in self._api_tokens.values() if t["user_id"] == user_id]
            rows.sort(key=lambda t: t["created_at"], reverse=True)
            return [ApiTokenRead.model_validate(t) for t in rows]

    def revoke_api_token(self, user_id: UUID, token_id: UUID) -> bool:
        with self._lock:
            rec = self._api_tokens.get(token_id)
            if rec is None or rec["user_id"] != user_id or rec["revoked_at"] is not None:
                return False
            rec["revoked_at"] = datetime.now(UTC)
            return True

    def authenticate_api_token(self, token_hash: bytes) -> tuple[UserRead, list[str], UUID] | None:
        with self._lock:
            tid = self._api_tokens_by_hash.get(token_hash)
            rec = self._api_tokens.get(tid) if tid is not None else None
            if rec is None or rec.get("revoked_at") is not None:
                return None
            user = self._users.get(rec["user_id"])
            if user is None or user.get("deleted_at") is not None:
                return None
            rec["last_used_at"] = datetime.now(UTC)
            return UserRead.model_validate(user), list(rec["scopes"]), rec["id"]

    def create_project(self, owner_id: UUID, data: ProjectCreate) -> ProjectRead:
        with self._lock:
            pid = uuid4()
            now = datetime.now(UTC)
            rec = {
                "id": pid,
                "owner_id": owner_id,
                **data.model_dump(),
                "share_token_hash": None,
                "current_version": 1,
                "created_at": now,
                "updated_at": now,
            }
            self._projects[pid] = rec
            self._project_members[(pid, owner_id)] = "owner"
            return ProjectRead.model_validate(rec)

    def list_projects_for_user(self, user_id: UUID) -> list[ProjectRead]:
        with self._lock:
            pids = {
                pid
                for pid, p in self._projects.items()
                if p["owner_id"] == user_id or (pid, user_id) in self._project_members
            }
            rows = [self._projects[pid] for pid in pids]
            rows.sort(key=lambda p: p["updated_at"], reverse=True)
            return [ProjectRead.model_validate(p) for p in rows]

    def get_project(self, project_id: UUID) -> dict[str, Any] | None:
        with self._lock:
            rec = self._projects.get(project_id)
            return dict(rec) if rec is not None else None

    def get_project_role(self, project_id: UUID, user_id: UUID) -> str | None:
        with self._lock:
            rec = self._projects.get(project_id)
            if rec is None:
                return None
            if rec["owner_id"] == user_id:
                return "owner"
            return self._project_members.get((project_id, user_id))

    def update_project(self, project_id: UUID, patch: Mapping[str, Any]) -> ProjectRead | None:
        with self._lock:
            rec = self._projects.get(project_id)
            if rec is None:
                return None
            for k, v in patch.items():
                if k in _PROJECT_META_KEYS:
                    rec[k] = v
            rec["updated_at"] = datetime.now(UTC)
            return ProjectRead.model_validate(rec)

    def delete_project(self, project_id: UUID) -> bool:
        with self._lock:
            if project_id not in self._projects:
                return False
            del self._projects[project_id]
            for mkey in [k for k in self._project_members if k[0] == project_id]:
                del self._project_members[mkey]
            for store in (self._notes, self._findings, self._saved_views):
                for eid in [k for k, v in store.items() if v["project_id"] == project_id]:
                    del store[eid]
            for vkey in [k for k in self._project_versions if k[0] == project_id]:
                del self._project_versions[vkey]
            self._project_objects.pop(project_id, None)
            return True

    def set_project_share(
        self, project_id: UUID, *, visibility: str, share_token_hash: bytes | None
    ) -> ProjectRead | None:
        with self._lock:
            rec = self._projects.get(project_id)
            if rec is None:
                return None
            rec["visibility"] = visibility
            rec["share_token_hash"] = share_token_hash
            rec["updated_at"] = datetime.now(UTC)
            return ProjectRead.model_validate(rec)

    def _sorted_rows(
        self, store: Mapping[UUID, dict[str, Any]], project_id: UUID
    ) -> list[dict[str, Any]]:
        return sorted(
            (r for r in store.values() if r["project_id"] == project_id),
            key=lambda r: r["created_at"],
        )

    def _list_child(
        self, store: Mapping[UUID, dict[str, Any]], project_id: UUID, schema: type[_T]
    ) -> list[_T]:
        with self._lock:
            return [schema.model_validate(r) for r in self._sorted_rows(store, project_id)]

    def _update_child(
        self,
        store: dict[UUID, dict[str, Any]],
        project_id: UUID,
        item_id: UUID,
        patch: Mapping[str, Any],
        fields: tuple[str, ...],
        schema: type[_T],
        *,
        touch_updated: bool = False,
    ) -> _T | None:
        with self._lock:
            rec = store.get(item_id)
            if rec is None or rec["project_id"] != project_id:
                return None
            for k in fields:
                if k in patch:
                    rec[k] = patch[k]
            if touch_updated:
                rec["updated_at"] = datetime.now(UTC)
            return schema.model_validate(rec)

    def _delete_child(
        self, store: dict[UUID, dict[str, Any]], project_id: UUID, item_id: UUID
    ) -> bool:
        with self._lock:
            rec = store.get(item_id)
            if rec is None or rec["project_id"] != project_id:
                return False
            del store[item_id]
            return True

    def list_notes(self, project_id: UUID) -> list[NoteRead]:
        return self._list_child(self._notes, project_id, NoteRead)

    def create_note(self, project_id: UUID, author_id: UUID, data: NoteCreate) -> NoteRead:
        with self._lock:
            now = datetime.now(UTC)
            nid = uuid4()
            rec: dict[str, Any] = {
                "id": nid,
                "project_id": project_id,
                "author_id": author_id,
                "body_md": data.body_md,
                "attachments": list(data.attachments),
                "created_at": now,
                "updated_at": now,
            }
            self._notes[nid] = rec
            return NoteRead.model_validate(rec)

    def update_note(
        self, project_id: UUID, note_id: UUID, patch: Mapping[str, Any]
    ) -> NoteRead | None:
        return self._update_child(
            self._notes,
            project_id,
            note_id,
            patch,
            ("body_md", "attachments"),
            NoteRead,
            touch_updated=True,
        )

    def delete_note(self, project_id: UUID, note_id: UUID) -> bool:
        return self._delete_child(self._notes, project_id, note_id)

    def list_findings(self, project_id: UUID) -> list[FindingRead]:
        return self._list_child(self._findings, project_id, FindingRead)

    def create_finding(self, project_id: UUID, data: FindingCreate) -> FindingRead:
        with self._lock:
            fid = uuid4()
            rec: dict[str, Any] = {
                "id": fid,
                "project_id": project_id,
                **data.model_dump(),
                "created_at": datetime.now(UTC),
            }
            self._findings[fid] = rec
            return FindingRead.model_validate(rec)

    def update_finding(
        self, project_id: UUID, finding_id: UUID, patch: Mapping[str, Any]
    ) -> FindingRead | None:
        return self._update_child(
            self._findings,
            project_id,
            finding_id,
            patch,
            ("title", "summary_md", "status", "evidence", "external_ref"),
            FindingRead,
        )

    def delete_finding(self, project_id: UUID, finding_id: UUID) -> bool:
        return self._delete_child(self._findings, project_id, finding_id)

    def list_saved_views(self, project_id: UUID) -> list[SavedViewRead]:
        return self._list_child(self._saved_views, project_id, SavedViewRead)

    def create_saved_view(self, project_id: UUID, data: SavedViewCreate) -> SavedViewRead:
        with self._lock:
            vid = uuid4()
            rec: dict[str, Any] = {
                "id": vid,
                "project_id": project_id,
                **data.model_dump(),
                "created_at": datetime.now(UTC),
            }
            self._saved_views[vid] = rec
            return SavedViewRead.model_validate(rec)

    def delete_saved_view(self, project_id: UUID, view_id: UUID) -> bool:
        return self._delete_child(self._saved_views, project_id, view_id)

    def _project_file_ids_unlocked(self, project_id: UUID) -> list[UUID]:
        seen: list[UUID] = []
        for fid, rec in self._fits_files.items():
            if rec.get("project_id") == project_id and fid not in seen:
                seen.append(fid)
        for oid in self._project_objects.get(project_id, []):
            obj = self._objects.get(oid) or {}
            for fid in obj.get("fits_file_ids", []):
                if fid not in seen:
                    seen.append(fid)
        return seen

    def list_project_file_ids(self, project_id: UUID) -> list[UUID]:
        with self._lock:
            return self._project_file_ids_unlocked(project_id)

    def list_project_versions(self, project_id: UUID) -> list[ProjectVersionRead]:
        with self._lock:
            rows = [v for (pid, _), v in self._project_versions.items() if pid == project_id]
            rows.sort(key=lambda v: v["version"])
            return [ProjectVersionRead.model_validate(v) for v in rows]

    def create_project_version(
        self, project_id: UUID, created_by: UUID, message: str | None
    ) -> ProjectVersionRead:
        with self._lock:
            proj = self._projects[project_id]
            existing = [ver for (pid, ver) in self._project_versions if pid == project_id]
            version = max(existing, default=0) + 1
            snapshot = _build_snapshot(
                {k: proj.get(k) for k in _PROJECT_META_KEYS},
                self._project_objects.get(project_id, []),
                self._project_file_ids_unlocked(project_id),
                self._sorted_rows(self._notes, project_id),
                self._sorted_rows(self._findings, project_id),
                self._sorted_rows(self._saved_views, project_id),
            )
            rec = {
                "project_id": project_id,
                "version": version,
                "snapshot": snapshot,
                "message": message,
                "created_by": created_by,
                "created_at": datetime.now(UTC),
            }
            self._project_versions[(project_id, version)] = rec
            proj["current_version"] = version
            proj["updated_at"] = datetime.now(UTC)
            return ProjectVersionRead.model_validate(rec)

    def restore_project_version(
        self, project_id: UUID, version: int, user_id: UUID
    ) -> ProjectRead | None:
        with self._lock:
            proj = self._projects.get(project_id)
            ver = self._project_versions.get((project_id, version))
            if proj is None or ver is None:
                return None
            snap = ver["snapshot"]
            meta = snap.get("project", {})
            for k in _PROJECT_META_KEYS:
                if k in meta:
                    proj[k] = meta[k]
            for store in (self._notes, self._findings, self._saved_views):
                for eid in [k for k, v in store.items() if v["project_id"] == project_id]:
                    del store[eid]
            for n in snap.get("notes", []):
                parsed_n = NoteRead.model_validate(n).model_dump()
                self._notes[parsed_n["id"]] = parsed_n
            for f in snap.get("findings", []):
                parsed_f = FindingRead.model_validate(f).model_dump()
                self._findings[parsed_f["id"]] = parsed_f
            for v in snap.get("saved_views", []):
                parsed_v = SavedViewRead.model_validate(v).model_dump()
                self._saved_views[parsed_v["id"]] = parsed_v
            proj["current_version"] = version
            proj["updated_at"] = datetime.now(UTC)
            self._audit_log.append(
                {
                    "user_id": user_id,
                    "action": "project.restore",
                    "entity_table": "projects",
                    "entity_id": str(project_id),
                    "before": None,
                    "after": {"version": version},
                }
            )
            return ProjectRead.model_validate(proj)

    def record_audit(
        self,
        user_id: UUID | None,
        action: str,
        *,
        entity_table: str | None = None,
        entity_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip: str | None = None,
    ) -> None:
        with self._lock:
            self._audit_log.append(
                {
                    "user_id": user_id,
                    "action": action,
                    "entity_table": entity_table,
                    "entity_id": entity_id,
                    "before": before,
                    "after": after,
                    "ip": ip,
                    "created_at": datetime.now(UTC),
                }
            )


class SqlAlchemyWorkspaceMixin:
    """PostgreSQL persistence for users, API tokens, projects, and workspace entities."""

    _session_factory: sessionmaker[Session]

    def create_user(
        self,
        email: str,
        password_hash: str,
        *,
        display_name: str | None = None,
        role: str = "user",
    ) -> UserRead:
        norm = email.strip().lower()
        with self._session_factory() as session:
            row = User(
                email=norm, password_hash=password_hash, display_name=display_name, role=role
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError("email_exists") from exc
            session.refresh(row)
            return UserRead.model_validate(row)

    def get_user_by_email(self, email: str) -> tuple[UserRead, str | None] | None:
        norm = email.strip().lower()
        with self._session_factory() as session:
            row = session.scalar(
                select(User).where(func.lower(User.email) == norm, User.deleted_at.is_(None))
            )
            if row is None:
                return None
            return UserRead.model_validate(row), row.password_hash

    def get_user(self, user_id: UUID) -> UserRead | None:
        with self._session_factory() as session:
            row = session.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
            return UserRead.model_validate(row) if row is not None else None

    def create_api_token(
        self, user_id: UUID, name: str | None, scopes: Sequence[str], token_hash: bytes
    ) -> ApiTokenRead:
        with self._session_factory() as session:
            row = ApiToken(user_id=user_id, name=name, scopes=list(scopes), token_hash=token_hash)
            session.add(row)
            session.commit()
            session.refresh(row)
            return ApiTokenRead.model_validate(row)

    def list_api_tokens(self, user_id: UUID) -> list[ApiTokenRead]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ApiToken)
                .where(ApiToken.user_id == user_id)
                .order_by(ApiToken.created_at.desc())
            ).all()
            return [ApiTokenRead.model_validate(r) for r in rows]

    def revoke_api_token(self, user_id: UUID, token_id: UUID) -> bool:
        with self._session_factory() as session:
            row = session.scalar(
                select(ApiToken).where(
                    ApiToken.id == token_id,
                    ApiToken.user_id == user_id,
                    ApiToken.revoked_at.is_(None),
                )
            )
            if row is None:
                return False
            row.revoked_at = datetime.now(UTC)
            session.commit()
            return True

    def authenticate_api_token(self, token_hash: bytes) -> tuple[UserRead, list[str], UUID] | None:
        with self._session_factory() as session:
            tok = session.scalar(
                select(ApiToken).where(
                    ApiToken.token_hash == token_hash, ApiToken.revoked_at.is_(None)
                )
            )
            if tok is None:
                return None
            user = session.scalar(
                select(User).where(User.id == tok.user_id, User.deleted_at.is_(None))
            )
            if user is None:
                return None
            tok.last_used_at = datetime.now(UTC)
            session.commit()
            return UserRead.model_validate(user), list(tok.scopes), tok.id

    def create_project(self, owner_id: UUID, data: ProjectCreate) -> ProjectRead:
        with self._session_factory() as session:
            proj = Project(owner_id=owner_id, **data.model_dump())
            session.add(proj)
            session.flush()
            session.add(ProjectMember(project_id=proj.id, user_id=owner_id, role="owner"))
            session.commit()
            session.refresh(proj)
            return ProjectRead.model_validate(proj)

    def list_projects_for_user(self, user_id: UUID) -> list[ProjectRead]:
        with self._session_factory() as session:
            member_pids = select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)
            rows = session.scalars(
                select(Project)
                .where((Project.owner_id == user_id) | Project.id.in_(member_pids))
                .order_by(Project.updated_at.desc(), Project.created_at.desc())
            ).all()
            return [ProjectRead.model_validate(r) for r in rows]

    def get_project(self, project_id: UUID) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(Project, project_id)
            if row is None:
                return None
            data = ProjectRead.model_validate(row).model_dump()
            data["share_token_hash"] = row.share_token_hash
            return data

    def get_project_role(self, project_id: UUID, user_id: UUID) -> str | None:
        with self._session_factory() as session:
            proj = session.get(Project, project_id)
            if proj is None:
                return None
            if proj.owner_id == user_id:
                return "owner"
            mem = session.get(ProjectMember, (project_id, user_id))
            return mem.role if mem is not None else None

    def update_project(self, project_id: UUID, patch: Mapping[str, Any]) -> ProjectRead | None:
        with self._session_factory() as session:
            row = session.get(Project, project_id)
            if row is None:
                return None
            for k, v in patch.items():
                if k in _PROJECT_META_KEYS:
                    setattr(row, k, v)
            row.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return ProjectRead.model_validate(row)

    def delete_project(self, project_id: UUID) -> bool:
        with self._session_factory() as session:
            row = session.get(Project, project_id)
            if row is None:
                return False
            session.execute(
                update(ProcessingJob)
                .where(ProcessingJob.project_id == project_id)
                .values(project_id=None)
            )
            session.delete(row)
            session.commit()
            return True

    def set_project_share(
        self, project_id: UUID, *, visibility: str, share_token_hash: bytes | None
    ) -> ProjectRead | None:
        with self._session_factory() as session:
            row = session.get(Project, project_id)
            if row is None:
                return None
            row.visibility = visibility
            row.share_token_hash = share_token_hash
            row.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return ProjectRead.model_validate(row)

    def _sa_list_child(self, model: Any, project_id: UUID, schema: type[_T]) -> list[_T]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(model).where(model.project_id == project_id).order_by(model.created_at)
            ).all()
            return [schema.model_validate(r) for r in rows]

    def _sa_create_child(self, model: Any, schema: type[_T], **kwargs: Any) -> _T:
        with self._session_factory() as session:
            row = model(**kwargs)
            session.add(row)
            session.commit()
            session.refresh(row)
            return schema.model_validate(row)

    def _sa_update_child(
        self,
        model: Any,
        project_id: UUID,
        item_id: UUID,
        patch: Mapping[str, Any],
        fields: tuple[str, ...],
        schema: type[_T],
        *,
        touch_updated: bool = False,
    ) -> _T | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(model).where(model.id == item_id, model.project_id == project_id)
            )
            if row is None:
                return None
            for k in fields:
                if k in patch:
                    setattr(row, k, patch[k])
            if touch_updated:
                row.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return schema.model_validate(row)

    def _sa_delete_child(self, model: Any, project_id: UUID, item_id: UUID) -> bool:
        with self._session_factory() as session:
            row = session.scalar(
                select(model).where(model.id == item_id, model.project_id == project_id)
            )
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def list_notes(self, project_id: UUID) -> list[NoteRead]:
        return self._sa_list_child(Note, project_id, NoteRead)

    def create_note(self, project_id: UUID, author_id: UUID, data: NoteCreate) -> NoteRead:
        return self._sa_create_child(
            Note,
            NoteRead,
            project_id=project_id,
            author_id=author_id,
            body_md=data.body_md,
            attachments=list(data.attachments),
        )

    def update_note(
        self, project_id: UUID, note_id: UUID, patch: Mapping[str, Any]
    ) -> NoteRead | None:
        return self._sa_update_child(
            Note,
            project_id,
            note_id,
            patch,
            ("body_md", "attachments"),
            NoteRead,
            touch_updated=True,
        )

    def delete_note(self, project_id: UUID, note_id: UUID) -> bool:
        return self._sa_delete_child(Note, project_id, note_id)

    def list_findings(self, project_id: UUID) -> list[FindingRead]:
        return self._sa_list_child(Finding, project_id, FindingRead)

    def create_finding(self, project_id: UUID, data: FindingCreate) -> FindingRead:
        return self._sa_create_child(
            Finding, FindingRead, project_id=project_id, **data.model_dump()
        )

    def update_finding(
        self, project_id: UUID, finding_id: UUID, patch: Mapping[str, Any]
    ) -> FindingRead | None:
        return self._sa_update_child(
            Finding,
            project_id,
            finding_id,
            patch,
            ("title", "summary_md", "status", "evidence", "external_ref"),
            FindingRead,
        )

    def delete_finding(self, project_id: UUID, finding_id: UUID) -> bool:
        return self._sa_delete_child(Finding, project_id, finding_id)

    def list_saved_views(self, project_id: UUID) -> list[SavedViewRead]:
        return self._sa_list_child(SavedView, project_id, SavedViewRead)

    def create_saved_view(self, project_id: UUID, data: SavedViewCreate) -> SavedViewRead:
        return self._sa_create_child(
            SavedView, SavedViewRead, project_id=project_id, **data.model_dump()
        )

    def delete_saved_view(self, project_id: UUID, view_id: UUID) -> bool:
        return self._sa_delete_child(SavedView, project_id, view_id)

    def list_project_file_ids(self, project_id: UUID) -> list[UUID]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(ProjectFile.fits_file_id).where(ProjectFile.project_id == project_id)
                ).all()
            )

    def list_project_versions(self, project_id: UUID) -> list[ProjectVersionRead]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ProjectVersion)
                .where(ProjectVersion.project_id == project_id)
                .order_by(ProjectVersion.version)
            ).all()
            return [ProjectVersionRead.model_validate(r) for r in rows]

    def create_project_version(
        self, project_id: UUID, created_by: UUID, message: str | None
    ) -> ProjectVersionRead:
        with self._session_factory() as session:
            proj = session.get(Project, project_id)
            assert proj is not None
            max_ver = session.scalar(
                select(func.max(ProjectVersion.version)).where(
                    ProjectVersion.project_id == project_id
                )
            )
            version = int(max_ver or 0) + 1
            obj_ids = session.scalars(
                select(TrackedObject.id).where(TrackedObject.project_id == project_id)
            ).all()
            file_ids = session.scalars(
                select(ProjectFile.fits_file_id).where(ProjectFile.project_id == project_id)
            ).all()
            notes = session.scalars(
                select(Note).where(Note.project_id == project_id).order_by(Note.created_at)
            ).all()
            findings = session.scalars(
                select(Finding).where(Finding.project_id == project_id).order_by(Finding.created_at)
            ).all()
            views = session.scalars(
                select(SavedView)
                .where(SavedView.project_id == project_id)
                .order_by(SavedView.created_at)
            ).all()
            snapshot = _build_snapshot(
                {k: getattr(proj, k) for k in _PROJECT_META_KEYS},
                obj_ids,
                file_ids,
                notes,
                findings,
                views,
            )
            ver = ProjectVersion(
                project_id=project_id,
                version=version,
                snapshot=snapshot,
                message=message,
                created_by=created_by,
            )
            session.add(ver)
            proj.current_version = version
            proj.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(ver)
            return ProjectVersionRead.model_validate(ver)

    def restore_project_version(
        self, project_id: UUID, version: int, user_id: UUID
    ) -> ProjectRead | None:
        with self._session_factory() as session:
            proj = session.get(Project, project_id)
            ver = session.get(ProjectVersion, (project_id, version))
            if proj is None or ver is None:
                return None
            snap = ver.snapshot
            meta = snap.get("project", {})
            for k in _PROJECT_META_KEYS:
                if k in meta:
                    setattr(proj, k, meta[k])
            session.execute(delete(Note).where(Note.project_id == project_id))
            session.execute(delete(Finding).where(Finding.project_id == project_id))
            session.execute(delete(SavedView).where(SavedView.project_id == project_id))
            for n in snap.get("notes", []):
                parsed_n = NoteRead.model_validate(n).model_dump()
                session.add(Note(**parsed_n))
            for f in snap.get("findings", []):
                parsed_f = FindingRead.model_validate(f).model_dump()
                session.add(Finding(**parsed_f))
            for v in snap.get("saved_views", []):
                parsed_v = SavedViewRead.model_validate(v).model_dump()
                session.add(SavedView(**parsed_v))
            proj.current_version = version
            proj.updated_at = datetime.now(UTC)
            session.add(
                AuditLog(
                    user_id=user_id,
                    action="project.restore",
                    entity_table="projects",
                    entity_id=str(project_id),
                    after={"version": version},
                )
            )
            session.commit()
            session.refresh(proj)
            return ProjectRead.model_validate(proj)

    def record_audit(
        self,
        user_id: UUID | None,
        action: str,
        *,
        entity_table: str | None = None,
        entity_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            session.add(
                AuditLog(
                    user_id=user_id,
                    action=action,
                    entity_table=entity_table,
                    entity_id=entity_id,
                    before=before,
                    after=after,
                    ip=ip,
                )
            )
            session.commit()
