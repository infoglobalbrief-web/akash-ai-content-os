
import logging
import time
from typing import Any, Callable

from app.queue_service import (
    claim_next_job,
    complete_job,
    fail_job,
    recover_stale_jobs,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

JobHandler = Callable[[dict[str, Any]], None]


def handle_demo_echo(payload: dict[str, Any]) -> None:
    """Demonstration handler that prints a message."""
    message = payload.get("message", "(no message)")
    logging.info("Demo job says: %s", message)


# Only registered job types can be claimed by this worker.
HANDLERS: dict[str, JobHandler] = {
    "demo.echo": handle_demo_echo,
}


def process_one_job() -> bool:
    """Claim and process one supported job. Return whether work was found."""

    job = claim_next_job(list(HANDLERS.keys()))

    if job is None:
        return False

    job_id = job["id"]

    try:
        handler = HANDLERS[job["job_type"]]
        handler(job["payload"])
        complete_job(job_id)

        logging.info(
            "Job %s completed successfully (%s).",
            job_id,
            job["job_type"],
        )

    except Exception as exc:
        logging.exception("Job %s failed.", job_id)

        try:
            fail_job(job_id, str(exc))
        except Exception:
            logging.exception(
                "Could not record failure for job %s.",
                job_id,
            )

    return True


def run_worker(poll_interval_seconds: int = 2) -> None:
    """Run continuously, checking PostgreSQL for supported jobs."""

    recovered = recover_stale_jobs()
    logging.info("Startup recovery processed %s stale job(s).", recovered)
    logging.info("Background worker started.")

    while True:
        try:
            found_work = process_one_job()

            if not found_work:
                time.sleep(poll_interval_seconds)

        except Exception:
            logging.exception("Worker loop encountered an error.")
            time.sleep(poll_interval_seconds)


if __name__ == "__main__":
    try:
        run_worker()
    except KeyboardInterrupt:
        logging.info("Worker stopped by user.")
