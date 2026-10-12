"""Offline sanity checks for the WordPress publishing batch."""
from app import models
from app.main import app
from app.worker import HANDLERS
from app.wordpress_publisher import build_post_payload, render_markdown, stable_post_slug

expected_models = {
    "sources",
    "background_jobs",
    "discovered_items",
    "content_drafts",
    "wordpress_publications",
}
missing_models = expected_models - set(models.Base.metadata.tables)
if missing_models:
    raise SystemExit(f"FAIL: Missing SQLAlchemy models: {sorted(missing_models)}")
print("SUCCESS: SQLAlchemy models include WordPress publications.")

routes = {(route.path, tuple(sorted(route.methods or []))) for route in app.routes}
required_routes = {
    ("/api/v1/wordpress/connection", ("GET",)),
    ("/api/v1/content/drafts/{draft_id}/publish", ("POST",)),
    ("/api/v1/wordpress/publications", ("GET",)),
    ("/api/v1/wordpress/publications/{publication_id}", ("GET",)),
}
missing_routes = required_routes - routes
if missing_routes:
    raise SystemExit(f"FAIL: Missing routes: {sorted(missing_routes)}")
print("SUCCESS: WordPress connection, publish, and status routes are registered.")

if "wordpress.publish" not in HANDLERS:
    raise SystemExit("FAIL: Worker does not register wordpress.publish.")
print("SUCCESS: Worker has the wordpress.publish handler.")

slug = stable_post_slug(123, "hello-world", "Hello World")
if slug != "hello-world-akcos-123":
    raise SystemExit(f"FAIL: Unexpected stable slug: {slug}")
html = render_markdown("## Hello\n\n**world**")
if "<h2>Hello</h2>" not in html or "<strong>world</strong>" not in html:
    raise SystemExit("FAIL: Markdown-to-HTML conversion did not produce expected HTML.")
print("SUCCESS: HTML conversion and stable publication slug checks passed.")

sample_draft = {
    "id": 123,
    "title": "Sample",
    "slug": "sample",
    "seo_title": "Sample SEO",
    "meta_description": "Sample description",
    "focus_keyword": "sample keyword",
    "content_markdown": "Hello **world**",
}
sample_publication = {
    "requested_status": "draft",
    "idempotency_key": "akcos-content-draft-123",
    "category_ids": [],
    "tag_ids": [],
}
payload = build_post_payload(sample_draft, sample_publication)
if payload["status"] != "draft" or payload["meta"]["akcos_draft_id"] != 123:
    raise SystemExit("FAIL: WordPress payload has incorrect status or idempotency metadata.")
print("SUCCESS: WordPress payload maps SEO and idempotency metadata.")
print("NOTE: Live WordPress authentication and database migration still require environment testing.")
