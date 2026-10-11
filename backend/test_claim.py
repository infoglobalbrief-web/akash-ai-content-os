
from sqlalchemy import select

from app.database import SessionLocal
from app.models import BackgroundJob
from app.queue_service import claim_next_job


if __name__ == "__main__":
    job = claim_next_job()

    if job is None:
        print("No eligible queued jobs found.")
    else:
        print("SUCCESS: Job claimed!")
        print(f"Job ID: {job['id']}")
        print(f"Job type: {job['job_type']}")
        print(f"Status: {job['status']}")
        print(f"Attempts: {job['attempts']}")
        print(f"Payload: {job['payload']}")

        with SessionLocal() as session:
            saved_job = session.scalar(
                select(BackgroundJob).where(
                    BackgroundJob.id == job["id"]
                )
            )

            if saved_job is None:
                raise RuntimeError("Claimed job was not found in the database.")

            assert saved_job.status == "running"
            assert saved_job.attempts == job["attempts"]

        print("SUCCESS: Database status verified!")

