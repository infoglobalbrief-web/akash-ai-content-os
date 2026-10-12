"""Offline check for content production/approval batch. No DB writes or network calls."""
from app import models
from app.main import app
from app.worker import HANDLERS
from app.content_pipeline import calculate_seo_checks, slugify


def main() -> None:
    expected_tables = {"sources", "background_jobs", "discovered_items", "content_drafts"}
    actual_tables = set(models.Base.metadata.tables)
    missing = expected_tables - actual_tables
    if missing:
        raise SystemExit(f"Missing SQLAlchemy tables: {sorted(missing)}")
    columns = set(models.ContentDraft.__table__.columns.keys())
    required = {
        "status", "title", "slug", "seo_title", "meta_description", "focus_keyword",
        "content_markdown", "discovery_ids", "evidence_snapshot", "request_options",
        "word_count", "seo_score", "seo_checks", "generation_method", "generation_error",
        "review_notes", "approved_at", "created_at", "updated_at",
    }
    missing_cols = required - columns
    if missing_cols:
        raise SystemExit(f"Missing ContentDraft columns: {sorted(missing_cols)}")
    routes = set()
    for route in app.routes:
        for method in (getattr(route, "methods", None) or set()):
            routes.add((method, getattr(route, "path", "")))
    expected_routes = {
        ("POST", "/api/v1/content/drafts"),
        ("GET", "/api/v1/content/drafts"),
        ("GET", "/api/v1/content/drafts/{draft_id}"),
        ("POST", "/api/v1/content/drafts/{draft_id}/regenerate"),
        ("PATCH", "/api/v1/content/drafts/{draft_id}/review"),
        ("GET", "/api/v1/content/summary"),
    }
    missing_routes = expected_routes - routes
    if missing_routes:
        raise SystemExit(f"Missing content routes: {sorted(missing_routes)}")
    if "content.generate" not in HANDLERS:
        raise SystemExit("Worker missing content.generate handler.")
    article = {
        "title": "A sample AI pricing update for developers",
        "seo_title": "A sample AI pricing update for developers",
        "meta_description": "A reviewed draft about a reported AI pricing update for developers. Confirm every price and plan limit against the provider's original documentation before publication.",
        "focus_keyword": "AI pricing update",
        "content_markdown": "## Overview\n\n" + ("Evidence-backed content [1]. " * 160) + "\n\n## Sources\n\n[1] https://example.com",
    }
    score, word_count, checks = calculate_seo_checks(article, 1)
    if not (0 <= score <= 100) or word_count < 300 or not checks["sources_section_present"]["passed"]:
        raise SystemExit("SEO check calculation failed its sample case.")
    if slugify("AI Model Pricing: Updated 2026!") != "ai-model-pricing-updated-2026":
        raise SystemExit("Slug generation failed.")
    print("SUCCESS: ContentDraft table and required fields are registered.")
    print("SUCCESS: Protected content API routes are registered.")
    print("SUCCESS: Worker has the content.generate handler.")
    print("SUCCESS: SEO score, word count, and source-section checks work offline.")
    print("SUCCESS: Slug normalization works.")
    print("NOTE: This offline check does not test PostgreSQL migration or Gemini connectivity.")


if __name__ == "__main__":
    main()
