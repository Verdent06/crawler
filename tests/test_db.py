"""Storage upsert tests."""

from app.db import Database


def test_rescrape_updates_instead_of_duplicating(db: Database):
    site_id = db.create_or_reset_site("https://example.gov/", "example.gov")
    db.upsert_link(
        site_id,
        url="https://example.gov/budget.pdf",
        source_page="https://example.gov/",
        anchor_text="Budget",
        link_type="document",
        follow_score=10,
        result_score=40,
        matched_keywords=["budget"],
        reason="test",
    )
    site_id2 = db.create_or_reset_site("https://example.gov/", "example.gov")
    assert site_id2 == site_id
    site = db.get_site(site_id)
    assert site is not None
    assert site["links"] == []
    db.upsert_link(
        site_id,
        url="https://example.gov/budget.pdf",
        source_page="https://example.gov/",
        anchor_text="Budget FY24",
        link_type="document",
        follow_score=12,
        result_score=45,
        matched_keywords=["budget"],
        reason="test2",
    )
    db.upsert_link(
        site_id,
        url="https://example.gov/budget.pdf",
        source_page="https://example.gov/finance",
        anchor_text="Budget FY24 updated",
        link_type="document",
        follow_score=12,
        result_score=50,
        matched_keywords=["budget", "fy"],
        reason="updated",
    )
    site = db.get_site(site_id)
    assert site is not None
    assert len(site["links"]) == 1
    assert site["links"][0]["result_score"] == 50
    assert site["links"][0]["anchor_text"] == "Budget FY24 updated"
