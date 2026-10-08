"""Seed URL normalization tests."""

import pytest
from fastapi.testclient import TestClient

from app.urls import normalize_seed_url


def test_bare_domain_gets_https():
    assert normalize_seed_url("asu.edu") == "https://asu.edu"
    assert normalize_seed_url("  www.a2gov.org  ") == "https://www.a2gov.org"


def test_keeps_explicit_scheme():
    assert normalize_seed_url("http://example.gov/finance") == "http://example.gov/finance"
    assert (
        normalize_seed_url("https://boerneisd.net/") == "https://boerneisd.net/"
    )


def test_rejects_empty_and_bad_schemes():
    with pytest.raises(ValueError):
        normalize_seed_url("")
    with pytest.raises(ValueError):
        normalize_seed_url("ftp://example.gov")
    with pytest.raises(ValueError):
        normalize_seed_url("not a url")


def test_api_accepts_bare_domain(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPER_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    import importlib
    import app.main as main

    importlib.reload(main)

    with TestClient(main.app) as client:
        with monkeypatch.context() as m:
            m.setattr(main, "assert_public_url", lambda url: None)
            m.setattr(main.CrawlRunner, "run", lambda self, *a, **k: 1)
            resp = client.post("/scrape", json={"url": "asu.edu", "max_pages": 1})
    assert resp.status_code == 200
    assert resp.json()["domain"] == "asu.edu"
