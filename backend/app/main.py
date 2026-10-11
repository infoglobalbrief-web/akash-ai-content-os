from fastapi import FastAPI
from typing import Any, Literal

from fastapi import Depends
from pydantic import BaseModel, Field

from app.queue_service import enqueue_job
from app.security import require_api_key

from fastapi import HTTPException
from app.database import SessionLocal
from app.models import BackgroundJob

app = FastAPI(
    title="Akash AI Content OS",
    version="0.1.0"
)
class JobCreateRequest(BaseModel):
    job_type: Literal["demo.echo"]
    payload: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=5, ge=1, le=10)

@app.get("/")
def root():
    return {
        "message": "Akash AI Content OS Backend is running!"
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }

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
