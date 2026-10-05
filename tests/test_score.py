"""Keyword scorer tests."""

from app.score import KeywordScorer


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


def test_mailto_is_contact():
    scorer = KeywordScorer()
    score = scorer.score(
        url="mailto:finance@example.gov",
        anchor_text="Email the Finance Director",
    )
    assert score.link_type == "contact"
