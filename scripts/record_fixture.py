#!/usr/bin/env python3
"""Crawl a live site once and save every HTTP response as a trimmed offline fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import httpx
from bs4 import BeautifulSoup, Comment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.crawl import CrawlConfig, CrawlRunner
from app.db import Database
from app.fetch import DEFAULT_TIMEOUT, USER_AGENT, Fetcher
from app.llm import LlmClient
from app.resolve import install_dns_fallback_from_env
from app.pdf_check import extract_pdf_text

DROP_TAGS = ("style", "svg", "link", "meta", "img", "picture", "source", "video", "audio")
KEEP_ATTRS = {"href", "title", "data-cfemail"}
PDF_TEXT_CHARS = 700


def trim_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(string=lambda text: isinstance(text, Comment)):
        node.extract()
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    for script in soup.find_all("script"):
        text = script.string or ""
        if "mailto:" not in text.lower() and "tel:" not in text.lower():
            script.decompose()
            continue
        script.attrs = {}
    for tag in soup.find_all(True):
        tag.attrs = {k: v for k, v in tag.attrs.items() if k in KEEP_ATTRS}
    return str(soup)


class RecordingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.inner = httpx.HTTPTransport()
        self.records: dict[str, dict] = {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.inner.handle_request(request)
        response.read()
        body = response.content
        headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
        }
        self.records[str(request.url)] = {
            "status": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "location": response.headers.get("location"),
            "body": body,
        }
        return httpx.Response(
            response.status_code, headers=headers, content=body, request=request
        )

    def close(self) -> None:
        self.inner.close()


def entry_for(url: str, record: dict, directory: Path) -> dict:
    entry = {
        "status": record["status"],
        "content_type": record["content_type"],
        "location": record["location"],
    }
    body: bytes = record["body"]
    ctype = record["content_type"].lower()
    if record["status"] != 200 or not body:
        return entry
    if body[:5] == b"%PDF-" or "pdf" in ctype:
        try:
            entry["pdf_text"] = " ".join(extract_pdf_text(body, 3).split())[:PDF_TEXT_CHARS]
        except Exception:
            entry["pdf_text"] = ""
        return entry
    if "html" in ctype or body.lstrip()[:5].lower() in {b"<!doc", b"<html"}:
        text = trim_html(body.decode("utf-8", errors="replace"))
    elif "text" in ctype or url.endswith("/robots.txt"):
        text = body.decode("utf-8", errors="replace")
    else:
        return entry
    name = hashlib.sha1(text.encode()).hexdigest()[:12] + ".html"
    (directory / name).write_text(text, encoding="utf-8")
    entry["file"] = name
    return entry


def main() -> int:
    install_dns_fallback_from_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("seed")
    parser.add_argument("name")
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--max-documents", type=int, default=8)
    args = parser.parse_args()

    transport = RecordingTransport()
    client = httpx.Client(
        transport=transport,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        follow_redirects=False,
        timeout=DEFAULT_TIMEOUT,
    )
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "record.db")
        runner = CrawlRunner(
            db, fetcher=Fetcher(client=client), llm=LlmClient(base_url="")
        )
        site_id = runner.run(
            args.seed,
            CrawlConfig(
                max_pages=args.max_pages,
                max_depth=args.max_depth,
                max_documents=args.max_documents,
            ),
        )
        site = db.get_site(site_id)

    directory = ROOT / "tests" / "fixtures" / "sites" / args.name
    directory.mkdir(parents=True, exist_ok=True)
    index = {url: entry_for(url, rec, directory) for url, rec in transport.records.items()}
    (directory / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"recorded {len(index)} responses into {directory}")
    print(
        f"contacts={len(site['contacts'])} documents={len(site['documents'])} "
        f"links={len(site['links'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
