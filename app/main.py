"""FastAPI entrypoint for the high-value link scraper."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl

from app import __version__
from app.crawl import CrawlConfig, CrawlRunner
from app.db import Database
from app.fetch import UnsafeURLError, assert_public_url, registrable_domain
from app.llm import LlamaClient

DATA_DIR = Path(
    os.environ.get(
        "SCRAPER_DATA_DIR", Path(__file__).resolve().parent.parent / "data"
    )
)
DB_PATH = Path(os.environ.get("SCRAPER_DB_PATH", DATA_DIR / "scraper.db"))

app = FastAPI(
    title="High-Value Link Scraper",
    description=(
        "Find finance contacts and ACFR/budget documents on public institution sites."
    ),
    version=__version__,
)

db = Database(DB_PATH)
_jobs_lock = threading.Lock()
_running_sites: set[int] = set()


class ScrapeRequest(BaseModel):
    url: HttpUrl
    keywords: list[str] | None = None
    max_pages: int = Field(default=25, ge=1, le=100)
    max_depth: int = Field(default=3, ge=0, le=6)
    max_documents: int = Field(default=8, ge=0, le=30)


@app.get("/health")
def health() -> dict[str, Any]:
    llm = LlamaClient()
    return {
        "status": "ok",
        "version": __version__,
        "llm_enabled": llm.enabled,
        "llm_available": llm.available() if llm.enabled else False,
        "db_path": str(DB_PATH),
    }


@app.post("/scrape")
def scrape(req: ScrapeRequest, background: BackgroundTasks) -> dict[str, Any]:
    seed = str(req.url)
    try:
        assert_public_url(seed)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    site_domain = registrable_domain(seed)
    with _jobs_lock:
        existing_id = db.get_site_id_by_seed(seed)
        if existing_id is not None and existing_id in _running_sites:
            return {
                "site_id": existing_id,
                "status": "running",
                "domain": site_domain,
            }
        site_id = db.create_or_reset_site(seed, site_domain)
        _running_sites.add(site_id)

    config = CrawlConfig(
        max_pages=req.max_pages,
        max_depth=req.max_depth,
        max_documents=req.max_documents,
        extra_keywords=req.keywords,
    )

    def job() -> None:
        runner = CrawlRunner(db)
        try:
            runner.run(seed, config, site_id=site_id)
        finally:
            runner.close()
            with _jobs_lock:
                _running_sites.discard(site_id)

    background.add_task(job)
    return {"site_id": site_id, "status": "running", "domain": site_domain}


@app.get("/sites")
def list_sites() -> list[dict[str, Any]]:
    return db.list_sites()


@app.get("/sites/{site_id}")
def get_site(site_id: int) -> dict[str, Any]:
    site = db.get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="site not found")
    with _jobs_lock:
        if site_id in _running_sites:
            site["status"] = "running"
    return site


@app.get("/links")
def list_links(
    domain: str | None = None,
    type: str | None = Query(default=None, alias="type"),
    min_score: float | None = None,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    rows = db.query_links(
        domain=domain,
        link_type=type,
        min_score=min_score,
        q=q,
        limit=limit,
        offset=offset,
    )
    return {"count": len(rows), "items": rows, "limit": limit, "offset": offset}
