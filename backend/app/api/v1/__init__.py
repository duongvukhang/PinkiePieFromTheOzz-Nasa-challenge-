"""API v1 router assembly (MVP endpoints; Planet X deferred to Phase 2)."""

from fastapi import APIRouter

from app.api.v1 import auth, compare, embed, fields, jobs, objects, projects, uploads

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth.router)
api_v1_router.include_router(fields.router)
api_v1_router.include_router(uploads.router)
api_v1_router.include_router(objects.router)
api_v1_router.include_router(compare.router)
api_v1_router.include_router(projects.router)
api_v1_router.include_router(jobs.router)
api_v1_router.include_router(embed.v1_router)
# PHASE2: api_v1_router.include_router(planetx.router) deferred per ROADMAP §2
