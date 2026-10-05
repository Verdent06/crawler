"""Bounded, score-ranked crawl for finance documents and contacts."""

from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.db import Database
from app.extract import (
    ExtractedContact,
    extract_contacts,
    extract_links,
    page_looks_like_staff,
)
from app.fetch import Fetcher, UnsafeURLError, registrable_domain, same_site
from app.llm import LlamaClient
from app.pdf_check import check_document, extract_pdf_text
from app.score import KeywordScorer

logger = logging.getLogger(__name__)


@dataclass(order=True)
class FrontierItem:
    priority: float
    depth: int
    url: str = field(compare=False)
    source_page: str | None = field(default=None, compare=False)
    anchor_text: str = field(default="", compare=False)


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
        llm: LlamaClient | None = None,
    ) -> None:
        self.db = db
        self.fetcher = fetcher or Fetcher()
        self._owns_fetcher = fetcher is None
        self.llm = llm or LlamaClient()
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
        heapq.heappush(frontier, FrontierItem(priority=0.0, depth=0, url=seed_url))
        seen: set[str] = set()
        pages_fetched = 0
        docs_checked = 0

        try:
            while frontier and pages_fetched < config.max_pages:
                item = heapq.heappop(frontier)
                url = item.url
                if url in seen:
                    continue
                if urlparse(url).scheme == "mailto":
                    continue
                if not same_site(seed_url, url):
                    continue
                seen.add(url)

                try:
                    result = self.fetcher.fetch(url)
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

                if not same_site(seed_url, final_url):
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

                is_pdf = "pdf" in content_type or final_url.lower().endswith(".pdf")
                if is_pdf:
                    if docs_checked >= config.max_documents:
                        continue
                    docs_checked += 1
                    pages_fetched += 1
                    self.db.upsert_page(
                        site_id,
                        final_url,
                        status_code=result.status_code,
                        content_type=content_type,
                    )
                    self.db.update_site(site_id, pages_fetched=pages_fetched)
                    self._handle_document(
                        site_id,
                        final_url,
                        result.body,
                        anchor_text=item.anchor_text,
                        scorer=scorer,
                    )
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

                if page_looks_like_staff(html, final_url):
                    self._handle_contacts(site_id, final_url, html)

                for link in extract_links(html, final_url):
                    if not same_site(seed_url, link.url) and not link.url.startswith(
                        "mailto:"
                    ):
                        continue
                    score = scorer.score(
                        url=link.url,
                        anchor_text=link.anchor_text,
                        context=link.context,
                    )
                    if score.uncertain and self.llm.enabled:
                        llm_score = self.llm.rerank_link(
                            url=link.url,
                            anchor_text=link.anchor_text,
                            context=link.context,
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

                    path = urlparse(link.url).path.lower()
                    is_doc = any(
                        path.endswith(ext) for ext in scorer.document_extensions
                    )
                    if is_doc and score.link_type == "document":
                        if (
                            link.url not in seen
                            and docs_checked < config.max_documents
                            and score.result_score >= scorer.result_threshold * 0.5
                        ):
                            heapq.heappush(
                                frontier,
                                FrontierItem(
                                    priority=-score.result_score,
                                    depth=item.depth + 1,
                                    url=link.url,
                                    source_page=final_url,
                                    anchor_text=link.anchor_text,
                                ),
                            )
                        continue

                    if scorer.should_follow(score, item.depth + 1, config.max_depth):
                        if link.url not in seen:
                            heapq.heappush(
                                frontier,
                                FrontierItem(
                                    priority=-score.follow_score,
                                    depth=item.depth + 1,
                                    url=link.url,
                                    source_page=final_url,
                                    anchor_text=link.anchor_text,
                                ),
                            )

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
            self.db.update_site(
                site_id,
                status="failed",
                error=str(exc),
                pages_fetched=pages_fetched,
            )
            return site_id

    def _merge_llm_score(self, score: Any, llm_score: dict[str, Any]) -> Any:
        try:
            score.result_score = float(
                llm_score.get("result_score", score.result_score)
            )
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
        if self.llm.enabled and len(contacts) < 2:
            from bs4 import BeautifulSoup

            text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
            for item in self.llm.extract_contacts(text):
                contacts.append(
                    ExtractedContact(
                        name=item.get("name"),
                        title=item.get("title"),
                        email=item.get("email"),
                        phone=item.get("phone"),
                    )
                )

        for contact in contacts:
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
                    reason="extracted finance contact from page",
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
