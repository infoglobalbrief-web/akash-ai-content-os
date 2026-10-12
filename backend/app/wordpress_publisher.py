"""WordPress REST API publisher with approval enforcement and idempotent post creation."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import bleach
import httpx
import markdown
from dotenv import load_dotenv
from sqlalchemy import select

from app.content_pipeline import slugify
from app.database import SessionLocal
from app.models import ContentDraft, WordPressPublication

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

ALLOWED_HTML_TAGS = {
    "a", "p", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    "strong", "em", "blockquote", "code", "pre", "hr", "br", "table",
    "thead", "tbody", "tr", "th", "td", "sup", "sub", "del", "ins",
}
ALLOWED_HTML_ATTRIBUTES = {
    "a": ["href", "title", "rel"],
    "th": ["colspan", "rowspan"],
    "td": ["colspan", "rowspan"],
}


def wordpress_config() -> dict[str, Any]:
    site_url = (os.getenv("WORDPRESS_SITE_URL") or "").strip().rstrip("/")
    username = (os.getenv("WORDPRESS_USERNAME") or "").strip()
    # WordPress displays Application Passwords in groups; spaces are optional.
    application_password = re.sub(r"\s+", "", os.getenv("WORDPRESS_APPLICATION_PASSWORD") or "")
    if not site_url or not username or not application_password:
        raise RuntimeError(
            "WordPress publishing is not configured. Set WORDPRESS_SITE_URL, "
            "WORDPRESS_USERNAME, and WORDPRESS_APPLICATION_PASSWORD in backend/.env."
        )
    parsed = urlparse(site_url)
    if parsed.username or parsed.password:
        raise RuntimeError("WORDPRESS_SITE_URL must not contain embedded credentials.")
    is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not is_local:
        raise RuntimeError("WORDPRESS_SITE_URL must use HTTPS except for localhost testing.")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("WORDPRESS_SITE_URL must be a valid HTTP(S) URL.")
    timeout_raw = os.getenv("WORDPRESS_TIMEOUT_SECONDS", "30")
    try:
        timeout = max(5, min(int(timeout_raw), 90))
    except ValueError:
        timeout = 30
    return {
        "site_url": site_url,
        "api_url": f"{site_url}/wp-json/wp/v2",
        "username": username,
        "application_password": application_password,
        "timeout": timeout,
    }


def render_markdown(content_markdown: str) -> str:
    """Render Markdown to WordPress-safe, allow-listed HTML."""
    rendered = markdown.markdown(
        content_markdown or "",
        extensions=["extra", "sane_lists"],
        output_format="html5",
    )
    return bleach.clean(
        rendered,
        tags=ALLOWED_HTML_TAGS,
        attributes=ALLOWED_HTML_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        strip=True,
        strip_comments=True,
    )


def stable_post_slug(draft_id: int, requested_slug: str | None, title: str) -> str:
    """Use a stable draft-specific slug to make retries discoverable."""
    base = slugify(requested_slug or title)
    suffix = f"-akcos-{draft_id}"
    base = base[: 200 - len(suffix)].rstrip("-") or "content"
    return f"{base}{suffix}"


def build_post_payload(draft: dict[str, Any], publication: dict[str, Any]) -> dict[str, Any]:
    """Build a REST payload, including Rank Math and bridge metadata."""
    draft_id = int(draft["id"])
    wp_slug = stable_post_slug(draft_id, draft.get("slug"), draft.get("title", "AI content"))
    seo_title = (draft.get("seo_title") or draft.get("title") or "")[:100]
    meta_description = (draft.get("meta_description") or "")[:320]
    focus_keyword = (draft.get("focus_keyword") or "")[:120]
    meta: dict[str, Any] = {
        "akcos_draft_id": draft_id,
        "akcos_idempotency_key": publication["idempotency_key"],
    }
    # Empty SEO values are omitted so WordPress/Rank Math may apply their defaults.
    if seo_title:
        meta["rank_math_title"] = seo_title
    if meta_description:
        meta["rank_math_description"] = meta_description
    if focus_keyword:
        meta["rank_math_focus_keyword"] = focus_keyword

    payload: dict[str, Any] = {
        "title": (draft.get("title") or "Untitled AI content")[:500],
        "slug": wp_slug,
        "content": render_markdown(draft.get("content_markdown") or ""),
        "status": publication["requested_status"],
        "meta": meta,
    }
    if meta_description:
        payload["excerpt"] = meta_description
    categories = publication.get("category_ids") or []
    tags = publication.get("tag_ids") or []
    if categories:
        payload["categories"] = [int(value) for value in categories]
    if tags:
        payload["tags"] = [int(value) for value in tags]
    return payload


def _wp_request(config: dict[str, Any], method: str, path: str, **kwargs: Any) -> Any:
    url = f"{config['api_url']}/{path.lstrip('/')}"
    auth = httpx.BasicAuth(config["username"], config["application_password"])
    with httpx.Client(timeout=config["timeout"], follow_redirects=False, auth=auth) as client:
        try:
            response = client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"WordPress network request failed: {type(exc).__name__}.") from None
    if response.status_code >= 400:
        try:
            body = response.json()
            code = str(body.get("code", "wordpress_error"))[:100]
            message = str(body.get("message", "WordPress REST API request failed."))[:500]
        except (ValueError, AttributeError):
            code, message = "wordpress_error", "WordPress REST API request failed."
        raise RuntimeError(f"WordPress REST API returned HTTP {response.status_code} ({code}): {message}")
    try:
        return response.json()
    except ValueError:
        raise RuntimeError("WordPress REST API returned invalid JSON.") from None


def check_wordpress_connection() -> dict[str, Any]:
    config = wordpress_config()
    user = _wp_request(config, "GET", "users/me?context=edit")
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise RuntimeError("WordPress authentication did not return a valid user.")
    return {
        "connected": True,
        "site_url": config["site_url"],
        "user_id": user["id"],
        "username": user.get("slug") or user.get("name"),
        "message": "Authenticated to WordPress REST API.",
    }


def _read_publication_and_draft(publication_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    with SessionLocal() as session:
        publication = session.get(WordPressPublication, publication_id)
        if publication is None:
            raise ValueError(f"WordPress publication {publication_id} was not found.")
        draft = session.get(ContentDraft, publication.draft_id)
        if draft is None:
            raise ValueError(f"Content draft {publication.draft_id} was not found.")
        if draft.status not in {"approved", "published"}:
            raise ValueError(
                f"Draft {draft.id} is not approved for publishing (status: {draft.status})."
            )
        if not (draft.content_markdown or "").strip():
            raise ValueError("The approved draft has no content.")
        if not draft.evidence_snapshot:
            raise ValueError("The draft has no evidence snapshot; publishing is blocked.")

        publication_data = {
            "id": publication.id,
            "draft_id": publication.draft_id,
            "requested_status": publication.requested_status,
            "status": publication.status,
            "wp_post_id": publication.wp_post_id,
            "wp_post_url": publication.wp_post_url,
            "wp_slug": publication.wp_slug,
            "idempotency_key": publication.idempotency_key,
            "category_ids": publication.category_ids or [],
            "tag_ids": publication.tag_ids or [],
        }
        draft_data = {
            "id": draft.id,
            "status": draft.status,
            "title": draft.title,
            "slug": draft.slug,
            "seo_title": draft.seo_title,
            "meta_description": draft.meta_description,
            "focus_keyword": draft.focus_keyword,
            "content_markdown": draft.content_markdown,
            "evidence_snapshot": draft.evidence_snapshot,
        }
        return publication_data, draft_data


def publish_approved_draft(publication_id: int) -> dict[str, Any]:
    """Create/update a WordPress post only for explicitly approved drafts."""
    with SessionLocal() as session:
        publication = session.scalar(
            select(WordPressPublication)
            .where(WordPressPublication.id == publication_id)
            .with_for_update()
        )
        if publication is None:
            raise ValueError(f"WordPress publication {publication_id} was not found.")
        if publication.status == "published" and publication.requested_status == "publish":
            return {
                "publication_id": publication.id,
                "status": publication.status,
                "wp_post_id": publication.wp_post_id,
                "wp_post_url": publication.wp_post_url,
                "idempotent_noop": True,
            }
        if publication.status == "wordpress_draft" and publication.requested_status == "draft":
            return {
                "publication_id": publication.id,
                "status": publication.status,
                "wp_post_id": publication.wp_post_id,
                "wp_post_url": publication.wp_post_url,
                "idempotent_noop": True,
            }
        publication.status = "publishing"
        publication.attempts += 1
        publication.last_error = None
        session.commit()

    try:
        config = wordpress_config()
        publication_data, draft_data = _read_publication_and_draft(publication_id)
        payload = build_post_payload(draft_data, publication_data)
        found_post: dict[str, Any] | None = None

        # If a prior network timeout happened after WordPress created the post,
        # look up the stable draft-specific slug before attempting another create.
        if publication_data.get("wp_post_id"):
            found_post = _wp_request(
                config, "GET", f"posts/{int(publication_data['wp_post_id'])}?context=edit"
            )
        else:
            candidates = _wp_request(
                config,
                "GET",
                "posts",
                params={"slug": payload["slug"], "context": "edit", "per_page": 10},
            )
            if isinstance(candidates, list):
                for candidate in candidates:
                    meta = candidate.get("meta") or {}
                    if meta.get("akcos_draft_id") == draft_data["id"]:
                        found_post = candidate
                        break
                if candidates and found_post is None:
                    raise RuntimeError(
                        "A WordPress post already uses this integration slug but does not "
                        "match this draft's integration metadata. No duplicate was created; inspect it manually."
                    )

        if found_post is not None:
            post = _wp_request(
                config,
                "POST",
                f"posts/{int(found_post['id'])}",
                json=payload,
            )
        else:
            post = _wp_request(config, "POST", "posts", json=payload)

        post_id = post.get("id") if isinstance(post, dict) else None
        post_link = post.get("link") if isinstance(post, dict) else None
        if not isinstance(post_id, int) or not isinstance(post_link, str):
            raise RuntimeError("WordPress returned a post response without an ID or URL.")

        post_status = post.get("status")
        if post_status not in {"draft", "publish"}:
            raise RuntimeError(f"WordPress returned unexpected post status: {post_status!r}.")
        final_publication_status = "published" if post_status == "publish" else "wordpress_draft"

        with SessionLocal() as session:
            publication = session.get(WordPressPublication, publication_id)
            draft = session.get(ContentDraft, publication.draft_id) if publication else None
            if publication is None or draft is None:
                raise RuntimeError("Publication record disappeared while saving WordPress response.")
            publication.wp_post_id = post_id
            publication.wp_post_url = post_link
            publication.wp_edit_url = f"{config['site_url']}/wp-admin/post.php?post={post_id}&action=edit"
            publication.wp_slug = str(post.get("slug") or payload["slug"])
            publication.status = final_publication_status
            publication.last_error = None
            if final_publication_status == "published":
                publication.published_at = datetime.now(timezone.utc)
                draft.status = "published"
            session.commit()

        return {
            "publication_id": publication_id,
            "draft_id": draft_data["id"],
            "status": final_publication_status,
            "wp_post_id": post_id,
            "wp_post_url": post_link,
            "wp_edit_url": f"{config['site_url']}/wp-admin/post.php?post={post_id}&action=edit",
            "wordpress_status": post_status,
            "seo_meta_sent": True,
            "message": "WordPress post created or updated successfully.",
        }
    except Exception as exc:
        safe_error = f"{type(exc).__name__}: {exc}"[:2000]
        with SessionLocal() as session:
            publication = session.get(WordPressPublication, publication_id)
            if publication is not None:
                publication.status = "failed"
                publication.last_error = safe_error
                session.commit()
        raise RuntimeError(safe_error) from None
