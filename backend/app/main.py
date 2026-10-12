from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import BackgroundJob, DiscoveredItem, Source
from app.queue_service import enqueue_job
from app.security import require_api_key

app = FastAPI(
    title="Akash AI Content OS API",
    version="0.3.0",
    description="Backend API for AI content research and automation.",
)

SourceType = Literal["rss", "github", "website"]


class JobCreateRequest(BaseModel):
    job_type: Literal["demo.echo"]
    payload: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=5, ge=1, le=10)


class SourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrl
    source_type: SourceType
    description: str | None = Field(default=None, max_length=2000)
    scan_interval_minutes: int = Field(default=360, ge=5, le=10080)


class ClassifyPendingRequest(BaseModel):
    source_id: int | None = Field(default=None, ge=1)
    limit: int = Field(default=100, ge=1, le=200)


class DiscoveryReviewRequest(BaseModel):
    reviewed: bool


class SourceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    url: HttpUrl | None = None
    source_type: SourceType | None = None
    is_active: bool | None = None
    description: str | None = Field(default=None, max_length=2000)
    scan_interval_minutes: int | None = Field(default=None, ge=5, le=10080)


def source_to_dict(source: Source) -> dict[str, Any]:
    return {
        "id": source.id,
        "name": source.name,
        "url": source.url,
        "source_type": source.source_type,
        "is_active": source.is_active,
        "description": source.description,
        "scan_interval_minutes": source.scan_interval_minutes,
        "last_scanned_at": source.last_scanned_at,
        "last_scan_status": source.last_scan_status,
        "last_scan_error": source.last_scan_error,
        "next_scan_at": source.next_scan_at,
        "created_at": source.created_at,
    }


def get_source_or_404(session, source_id: int) -> Source:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return source


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "Akash AI Content OS API", "status": "running"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/jobs", status_code=202)
def create_job(request: JobCreateRequest, _: None = Depends(require_api_key)) -> dict[str, Any]:
    job_id = enqueue_job(
        job_type=request.job_type,
        payload=request.payload,
        max_attempts=request.max_attempts,
    )
    return {"id": job_id, "job_type": request.job_type, "status": "queued", "message": "Job added to the background queue."}


@app.get("/api/v1/jobs/{job_id}")
def get_job_status(job_id: int, _: None = Depends(require_api_key)) -> dict[str, Any]:
    with SessionLocal() as session:
        job = session.get(BackgroundJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
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


@app.get("/api/v1/sources")
def list_sources(
    active_only: bool | None = None,
    source_type: SourceType | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    statement = select(Source)
    if active_only is not None:
        statement = statement.where(Source.is_active == active_only)
    if source_type is not None:
        statement = statement.where(Source.source_type == source_type)
    statement = statement.order_by(Source.id).offset(offset).limit(limit)
    with SessionLocal() as session:
        sources = session.scalars(statement).all()
        items = [source_to_dict(source) for source in sources]
    return {"count": len(items), "limit": limit, "offset": offset, "items": items}


@app.post("/api/v1/sources", status_code=201)
def create_source(request: SourceCreateRequest, _: None = Depends(require_api_key)) -> dict[str, Any]:
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Source name cannot be blank.")
    source_url = str(request.url)
    with SessionLocal() as session:
        existing_id = session.scalar(select(Source.id).where(Source.url == source_url))
        if existing_id is not None:
            raise HTTPException(status_code=409, detail="A source with this URL already exists.")
        source = Source(
            name=name,
            url=source_url,
            source_type=request.source_type,
            description=request.description,
            is_active=True,
            scan_interval_minutes=request.scan_interval_minutes,
        )
        session.add(source)
        try:
            session.commit()
            session.refresh(source)
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=409, detail="A source with this URL already exists.") from None
        return source_to_dict(source)


@app.get("/api/v1/sources/{source_id}")
def get_source(source_id: int, _: None = Depends(require_api_key)) -> dict[str, Any]:
    with SessionLocal() as session:
        return source_to_dict(get_source_or_404(session, source_id))


@app.patch("/api/v1/sources/{source_id}")
def update_source(source_id: int, request: SourceUpdateRequest, _: None = Depends(require_api_key)) -> dict[str, Any]:
    changes = request.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Provide at least one field to update.")

    with SessionLocal() as session:
        source = get_source_or_404(session, source_id)
        if "name" in changes:
            name = changes["name"]
            if name is None or not name.strip():
                raise HTTPException(status_code=422, detail="Source name cannot be blank.")
            source.name = name.strip()
        if "url" in changes:
            if changes["url"] is None:
                raise HTTPException(status_code=422, detail="Source URL cannot be null.")
            source.url = str(changes["url"])
        if "source_type" in changes:
            if changes["source_type"] is None:
                raise HTTPException(status_code=422, detail="Source type cannot be null.")
            source.source_type = changes["source_type"]
        if "is_active" in changes:
            if changes["is_active"] is None:
                raise HTTPException(status_code=422, detail="is_active must be true or false.")
            source.is_active = changes["is_active"]
        if "description" in changes:
            source.description = changes["description"]
        if "scan_interval_minutes" in changes:
            interval = changes["scan_interval_minutes"]
            if interval is None:
                raise HTTPException(status_code=422, detail="scan_interval_minutes cannot be null.")
            source.scan_interval_minutes = interval
            # Apply a changed interval starting from now, rather than waiting on the old schedule.
            from datetime import datetime, timedelta, timezone
            source.next_scan_at = datetime.now(timezone.utc) + timedelta(minutes=interval)
        try:
            session.commit()
            session.refresh(source)
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=409, detail="Another source already uses this URL.") from None
        return source_to_dict(source)


