
from app.queue_service import enqueue_job, claim_next_job, complete_job


if __name__ == "__main__":
    job_id = enqueue_job(
        job_type="demo.echo",
        payload={"message": "Testing the job-type filter"},
    )

    job = claim_next_job(["demo.echo"])

    if job is None:
        raise RuntimeError("No matching demo.echo job was claimed.")

    assert job["id"] == job_id, (
        f"Expected job {job_id}, but claimed job {job['id']}."
    )
    assert job["job_type"] == "demo.echo"
    assert job["status"] == "running"

    print("SUCCESS: Job-type filter works!")
    print(f"Job ID: {job['id']}")
    print(f"Job type: {job['job_type']}")
    print(f"Status: {job['status']}")

    complete_job(job["id"])
    print("SUCCESS: Test job marked completed.")
