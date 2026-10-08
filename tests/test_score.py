"""Keyword scorer tests."""

from app.score import KeywordScorer, is_file_url


def test_acfr_pdf_ranks_as_document():
    scorer = KeywordScorer()
    score = scorer.score(
        url="https://example.gov/finance/fy2024-acfr.pdf",
        anchor_text="FY2024 ACFR",
        context="Financial Reports",
    )
    assert score.link_type == "document"
    assert score.result_score >= scorer.result_threshold * 0.5
    assert any("acfr" in k for k in score.matched_keywords)


def test_negative_terms_from_ann_arbor_homepage():
    scorer = KeywordScorer()
    parking = scorer.score(
        url="https://www.a2gov.org/services/parking/",
        anchor_text="Parking Ticket",
    )
    trash = scorer.score(
        url="https://www.a2gov.org/trash-recycle/",
        anchor_text="Solid Waste Bill",
    )
    finance = scorer.score(
        url="https://www.a2gov.org/finance-and-administrative-services/",
        anchor_text="Finance and Administrative Services",
    )
    assert finance.follow_score > parking.follow_score
    assert finance.follow_score > trash.follow_score


def test_extra_keywords_raise_priority():
    scorer = KeywordScorer(extra_keywords=["Bond Report"])
    score = scorer.score(
        url="https://example.gov/docs/bond-report.pdf",
        anchor_text="Bond Report 2023",
    )
    assert "bond report" in score.matched_keywords


def test_finance_leadership_is_followed_ahead_of_a_plain_page():
    scorer = KeywordScorer()
    leadership = scorer.score(
        url="https://cfo.asu.edu/business-finance-leadership",
        anchor_text="Business and Finance Leadership",
    )
    plain = scorer.score(
        url="https://cfo.asu.edu/about",
        anchor_text="About",
    )
    assert leadership.follow_score >= 50
    assert leadership.follow_score > plain.follow_score


def test_mailto_is_contact():
    scorer = KeywordScorer()
    score = scorer.score(
        url="mailto:finance@example.gov",
        anchor_text="Email the Finance Director",
    )
    assert score.link_type == "contact"


def test_public_records_request_form_is_not_a_finance_document():
    scorer = KeywordScorer()
    score = scorer.score(
        url="https://www.asu.edu/police/documents/public_records_request.pdf",
        anchor_text="Public Records Request Form",
        context="Police Records",
    )
    assert score.link_type != "document"
    assert score.result_score < scorer.result_threshold * 0.5


def test_generic_pdf_without_finance_terms_is_not_a_document():
    scorer = KeywordScorer()
    score = scorer.score(
        url="https://example.gov/documents/handbook.pdf",
        anchor_text="Student Handbook",
    )
    assert score.link_type != "document"


def test_budget_hub_outranks_a_shuttle_page_on_the_same_host():
    scorer = KeywordScorer()
    budget = scorer.score(
        url="https://cfo.asu.edu/budget",
        anchor_text="Budget",
    )
    operating = scorer.score(
        url="https://cfo.asu.edu/annual-operating-budget",
        anchor_text="Annual Operating Budget",
    )
    shuttle = scorer.score(
        url="https://cfo.asu.edu/shuttles",
        anchor_text="Campus shuttles",
    )
    assert budget.follow_score >= 50
    assert operating.follow_score >= 50
    assert shuttle.follow_score < 50
    assert budget.follow_score > shuttle.follow_score


def test_sharepoint_budget_file_is_a_document():
    scorer = KeywordScorer()
    url = "https://azregents.sharepoint.com/:b:/s/ABORPublic/Committee/Board/abc"
    assert is_file_url(url, scorer.document_extensions)
    score = scorer.score(
        url=url,
        anchor_text="FY 2027",
        context="Recent Operating Budget Submissions",
    )
    assert score.link_type == "document"
    assert score.result_score >= scorer.result_threshold


def test_leadership_and_directory_links_are_contact_pages_but_programs_are_not():
    scorer = KeywordScorer()
    assert scorer.is_contact_page(
        "https://cfo.example.edu/business-finance-leadership",
        "Business and Finance Leadership",
    )
    assert scorer.is_contact_page("https://x.gov/staff", "Staff Directory")
    assert not scorer.is_contact_page(
        "https://cfo.example.edu/leadership-programs", "Leadership Programs"
    )
    assert not scorer.is_contact_page("https://x.gov/budget", "Budget")


def test_leadership_and_directory_links_are_contact_pages_but_programs_are_not():
    scorer = KeywordScorer()
    assert scorer.is_contact_page(
        "https://cfo.example.edu/business-finance-leadership",
        "Business and Finance Leadership",
    )
    assert scorer.is_contact_page("https://x.gov/staff", "Staff Directory")
    assert not scorer.is_contact_page(
        "https://cfo.example.edu/leadership-programs", "Leadership Programs"
    )
    assert not scorer.is_contact_page("https://x.gov/budget", "Budget")
