"""FastAPI entrypoint for the high-value link scraper."""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)
logging.getLogger("app.crawl").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

from app import __version__
from app.crawl import CrawlConfig, CrawlRunner
from app.db import Database
from app.fetch import UnsafeURLError, assert_public_url, registrable_domain
from app.llm import LlmClient
from app.resolve import install_dns_fallback_from_env
from app.score import is_file_link
from app.security import (
    DEFAULT_MAX_CONCURRENT_SCRAPES,
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    SlidingWindowLimiter,
    api_key_matches,
    env_int,
)
from app.urls import normalize_seed_url

DATA_DIR = Path(
    os.environ.get(
        "SCRAPER_DATA_DIR", Path(__file__).resolve().parent.parent / "data"
    )
)
DB_PATH = Path(os.environ.get("SCRAPER_DB_PATH", DATA_DIR / "scraper.db"))



@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    install_dns_fallback_from_env()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="High-Value Link Scraper",
    description=(
        "Find finance contacts and ACFR/budget documents on public institution sites. "
        "The React UI lives in /frontend and proxies to this API during local development."
    ),
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = Database(DB_PATH)
_jobs_lock = threading.Lock()
_running_sites: set[int] = set()
rate_limiter = SlidingWindowLimiter(
    env_int("SCRAPE_RATE_LIMIT_PER_MINUTE", DEFAULT_RATE_LIMIT_PER_MINUTE)
)
max_concurrent_scrapes = env_int("MAX_CONCURRENT_SCRAPES", DEFAULT_MAX_CONCURRENT_SCRAPES)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("SCRAPER_API_KEY", "")
    if expected and not api_key_matches(expected, x_api_key):
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


def enforce_rate_limit(request: Request, _: None = Depends(require_api_key)) -> None:
    client = request.client.host if request.client else "unknown"
    wait = rate_limiter.retry_after(client)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded; retry in {wait}s",
            headers={"Retry-After": str(wait)},
        )


def reject_if_at_capacity() -> None:
    if max_concurrent_scrapes and len(_running_sites) >= max_concurrent_scrapes:
        raise HTTPException(
            status_code=503,
            detail=f"server is busy: {max_concurrent_scrapes} crawls already running; try again shortly",
            headers={"Retry-After": "30"},
        )


class ScrapeRequest(BaseModel):
    url: str
    keywords: list[str] | None = None
    max_pages: int = Field(default=25, ge=1, le=100)
    max_depth: int = Field(default=3, ge=0, le=6)
    max_documents: int = Field(default=8, ge=0, le=30)

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        try:
            return normalize_seed_url(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


@app.get("/health")
def health() -> dict[str, Any]:
    llm = LlmClient()
    return {
        "status": "ok",
        "version": __version__,
        "llm_enabled": llm.enabled,
        "llm_available": llm.available() if llm.enabled else False,
        "db_path": str(DB_PATH),
    }


@app.post("/scrape", dependencies=[Depends(enforce_rate_limit)])
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
        reject_if_at_capacity()
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


def present_site(site: dict[str, Any]) -> dict[str, Any]:
    links = [row for row in site["links"] if not is_file_link(row)]
    return {**site, "links": links}


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
    return present_site(site)


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
