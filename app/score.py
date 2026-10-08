"""Keyword-based follow and result scoring for links."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

DEFAULT_KEYWORDS_PATH = Path(__file__).with_name("keywords.yaml")
_SHAREPOINT_FILES = ("/:b:/", "/:x:/", "/:w:/", "/:p:/")
_FINANCE_FILE_TERMS = ("budget", "acfr", "cafr", "financial", "finance", "audit")


def is_file_url(url: str, extensions: list[str]) -> bool:
    path = urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in extensions):
        return True
    lowered = url.lower()
    return any(token in lowered for token in _SHAREPOINT_FILES)


@dataclass
class ScoreResult:
    follow_score: float
    result_score: float
    link_type: str
    matched_keywords: list[str] = field(default_factory=list)
    reason: str = ""
    uncertain: bool = False


class KeywordScorer:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        extra_keywords: list[str] | None = None,
        config_path: Path | None = None,
    ) -> None:
        if config is None:
            path = config_path or DEFAULT_KEYWORDS_PATH
            with open(path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
        self.config = config
        self.positive: dict[str, float] = {
            k.lower(): float(v) for k, v in (config.get("positive") or {}).items()
        }
        self.negative: dict[str, float] = {
            k.lower(): float(v) for k, v in (config.get("negative") or {}).items()
        }
        if extra_keywords:
            for kw in extra_keywords:
                key = kw.strip().lower()
                if key and key not in self.positive:
                    self.positive[key] = 30.0
        self.document_extensions = [
            e.lower() for e in (config.get("document_extensions") or [".pdf"])
        ]
        self.document_boost = float(config.get("document_boost", 15))
        signals = config.get("contact_signals") or []
        self.contact_signals = [
            str(s).lower() for s in signals if not isinstance(s, dict)
        ]
        self.contact_page_terms = [
            str(t).lower() for t in config.get("contact_page_terms") or []
        ]
        self.contact_page_exclusions = [
            str(t).lower() for t in config.get("contact_page_exclusions") or []
        ]
        self.follow_threshold = float(config.get("follow_threshold", 8))
        self.result_threshold = float(config.get("result_threshold", 20))
        self.uncertain_low = float(config.get("uncertain_low", 15))
        self.uncertain_high = float(config.get("uncertain_high", 35))

    def score(
        self,
        *,
        url: str,
        anchor_text: str = "",
        context: str = "",
    ) -> ScoreResult:
        blob = " ".join(
            part for part in (url.lower(), anchor_text.lower(), context.lower()) if part
        )
        matched: list[str] = []
        total = 0.0
        for term, weight in self.positive.items():
            if term in blob:
                matched.append(term)
                total += weight
        for term, weight in self.negative.items():
            if term in blob:
                matched.append(f"-{term}")
                total += weight

        is_file = is_file_url(url, self.document_extensions)
        finance_file = is_file and any(term in blob for term in _FINANCE_FILE_TERMS)
        if finance_file:
            total += self.document_boost
            matched.append("document_ext")

        has_contact = any(sig in blob for sig in self.contact_signals)
        mailto = url.lower().startswith("mailto:")

        follow_score = total
        if "government" in blob or "departments" in blob or "department" in blob:
            follow_score += 5

        if finance_file and total >= self.result_threshold * 0.5:
            link_type = "document"
            result_score = total
        elif mailto or (has_contact and total >= 10):
            link_type = "contact"
            result_score = total + (10 if mailto else 0)
        elif total >= self.result_threshold:
            link_type = "navigation"
            result_score = total * 0.6
        else:
            link_type = "navigation"
            result_score = max(0.0, total * 0.4)

        uncertain = self.uncertain_low <= total <= self.uncertain_high
        reason_parts = []
        if matched:
            reason_parts.append("matched: " + ", ".join(matched[:8]))
        if finance_file:
            reason_parts.append("file extension looks like a document")
        if mailto:
            reason_parts.append("mailto link")
        reason = "; ".join(reason_parts) or "no strong keyword signal"

        return ScoreResult(
            follow_score=follow_score,
            result_score=result_score,
            link_type=link_type,
            matched_keywords=matched,
            reason=reason,
            uncertain=uncertain,
        )

    def is_contact_page(self, url: str, anchor_text: str = "") -> bool:
        blob = f"{urlparse(url).path.lower()} {anchor_text.lower()}"
        if any(term in blob for term in self.contact_page_exclusions):
            return False
        return any(term in blob for term in self.contact_page_terms)

    def should_follow(self, score: ScoreResult, depth: int, max_depth: int) -> bool:
        if depth >= max_depth:
            return False
        if score.link_type == "document":
            return False
        return score.follow_score >= self.follow_threshold

    def should_persist(self, score: ScoreResult) -> bool:
        if score.link_type in ("document", "contact"):
            return score.result_score >= self.result_threshold * 0.5
        return score.result_score >= self.result_threshold
