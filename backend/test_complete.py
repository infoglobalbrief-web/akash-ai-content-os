
from sqlalchemy import select

from app.database import SessionLocal
from app.models import BackgroundJob
from app.queue_service import complete_job


if __name__ == "__main__":
    with SessionLocal() as session:
        job_id = session.scalar(
            select(BackgroundJob.id)
            .where(BackgroundJob.status == "running")
            .order_by(BackgroundJob.id.desc())
            .limit(1)
        )

    if job_id is None:
        raise RuntimeError(
            "No running job found. Run test_claim.py first."
        )

    complete_job(job_id)

    with SessionLocal() as session:
        job = session.get(BackgroundJob, job_id)

        if job is None:
            raise RuntimeError("Job not found after completion.")

        assert job.status == "completed"
        assert job.locked_at is None

        print("SUCCESS: Job completed!")
        print(f"Job ID: {job.id}")
        print(f"Status: {job.status}")
        print(f"Lock released: {job.locked_at is None}")
