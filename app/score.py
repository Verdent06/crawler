"""Keyword-based follow and result scoring for links."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

DEFAULT_KEYWORDS_PATH = Path(__file__).with_name("keywords.yaml")


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

        path = urlparse(url).path.lower()
        is_document = any(path.endswith(ext) for ext in self.document_extensions)
        if is_document and any(
            t in blob for t in ("budget", "acfr", "cafr", "financial", "finance", "audit")
        ):
            total += self.document_boost
            matched.append("document_ext")

        has_contact = any(sig in blob for sig in self.contact_signals)
        mailto = url.lower().startswith("mailto:")

        follow_score = total
        # Navigation-friendly terms still matter for the frontier.
        if "government" in blob or "department" in blob or "finance" in blob:
            follow_score = max(follow_score, total)

        if is_document and total >= self.result_threshold * 0.5:
            link_type = "document"
            result_score = total + (self.document_boost if "document_ext" not in matched else 0)
        elif mailto or (has_contact and total >= 10):
            link_type = "contact"
            result_score = total + (10 if mailto else 0)
        elif total >= self.result_threshold:
            # Likely a finance page that may yield documents/contacts.
            link_type = "navigation"
            result_score = total * 0.6
        else:
            link_type = "navigation"
            result_score = max(0.0, total * 0.4)

        uncertain = self.uncertain_low <= total <= self.uncertain_high
        reason_parts = []
        if matched:
            reason_parts.append("matched: " + ", ".join(matched[:8]))
        if is_document:
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