@app.delete("/api/v1/sources/{source_id}")
def delete_source(source_id: int, _: None = Depends(require_api_key)) -> dict[str, Any]:
    with SessionLocal() as session:
        source = get_source_or_404(session, source_id)
        session.delete(source)
        session.commit()
    return {"id": source_id, "deleted": True, "message": "Source deleted successfully."}


@app.post("/api/v1/sources/{source_id}/scan", status_code=202)
def queue_source_scan(source_id: int, _: None = Depends(require_api_key)) -> dict[str, Any]:
    """Queue an immediate scan. The background worker performs the fetch."""
    from datetime import datetime, timedelta, timezone

    with SessionLocal() as session:
        source = get_source_or_404(session, source_id)
        if not source.is_active:
            raise HTTPException(status_code=409, detail="Source is inactive; activate it before scanning.")
        job = BackgroundJob(
            job_type="source.scan",
            payload={"source_id": source_id},
            status="queued",
            attempts=0,
            max_attempts=3,
        )
        source.next_scan_at = datetime.now(timezone.utc) + timedelta(minutes=source.scan_interval_minutes)
        session.add(job)
        session.commit()
        session.refresh(job)
    return {"id": job.id, "job_type": job.job_type, "status": job.status, "source_id": source_id, "message": "Source scan queued."}


