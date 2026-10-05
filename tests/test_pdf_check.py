"""PDF confirmation tests."""

from __future__ import annotations

import io
from unittest.mock import patch

from pypdf import PdfWriter

from app.pdf_check import check_document


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_confirms_acfr_language():
    with patch(
        "app.pdf_check.extract_pdf_text",
        return_value=(
            "City of Example Annual Comprehensive Financial Report "
            "for the fiscal year ended June 30, 2024"
        ),
    ):
        result = check_document(
            b"%PDF-fake",
            url="https://example.gov/fy2024-acfr.pdf",
            anchor_text="FY2024 ACFR",
        )
    assert result.claimed_type == "acfr"
    assert result.verdict == "confirmed"
    assert result.fiscal_year == "2024"


def test_mismatch_when_claim_does_not_match_body():
    with patch(
        "app.pdf_check.extract_pdf_text",
        return_value="Adopted Operating Budget Fiscal Year 2023",
    ):
        result = check_document(
            b"%PDF-fake",
            url="https://example.gov/fy2024-acfr.pdf",
            anchor_text="ACFR",
        )
    assert result.verdict == "mismatch"


def test_empty_pdf_marked_unreadable():
    result = check_document(
        _blank_pdf(),
        url="https://example.gov/scan.pdf",
        anchor_text="Budget",
    )
    assert result.verdict == "unreadable"
    assert result.claimed_type == "budget"
