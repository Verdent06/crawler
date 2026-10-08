"""Seed URL normalization helpers."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_seed_url(value: str) -> str:
    """Turn bare domains into https URLs.

    Examples:
        asu.edu -> https://asu.edu
        www.a2gov.org/ -> https://www.a2gov.org/
        http://example.gov -> http://example.gov
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("url is required")
    if any(ch.isspace() for ch in raw):
        raise ValueError("url must not contain spaces")
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url must use http or https")
    if not parsed.netloc:
        raise ValueError("url is missing a hostname")
    return raw
