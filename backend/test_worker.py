
from sqlalchemy import select

from app.database import SessionLocal
from app.models import BackgroundJob
from app.queue_service import enqueue_job
from app.worker import process_one_job


if __name__ == "__main__":
    job_id = enqueue_job(
        job_type="demo.echo",
        payload={
            "message": "Akash AI Content OS worker is working!"
        },
    )

    print(f"Created test job: {job_id}")

    processed = process_one_job()

    if not processed:
        raise RuntimeError("The worker did not find the test job.")

    with SessionLocal() as session:
        job = session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.id == job_id
            )
        )

        if job is None:
            raise RuntimeError("The test job was not found.")

        assert job.status == "completed", (
            f"Expected completed, got {job.status!r}"
        )
        assert job.attempts == 1

        print("SUCCESS: Background worker processed the job!")
        print(f"Job ID: {job.id}")
        print(f"Final status: {job.status}")
        print(f"Attempts: {job.attempts}")
