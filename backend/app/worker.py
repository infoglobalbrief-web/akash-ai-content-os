import logging
import time
from typing import Any, Callable

from app.queue_service import (
    claim_next_job,
    complete_job,
    fail_job,
    recover_stale_jobs,
)
from app.source_monitor import enqueue_due_source_scans, scan_source
from app.intelligence import classify_pending_discoveries
from app.content_pipeline import generate_content_draft

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

JobHandler = Callable[[dict[str, Any]], None]


def handle_demo_echo(payload: dict[str, Any]) -> None:
    message = payload.get("message", "(no message)")
    logging.info("Demo job says: %s", message)


def handle_source_scan(payload: dict[str, Any]) -> None:
    source_id = payload.get("source_id")
    if not isinstance(source_id, int) or source_id < 1:
        raise ValueError("source.scan payload must include a positive integer source_id.")
    result = scan_source(source_id)
    logging.info(
        "Source scan result: source_id=%s status=%s seen=%s new=%s changed=%s unchanged=%s",
        source_id,
        result.get("status"),
        result.get("seen_count", 0),
        result.get("new_count", 0),
        result.get("changed_count", 0),
        result.get("unchanged_count", 0),
    )
    if result.get("status") == "success":
        triage = classify_pending_discoveries(source_id=source_id, limit=100)
        logging.info(
            "Discovery triage: source_id=%s selected=%s classified=%s errors=%s",
            source_id,
            triage.get("selected", 0),
            triage.get("classified", 0),
            triage.get("errors", 0),
        )


def handle_intelligence_classify_pending(payload: dict[str, Any]) -> None:
    source_id = payload.get("source_id")
    discovery_id = payload.get("discovery_id")
    limit = payload.get("limit", 100)
    if source_id is not None and (not isinstance(source_id, int) or isinstance(source_id, bool) or source_id < 1):
        raise ValueError("source_id must be a positive integer when provided.")
    if discovery_id is not None and (not isinstance(discovery_id, int) or isinstance(discovery_id, bool) or discovery_id < 1):
        raise ValueError("discovery_id must be a positive integer when provided.")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer between 1 and 200.")
    result = classify_pending_discoveries(
        source_id=source_id,
        limit=limit,
        discovery_id=discovery_id,
    )
    logging.info(
        "Intelligence classification batch: selected=%s classified=%s errors=%s",
        result.get("selected", 0), result.get("classified", 0), result.get("errors", 0),
    )


def handle_content_generate(payload: dict[str, Any]) -> None:
    draft_id = payload.get("draft_id")
    if not isinstance(draft_id, int) or isinstance(draft_id, bool) or draft_id < 1:
        raise ValueError("content.generate payload must include a positive integer draft_id.")
    result = generate_content_draft(draft_id)
    logging.info("Content draft generated: %s", result)


HANDLERS: dict[str, JobHandler] = {
    "demo.echo": handle_demo_echo,
    "source.scan": handle_source_scan,
    "intelligence.classify_pending": handle_intelligence_classify_pending,
    "content.generate": handle_content_generate,
}


def process_one_job() -> bool:
    """Claim and process one supported job; return whether a job was found."""
    job = claim_next_job(list(HANDLERS.keys()))
    if job is None:
        return False

    job_id = job["id"]
    try:
        HANDLERS[job["job_type"]](job["payload"])
        complete_job(job_id)
        logging.info("Job %s completed successfully (%s).", job_id, job["job_type"])
    except Exception as exc:
        logging.exception("Job %s failed.", job_id)
        try:
            fail_job(job_id, str(exc))
        except Exception:
            logging.exception("Could not record failure for job %s.", job_id)
    return True


def run_worker(poll_interval_seconds: int = 2) -> None:
    recovered = recover_stale_jobs()
    logging.info("Startup recovery processed %s stale job(s).", recovered)
    logging.info("Background worker started.")

    last_schedule_check = 0.0
    while True:
        now = time.monotonic()
        if now - last_schedule_check >= 10:
            try:
                queued = enqueue_due_source_scans()
                if queued:
                    logging.info("Scheduled %s due source scan(s).", queued)
            except Exception:
                logging.exception("Could not enqueue due source scans.")
            last_schedule_check = now

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
