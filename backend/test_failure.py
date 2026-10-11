
from sqlalchemy import select

from app.database import SessionLocal
from app.models import BackgroundJob
from app.queue_service import enqueue_job, claim_next_job, fail_job


if __name__ == "__main__":
    enqueue_job(
        job_type="test_failure",
        payload={"message": "Testing failure and retry handling"},
        max_attempts=3,
    )

    job = claim_next_job()

    if job is None:
        raise RuntimeError("No eligible queued job was found.")

    fail_job(
        job_id=job["id"],
        error="Test error: simulated temporary failure",
        retry_delay_seconds=30,
    )

    with SessionLocal() as session:
        saved_job = session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.id == job["id"]
            )
        )

        if saved_job is None:
            raise RuntimeError("Job was not found after failure handling.")

        assert saved_job.status == "queued"
        assert saved_job.attempts == 1
        assert saved_job.locked_at is None
        assert saved_job.last_error is not None

        print("SUCCESS: Failure handling works!")
        print(f"Job ID: {saved_job.id}")
        print(f"Status: {saved_job.status}")
        print(f"Attempts: {saved_job.attempts}")
        print(f"Lock released: {saved_job.locked_at is None}")
        print(f"Last error: {saved_job.last_error}")
        print("Retry scheduled for a later time.")

def recover_stale_jobs(lease_timeout_seconds: int = 300) -> int:
    """Recover jobs that remained running after a worker interruption."""

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
