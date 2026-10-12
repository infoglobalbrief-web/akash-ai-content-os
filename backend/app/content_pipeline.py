"""Evidence-grounded content drafting, SEO checks, and explicit review gate.

No content is published from this module. The optional Gemini adapter may incur API costs.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from app.database import SessionLocal
from app.models import ContentDraft, DiscoveredItem, Source


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized.lower()).strip("-")
    return (slug or "ai-content-draft")[:200].strip("-")


def _safe_http_url(value: str) -> bool:
    parsed = urlparse(value or "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def load_evidence(discovery_ids: list[int]) -> list[dict[str, Any]]:
    """Load selected classified discoveries with source provenance snapshots."""
    with SessionLocal() as session:
        rows = session.execute(
            select(DiscoveredItem, Source)
            .join(Source, Source.id == DiscoveredItem.source_id)
            .where(DiscoveredItem.id.in_(discovery_ids))
            .order_by(DiscoveredItem.id)
        ).all()

        by_id = {item.id: (item, source) for item, source in rows}
        missing = [item_id for item_id in discovery_ids if item_id not in by_id]
        if missing:
            raise ValueError(f"Discovery IDs not found: {missing}")

        evidence: list[dict[str, Any]] = []
        for index, discovery_id in enumerate(discovery_ids, start=1):
            item, source = by_id[discovery_id]
            if item.classification_status != "classified":
                raise ValueError(
                    f"Discovery {discovery_id} is not classified. Classify it before drafting."
                )
            if not _safe_http_url(item.url) or not _safe_http_url(source.url):
                raise ValueError(f"Discovery {discovery_id} or its source has an invalid URL.")
            evidence.append({
                "reference": index,
                "discovery_id": item.id,
                "source_id": source.id,
                "source_name": source.name,
                "source_type": source.source_type,
                "source_homepage": source.url,
                "title": item.title,
                "url": item.url,
                "summary": (item.summary or item.classification_summary or "").strip()[:5000],
                "category": item.category,
                "event_type": item.event_type,
                "provider_name": item.provider_name,
                "relevance_score": item.relevance_score,
                "importance_score": item.importance_score,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "first_seen_at": item.first_seen_at.isoformat() if item.first_seen_at else None,
                "classification_method": item.classification_method,
                "classification_reason": item.classification_reason,
            })
    return evidence


def _focus_keyword(evidence: list[dict[str, Any]]) -> str:
    for item in evidence:
        if item.get("provider_name"):
            return str(item["provider_name"])[:120]
    title = evidence[0].get("title", "AI update") if evidence else "AI update"
    words = re.findall(r"[A-Za-z0-9]+", title)
    return " ".join(words[:3])[:120] or "AI update"


def _build_rule_draft(
    evidence: list[dict[str, Any]],
    title_hint: str | None,
    audience: str,
    tone: str,
) -> dict[str, str]:
    """A conservative offline draft using only supplied discovery summaries."""
    first = evidence[0]
    title = (title_hint or first.get("title") or "AI update research brief").strip()[:150]
    focus_keyword = _focus_keyword(evidence)
    descriptions = [str(item.get("summary") or "").strip() for item in evidence]
    usable = [description for description in descriptions if description]
    description_seed = (usable[0] if usable else title).replace("\n", " ")
    meta_description = re.sub(r"\s+", " ", description_seed).strip()
    if len(meta_description) < 100:
        meta_description = (
            f"Research brief on {focus_keyword}: review source-backed AI updates, their context, "
            "and the checks needed before publication."
        )
    meta_description = meta_description[:158].rstrip(" ,;:-") + ("." if meta_description[:158].rstrip(" ,;:-") else "")

    paragraphs: list[str] = [
        f"This working draft summarizes {len(evidence)} monitored finding(s) for {audience}. "
        "It is based on the recorded source entries listed below, not on independent verification. "
        "Confirm material claims, dates, pricing, availability, and product names against the original pages before publication.",
        "",
        "## What the monitored sources report",
        "",
    ]
    for item in evidence:
        ref = item["reference"]
        paragraphs.append(f"### {item['title']}")
        paragraphs.append("")
        summary = re.sub(r"\s+", " ", str(item.get("summary") or "")).strip()
        if summary:
            paragraphs.append(summary[:2500])
        else:
            paragraphs.append(
                "The feed record does not contain enough detail to summarize this item reliably. "
                "Open the original source and add verified notes before publication."
            )
        event = item.get("event_type") or "general_update"
        provider = item.get("provider_name") or "Provider not identified"
        paragraphs.append("")
        paragraphs.append(
            f"Monitoring metadata labels this as **{event.replace('_', ' ')}** and associates it with "
            f"**{provider}**. These are automated triage labels, not independently verified conclusions. [{ref}]"
        )
        paragraphs.append("")

    paragraphs.extend([
        "## Why it may matter",
        "",
        "The practical significance depends on whether the reported change is confirmed in primary documentation, "
        "which products or plans it affects, when it takes effect, and whether regional or account-level conditions apply. "
        "Do not infer a price reduction, release date, benchmark advantage, or universal availability unless the original source states it clearly.",
        "",
        "## Verification checklist before publication",
        "",
        "- Open each linked original item and compare the headline with the source body.",
        "- Confirm dates, model names, pricing units, plan limits, availability, and any quoted numeric values.",
        "- Prefer the provider's official documentation or announcement for product and pricing claims.",
        "- Add context and independent sources where a single feed entry is not enough.",
        "- Recheck all links and update the article if the source has changed since it was collected.",
        "",
        "## Sources",
        "",
    ])
    for item in evidence:
        ref = item["reference"]
        paragraphs.append(f"[{ref}] [{item['title']}]({item['url']}) — {item['source_name']} (source type: {item['source_type']}).")
    content = "\n".join(paragraphs).strip()
    seo_title = title[:65].rstrip()
    return {
        "title": title,
        "seo_title": seo_title,
        "meta_description": meta_description,
        "focus_keyword": focus_keyword,
        "content_markdown": content,
    }


def _gemini_draft(
    evidence: list[dict[str, Any]],
    title_hint: str | None,
    audience: str,
    tone: str,
) -> dict[str, str]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("CONTENT_GENERATION_PROVIDER=gemini but GEMINI_API_KEY is not configured.")
    model = os.getenv("CONTENT_GENERATION_MODEL", "gemini-2.5-flash").strip()
    prompt = f"""You are an evidence-grounded editorial assistant. Generate a draft, never publish it.
