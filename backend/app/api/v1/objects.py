"""Analyses, tracked objects, associations, spectra, lightcurves, and A-vs-B compare."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import COMMON_501, not_implemented
from app.schemas.common import JobAcceptedResponse, PaginatedResponse
from app.schemas.objects import (
    AnalysisAcceptedResponse,
    AnalysisCreateRequest,
    AssociationOverrideRequest,
    AssociationOverrideResponse,
    CompareAcceptedResponse,
    CompareRequest,
    CompareResultResponse,
    DetectionRead,
    LightcurveResponse,
    ObjectAssociationsResponse,
    SpectrumResponse,
    SuggestionsResponse,
    TrackedObjectDetailResponse,
    TrackedObjectSelectRequest,
    TrackedObjectSelectResponse,
    TrackRunRequest,
)

router = APIRouter(responses=COMMON_501)


@router.post("/analyses", status_code=status.HTTP_202_ACCEPTED, tags=["analyses"])
async def create_analysis(payload: AnalysisCreateRequest) -> AnalysisAcceptedResponse:
    """Run registration, source extraction, and object suggestion on a frame set."""
    not_implemented()


@router.get("/analyses/{analysis_id}/sources", tags=["analyses"])
async def list_analysis_sources(
    analysis_id: UUID,
    bbox: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> PaginatedResponse[DetectionRead]:
    """List extracted sources within optional ICRS bounding box."""
    not_implemented()


@router.get("/analyses/{analysis_id}/suggestions", tags=["analyses"])
async def list_analysis_suggestions(analysis_id: UUID) -> SuggestionsResponse:
    """List ranked moving/anomalous object suggestions with reasons."""
    not_implemented()


@router.post(
    "/projects/{project_id}/objects", status_code=status.HTTP_201_CREATED, tags=["objects"]
)
async def select_project_objects(
    project_id: UUID, payload: TrackedObjectSelectRequest
) -> TrackedObjectSelectResponse:
    """Select objects via box-drag or suggestion into persistent project labels."""
    not_implemented()


@router.post(
    "/projects/{project_id}/tracks", status_code=status.HTTP_202_ACCEPTED, tags=["objects"]
)
async def run_project_tracks(project_id: UUID, payload: TrackRunRequest) -> JobAcceptedResponse:
    """Associate detections across epochs, fit astrometric motion, and classify."""
    not_implemented()


@router.get("/objects/{object_id}", tags=["objects"])
async def get_tracked_object(object_id: UUID) -> TrackedObjectDetailResponse:
    """Retrieve full tracked object measurements, motion fit, classification, and caveats."""
    not_implemented()


@router.get("/objects/{object_id}/associations", tags=["objects"])
async def get_object_associations(object_id: UUID) -> ObjectAssociationsResponse:
    """Retrieve per-epoch detection match hypotheses and alternatives."""
    not_implemented()


@router.patch("/objects/{object_id}/associations/{epoch}", tags=["objects"])
async def override_object_association(
    object_id: UUID, epoch: str, payload: AssociationOverrideRequest
) -> AssociationOverrideResponse:
    """Accept, reject, or reassign a per-epoch detection match."""
    not_implemented()


@router.get("/objects/{object_id}/spectrum", tags=["objects"])
async def get_object_spectrum(object_id: UUID, epoch: str | None = None) -> SpectrumResponse:
    """Fetch on-the-fly forced-PSF SED across channels (um, uJy)."""
    not_implemented()


@router.get("/objects/{object_id}/lightcurve", tags=["objects"])
async def get_object_lightcurve(object_id: UUID) -> LightcurveResponse:
    """Fetch multi-epoch photometry lightcurve and variability indices."""
    not_implemented()


@router.post("/compare", status_code=status.HTTP_202_ACCEPTED, tags=["compare"])
async def create_comparison(payload: CompareRequest) -> CompareAcceptedResponse:
    """Launch an A-vs-B FITS comparison job (diff, sum, ratio, headers, sources)."""
    not_implemented()


@router.get("/compare/{compare_id}", tags=["compare"])
async def get_comparison(compare_id: UUID) -> CompareResultResponse:
    """Retrieve A-vs-B comparison products, header diff, and source changes."""
    not_implemented()
