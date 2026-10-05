"""HTML link and contact extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+\-]+)\s*(?:@|\[at\]|\(at\))\s*([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b",
    re.I,
)
PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
)
TITLE_HINTS = (
    "finance director",
    "chief financial",
    "cfo",
    "controller",
    "treasurer",
    "budget director",
    "director of finance",
    "deputy finance",
    "assistant finance",
    "business manager",
    "chief business",
)


@dataclass
class ExtractedLink:
    url: str
    anchor_text: str
    context: str


@dataclass
class ExtractedContact:
    name: str | None
    title: str | None
    email: str | None
    phone: str | None


def normalize_url(base_url: str, href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("#", "javascript:", "data:")):
        return None
    if href.startswith("mailto:"):
        return href
    absolute = urljoin(base_url, href)
    absolute, _ = urldefrag(absolute)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https", "mailto"}:
        return None
    return absolute


def _nearby_context(tag: Tag) -> str:
    parts: list[str] = []
    parent = tag.parent
    depth = 0
    while parent is not None and depth < 4:
        if isinstance(parent, Tag):
            heading = parent.find(["h1", "h2", "h3", "h4", "h5", "h6"])
            if heading:
                parts.append(heading.get_text(" ", strip=True))
            if parent.name in {"li", "td", "tr", "article", "section"}:
                parts.append(parent.get_text(" ", strip=True)[:240])
                break
        parent = parent.parent
        depth += 1
    return " ".join(p for p in parts if p)[:400]


def extract_links(html: str, base_url: str) -> list[ExtractedLink]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    results: list[ExtractedLink] = []
    for a in soup.find_all("a", href=True):
        url = normalize_url(base_url, a["href"])
        if not url or url in seen:
            continue
        seen.add(url)
        anchor = a.get_text(" ", strip=True)
        title = a.get("title") or ""
        context = _nearby_context(a)
        if title:
            context = f"{title} {context}".strip()
        results.append(ExtractedLink(url=url, anchor_text=anchor, context=context))
    return results


def _normalize_email(local: str, domain: str) -> str:
    return f"{local.strip().lower()}@{domain.strip().lower()}"


def extract_contacts(html: str) -> list[ExtractedContact]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    contacts: list[ExtractedContact] = []
    seen: set[tuple[str | None, str | None]] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        email = None
        if href.lower().startswith("mailto:"):
            email = href.split(":", 1)[1].split("?", 1)[0].strip()
        block = a.parent.get_text(" ", strip=True) if a.parent else a.get_text(" ", strip=True)
        title = _find_title(block)
        name = _guess_name(block, email)
        phone = _find_phone(block)
        key = (email, name)
        if email or title:
            if key not in seen:
                seen.add(key)
                contacts.append(
                    ExtractedContact(name=name, title=title, email=email, phone=phone)
                )

    for match in EMAIL_RE.finditer(text):
        email = _normalize_email(match.group(1), match.group(2))
        start = max(0, match.start() - 160)
        end = min(len(text), match.end() + 160)
        window = text[start:end]
        title = _find_title(window)
        name = _guess_name(window, email)
        phone = _find_phone(window)
        key = (email, name)
        if key in seen:
            continue
        if title or _looks_finance_related(window):
            seen.add(key)
            contacts.append(
                ExtractedContact(name=name, title=title, email=email, phone=phone)
            )

    # Title-only rows near phones when no email is present.
    for line in text.splitlines():
        title = _find_title(line)
        if not title:
            continue
        phone = _find_phone(line)
        name = _guess_name(line, None)
        if not name and not phone:
            continue
        key = (None, name)
        if key in seen:
            continue
        seen.add(key)
        contacts.append(
            ExtractedContact(name=name, title=title, email=None, phone=phone)
        )

    return contacts


def _find_title(text: str) -> str | None:
    lower = text.lower()
    for hint in TITLE_HINTS:
        idx = lower.find(hint)
        if idx >= 0:
            # Return a short slice around the matched title phrase.
            snippet = text[idx : idx + len(hint) + 40]
            snippet = re.split(r"[|\n•]", snippet)[0].strip(" ,;-")
            return snippet[:80]
    return None


def _looks_finance_related(text: str) -> bool:
    lower = text.lower()
    return any(
        k in lower
        for k in ("finance", "budget", "treasur", "controller", "cfo", "audit")
    )


def _find_phone(text: str) -> str | None:
    m = PHONE_RE.search(text)
    return m.group(0) if m else None


def _guess_name(text: str, email: str | None) -> str | None:
    # Prefer "Name, Title" or "Name - Title" patterns.
    for pattern in (
        r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)\s*[,–-]\s*(?:Finance|Budget|Chief|Controller|Treasurer)",
        r"([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)\s+(?:Finance Director|CFO|Controller|Treasurer)",
    ):
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()
    if email:
        local = email.split("@", 1)[0]
        parts = re.split(r"[._\-]+", local)
        if 1 < len(parts) <= 3 and all(p.isalpha() for p in parts):
            return " ".join(p.capitalize() for p in parts)
    # Fallback: first Proper Name in the window.
    m = re.search(r"\b([A-Z][a-z]+\s[A-Z][a-z]+)\b", text)
    if m:
        return m.group(1)
    return None


def page_looks_like_staff(html: str, url: str) -> bool:
    blob = f"{url} {BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)[:2000]}".lower()
    return any(
        k in blob
        for k in (
            "staff directory",
            "finance director",
            "contact us",
            "leadership",
            "directory",
            "mailto:",
        )
    )
