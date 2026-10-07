"""Crawl runner tests with a mocked HTTP transport."""

from __future__ import annotations

import httpx

from app.crawl import CrawlConfig, CrawlRunner
from app.db import Database
from app.fetch import Fetcher
from app.llm import LlmClient


SITE = {
    "/": """
    <html><body>
      <a href="/finance/">Finance Department</a>
      <a href="/parking/">Parking Ticket</a>
    </body></html>
    """,
    "/finance/": """
    <html><body>
      <h1>Finance</h1>
      <a href="/finance/fy2024-budget.pdf">Adopted Budget FY2024</a>
      <p>Jane Smith, Finance Director —
         <a href="mailto:jane@example.gov">jane@example.gov</a>
         (555) 111-2222</p>
    </body></html>
    """,
    "/parking/": "<html><body>Parking</body></html>",
}


def test_crawl_finds_budget_link_and_contact(db: Database):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path.endswith(".pdf"):
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.4 blank",
            )
        html = SITE.get(path)
        if html is None:
            return httpx.Response(404, text="missing")
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text=html
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, follow_redirects=False)
    fetcher = Fetcher(
        client=client,
        sleep_fn=lambda _: None,
        resolve_check=lambda url: None,
    )
    runner = CrawlRunner(
        db, fetcher=fetcher, llm=LlmClient(base_url="")
    )
    site_id = runner.run(
        "https://example.gov/",
        CrawlConfig(max_pages=10, max_depth=2, max_documents=2),
    )
    runner.close()
    site = db.get_site(site_id)
    assert site is not None
    assert site["status"] == "completed"
    urls = {link["url"] for link in site["links"]}
    assert any("budget.pdf" in u for u in urls)
    assert any(link["link_type"] == "document" for link in site["links"])
    assert site["contacts"]
    assert any(
        (c.get("email") == "jane@example.gov") or (c.get("title") and "finance" in c["title"].lower())
        for c in site["contacts"]
    )


class _DropMiddleLinks:
    enabled = True

    def __init__(self) -> None:
        self.calls = 0

    def rerank_link(self, **kwargs: object) -> dict[str, object]:
        self.calls += 1
        return {
            "link_type": "navigation",
            "result_score": 5,
            "follow_score": 1,
            "reason": "not a finance destination",
        }

    def extract_contacts(self, text: str) -> list[dict[str, object]]:
        return []

    def confirm_document(self, text: str, url: str) -> None:
        return None


def test_model_can_drop_an_uncertain_link(db: Database):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        html = SITE.get(path)
        if html is None:
            return httpx.Response(404, text="missing")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, follow_redirects=False)
    fetcher = Fetcher(
        client=client,
        sleep_fn=lambda _: None,
        resolve_check=lambda url: None,
    )
    model = _DropMiddleLinks()
    runner = CrawlRunner(db, fetcher=fetcher, llm=model)  # type: ignore[arg-type]
    site_id = runner.run(
        "https://example.gov/",
        CrawlConfig(max_pages=5, max_depth=2, max_documents=2),
    )
    runner.close()
    site = db.get_site(site_id)
    assert site is not None
    assert model.calls >= 1
    assert site["pages_fetched"] == 1
    assert not any("budget.pdf" in link["url"] for link in site["links"])
