#!/usr/bin/env python3
"""Crawl a site with UI-like settings, print what was stored, and capture raw pages."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.crawl import CrawlConfig, CrawlRunner
from app.db import Database
from app.fetch import Fetcher
from app.llm import LlmClient
from app.resolve import install_dns_fallback_from_env


class RecordingFetcher(Fetcher):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.captured: dict[str, tuple[str, bytes, str]] = {}
        self.order: list[str] = []

    def fetch(self, url, *, respect_robots=True):
        result = super().fetch(url, respect_robots=respect_robots)
        self.order.append(url)
        if not result.error:
            self.captured[url] = (result.content_type, result.body, result.final_url)
        return result


def snapshot(site: dict, db: Database, site_id: int, order: list[str]) -> dict:
    with db.connection() as conn:
        pages = [
            row["url"]
            for row in conn.execute(
                "SELECT url FROM pages WHERE site_id = ? ORDER BY id", (site_id,)
            )
        ]
    return {
        "seed_url": site["seed_url"],
        "pages_fetched": site["pages_fetched"],
        "fetch_order": order,
        "pages": pages,
        "links": [
            {
                "url": r["url"],
                "type": r["link_type"],
                "follow": r["follow_score"],
                "result": r["result_score"],
            }
            for r in site["links"]
        ],
        "contacts": [
            {k: c.get(k) for k in ("name", "title", "email", "phone", "source_url")}
            for c in site["contacts"]
        ],
        "documents": [
            {
                k: d.get(k)
                for k in ("url", "claimed_type", "fiscal_year", "verdict", "evidence")
            }
            for d in site["documents"]
        ],
    }


def main() -> int:
    install_dns_fallback_from_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("seed")
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--max-documents", type=int, default=8)
    parser.add_argument("--out", type=Path, help="write snapshot JSON here")
    parser.add_argument("--capture", type=Path, help="save raw bodies into this dir")
    parser.add_argument("--llm", action="store_true", help="use env-configured LLM")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "dump.db")
        fetcher = RecordingFetcher()
        llm = LlmClient() if args.llm else LlmClient(base_url="")
        runner = CrawlRunner(db, fetcher=fetcher, llm=llm)
        config = CrawlConfig(
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            max_documents=args.max_documents,
        )
        try:
            site_id = runner.run(args.seed, config)
        finally:
            runner.close()
            fetcher.close()
        site = db.get_site(site_id)
        snap = snapshot(site, db, site_id, fetcher.order)

    print(f"status={site['status']} pages={snap['pages_fetched']} llm={llm.enabled}")
    print("fetch order:")
    for url in snap["fetch_order"]:
        print("  ", url[:120])
    print(f"contacts ({len(snap['contacts'])}):")
    for c in snap["contacts"]:
        print("  ", c)
    print(f"documents ({len(snap['documents'])}):")
    for d in snap["documents"]:
        print("  ", d["verdict"], d["claimed_type"], d["fiscal_year"], d["url"][:100])
    print(f"links: {len(snap['links'])}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    if args.capture:
        args.capture.mkdir(parents=True, exist_ok=True)
        index = {}
        for i, (url, (ctype, body, final)) in enumerate(fetcher.captured.items()):
            name = f"{i:03d}.bin"
            (args.capture / name).write_bytes(body[:400_000])
            index[url] = {"file": name, "content_type": ctype, "final_url": final}
        (args.capture / "index.json").write_text(json.dumps(index, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
