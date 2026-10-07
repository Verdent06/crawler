"""Optional live checks against the assignment sample sites."""

from __future__ import annotations

import pytest

from app.crawl import CrawlConfig, CrawlRunner
from app.db import Database
from app.llm import LlmClient

SAMPLES = [
    "https://www.a2gov.org/",
    "https://bozeman.net/",
    "https://asu.edu/",
    "https://boerneisd.net/",
]


@pytest.mark.live
@pytest.mark.parametrize("seed", SAMPLES)
def test_live_sample_does_not_crash(tmp_path, seed: str):
    db = Database(tmp_path / "live.db")
    runner = CrawlRunner(db, llm=LlmClient(base_url=""))
    try:
        site_id = runner.run(
            seed, CrawlConfig(max_pages=3, max_depth=1, max_documents=1)
        )
    finally:
        runner.close()
    site = db.get_site(site_id)
    assert site is not None
    assert site["status"] in {"completed", "failed"}
