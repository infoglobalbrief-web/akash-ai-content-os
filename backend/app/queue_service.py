
from typing import Any

from app.database import SessionLocal
from app.models import BackgroundJob
from datetime import datetime, timezone
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select

def enqueue_job(
    job_type: str,
    payload: dict[str, Any],
    max_attempts: int = 5,
) -> int:
    """Save a background job to PostgreSQL and return its ID."""

    if not job_type.strip():
        raise ValueError("job_type cannot be empty.")

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1.")

    with SessionLocal() as session:
        with session.begin():
            job = BackgroundJob(
                job_type=job_type.strip(),
                payload=payload,
                status="queued",
                max_attempts=max_attempts,
            )

            session.add(job)
            session.flush()

            job_id = job.id

    return job_id


def claim_next_job(
    job_types: list[str] | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the next eligible job.

    If job_types is provided, only claim jobs with those types.
    """

    if job_types is not None and not job_types:
        return None

    with SessionLocal() as session:
        with session.begin():
            conditions = [
                BackgroundJob.status == "queued",
                BackgroundJob.available_at <= func.now(),
                BackgroundJob.attempts < BackgroundJob.max_attempts,
            ]

            if job_types is not None:
                conditions.append(
                    BackgroundJob.job_type.in_(job_types)
                )

            statement = (
                select(BackgroundJob)
                .where(*conditions)
                .order_by(
                    BackgroundJob.available_at,
                    BackgroundJob.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )

            job = session.scalar(statement)

            if job is None:
                return None

            job.status = "running"
            job.locked_at = datetime.now(timezone.utc)
            job.attempts += 1

            session.flush()

            claimed_job = {
                "id": job.id,
                "job_type": job.job_type,
                "payload": job.payload,
                "status": job.status,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
            }

    return claimed_job

def complete_job(job_id: int) -> None:
    """Mark a running job as completed."""

    with SessionLocal() as session:
        with session.begin():
            job = session.scalar(
                select(BackgroundJob)
                .where(BackgroundJob.id == job_id)
                .with_for_update()
            )

            if job is None:
                raise ValueError(f"Job {job_id} was not found.")

            if job.status != "running":
                raise ValueError(
                    f"Job {job_id} is not running; "
                    f"current status is '{job.status}'."
                )

            job.status = "completed"
            job.locked_at = None
            job.last_error = None

def fail_job(job_id: int, error: str, retry_delay_seconds: int = 30) -> None:
    """Record a job failure and schedule a retry when eligible."""

    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds cannot be negative.")

    with SessionLocal() as session:
        with session.begin():
            job = session.scalar(
                select(BackgroundJob)
                .where(BackgroundJob.id == job_id)
                .with_for_update()
            )

            if job is None:
                raise ValueError(f"Job {job_id} was not found.")

            if job.status != "running":
                raise ValueError(
                    f"Job {job_id} is not running; "
                    f"current status is '{job.status}'."
                )

            job.last_error = error[:5000]
            job.locked_at = None

            if job.attempts >= job.max_attempts:
                job.status = "failed"
            else:
                job.status = "queued"

                delay = min(
                    retry_delay_seconds * (2 ** (job.attempts - 1)),
                    3600,
                )

                job.available_at = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=delay)
                )


def recover_stale_jobs(lease_timeout_seconds: int = 300) -> int:
    """Recover jobs abandoned by interrupted workers."""

    if lease_timeout_seconds < 1:
        raise ValueError("lease_timeout_seconds must be at least 1.")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=lease_timeout_seconds)
    recovered_count = 0

    with SessionLocal() as session:
        with session.begin():
            statement = (
                select(BackgroundJob)
                .where(
                    BackgroundJob.status == "running",
                    BackgroundJob.locked_at.is_not(None),
                    BackgroundJob.locked_at < cutoff,
                )
                .order_by(BackgroundJob.id)
                .with_for_update(skip_locked=True)
            )

            stale_jobs = session.scalars(statement).all()

            for job in stale_jobs:
                job.locked_at = None
                job.last_error = (
                    "Worker lease expired; recovered after interruption."
                )

                if job.attempts >= job.max_attempts:
                    job.status = "failed"
                else:
                    job.status = "queued"
                    job.available_at = now

                recovered_count += 1

    return recovered_count
