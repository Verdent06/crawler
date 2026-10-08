#!/usr/bin/env python3
"""Crawl real sites with the UI settings and print a diff against tests/fixtures/*_expected.json."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.crawl import CrawlRunner
from app.db import Database
from app.llm import LlmClient
from app.resolve import install_dns_fallback_from_env
from tests.ground_truth import config_for, diff_site, fetched_pages, load_expected


def diff_live(name: str, *, use_llm: bool = False) -> list[str]:
    install_dns_fallback_from_env()
    expected = load_expected(name)
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "live.db")
        runner = CrawlRunner(db, llm=LlmClient() if use_llm else LlmClient(base_url=""))
        try:
            site_id = runner.run(expected["seed"], config_for(expected))
        finally:
            runner.close()
        site = db.get_site(site_id)
        assert site is not None
        return diff_site(site, fetched_pages(db, site_id), expected)


def main() -> int:
    names = [arg for arg in sys.argv[1:] if not arg.startswith("--")] or ["asu", "a2gov"]
    use_llm = "--llm" in sys.argv
    failed = 0
    for name in names:
        problems = diff_live(name, use_llm=use_llm)
        print(f"== {name}: {'MATCH' if not problems else f'{len(problems)} differences'}")
        for problem in problems:
            print("  -", problem)
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
