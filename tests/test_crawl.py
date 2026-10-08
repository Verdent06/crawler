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


def test_offsite_budget_file_is_saved_when_found_at_the_page_cap(db: Database):
    pages = {
        "/": """
        <html><body>
          <a href="/annual-operating-budget">Annual Operating Budget</a>
          <a href="/parking/">Parking Ticket</a>
        </body></html>
        """,
        "/annual-operating-budget": """
        <html><body>
          <p><strong>Recent Operating Budget Submissions:</strong></p>
          <ul>
            <li><a href="https://files.example.com/:b:/s/Board/fy2027">FY 2027</a>; pp. 106-111.</li>
          </ul>
        </body></html>
        """,
        "/parking/": "<html><body>Parking</body></html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "example.gov":
            return httpx.Response(200, text="<html>sign in</html>")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        html = pages.get(request.url.path)
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
    runner = CrawlRunner(db, fetcher=fetcher, llm=LlmClient(base_url=""))
    site_id = runner.run(
        "https://example.gov/",
        CrawlConfig(max_pages=1, max_depth=2, max_documents=2),
    )
    runner.close()
    site = db.get_site(site_id)
    assert site is not None
    assert any("fy2027" in doc["url"] for doc in site["documents"])
    assert not any(link["url"].endswith("/parking/") for link in site["links"])


def _runner(db: Database, handler) -> CrawlRunner:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, follow_redirects=False)
    fetcher = Fetcher(
        client=client,
        sleep_fn=lambda _: None,
        resolve_check=lambda url: None,
    )
    return CrawlRunner(db, fetcher=fetcher, llm=LlmClient(base_url=""))


