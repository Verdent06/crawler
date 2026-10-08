"""Replay the UI's crawl settings against recorded sites and compare to ground truth."""

from __future__ import annotations

import pytest

from app.db import Database
from tests.ground_truth import config_for, diff_site, fetched_pages, load_expected
from tests.replay import replay_site

SITES = ["asu", "a2gov"]


def _run(db: Database, name: str) -> tuple[dict, set[str], dict]:
    expected = load_expected(name)
    site = replay_site(db, expected["fixture"], expected["seed"], config_for(expected))
    return site, fetched_pages(db, site["id"]), expected


@pytest.mark.parametrize("name", SITES)
def test_crawl_matches_ground_truth(db: Database, name: str):
    site, pages, expected = _run(db, name)
    assert site["status"] == "completed"
    assert diff_site(site, pages, expected) == []


def test_asu_leadership_page_is_reached_under_ui_limits(db: Database):
    site, pages, _ = _run(db, "asu")
    assert "https://cfo.asu.edu/business-finance-leadership" in pages
    assert len(site["contacts"]) >= 7
    assert all(c["email"] or c["phone"] for c in site["contacts"])


def test_asu_rescrape_does_not_duplicate_rows(db: Database):
    expected = load_expected("asu")
    first = replay_site(db, "asu", expected["seed"], config_for(expected))
    second = replay_site(db, "asu", expected["seed"], config_for(expected))
    assert first["id"] == second["id"]
    assert len(first["contacts"]) == len(second["contacts"])
    assert len(first["documents"]) == len(second["documents"])
    assert len(first["links"]) == len(second["links"])


def test_asu_sharepoint_budgets_are_recorded_as_robots_blocked(db: Database):
    site, _, _ = _run(db, "asu")
    budgets = [d for d in site["documents"] if "sharepoint.com" in d["url"]]
    assert {d["fiscal_year"] for d in budgets} == {"2025", "2026", "2027"}
    assert all(d["evidence"] == "blocked by robots.txt" for d in budgets)
