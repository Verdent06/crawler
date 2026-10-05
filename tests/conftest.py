"""Shared fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCRAPER_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OLLAMA_URL", raising=False)
