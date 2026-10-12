"""Offline sanity check for the AI intelligence / discovery triage batch.

No external request is made and no database write occurs.
"""
from app import models
from app.intelligence import CATEGORIES, EVENT_TYPES, classify_item, classify_with_rules
from app.main import app
from app.worker import HANDLERS


def main() -> None:
    expected_tables = {"sources", "background_jobs", "discovered_items"}
    actual_tables = set(models.Base.metadata.tables)
    missing = expected_tables - actual_tables
    if missing:
        raise SystemExit(f"Missing SQLAlchemy tables: {sorted(missing)}")

    discovery_columns = set(models.DiscoveredItem.__table__.columns.keys())
    expected_columns = {
        "category", "event_type", "provider_name", "relevance_score",
        "importance_score", "classification_status", "classification_method",
        "classification_summary", "classification_reason", "keywords",
        "needs_review", "classified_at",
    }
    missing_columns = expected_columns - discovery_columns
    if missing_columns:
        raise SystemExit(f"Missing discovery intelligence columns: {sorted(missing_columns)}")

    expected_routes = {
        ("GET", "/api/v1/discoveries"),
        ("POST", "/api/v1/intelligence/classify-pending"),
        ("GET", "/api/v1/intelligence/summary"),
        ("POST", "/api/v1/discoveries/{discovery_id}/classify"),
        ("PATCH", "/api/v1/discoveries/{discovery_id}/review"),
    }
    actual_routes = set()
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        path = getattr(route, "path", "")
        actual_routes.update((method, path) for method in methods)
    missing_routes = expected_routes - actual_routes
    if missing_routes:
        raise SystemExit(f"Missing intelligence API routes: {sorted(missing_routes)}")

    if "intelligence.classify_pending" not in HANDLERS:
        raise SystemExit("Worker is missing intelligence.classify_pending.")
    if "source.scan" not in HANDLERS:
        raise SystemExit("Worker is missing source.scan.")

    result = classify_with_rules(
        "Anthropic announces a new Claude model and lower API pricing per token",
        "A new Claude model is released with a lower price per token for API customers.",
        "https://example.com/announcement",
        {"kind": "release"},
    )
    if result["category"] != "pricing" or result["event_type"] != "pricing_change":
        raise SystemExit(f"Unexpected rule classifier output: {result}")
    if result["provider_name"] != "Anthropic":
        raise SystemExit(f"Provider detection failed: {result['provider_name']!r}")
    if not (0 <= result["relevance_score"] <= 100 and 0 <= result["importance_score"] <= 100):
        raise SystemExit("Classifier scores are outside 0-100.")
    if result["category"] not in CATEGORIES or result["event_type"] not in EVENT_TYPES:
        raise SystemExit("Classifier produced an unsupported label.")

    # Default classify_item must work without an external AI key or network.
    default_result = classify_item("Google Gemini adds a new model", "AI model release")
    if default_result["method"] != "rules":
        raise SystemExit("Default classifier is not operating in rules mode.")

    print("SUCCESS: Intelligence models and discovery columns are registered.")
    print("SUCCESS: Protected classification, review, and summary routes are registered.")
    print("SUCCESS: Worker has source.scan and intelligence.classify_pending handlers.")
    print("SUCCESS: Rule classifier detects a candidate pricing change and provider.")
    print("SUCCESS: Rule-first mode works without a Gemini key or external request.")
    print("This check does not modify the database or contact an external AI service.")


if __name__ == "__main__":
    main()
