"""Offline sanity check for the source-monitoring code and models."""
from app import models
from app.main import app
from app.source_monitor import enqueue_due_source_scans, fetch_source_items, scan_source
from app.worker import HANDLERS


def main() -> None:
    expected_tables = {"sources", "background_jobs", "discovered_items"}
    actual_tables = set(models.Base.metadata.tables)
    missing = expected_tables - actual_tables
    if missing:
        raise SystemExit(f"Missing SQLAlchemy tables: {sorted(missing)}")

    expected_routes = {
        ("POST", "/api/v1/sources/{source_id}/scan"),
        ("GET", "/api/v1/discoveries"),
        ("POST", "/api/v1/sources"),
        ("GET", "/api/v1/sources"),
    }
    actual_routes = set()
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        path = getattr(route, "path", "")
        actual_routes.update((method, path) for method in methods)
    missing_routes = expected_routes - actual_routes
    if missing_routes:
        raise SystemExit(f"Missing API routes: {sorted(missing_routes)}")

    if "source.scan" not in HANDLERS:
        raise SystemExit("Worker is missing the source.scan handler.")

    for value in (enqueue_due_source_scans, fetch_source_items, scan_source):
        if not callable(value):
            raise SystemExit("Source monitor function import failed.")

    print("SUCCESS: Monitoring modules import correctly.")
    print("SUCCESS: SQLAlchemy knows about sources, background_jobs, discovered_items.")
    print("SUCCESS: Source scan and discoveries API routes are registered.")
    print("SUCCESS: Worker has the source.scan handler.")
    print("This check does not perform an external network scan or modify the database.")


if __name__ == "__main__":
    main()
