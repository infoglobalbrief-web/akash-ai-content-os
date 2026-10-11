from __future__ import annotations

import calendar
import hashlib
import ipaddress
import json
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select

from app.database import SessionLocal
from app.models import BackgroundJob, DiscoveredItem, Source

USER_AGENT = "AkashAIContentOS/0.1 (+source-monitor; contact site administrator)"
HTTP_TIMEOUT_SECONDS = 15.0
MAX_REDIRECTS = 5
MAX_ITEMS_PER_SCAN = 50


def _assert_public_http_url(url: str) -> None:
    """Basic SSRF protection: only public HTTP(S) hosts are fetched."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only valid public HTTP/HTTPS URLs can be scanned.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing embedded credentials are not allowed.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("Local hostnames cannot be scanned.")

    try:
        address = ipaddress.ip_address(hostname)
        addresses = [address]
    except ValueError:
        try:
            records = socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise RuntimeError(f"Could not resolve source hostname: {hostname}") from exc
        addresses = [ipaddress.ip_address(record[4][0]) for record in records]

    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Private, local, or non-public IP addresses cannot be scanned.")


def _safe_get(url: str, headers: dict[str, str] | None = None) -> httpx.Response:
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)

    current_url = url
    current_headers = dict(request_headers)
    with httpx.Client(
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _assert_public_http_url(current_url)
            response = client.get(current_url, headers=current_headers)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError("Source returned a redirect without a Location header.")
                next_url = urljoin(current_url, location)
                current_host = (urlsplit(current_url).hostname or "").lower()
                next_host = (urlsplit(next_url).hostname or "").lower()
                if current_host != next_host:
                    # Never forward credentials to a different redirect host.
                    current_headers = {
                        key: value
                        for key, value in current_headers.items()
                        if key.lower() not in {"authorization", "cookie", "proxy-authorization"}
                    }
                current_url = next_url
                continue
            response.raise_for_status()
            return response

    raise RuntimeError("Source exceeded the maximum allowed redirects.")


def _plain_text(value: Any, limit: int = 5000) -> str:
    if value is None:
        return ""
    text_value = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    return " ".join(text_value.split())[:limit]


def _published_at(entry: Any) -> datetime | None:
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(field) if hasattr(entry, "get") else None
        if parsed:
            try:
                return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
            except (OverflowError, OSError, TypeError, ValueError):
                pass
    for field in ("published", "updated", "created"): 
        value = entry.get(field) if hasattr(entry, "get") else None
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                pass
    return None


def _normalise_item(item: dict[str, Any]) -> dict[str, Any]:
    item_key = str(item.get("item_key") or item.get("url") or item.get("title") or "unknown")[:2048]
    title = _plain_text(item.get("title") or "Untitled item", limit=500) or "Untitled item"
    url = str(item.get("url") or "")[:2048]
    summary = _plain_text(item.get("summary"), limit=10000) or None
    published_at = item.get("published_at")
    if published_at is not None and published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    raw_metadata = item.get("raw_metadata") or {}
    try:
        metadata_json = json.dumps(raw_metadata, sort_keys=True, default=str)
    except (TypeError, ValueError):
        metadata_json = "{}"
    digest_payload = "\n".join((title, url, summary or ""))
    return {
        "item_key": item_key,
        "title": title,
        "url": url,
        "summary": summary,
        "published_at": published_at,
        "content_hash": hashlib.sha256(digest_payload.encode("utf-8")).hexdigest(),
        "raw_metadata": json.loads(metadata_json),
    }


def _fetch_rss(source_url: str) -> list[dict[str, Any]]:
    response = _safe_get(source_url, {"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    feed = feedparser.parse(response.content)
    entries = list(feed.entries or [])[:MAX_ITEMS_PER_SCAN]
    if getattr(feed, "bozo", False) and not entries:
        raise RuntimeError(f"Could not parse RSS/Atom feed: {getattr(feed, 'bozo_exception', 'unknown parse error')}")

    items: list[dict[str, Any]] = []
    for entry in entries:
        link = str(entry.get("link") or source_url)
        title = str(entry.get("title") or link)
        stable_key = str(entry.get("id") or entry.get("guid") or link or title)
        items.append({
            "item_key": stable_key,
            "title": title,
            "url": link,
            "summary": entry.get("summary") or entry.get("description") or "",
            "published_at": _published_at(entry),
            "raw_metadata": {
                "author": entry.get("author"),
                "tags": [tag.get("term") for tag in (entry.get("tags") or []) if tag.get("term")],
            },
        })
    return items


def _fetch_github(source_url: str) -> list[dict[str, Any]]:
    parsed = urlsplit(source_url)
    if parsed.hostname is None or parsed.hostname.lower() not in {"github.com", "www.github.com"}:
        raise ValueError("GitHub sources must use a github.com repository URL.")
    segments = [segment for segment in parsed.path.strip("/").split("/") if segment]
    if len(segments) < 2:
        raise ValueError("GitHub source URL must identify an owner and repository.")
    owner = segments[0]
    repo = segments[1].removesuffix(".git")
    if not owner or not repo:
        raise ValueError("GitHub source URL must identify an owner and repository.")

    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    base_url = f"https://api.github.com/repos/{owner}/{repo}"
    release_response = _safe_get(f"{base_url}/releases?per_page=10", headers)
    commit_response = _safe_get(f"{base_url}/commits?per_page=10", headers)
    releases = release_response.json()
    commits = commit_response.json()
    if not isinstance(releases, list) or not isinstance(commits, list):
        raise RuntimeError("GitHub API returned an unexpected response shape.")

    items: list[dict[str, Any]] = []
    for release in releases:
        tag = str(release.get("tag_name") or "unknown")
        name = str(release.get("name") or tag)
        items.append({
            "item_key": f"github-release:{release.get('id') or tag}",
            "title": f"GitHub release: {name}",
            "url": str(release.get("html_url") or source_url),
            "summary": release.get("body") or f"Release {tag} for {owner}/{repo}",
            "published_at": _published_at({"published": release.get("published_at")} ),
            "raw_metadata": {"kind": "release", "tag_name": tag, "draft": release.get("draft"), "prerelease": release.get("prerelease")},
        })

    for commit in commits:
        sha = str(commit.get("sha") or "unknown")
        commit_data = commit.get("commit") or {}
        message = str(commit_data.get("message") or "Commit")
        items.append({
            "item_key": f"github-commit:{sha}",
            "title": f"GitHub commit: {message.splitlines()[0][:450]}",
            "url": str((commit.get("html_url") or source_url)),
            "summary": message,
            "published_at": _published_at({"published": (commit_data.get("committer") or {}).get("date")}),
            "raw_metadata": {"kind": "commit", "sha": sha, "author": (commit_data.get("author") or {}).get("name")},
        })

    return items[:MAX_ITEMS_PER_SCAN]


def _fetch_website(source_url: str) -> list[dict[str, Any]]:
    response = _safe_get(source_url, {"Accept": "text/html,application/xhtml+xml,*/*"})
    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type and "xml" not in content_type and response.text[:500].find("<") < 0:
        raise RuntimeError(f"URL did not appear to return an HTML page (content-type: {content_type or 'unknown'}).")

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else source_url
    description_tag = soup.find("meta", attrs={"name": "description"})
    description = description_tag.get("content", "") if description_tag else ""
    for element in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        element.decompose()
    content_root = soup.find("main") or soup.find("article") or soup.body or soup
    body_text = " ".join(content_root.get_text(" ", strip=True).split())[:10000]
    summary = description or body_text[:3000]
    return [{
        "item_key": f"website-page:{source_url}",
        "title": title,
        "url": source_url,
        "summary": summary,
        "published_at": None,
        "raw_metadata": {
            "kind": "website_page",
            "etag": response.headers.get("etag"),
            "last_modified": response.headers.get("last-modified"),
            "status_code": response.status_code,
        },
        # Body content is included in the internal hash below so page changes are detectable.
        "_page_body": body_text,
    }]


def fetch_source_items(source_type: str, source_url: str) -> list[dict[str, Any]]:
    if source_type == "rss":
        items = _fetch_rss(source_url)
    elif source_type == "github":
        items = _fetch_github(source_url)
    elif source_type == "website":
        items = _fetch_website(source_url)
    else:
        raise ValueError(f"Unsupported source type: {source_type}")

    normalised: list[dict[str, Any]] = []
    for item in items:
        page_body = item.pop("_page_body", None)
        normalized = _normalise_item(item)
        if page_body is not None:
            normalized["content_hash"] = hashlib.sha256(
                (normalized["title"] + "\n" + normalized["url"] + "\n" + page_body).encode("utf-8")
            ).hexdigest()
            normalized["summary"] = normalized["summary"] or page_body[:3000] or None
        normalised.append(normalized)
    return normalised


def scan_source(source_id: int) -> dict[str, Any]:
    """Fetch one source and save new/changed items with per-source deduplication."""
    with SessionLocal() as session:
        source = session.get(Source, source_id)
        if source is None:
            raise ValueError(f"Source {source_id} was not found.")
        if not source.is_active:
            return {"source_id": source_id, "status": "skipped", "reason": "source_inactive", "new_count": 0, "changed_count": 0, "seen_count": 0}
        source_type = source.source_type
        source_url = source.url

    try:
        items = fetch_source_items(source_type, source_url)
    except Exception as exc:
        with SessionLocal() as session:
            with session.begin():
                source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
                if source is not None:
                    source.last_scanned_at = datetime.now(timezone.utc)
                    source.last_scan_status = "error"
                    source.last_scan_error = str(exc)[:5000]
        raise

    now = datetime.now(timezone.utc)
    new_count = 0
    changed_count = 0
    unchanged_count = 0

    # Lock the source row so overlapping scans for a single source are serialized.
    with SessionLocal() as session:
        with session.begin():
            source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
            if source is None:
                raise ValueError(f"Source {source_id} was deleted while its scan was running.")
            if not source.is_active:
                return {"source_id": source_id, "status": "skipped", "reason": "source_inactive", "new_count": 0, "changed_count": 0, "seen_count": 0}

            for item in items:
                existing = session.scalar(
                    select(DiscoveredItem)
                    .where(
                        DiscoveredItem.source_id == source_id,
                        DiscoveredItem.item_key == item["item_key"],
                    )
                    .with_for_update()
                )
                if existing is None:
                    session.add(DiscoveredItem(
                        source_id=source_id,
                        item_key=item["item_key"],
                        title=item["title"],
                        url=item["url"],
                        summary=item["summary"],
                        content_hash=item["content_hash"],
                        published_at=item["published_at"],
                        first_seen_at=now,
                        last_seen_at=now,
                        raw_metadata=item["raw_metadata"],
                    ))
                    # Flush prevents duplicate keys within a single feed batch.
                    session.flush()
                    new_count += 1
                else:
                    existing.last_seen_at = now
                    if existing.content_hash != item["content_hash"]:
                        existing.title = item["title"]
                        existing.url = item["url"]
                        existing.summary = item["summary"]
                        existing.content_hash = item["content_hash"]
                        existing.published_at = item["published_at"]
                        existing.last_changed_at = now
                        existing.change_count += 1
                        existing.raw_metadata = item["raw_metadata"]
                        changed_count += 1
                    else:
                        unchanged_count += 1

            source.last_scanned_at = now
            source.last_scan_status = "success"
            source.last_scan_error = None

    return {
        "source_id": source_id,
        "status": "success",
        "seen_count": len(items),
        "new_count": new_count,
        "changed_count": changed_count,
        "unchanged_count": unchanged_count,
    }


def enqueue_due_source_scans(limit: int = 25) -> int:
    """Atomically enqueue scans for active sources whose scheduled time is due."""
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200.")

    now = datetime.now(timezone.utc)
    queued_count = 0
    with SessionLocal() as session:
        with session.begin():
            due_sources = session.scalars(
                select(Source)
                .where(Source.is_active.is_(True), Source.next_scan_at <= now)
                .order_by(Source.next_scan_at, Source.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()

            for source in due_sources:
                session.add(BackgroundJob(
                    job_type="source.scan",
                    payload={"source_id": source.id},
                    status="queued",
                    attempts=0,
                    max_attempts=3,
                ))
                interval = max(5, min(int(source.scan_interval_minutes), 10080))
                source.next_scan_at = now + timedelta(minutes=interval)
                queued_count += 1

    return queued_count
