
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import BackgroundJob, Source
from app.queue_service import enqueue_job
from app.security import require_api_key


app = FastAPI(
    title="Akash AI Content OS API",
    version="0.1.0",
    description="Backend API for AI content research and automation.",
)

SourceType = Literal["rss", "github", "website"]


# ---------------------------------------------------------
# Request models
# ---------------------------------------------------------

class JobCreateRequest(BaseModel):
    job_type: Literal["demo.echo"]
    payload: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=5, ge=1, le=10)


class SourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrl
    source_type: SourceType
    description: str | None = Field(default=None, max_length=2000)


class SourceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    url: HttpUrl | None = None
    source_type: SourceType | None = None
    is_active: bool | None = None
    description: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def source_to_dict(source: Source) -> dict[str, Any]:
    return {
        "id": source.id,
        "name": source.name,
        "url": source.url,
        "source_type": source.source_type,
        "is_active": source.is_active,
        "description": source.description,
        "created_at": source.created_at,
    }


def get_source_or_404(session, source_id: int) -> Source:
    source = session.get(Source, source_id)

    if source is None:
        raise HTTPException(
            status_code=404,
            detail="Source not found.",
        )

    return source


# ---------------------------------------------------------
# Basic health endpoints
# ---------------------------------------------------------

@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "Akash AI Content OS API",
        "status": "running",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------
# Background job endpoints
# ---------------------------------------------------------

@app.post("/api/v1/jobs", status_code=202)
def create_job(
    request: JobCreateRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    job_id = enqueue_job(
        job_type=request.job_type,
        payload=request.payload,
        max_attempts=request.max_attempts,
    )

    return {
        "id": job_id,
        "job_type": request.job_type,
        "status": "queued",
        "message": "Job added to the background queue.",
    }


@app.get("/api/v1/jobs/{job_id}")
def get_job_status(
    job_id: int,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    with SessionLocal() as session:
        job = session.get(BackgroundJob, job_id)

        if job is None:
            raise HTTPException(
                status_code=404,
                detail="Job not found.",
            )

        return {
            "id": job.id,
            "job_type": job.job_type,
            "status": job.status,
            "payload": job.payload,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "last_error": job.last_error,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }


# ---------------------------------------------------------
# Source management endpoints
# ---------------------------------------------------------

@app.get("/api/v1/sources")
def list_sources(
    active_only: bool | None = None,
    source_type: SourceType | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """List sources, optionally filtered by status and type."""

    statement = select(Source)

    if active_only is not None:
        statement = statement.where(Source.is_active == active_only)

    if source_type is not None:
        statement = statement.where(Source.source_type == source_type)

    statement = statement.order_by(Source.id).offset(offset).limit(limit)

    with SessionLocal() as session:
        sources = session.scalars(statement).all()
        items = [source_to_dict(source) for source in sources]

    return {
        "count": len(items),
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@app.post("/api/v1/sources", status_code=201)
def create_source(
    request: SourceCreateRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Create a source for future monitoring."""

    name = request.name.strip()

    if not name:
        raise HTTPException(
            status_code=422,
            detail="Source name cannot be blank.",
        )

    source_url = str(request.url)

    with SessionLocal() as session:
        existing_id = session.scalar(
            select(Source.id).where(Source.url == source_url)
        )

        if existing_id is not None:
            raise HTTPException(
                status_code=409,
                detail="A source with this URL already exists.",
            )

        source = Source(
            name=name,
            url=source_url,
            source_type=request.source_type,
            description=request.description,
            is_active=True,
        )

        session.add(source)

        try:
            session.commit()
            session.refresh(source)
        except IntegrityError:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="A source with this URL already exists.",
            ) from None

        return source_to_dict(source)


@app.get("/api/v1/sources/{source_id}")
def get_source(
    source_id: int,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Retrieve one source."""

    with SessionLocal() as session:
        source = get_source_or_404(session, source_id)
        return source_to_dict(source)


@app.patch("/api/v1/sources/{source_id}")
def update_source(
    source_id: int,
    request: SourceUpdateRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Update source information or activate/deactivate it."""

    changes = request.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="Provide at least one field to update.",
        )

    with SessionLocal() as session:
        source = get_source_or_404(session, source_id)

        if "name" in changes:
            name = changes["name"]

            if name is None or not name.strip():
                raise HTTPException(
                    status_code=422,
                    detail="Source name cannot be blank.",
                )

            source.name = name.strip()

        if "url" in changes:
            url = changes["url"]

            if url is None:
                raise HTTPException(
                    status_code=422,
                    detail="Source URL cannot be null.",
                )

            source.url = str(url)

        if "source_type" in changes:
            if changes["source_type"] is None:
                raise HTTPException(
                    status_code=422,
                    detail="Source type cannot be null.",
                )

            source.source_type = changes["source_type"]

        if "is_active" in changes:
            if changes["is_active"] is None:
                raise HTTPException(
                    status_code=422,
                    detail="is_active must be true or false.",
                )

            source.is_active = changes["is_active"]

        if "description" in changes:
            source.description = changes["description"]

        try:
            session.commit()
            session.refresh(source)
        except IntegrityError:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Another source already uses this URL.",
            ) from None

        return source_to_dict(source)


@app.delete("/api/v1/sources/{source_id}")
def delete_source(
    source_id: int,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Permanently remove a source."""

    with SessionLocal() as session:
        source = get_source_or_404(session, source_id)
        session.delete(source)
        session.commit()

    return {
        "id": source_id,
        "deleted": True,
        "message": "Source deleted successfully.",
    }
