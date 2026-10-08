"""Bounded, score-ranked crawl for finance documents and contacts."""

from __future__ import annotations

import heapq
import itertools
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.db import CONTACT_PAGE_REASON, Database
from app.extract import (
    ExtractedContact,
    clean_contacts,
    extract_contacts,
    extract_links,
    page_contact_text,
    page_title,
    page_looks_like_staff,
)
from app.fetch import Fetcher, UnsafeURLError, registrable_domain, same_site
from app.llm import LlmClient
from app.pdf_check import check_document, extract_pdf_text
from app.score import KeywordScorer, is_file_url

logger = logging.getLogger(__name__)


def _text_field(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _on_page(value: str, page: str) -> bool:
    if value.lower() in page.lower():
        return True
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 10 and digits[-10:] in re.sub(r"\D", "", page):
        return True
    return False


@dataclass(order=True)
class FrontierItem:
    priority: float
    depth: int
    url: str = field(compare=False)
    source_page: str | None = field(default=None, compare=False)
    anchor_text: str = field(default="", compare=False)
    is_document: bool = field(default=False, compare=False)
    seq: int = 0


_HIGH_FOLLOW = 50
_EXTRA_HTML_PAGES = 2
_EXTRA_DEPTH = 2
_CONTACT_EXTRA_DEPTH = 1
_CONTACT_PRIORITY_BONUS = 40
_FY_RE = re.compile(r"\bFY\s*(20\d{2})\b", re.I)


def _html_room(pages_fetched: int, follow_score: float, max_pages: int) -> bool:
    limit = max_pages + (_EXTRA_HTML_PAGES if follow_score >= _HIGH_FOLLOW else 0)
    return pages_fetched < limit


def _follow_limit(
    follow_score: float, max_depth: int, *, contact_page: bool = False
) -> int:
    if contact_page:
        return max_depth + _EXTRA_DEPTH + _CONTACT_EXTRA_DEPTH
    if follow_score >= _HIGH_FOLLOW:
        return max_depth + _EXTRA_DEPTH
    return max_depth


def _finance_page(scorer: KeywordScorer, url: str, html: str) -> bool:
    score = scorer.score(
        url=urlparse(url).path, anchor_text=page_title(html)
    )
    return score.follow_score >= scorer.result_threshold


def _year_hint(text: str) -> str | None:
    match = _FY_RE.search(text)
    return match.group(1) if match else None


def _response_is_pdf(content_type: str, url: str, body: bytes) -> bool:
    if "pdf" in content_type or url.lower().endswith(".pdf"):
        return True
    return body.lstrip()[:5] == b"%PDF-"


@dataclass
class CrawlConfig:
    max_pages: int = 25
    max_depth: int = 3
    max_documents: int = 8
    extra_keywords: list[str] | None = None


class CrawlRunner:
    def __init__(
        self,
        db: Database,
        *,
        fetcher: Fetcher | None = None,
        scorer: KeywordScorer | None = None,
        llm: LlmClient | None = None,
    ) -> None:
        self.db = db
        self.fetcher = fetcher or Fetcher()
        self._owns_fetcher = fetcher is None
        self.llm = llm or LlmClient()
        self.scorer = scorer

    def close(self) -> None:
        if self._owns_fetcher:
            self.fetcher.close()

    def run(
        self,
        seed_url: str,
        config: CrawlConfig | None = None,
        *,
        site_id: int | None = None,
    ) -> int:
        config = config or CrawlConfig()
        seed_url = seed_url.strip()
        domain = registrable_domain(seed_url)
        if site_id is None:
            site_id = self.db.create_or_reset_site(seed_url, domain)
        else:
            self.db.update_site(site_id, status="running", pages_fetched=0)
        scorer = self.scorer or KeywordScorer(extra_keywords=config.extra_keywords)

        frontier: list[FrontierItem] = []
        order = itertools.count()
        heapq.heappush(frontier, FrontierItem(priority=0.0, depth=0, url=seed_url))
        seen: set[str] = set()
        pages_fetched = 0
        docs_checked = 0
        crawl_started = time.monotonic()
        timing = {
            "fetches": 0,
            "fetch_s": 0.0,
            "llm_calls": 0,
            "llm_s": 0.0,
            "pdf_checks": 0,
            "pdf_s": 0.0,
        }
        logger.info(
            "crawl start seed=%s max_pages=%s llm_enabled=%s",
            seed_url,
            config.max_pages,
            self.llm.enabled,
        )

        try:
            while frontier:
                item = heapq.heappop(frontier)
                url = item.url
                if url in seen:
                    continue
                if urlparse(url).scheme == "mailto":
                    continue
                if not same_site(seed_url, url) and not item.is_document:
                    continue
                if not item.is_document and not _html_room(
                    pages_fetched, -item.priority, config.max_pages
                ):
                    continue
                looks_like_document = item.is_document or is_file_url(
                    url, scorer.document_extensions
                )
                if looks_like_document and docs_checked >= config.max_documents:
                    if item.is_document:
                        evidence = (
                            "blocked by robots.txt"
                            if not self.fetcher.robots.allowed(url)
                            else "document check cap reached"
                        )
                        self._record_file_link(
                            site_id,
                            url,
                            anchor_text=item.anchor_text,
                            scorer=scorer,
                            evidence=evidence,
                        )
                    seen.add(url)
                    continue
                seen.add(url)

                try:
                    fetch_started = time.monotonic()
                    result = self.fetcher.fetch(url)
                    fetch_s = time.monotonic() - fetch_started
                    timing["fetches"] += 1
                    timing["fetch_s"] += fetch_s
                    logger.info("fetch %.2fs %s", fetch_s, url[:120])
                except UnsafeURLError as exc:
                    self.db.upsert_page(site_id, url, error=str(exc))
                    if pages_fetched == 0 and url == seed_url:
                        self.db.update_site(
                            site_id, status="failed", error=str(exc), pages_fetched=0
                        )
                        return site_id
                    continue

                content_type = (result.content_type or "").lower()
                final_url = result.final_url or url

                if result.error:
                    if item.is_document:
                        self._record_file_link(
                            site_id,
                            url,
                            anchor_text=item.anchor_text,
                            scorer=scorer,
                            evidence=result.error,
                        )
                    self.db.upsert_page(
                        site_id,
                        url,
                        status_code=result.status_code or None,
                        content_type=content_type or None,
                        error=result.error,
                    )
                    if pages_fetched == 0 and url == seed_url:
                        self.db.update_site(
                            site_id,
                            status="failed",
                            error=result.error,
                            pages_fetched=0,
                        )
                        return site_id
                    continue

                if not same_site(seed_url, final_url) and not item.is_document:
                    err = f"redirect left seed domain -> {final_url}"
                    self.db.upsert_page(
                        site_id,
                        url,
                        status_code=result.status_code,
                        content_type=content_type,
                        error=err,
                    )
                    if pages_fetched == 0 and url == seed_url:
                        self.db.update_site(
                            site_id, status="failed", error=err, pages_fetched=0
                        )
                        return site_id
                    continue

                is_pdf = _response_is_pdf(content_type, final_url, result.body)
                if item.is_document and not is_pdf:
                    docs_checked += 1
                    self.db.upsert_page(
                        site_id,
                        final_url,
                        status_code=result.status_code,
                        content_type=content_type,
                    )
                    self._record_file_link(
                        site_id,
                        final_url,
                        anchor_text=item.anchor_text,
                        scorer=scorer,
                        evidence="file link did not return a PDF",
                    )
                    continue
                if is_pdf:
                    if docs_checked >= config.max_documents:
                        continue
                    docs_checked += 1
                    self.db.upsert_page(
                        site_id,
                        final_url,
                        status_code=result.status_code,
                        content_type=content_type,
                    )
                    pdf_started = time.monotonic()
                    self._handle_document(
                        site_id,
                        final_url,
                        result.body,
                        anchor_text=item.anchor_text,
                        scorer=scorer,
                    )
                    pdf_s = time.monotonic() - pdf_started
                    timing["pdf_checks"] += 1
                    timing["pdf_s"] += pdf_s
                    logger.info("pdf_check %.2fs %s", pdf_s, final_url[:120])
                    continue

                if "html" not in content_type and not result.body.lstrip().startswith(
                    (b"<!DOCTYPE", b"<html", b"<HTML")
                ):
                    self.db.upsert_page(
                        site_id,
                        final_url,
                        status_code=result.status_code,
                        content_type=content_type,
                        error="unsupported content type",
                    )
                    continue

                pages_fetched += 1
                self.db.upsert_page(
                    site_id,
                    final_url,
                    status_code=result.status_code,
                    content_type=content_type or "text/html",
                )
                self.db.update_site(site_id, pages_fetched=pages_fetched)

                try:
                    html = result.body.decode("utf-8", errors="replace")
                except Exception:
                    html = result.body.decode("latin-1", errors="replace")

                if page_looks_like_staff(html, final_url) and _finance_page(
                    scorer, final_url, html
                ):
                    self._handle_contacts(site_id, final_url, html)

                for link in extract_links(html, final_url):
                    file_link = is_file_url(link.url, scorer.document_extensions)
                    if (
                        not same_site(seed_url, link.url)
                        and not link.url.startswith("mailto:")
                        and not file_link
                    ):
                        continue
                    score = scorer.score(
                        url=link.url,
                        anchor_text=link.anchor_text,
                        context=link.context,
                    )
                    if score.uncertain and self.llm.enabled:
                        llm_started = time.monotonic()
                        llm_score = self.llm.rerank_link(
                            url=link.url,
                            anchor_text=link.anchor_text,
                            context=link.context,
                        )
                        llm_s = time.monotonic() - llm_started
                        timing["llm_calls"] += 1
                        timing["llm_s"] += llm_s
                        logger.info(
                            "llm_rerank %.2fs score_band=uncertain %s",
                            llm_s,
                            link.url[:120],
                        )
                        if llm_score:
                            score = self._merge_llm_score(score, llm_score)

                    if scorer.should_persist(score) or score.follow_score >= scorer.follow_threshold:
                        self.db.upsert_link(
                            site_id,
                            url=link.url,
                            source_page=final_url,
                            anchor_text=link.anchor_text,
                            link_type=score.link_type,
                            follow_score=score.follow_score,
                            result_score=score.result_score,
                            matched_keywords=score.matched_keywords,
                            reason=score.reason,
                        )

                    if link.url.startswith("mailto:"):
                        continue

                    is_doc = is_file_url(link.url, scorer.document_extensions)
                    if is_doc and score.link_type == "document":
                        if (
                            link.url not in seen
                            and score.result_score >= scorer.result_threshold * 0.5
                        ):
                            heapq.heappush(
                                frontier,
                                FrontierItem(
                                    priority=-score.result_score,
                                    depth=item.depth + 1,
                                    url=link.url,
                                    source_page=final_url,
                                    anchor_text=f"{link.anchor_text} {link.context}".strip()[:180],
                                    is_document=True,
                                    seq=next(order),
                                ),
                            )
                        continue

                    contact_page = score.follow_score >= _HIGH_FOLLOW and (
                        scorer.is_contact_page(link.url, link.anchor_text)
                    )
                    if scorer.should_follow(
                        score,
                        item.depth + 1,
                        _follow_limit(
                            score.follow_score,
                            config.max_depth,
                            contact_page=contact_page,
                        ),
                    ):
                        if link.url not in seen:
                            bonus = _CONTACT_PRIORITY_BONUS if contact_page else 0
                            heapq.heappush(
                                frontier,
                                FrontierItem(
                                    priority=-(score.follow_score + bonus),
                                    depth=item.depth + 1,
                                    url=link.url,
                                    source_page=final_url,
                                    anchor_text=link.anchor_text,
                                    seq=next(order),
                                ),
                            )

            total_s = time.monotonic() - crawl_started
            logger.info(
                "crawl summary seed=%s status_pages=%s fetches=%s fetch_s=%.1f "
                "llm_calls=%s llm_s=%.1f pdf_checks=%s pdf_s=%.1f total_s=%.1f",
                seed_url,
                pages_fetched,
                timing["fetches"],
                timing["fetch_s"],
                timing["llm_calls"],
                timing["llm_s"],
                timing["pdf_checks"],
                timing["pdf_s"],
                total_s,
            )
            self._backfill_documents(site_id)
            if pages_fetched == 0:
                self.db.update_site(
                    site_id,
                    status="failed",
                    error="no pages fetched",
                    pages_fetched=0,
                )
            else:
                self.db.update_site(
                    site_id, status="completed", pages_fetched=pages_fetched
                )
            return site_id
        except Exception as exc:
            logger.exception("crawl failed for %s", seed_url)
            self._backfill_documents(site_id)
            self.db.update_site(
                site_id,
                status="failed",
                error=str(exc),
                pages_fetched=pages_fetched,
            )
            return site_id

    def _backfill_documents(self, site_id: int) -> None:
        try:
            self.db.record_unchecked_documents(site_id)
        except Exception:
            logger.exception("document backfill failed for site %s", site_id)

    def _merge_llm_score(self, score: Any, llm_score: dict[str, Any]) -> Any:
        try:
            score.result_score = float(
                llm_score.get("result_score", score.result_score)
            )
            if llm_score.get("follow_score") is not None:
                score.follow_score = float(llm_score["follow_score"])
            link_type = llm_score.get("link_type")
            if link_type in {"document", "contact", "navigation"}:
                score.link_type = link_type
            reason = llm_score.get("reason")
            if reason:
                score.reason = f"{score.reason}; llm: {reason}"
            score.uncertain = False
        except (TypeError, ValueError):
            pass
        return score

    def _handle_contacts(self, site_id: int, url: str, html: str) -> None:
        contacts: list[ExtractedContact] = extract_contacts(html)
        page_text = page_contact_text(html)
        if self.llm.enabled and len(contacts) < 2:
            for item in self.llm.extract_contacts(page_text):
                email = _text_field(item.get("email"))
                phone = _text_field(item.get("phone"))
                if email and not _on_page(email, page_text):
                    email = None
                if phone and not _on_page(phone, page_text):
                    phone = None
                if not email and not phone:
                    continue
                contacts.append(
                    ExtractedContact(
                        name=_text_field(item.get("name")),
                        title=_text_field(item.get("title")),
                        email=email,
                        phone=phone,
                    )
                )
            contacts = clean_contacts(contacts)

        for contact in contacts:
            if not contact.email and not contact.phone:
                continue
            self.db.upsert_contact(
                site_id,
                source_url=url,
                name=contact.name,
                title=contact.title,
                email=contact.email,
                phone=contact.phone,
            )
            if contact.email or contact.title:
                self.db.upsert_link(
                    site_id,
                    url=url,
                    source_page=url,
                    anchor_text=contact.title or contact.name or "contact",
                    link_type="contact",
                    follow_score=20,
                    result_score=40,
                    matched_keywords=["contact"],
                    reason=CONTACT_PAGE_REASON,
                )

    def _record_file_link(
        self,
        site_id: int,
        url: str,
        *,
        anchor_text: str,
        scorer: KeywordScorer,
        evidence: str,
    ) -> None:
        score = scorer.score(url=url, anchor_text=anchor_text)
        blob = f"{url} {anchor_text}".lower()
        if "budget" in blob:
            claimed = "budget"
        elif any(token in blob for token in ("acfr", "cafr", "comprehensive financial")):
            claimed = "acfr"
        else:
            claimed = None
        self.db.upsert_link(
            site_id,
            url=url,
            source_page=None,
            anchor_text=anchor_text,
            link_type="document",
            follow_score=score.follow_score,
            result_score=max(score.result_score, 25),
            matched_keywords=score.matched_keywords,
            reason=score.reason,
        )
        self.db.upsert_document(
            site_id,
            url=url,
            claimed_type=claimed,
            fiscal_year=_year_hint(anchor_text),
            title=anchor_text or None,
            verdict="skipped",
            evidence=evidence,
        )

    def _handle_document(
        self,
        site_id: int,
        url: str,
        body: bytes,
        *,
        anchor_text: str,
        scorer: KeywordScorer,
    ) -> None:
        score = scorer.score(url=url, anchor_text=anchor_text)
        self.db.upsert_link(
            site_id,
            url=url,
            source_page=None,
            anchor_text=anchor_text,
            link_type="document",
            follow_score=score.follow_score,
            result_score=max(score.result_score, 25),
            matched_keywords=score.matched_keywords,
            reason=score.reason,
        )
        check = check_document(body, url=url, anchor_text=anchor_text)
        if self.llm.enabled and check.verdict in {"mismatch", "skipped"}:
            try:
                text = extract_pdf_text(body, max_pages=3)
            except Exception:
                text = ""
            if text:
                llm_result = self.llm.confirm_document(text, url)
                if llm_result:
                    check.verdict = llm_result.get("verdict", check.verdict)
                    check.claimed_type = llm_result.get(
                        "claimed_type", check.claimed_type
                    )
                    check.fiscal_year = llm_result.get("fiscal_year") or check.fiscal_year
                    check.title = llm_result.get("title") or check.title
                    check.evidence = llm_result.get("evidence") or check.evidence
        self.db.upsert_document(
            site_id,
            url=url,
            claimed_type=check.claimed_type,
            fiscal_year=check.fiscal_year,
            title=check.title,
            verdict=check.verdict,
            evidence=check.evidence,
        )
