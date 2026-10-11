
from sqlalchemy import select

from app.database import SessionLocal
from app.models import BackgroundJob
from app.queue_service import enqueue_job


if __name__ == "__main__":
    job_id = enqueue_job(
        job_type="test_job",
        payload={
            "message": "Hello from Akash AI Content OS",
            "purpose": "Test PostgreSQL background queue",
        },
    )

    print("SUCCESS: Job added to the queue!")
    print(f"Job ID: {job_id}")

    with SessionLocal() as session:
        job = session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == job_id)
        )

        if job is None:
            raise RuntimeError("The queued job could not be found.")

        print(f"Job type: {job.job_type}")
        print(f"Status: {job.status}")
        print(f"Attempts: {job.attempts}")
        print(f"Payload: {job.payload}")
