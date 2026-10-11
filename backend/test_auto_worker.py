
import time

from app.database import SessionLocal
from app.models import BackgroundJob
from app.queue_service import enqueue_job


if __name__ == "__main__":
    job_id = enqueue_job(
        job_type="demo.echo",
        payload={
            "message": "Automatic worker test passed!"
        },
    )

    print(f"Job {job_id} added to PostgreSQL.")
    print("Waiting for the running worker to process it...")

    deadline = time.monotonic() + 20

    while time.monotonic() < deadline:
        with SessionLocal() as session:
            job = session.get(BackgroundJob, job_id)

            if job is None:
                raise RuntimeError("Test job was not found.")

            status = job.status

        if status == "completed":
            print("SUCCESS: Worker processed the job automatically!")
            print(f"Job ID: {job_id}")
            print(f"Final status: {status}")
            break

        if status == "failed":
            raise RuntimeError(
                f"Worker marked the test job as failed: {job_id}"
            )

        time.sleep(0.5)
    else:
        raise TimeoutError(
            "The job was not completed within 20 seconds. "
            "Check the worker terminal for errors."
        )