@app.get("/api/v1/discoveries")
def list_discoveries(
    source_id: int | None = Query(default=None, ge=1),
    category: str | None = Query(default=None, max_length=50),
    event_type: str | None = Query(default=None, max_length=80),
    classification_status: Literal["pending", "classified", "error"] | None = None,
    needs_review: bool | None = None,
    min_relevance: int = Query(default=0, ge=0, le=100),
    min_importance: int = Query(default=0, ge=0, le=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """List and filter source discoveries by classification and triage priority."""
    statement = select(DiscoveredItem)
    if source_id is not None:
        statement = statement.where(DiscoveredItem.source_id == source_id)
    if category is not None:
        statement = statement.where(DiscoveredItem.category == category)
    if event_type is not None:
        statement = statement.where(DiscoveredItem.event_type == event_type)
    if classification_status is not None:
        statement = statement.where(DiscoveredItem.classification_status == classification_status)
    if needs_review is not None:
        statement = statement.where(DiscoveredItem.needs_review.is_(needs_review))
    if min_relevance:
        statement = statement.where(DiscoveredItem.relevance_score >= min_relevance)
    if min_importance:
        statement = statement.where(DiscoveredItem.importance_score >= min_importance)

    statement = (
        statement.order_by(
            DiscoveredItem.relevance_score.desc(),
            DiscoveredItem.importance_score.desc(),
            DiscoveredItem.first_seen_at.desc(),
            DiscoveredItem.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    with SessionLocal() as session:
        discoveries = session.scalars(statement).all()
        items = [
            {
                "id": item.id,
                "source_id": item.source_id,
                "item_key": item.item_key,
                "title": item.title,
                "url": item.url,
                "summary": item.summary,
                "published_at": item.published_at,
                "first_seen_at": item.first_seen_at,
                "last_seen_at": item.last_seen_at,
                "last_changed_at": item.last_changed_at,
                "change_count": item.change_count,
                "raw_metadata": item.raw_metadata,
                "category": item.category,
                "event_type": item.event_type,
                "provider_name": item.provider_name,
                "relevance_score": item.relevance_score,
                "importance_score": item.importance_score,
                "classification_status": item.classification_status,
                "classification_method": item.classification_method,
                "classification_summary": item.classification_summary,
                "classification_reason": item.classification_reason,
                "keywords": item.keywords,
                "needs_review": item.needs_review,
                "classified_at": item.classified_at,
            }
            for item in discoveries
        ]
    return {"count": len(items), "limit": limit, "offset": offset, "items": items}


@app.post("/api/v1/intelligence/classify-pending", status_code=202)
def queue_classification(
    request: ClassifyPendingRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Queue a batch classification job; the worker performs the processing."""
    if request.source_id is not None:
        with SessionLocal() as session:
            if session.get(Source, request.source_id) is None:
                raise HTTPException(status_code=404, detail="Source not found.")

    job_id = enqueue_job(
        job_type="intelligence.classify_pending",
        payload={"source_id": request.source_id, "limit": request.limit},
        max_attempts=3,
    )
    return {
        "id": job_id,
        "job_type": "intelligence.classify_pending",
        "status": "queued",
        "source_id": request.source_id,
        "limit": request.limit,
        "message": "Discovery classification queued.",
    }


@app.post("/api/v1/discoveries/{discovery_id}/classify", status_code=202)
def queue_one_classification(
    discovery_id: int,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Queue one discovery for classification if it is still pending."""
    with SessionLocal() as session:
        discovery = session.get(DiscoveredItem, discovery_id)
        if discovery is None:
            raise HTTPException(status_code=404, detail="Discovery not found.")
        if discovery.classification_status == "classified":
            return {"id": discovery.id, "status": "already_classified", "message": "This discovery is already classified."}
        if discovery.classification_status == "error":
            # Allow a user-requested retry to re-enter the pending queue.
            discovery.classification_status = "pending"
            discovery.classification_method = None
            discovery.classification_reason = None
            session.commit()

    job_id = enqueue_job(
        job_type="intelligence.classify_pending",
        payload={"discovery_id": discovery_id, "limit": 1},
        max_attempts=3,
    )
    return {"id": job_id, "job_type": "intelligence.classify_pending", "status": "queued", "discovery_id": discovery_id}


@app.patch("/api/v1/discoveries/{discovery_id}/review")
def update_discovery_review(
    discovery_id: int,
    request: DiscoveryReviewRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Mark a triage item reviewed or put it back in the review queue."""
    with SessionLocal() as session:
        item = session.get(DiscoveredItem, discovery_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Discovery not found.")
        item.needs_review = not request.reviewed
        session.commit()
        return {
            "id": item.id,
            "reviewed": request.reviewed,
            "needs_review": item.needs_review,
            "message": "Review status updated. This does not publish or approve content.",
        }


@app.get("/api/v1/intelligence/summary")
def intelligence_summary(
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Return discovery triage totals for the future admin dashboard."""
    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(DiscoveredItem)) or 0
        pending = session.scalar(
            select(func.count()).select_from(DiscoveredItem).where(
                DiscoveredItem.classification_status == "pending"
            )
        ) or 0
        classified = session.scalar(
            select(func.count()).select_from(DiscoveredItem).where(
                DiscoveredItem.classification_status == "classified"
            )
        ) or 0
        errors = session.scalar(
            select(func.count()).select_from(DiscoveredItem).where(
                DiscoveredItem.classification_status == "error"
            )
        ) or 0
        review_count = session.scalar(
            select(func.count()).select_from(DiscoveredItem).where(
                DiscoveredItem.needs_review.is_(True)
            )
        ) or 0
        high_relevance = session.scalar(
            select(func.count()).select_from(DiscoveredItem).where(
                DiscoveredItem.relevance_score >= 70
            )
        ) or 0
        by_category = session.execute(
            select(DiscoveredItem.category, func.count())
            .group_by(DiscoveredItem.category)
            .order_by(func.count().desc())
        ).all()
        by_event_type = session.execute(
            select(DiscoveredItem.event_type, func.count())
            .group_by(DiscoveredItem.event_type)
            .order_by(func.count().desc())
        ).all()

    return {
        "total_discoveries": total,
        "pending_classification": pending,
        "classified": classified,
        "classification_errors": errors,
        "needs_review": review_count,
        "high_relevance": high_relevance,
        "by_category": [{"category": key, "count": count} for key, count in by_category],
        "by_event_type": [{"event_type": key, "count": count} for key, count in by_event_type],
    }