Return ONLY a valid JSON object with string fields: title, seo_title, meta_description, focus_keyword, content_markdown.
Audience: {audience}. Tone: {tone}. Requested title idea: {title_hint or '(choose an accurate title)'}.
Rules:
- Use only facts present in the evidence JSON. Never invent metrics, dates, prices, availability, quotes, product details, or causal claims.
- If evidence is thin or ambiguous, state that it needs verification and provide an editor checklist.
- Cite evidence in the article with [1], [2], etc. matching each item's reference number.
- Include useful Markdown headings and a final Sources section with the exact original URLs supplied.
- Do not claim you independently verified any source. Keep the draft useful, neutral, and specific.
- Provide an SEO title and meta description grounded in evidence. No clickbait or unsupported superlatives.
EVIDENCE JSON:
{json.dumps(evidence, ensure_ascii=False)[:26000]}
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 5000},
            },
        )
        response.raise_for_status()
        body = response.json()
    output = body["candidates"][0]["content"]["parts"][0]["text"].strip()
    output = re.sub(r"^```(?:json)?\s*|\s*```$", "", output, flags=re.IGNORECASE)
    value = json.loads(output)
    if not isinstance(value, dict):
        raise ValueError("Gemini content response must be a JSON object.")
    required = ("title", "seo_title", "meta_description", "focus_keyword", "content_markdown")
    for key in required:
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"Gemini content response has an invalid {key} field.")
    return {key: value[key].strip() for key in required}


def _ensure_sources_appendix(content: str, evidence: list[dict[str, Any]]) -> str:
    """Replace any model-written source appendix with exact URLs from our database."""
    content = re.sub(
        r"(?ims)^##\s+Sources\s*$.*?(?=^##\s+|\Z)",
        "",
        content,
    ).rstrip()
    appendix = ["", "## Sources", ""]
    appendix.extend(
        f"[{item['reference']}] [{item['title']}]({item['url']}) — {item['source_name']} (recorded source: {item['source_homepage']})."
        for item in evidence
    )
    return content + "\n" + "\n".join(appendix)


