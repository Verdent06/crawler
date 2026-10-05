"""Confirm candidate finance PDFs from the first few pages only."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from pypdf import PdfReader

YEAR_RE = re.compile(r"\b(?:fy\s*)?(20\d{2})\b", re.I)
ACFR_RE = re.compile(
    r"annual\s+comprehensive\s+financial\s+report|\bACFR\b|"
    r"comprehensive\s+annual\s+financial\s+report|\bCAFR\b",
    re.I,
)
BUDGET_RE = re.compile(
    r"\b(?:adopted|proposed|operating|annual)?\s*budget\b|"
    r"budget\s+(?:document|book|summary)",
    re.I,
)


@dataclass
class DocumentCheck:
    verdict: str
    claimed_type: str | None
    fiscal_year: str | None
    title: str | None
    evidence: str


def extract_pdf_text(data: bytes, max_pages: int = 3) -> str:
    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages[:max_pages]:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def check_document(
    data: bytes,
    *,
    url: str = "",
    anchor_text: str = "",
    max_pages: int = 3,
) -> DocumentCheck:
    claim_blob = f"{url} {anchor_text}".lower()
    claimed_type = None
    if ACFR_RE.search(claim_blob) or "acfr" in claim_blob or "cafr" in claim_blob:
        claimed_type = "acfr"
    elif "budget" in claim_blob:
        claimed_type = "budget"
    elif "financial" in claim_blob:
        claimed_type = "financial_report"

    try:
        text = extract_pdf_text(data, max_pages=max_pages)
    except Exception as exc:
        return DocumentCheck(
            verdict="unreadable",
            claimed_type=claimed_type,
            fiscal_year=None,
            title=None,
            evidence=f"pdf parse error: {exc}",
        )

    compact = " ".join(text.split())
    if len(compact) < 40:
        return DocumentCheck(
            verdict="unreadable",
            claimed_type=claimed_type,
            fiscal_year=_first_year(claim_blob),
            title=None,
            evidence="little or no extractable text (likely scanned)",
        )

    title = compact[:160]
    years = YEAR_RE.findall(compact[:2000])
    fiscal_year = years[0] if years else _first_year(claim_blob)

    is_acfr = bool(ACFR_RE.search(compact[:4000]))
    is_budget = bool(BUDGET_RE.search(compact[:4000]))

    found_type = None
    if is_acfr:
        found_type = "acfr"
    elif is_budget:
        found_type = "budget"
    elif "financial report" in compact[:4000].lower():
        found_type = "financial_report"

    if claimed_type and found_type and claimed_type != found_type:
        return DocumentCheck(
            verdict="mismatch",
            claimed_type=claimed_type,
            fiscal_year=fiscal_year,
            title=title,
            evidence=f"link claims {claimed_type} but text looks like {found_type}",
        )

    if found_type:
        return DocumentCheck(
            verdict="confirmed",
            claimed_type=claimed_type or found_type,
            fiscal_year=fiscal_year,
            title=title,
            evidence=f"found {found_type} language in first pages",
        )

    if claimed_type:
        return DocumentCheck(
            verdict="mismatch",
            claimed_type=claimed_type,
            fiscal_year=fiscal_year,
            title=title,
            evidence=f"link claims {claimed_type} but first pages lack matching language",
        )

    return DocumentCheck(
        verdict="skipped",
        claimed_type=None,
        fiscal_year=fiscal_year,
        title=title,
        evidence="no clear finance document language",
    )


def _first_year(text: str) -> str | None:
    m = YEAR_RE.search(text)
    return m.group(1) if m else None
