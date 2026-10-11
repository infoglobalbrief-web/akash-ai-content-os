
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import BackgroundJob
from app.queue_service import enqueue_job, recover_stale_jobs


if __name__ == "__main__":
    job_id = enqueue_job(
        job_type="test_recovery",
        payload={"purpose": "simulate a crashed worker"},
        max_attempts=3,
    )

    # Simulate a worker crash by leaving this job running
    # with a lock timestamp 10 minutes in the past.
    with SessionLocal() as session:
        with session.begin():
            job = session.scalar(
                select(BackgroundJob)
                .where(BackgroundJob.id == job_id)
                .with_for_update()
            )

            if job is None:
                raise RuntimeError("Test job was not found.")

            job.status = "running"
            job.attempts = 1
            job.locked_at = (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            )

    recovered = recover_stale_jobs(lease_timeout_seconds=60)

    with SessionLocal() as session:
        job = session.get(BackgroundJob, job_id)

        if job is None:
            raise RuntimeError("Test job was not found after recovery.")

        assert job.status == "queued"
        assert job.locked_at is None

        print("SUCCESS: Stale job recovery works!")
        print(f"Job ID: {job.id}")
        print(f"Status: {job.status}")
        print(f"Lock released: {job.locked_at is None}")
        print(f"Jobs recovered in this run: {recovered}")
