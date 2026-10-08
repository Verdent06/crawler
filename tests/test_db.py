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


def test_contact_page_link_is_not_downgraded_by_a_later_navigation_row(db: Database):
    from app.db import CONTACT_PAGE_REASON

    site_id = db.create_or_reset_site("https://example.gov/", "example.gov")
    url = "https://example.gov/leadership"
    common = dict(source_page=None, anchor_text="x", follow_score=1, result_score=1, matched_keywords=[])
    db.upsert_link(site_id, url=url, link_type="contact", reason=CONTACT_PAGE_REASON, **common)
    db.upsert_link(site_id, url=url, link_type="navigation", reason="matched: finance", **common)
    site = db.get_site(site_id)
    assert [link["link_type"] for link in site["links"]] == ["contact"]


def test_contact_page_link_is_not_downgraded_by_a_later_navigation_row(db: Database):
    from app.db import CONTACT_PAGE_REASON

    site_id = db.create_or_reset_site("https://example.gov/", "example.gov")
    url = "https://example.gov/leadership"
    common = dict(source_page=None, anchor_text="x", follow_score=1, result_score=1, matched_keywords=[])
    db.upsert_link(site_id, url=url, link_type="contact", reason=CONTACT_PAGE_REASON, **common)
    db.upsert_link(site_id, url=url, link_type="navigation", reason="matched: finance", **common)
    site = db.get_site(site_id)
    assert [link["link_type"] for link in site["links"]] == ["contact"]
