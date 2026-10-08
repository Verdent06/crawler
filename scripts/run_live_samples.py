#!/usr/bin/env python3
"""Crawl the four assignment sample sites and print a compact summary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.crawl import CrawlConfig, CrawlRunner
from app.db import Database
from app.llm import LlmClient
from app.resolve import install_dns_fallback_from_env

SAMPLES = [
    "https://www.a2gov.org/",
    "https://bozeman.net/",
    "https://asu.edu/",
    "https://boerneisd.net/",
]


def summarize(site: dict) -> dict:
    top_links = sorted(
        site.get("links") or [],
        key=lambda row: row.get("result_score") or 0,
        reverse=True,
    )[:8]
    return {
        "seed_url": site.get("seed_url"),
        "domain": site.get("domain"),
        "status": site.get("status"),
        "error": site.get("error"),
        "pages_fetched": site.get("pages_fetched"),
        "link_count": len(site.get("links") or []),
        "contact_count": len(site.get("contacts") or []),
        "document_count": len(site.get("documents") or []),
        "top_links": [
            {
                "url": row["url"],
                "type": row["link_type"],
                "score": row["result_score"],
                "anchor": row.get("anchor_text"),
                "reason": row.get("reason"),
            }
            for row in top_links
        ],
        "contacts": site.get("contacts") or [],
        "documents": site.get("documents") or [],
    }


def main() -> int:
    install_dns_fallback_from_env()
    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    db = Database(out_dir / "live_samples.db")
    config = CrawlConfig(max_pages=8, max_depth=2, max_documents=3)
    runner = CrawlRunner(db, llm=LlmClient(base_url=""))
    results = []
    try:
        for seed in SAMPLES:
            print(f"\n=== Crawling {seed} ===", flush=True)
            site_id = runner.run(seed, config)
            site = db.get_site(site_id)
            summary = summarize(site or {})
            results.append(summary)
            print(
                f"status={summary['status']} pages={summary['pages_fetched']} "
                f"links={summary['link_count']} contacts={summary['contact_count']} "
                f"docs={summary['document_count']} error={summary['error']}",
                flush=True,
            )
            for link in summary["top_links"][:5]:
                print(
                    f"  [{link['score']:.0f}] {link['type']}: {link['url'][:100]}",
                    flush=True,
                )
    finally:
        runner.close()

    path = out_dir / "live_results.json"
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