def test_ui_depth_reaches_offsite_budget_files_and_skips_records_form(db: Database):
    pages = {
        ("www.example.gov", "/"): """
        <html><body>
          <a href="https://cfo.example.gov/applicant">Jobs</a>
          <a href="https://www.example.gov/parking">Parking Ticket</a>
        </body></html>
        """,
        ("cfo.example.gov", "/applicant"): """
        <html><body>
          <a href="https://cfo.example.gov/shuttles">Campus shuttles</a>
          <a href="https://cfo.example.gov/budget">Budget</a>
        </body></html>
        """,
        ("cfo.example.gov", "/shuttles"): "<html><body>Campus shuttles</body></html>",
        ("cfo.example.gov", "/budget"): """
        <html><body>
          <a href="https://cfo.example.gov/annual-operating-budget">Annual Operating Budget</a>
          <a href="https://www.example.gov/police/documents/public_records_request.pdf">Public Records Request Form</a>
        </body></html>
        """,
        ("cfo.example.gov", "/annual-operating-budget"): """
        <html><body>
          <p><strong>Recent Operating Budget Submissions:</strong></p>
          <ul>
            <li><a href="https://files.example.com/:b:/s/Board/fy2027">FY 2027</a>; pp. 106-111.</li>
            <li><a href="https://files.example.com/:b:/s/Board/fy2026">FY 2026</a>; pp. 43-55.</li>
          </ul>
        </body></html>
        """,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "files.example.com":
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
            return httpx.Response(403, text="blocked")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        html = pages.get((host, request.url.path))
        if html is None:
            return httpx.Response(404, text="missing")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    runner = _runner(db, handler)
    site_id = runner.run(
        "https://www.example.gov/",
        CrawlConfig(max_pages=12, max_depth=2, max_documents=4),
    )
    runner.close()
    site = db.get_site(site_id)
    assert site is not None
    assert site["status"] == "completed"
    with db.connection() as conn:
        fetched = {
            row["url"]
            for row in conn.execute(
                "SELECT url FROM pages WHERE site_id = ?", (site_id,)
            )
        }
    assert any(url.endswith("/annual-operating-budget") for url in fetched)
    assert not any(url.endswith("/shuttles") for url in fetched)
    budgets = [doc for doc in site["documents"] if "fy202" in doc["url"]]
    assert {doc["url"].rsplit("/", 1)[-1] for doc in budgets} == {"fy2027", "fy2026"}
    assert all(doc["verdict"] == "skipped" for doc in budgets)
    assert all(doc["evidence"] == "blocked by robots.txt" for doc in budgets)
    assert all(doc["claimed_type"] == "budget" for doc in budgets)
    assert {doc["fiscal_year"] for doc in budgets} == {"2027", "2026"}
    assert not any("public_records_request" in doc["url"] for doc in site["documents"])


def test_pdf_checks_do_not_crowd_out_a_later_budget_page(db: Database):
    pages = {
        ("www.example.gov", "/"): """
        <html><body>
          <a href="https://cfo.example.gov/budget">Budget</a>
          <a href="https://www.example.gov/fy2024-acfr.pdf">Annual Comprehensive Financial Report ACFR</a>
        </body></html>
        """,
        ("cfo.example.gov", "/budget"): """
        <html><body>
          <a href="https://cfo.example.gov/annual-operating-budget">Annual Operating Budget</a>
        </body></html>
        """,
        ("cfo.example.gov", "/annual-operating-budget"): """
        <html><body>
          <p><strong>Recent Operating Budget Submissions:</strong></p>
          <ul>
            <li><a href="https://files.example.com/:b:/s/Board/fy2027">FY 2027</a></li>
          </ul>
        </body></html>
        """,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "files.example.com":
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
            return httpx.Response(403, text="blocked")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path.endswith(".pdf"):
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.4 blank",
            )
        html = pages.get((request.url.host, request.url.path))
        if html is None:
            return httpx.Response(404, text="missing")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    runner = _runner(db, handler)
    site_id = runner.run(
        "https://www.example.gov/",
        CrawlConfig(max_pages=1, max_depth=2, max_documents=1),
    )
    runner.close()
    site = db.get_site(site_id)
    assert site is not None
    with db.connection() as conn:
        fetched = {
            row["url"]
            for row in conn.execute(
                "SELECT url FROM pages WHERE site_id = ?", (site_id,)
            )
        }
    assert any(url.endswith("/annual-operating-budget") for url in fetched)
    skipped = next(doc for doc in site["documents"] if "fy2027" in doc["url"])
    assert skipped["verdict"] == "skipped"
    assert skipped["evidence"] == "blocked by robots.txt"


def test_document_cap_still_records_a_robots_blocked_budget_file(db: Database):
    pages = {
        "/": """
        <html><body>
          <a href="/finance/">Finance Department</a>
        </body></html>
        """,
        "/finance/": """
        <html><body>
          <a href="/fy2024-acfr.pdf">FY2024 ACFR</a>
          <p><strong>Recent Operating Budget Submissions:</strong></p>
          <ul>
            <li><a href="https://files.example.net/:b:/s/Board/fy2027">FY 2027</a></li>
          </ul>
        </body></html>
        """,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "files.example.net":
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
            return httpx.Response(403, text="blocked")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path.endswith(".pdf"):
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.4 blank",
            )
        html = pages.get(request.url.path)
        if html is None:
            return httpx.Response(404, text="missing")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    runner = _runner(db, handler)
    site_id = runner.run(
        "https://example.gov/",
        CrawlConfig(max_pages=5, max_depth=2, max_documents=1),
    )
    runner.close()
    site = db.get_site(site_id)
    assert site is not None
    skipped = next(doc for doc in site["documents"] if "fy2027" in doc["url"])
    assert skipped["verdict"] == "skipped"
    assert skipped["evidence"] == "blocked by robots.txt"
    assert any(doc["url"].endswith("fy2024-acfr.pdf") for doc in site["documents"])


def test_leadership_page_past_the_depth_limit_is_still_reached(db: Database):
    pages = {
        "/": '<html><body><a href="/government">Government</a></body></html>',
        "/government": '<html><body><a href="/finance-budget">Finance and Budget</a></body></html>',
        "/finance-budget": '<html><body><a href="/budget-reports">Annual Operating Budget</a></body></html>',
        "/budget-reports": """
        <html><body>
          <a href="/business-finance-leadership">Business and Finance Leadership</a>
        </body></html>
        """,
        "/business-finance-leadership": """
        <html><head><title>Business and Finance Leadership</title></head><body>
          <h1>Leadership</h1>
          <p>Jane Smith,<br>Chief Financial Officer</p>
          <p><a href="mailto:jsmith@example.gov">jsmith@example.gov</a> 555-111-2222</p>
        </body></html>
        """,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        html = pages.get(request.url.path)
        if html is None:
            return httpx.Response(404, text="missing")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    runner = _runner(db, handler)
    site_id = runner.run(
        "https://example.gov/",
        CrawlConfig(max_pages=12, max_depth=2, max_documents=4),
    )
    runner.close()
    site = db.get_site(site_id)
    assert site is not None
    assert any(c["email"] == "jsmith@example.gov" for c in site["contacts"])
    leadership = next(
        link for link in site["links"] if link["url"].endswith("/business-finance-leadership")
    )
    assert leadership["link_type"] == "contact"