def calculate_seo_checks(article: dict[str, str], evidence_count: int) -> tuple[int, int, dict[str, Any]]:
    title = article.get("title", "").strip()
    seo_title = article.get("seo_title", "").strip()
    meta = article.get("meta_description", "").strip()
    keyword = article.get("focus_keyword", "").strip().lower()
    content = article.get("content_markdown", "")
    sources_head = re.search(r"^##\s+Sources\s*$", content, flags=re.MULTILINE | re.IGNORECASE)
    article_body = content[:sources_head.start()] if sources_head else content
    plain = re.sub(r"[`*_>#\[\]()]", " ", article_body)
    words = re.findall(r"\b[\w'-]+\b", plain, flags=re.UNICODE)
    word_count = len(words)
    headings = len(re.findall(r"^##?\s+", article_body, flags=re.MULTILINE))
    # Citations in the bibliography alone do not count: require a citation in the article body.
    citations = sorted({int(number) for number in re.findall(r"\[(\d+)\]", article_body)})
    checks = {
        "title_length_30_65": {"passed": 30 <= len(seo_title) <= 65, "actual": len(seo_title), "target": "30–65 characters"},
        "meta_description_120_160": {"passed": 120 <= len(meta) <= 160, "actual": len(meta), "target": "120–160 characters"},
        "focus_keyword_present": {"passed": bool(keyword), "actual": article.get("focus_keyword", "")},
        "keyword_in_title_or_content": {"passed": bool(keyword) and (keyword in title.lower() or keyword in content.lower())},
        "at_least_two_headings": {"passed": headings >= 2, "actual": headings},
        "at_least_300_words": {"passed": word_count >= 300, "actual": word_count, "target": 300},
        "sources_section_present": {"passed": bool(sources_head)},
        "evidence_citations_present": {"passed": evidence_count > 0 and len(citations) > 0, "citation_numbers": citations},
    }
    weights = {
        "title_length_30_65": 15,
        "meta_description_120_160": 15,
        "focus_keyword_present": 10,
        "keyword_in_title_or_content": 10,
        "at_least_two_headings": 10,
        "at_least_300_words": 15,
        "sources_section_present": 10,
        "evidence_citations_present": 15,
    }
    score = sum(weights[name] for name, check in checks.items() if check["passed"])
    return score, word_count, checks


def generate_content_draft(draft_id: int) -> dict[str, Any]:
    """Generate and store a draft. Does not approve or publish it."""
    with SessionLocal() as session:
        draft = session.get(ContentDraft, draft_id)
        if draft is None:
            raise ValueError(f"Content draft {draft_id} not found.")
        if draft.status in {"approved", "rejected"}:
            raise ValueError(f"Draft {draft_id} is {draft.status}; regeneration is not allowed.")
        discovery_ids = list(draft.discovery_ids or [])
        options = dict(draft.request_options or {})
        title_hint = options.get("title_hint")
        audience = str(options.get("audience") or "AI professionals, creators, and digital marketers")[:200]
        tone = str(options.get("tone") or "professional")[:50]
        draft.status = "generating"
        draft.generation_error = None
        session.commit()

    try:
        evidence = load_evidence(discovery_ids)
        provider = os.getenv("CONTENT_GENERATION_PROVIDER", "rules").strip().lower()
        method = "rules"
        if provider == "gemini":
            try:
                article = _gemini_draft(evidence, title_hint, audience, tone)
                method = "gemini"
            except Exception as exc:
                # The fallback is deliberately explicit in generation_method and logs.
                article = _build_rule_draft(evidence, title_hint, audience, tone)
                method = "rules_fallback"
                generation_error = f"Gemini failed ({type(exc).__name__}); conservative rules draft used."
            else:
                generation_error = None
        else:
            article = _build_rule_draft(evidence, title_hint, audience, tone)
            generation_error = None

        # Always replace any model-written Sources section with exact URLs from the DB.
        article["content_markdown"] = _ensure_sources_appendix(article["content_markdown"], evidence)
        score, word_count, checks = calculate_seo_checks(article, len(evidence))
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            draft = session.get(ContentDraft, draft_id)
            if draft is None:
                raise ValueError(f"Content draft {draft_id} disappeared during generation.")
            if draft.status in {"approved", "rejected"}:
                raise ValueError(f"Draft {draft_id} was reviewed while generation was running.")
            draft.title = article["title"][:500]
            draft.slug = slugify(article["title"])
            draft.seo_title = article["seo_title"][:100]
            draft.meta_description = article["meta_description"][:320]
            draft.focus_keyword = article["focus_keyword"][:120]
            draft.content_markdown = article["content_markdown"]
            draft.evidence_snapshot = evidence
            draft.word_count = word_count
            draft.seo_score = score
            draft.seo_checks = checks
            draft.generation_method = method
            draft.generation_error = generation_error
            draft.status = "needs_review"
            draft.updated_at = now
            session.commit()
        return {"draft_id": draft_id, "status": "needs_review", "word_count": word_count, "seo_score": score, "generation_method": method}
    except Exception as exc:
        with SessionLocal() as session:
            draft = session.get(ContentDraft, draft_id)
            if draft is not None and draft.status not in {"approved", "rejected"}:
                draft.status = "generation_error"
                draft.generation_error = f"{type(exc).__name__}: {str(exc)}"[:4000]
                draft.updated_at = datetime.now(timezone.utc)
                session.commit()
        raise
