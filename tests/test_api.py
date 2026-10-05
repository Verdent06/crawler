"""API flow tests with mocked crawler."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPER_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    # Re-import app with new DB path
    import importlib
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c, main


def test_health(client):
    c, _ = client
    resp = c.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["llm_enabled"] is False


def test_scrape_rejects_localhost(client):
    c, _ = client
    resp = c.post("/scrape", json={"url": "http://127.0.0.1/"})
    assert resp.status_code == 400


def test_scrape_starts_background_job(client):
    c, main = client

    def fake_run(self, seed_url, config=None, site_id=None):
        main.db.update_site(site_id, status="completed", pages_fetched=1)
        main.db.upsert_link(
            site_id,
            url=f"{seed_url}finance/budget.pdf",
            source_page=seed_url,
            anchor_text="Budget",
            link_type="document",
            follow_score=10,
            result_score=40,
            matched_keywords=["budget"],
            reason="mock",
        )
        return site_id

    with patch.object(main.CrawlRunner, "run", fake_run):
        with patch("app.fetch.assert_public_url", lambda url: None):
            with patch("app.main.assert_public_url", lambda url: None):
                resp = c.post(
                    "/scrape",
                    json={"url": "https://example.gov/", "max_pages": 5},
                )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    site_id = data["site_id"]
    # BackgroundTasks run eagerly in TestClient
    site = c.get(f"/sites/{site_id}").json()
    assert site["status"] == "completed"
    assert len(site["links"]) == 1

    links = c.get("/links", params={"q": "budget"}).json()
    assert links["count"] >= 1
