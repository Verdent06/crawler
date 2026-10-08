"""Production static/SPA serving must not shadow API routes."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client_with_ui(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><body>Link Scraper UI</body></html>",
        encoding="utf-8",
    )
    (assets / "app.js").write_text("window.ui=true;", encoding="utf-8")
    monkeypatch.setenv("SCRAPER_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def test_ui_root_and_asset(client_with_ui):
    root = client_with_ui.get("/")
    assert root.status_code == 200
    assert "Link Scraper UI" in root.text
    asset = client_with_ui.get("/assets/app.js")
    assert asset.status_code == 200
    assert "window.ui=true" in asset.text


def test_health_not_shadowed_by_spa(client_with_ui):
    resp = client_with_ui.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.headers["content-type"].startswith("application/json")


def test_api_prefix_not_shadowed_by_spa(client_with_ui):
    health = client_with_ui.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    sites = client_with_ui.get("/api/sites")
    assert sites.status_code == 200
    assert sites.json() == []


def test_openapi_not_shadowed_by_spa(client_with_ui):
    resp = client_with_ui.get("/openapi.json")
    assert resp.status_code == 200
    assert "openapi" in resp.json()
