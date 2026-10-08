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
    r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
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


_CF_HREF = re.compile(r"/cdn-cgi/l/email-protection#([0-9a-fA-F]{6,})")


def _decode_cf_email(token: str) -> str | None:
    try:
        raw = bytes.fromhex(token)
        key = raw[0]
        return bytes(b ^ key for b in raw[1:]).decode("utf-8")
    except (ValueError, IndexError, UnicodeDecodeError):
        return None


def _reveal_protected_emails(soup: BeautifulSoup) -> BeautifulSoup:
    for a in soup.find_all("a", href=True):
        match = _CF_HREF.search(a["href"])
        decoded = _decode_cf_email(match.group(1)) if match else None
        if decoded and "@" in decoded:
            a["href"] = f"mailto:{decoded}"
    for node in soup.find_all(attrs={"data-cfemail": True}):
        decoded = _decode_cf_email(node["data-cfemail"])
        if decoded and "@" in decoded:
            node.string = decoded
    return soup


def extract_links(html: str, base_url: str) -> list[ExtractedLink]:
    soup = _reveal_protected_emails(BeautifulSoup(html, "html.parser"))
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


_JS_U_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_NAME_PARTICLES = {"del", "de", "van", "von", "da", "di", "la"}


def extract_contacts(html: str) -> list[ExtractedContact]:
    contacts = _contacts_in_markup(html)
    embedded = _embedded_markup(html)
    if embedded:
        contacts.extend(_contacts_in_markup(embedded))
    return _finalize_contacts(contacts)


def page_contact_text(html: str) -> str:
    visible = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    embedded = _embedded_markup(html)
    if not embedded:
        return visible
    extra = BeautifulSoup(embedded, "html.parser").get_text("\n", strip=True)
    return f"{visible}\n{extra}"


def clean_contacts(contacts: list[ExtractedContact]) -> list[ExtractedContact]:
    return _finalize_contacts(contacts)


def _decode_js_escapes(html: str) -> str:
    decoded = _JS_U_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), html)
    return decoded.replace("\\/", "/")


def _embedded_markup(html: str) -> str:
    if "\\u003c" not in html.lower():
        return ""
    soup = BeautifulSoup(_decode_js_escapes(html), "html.parser")
    parts: list[str] = []
    for script in soup.find_all("script"):
        text = script.string or ""
        lowered = text.lower()
        if "mailto:" not in lowered and "tel:" not in lowered:
            continue
        text = (
            text.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", " ")
            .replace("&nbsp;", " ")
        )
        parts.append(text)
    return "\n".join(parts)


def _contacts_in_markup(html: str) -> list[ExtractedContact]:
    soup = _reveal_protected_emails(BeautifulSoup(html, "html.parser"))
    text = soup.get_text("\n", strip=True).replace("\u200b", "")
    contacts: list[ExtractedContact] = []
    seen: set[tuple[str | None, str | None]] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().startswith("mailto:"):
            continue
        email = href.split(":", 1)[1].split("?", 1)[0].strip().lower()
        block = a.parent.get_text(" ", strip=True) if a.parent else a.get_text(" ", strip=True)
        anchor = a.get_text(" ", strip=True)
        name = _person_name(anchor) or _guess_name(block, email)
        title = _role_near_contact(block, name, email)
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

    for line in text.splitlines():
        title = _find_title(line)
        if not title:
            continue
        phone = _find_phone(line)
        name = _guess_name(line, None)
        if not phone:
            continue
        key = (None, name)
        if key in seen:
            continue
        seen.add(key)
        contacts.append(
            ExtractedContact(name=name, title=title, email=None, phone=phone)
        )

    contacts.extend(_stacked_contacts(text))
    return contacts


_ROLE_WORDS = (
    "manager", "director", "treasurer", "officer", "controller", "assistant",
    "specialist", "counsel", "coordinator", "analyst", "supervisor", "chief",
    "president", "executive", "administrator", "clerk", "auditor", "assessor",
    "accountant", "superintendent", "comptroller", "lead", "vice", "deputy",
    "associate", "head", "dean", "secretary",
)
_BLOCK_LOOKAHEAD = 4


def _is_role_line(line: str) -> bool:
    lower = line.lower()
    if len(line) > 70 or re.search(r"[@\d]", line):
        return False
    return any(re.search(rf"\b{word}\b", lower) for word in _ROLE_WORDS)


def _stacked_contacts(text: str) -> list[ExtractedContact]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    found: list[ExtractedContact] = []
    for index in range(len(lines) - 2):
        name = _person_name(lines[index].rstrip(",").strip())
        if not name or not _is_role_line(lines[index + 1]):
            continue
        tail = lines[index + 2 : index + 2 + _BLOCK_LOOKAHEAD]
        email = next(
            (_first_email(line) for line in tail if _first_email(line)), None
        )
        phone = next((_find_phone(line) for line in tail if _find_phone(line)), None)
        if email or phone:
            found.append(
                ExtractedContact(
                    name=name,
                    title=lines[index + 1].strip(" ,;"),
                    email=email,
                    phone=phone,
                )
            )
    return found


def _first_email(line: str) -> str | None:
    match = EMAIL_RE.search(line)
    return _normalize_email(match.group(1), match.group(2)) if match else None


def _role_near_contact(text: str, name: str | None, email: str | None) -> str | None:
    found = _find_title(text)
    if found:
        return found
    rest = text
    if name:
        rest = re.sub(re.escape(name), " ", rest, count=1)
    if email:
        rest = re.sub(re.escape(email), " ", rest, count=1, flags=re.I)
    rest = PHONE_RE.sub(" ", rest)
    rest = re.sub(r"\s+", " ", rest).strip(" ,;|/-–—")
    if not rest or len(rest) > 60 or rest.count(" ") > 6:
        return None
    if not _is_role_line(rest):
        return None
    return rest


def _find_title(text: str) -> str | None:
    lower = text.lower()
    for hint in TITLE_HINTS:
        idx = lower.find(hint)
        if idx >= 0:
            snippet = text[idx : idx + len(hint) + 40]
            snippet = re.split(r"[|\n•:@\d,.;—–]|'s\b|\s-\s", snippet)[0].strip()
            return _trim_title_tail(snippet)[:80]
    return None


_TITLE_JOINERS = {"of", "and", "the", "&", "to"}


def _trim_title_tail(snippet: str) -> str:
    words = snippet.split()
    for index, word in enumerate(words):
        if index and word[0].islower() and word not in _TITLE_JOINERS:
            return " ".join(words[:index])
    return snippet


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
    m = re.search(r"\b([A-Z][a-z]+\s[A-Z][a-z]+)\b", text)
    if m and email and _name_fits_email(m.group(1), email):
        return m.group(1)
    return None


def _name_fits_email(name: str, email: str) -> bool:
    local = re.sub(r"[^a-z]", "", email.split("@", 1)[0].lower())
    tokens = [t.lower() for t in name.split()]
    if any(len(t) >= 3 and t in local for t in tokens):
        return True
    return len(tokens) >= 2 and (tokens[0][0] + tokens[-1]) in local


def page_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def page_looks_like_staff(html: str, url: str) -> bool:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()
    blob = f"{url.lower()} {text[:15000]}"
    if any(k in blob for k in _STAFF_PAGE_HINTS):
        return True
    return "mailto:" in html.lower()


_STAFF_PAGE_HINTS = (
    "staff directory",
    "finance director",
    "chief financial",
    "contact us",
    "leadership",
    "directory",
    "cfo",
    "treasurer",
    "controller",
)

_NOT_A_PERSON = {
    "treasurer",
    "service",
    "zone",
    "county",
    "city",
    "department",
    "office",
    "customer",
    "chief",
    "financial",
    "director",
    "budget",
    "contact",
    "arbor",
    "email",
    "us",
    "here",
    "click",
    "services",
    "questions",
    "info",
    "website",
    "form",
    "forms",
    "staff",
    "team",
    "support",
}

_GENERIC_EMAIL = ("customer", "info", "webmaster", "helpdesk", "noreply", "no-reply")


def _person_name(name: str | None) -> str | None:
    if not name:
        return None
    parts = name.strip(" ,;").split()
    if not 2 <= len(parts) <= 4:
        return None
    name = " ".join(parts)
    for part in parts:
        if part.lower() in _NAME_PARTICLES:
            continue
        if not re.fullmatch(r"[A-Z][A-Za-z'\-]+|[A-Z]\.?", part):
            return None
        if part.lower() in _NOT_A_PERSON:
            return None
    return name


def _usable_title(title: str | None) -> str | None:
    if not title:
        return None
    title = re.sub(r"\s+", " ", title).strip()
    if re.search(r"[@:\d]|\b(?:email|e-mail|address|phone|number|fax)\b", title, re.I):
        return None
    if len(title) > 70 or title.count(" ") > 6:
        lower = title.lower()
        for hint in TITLE_HINTS:
            if hint in lower:
                return hint.upper() if hint == "cfo" else hint.title()
        return None
    return title


def _generic_inbox(email: str | None) -> bool:
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].lower()
    return any(part in local for part in _GENERIC_EMAIL)


def _finalize_contacts(contacts: list[ExtractedContact]) -> list[ExtractedContact]:
    by_email: dict[str, ExtractedContact] = {}
    rest: list[ExtractedContact] = []
    for contact in contacts:
        name = _person_name(contact.name)
        title = _usable_title(contact.title)
        email = contact.email
        if _generic_inbox(email) and not name:
            email = None
        if not email and not (contact.phone and name):
            continue
        cleaned = ExtractedContact(
            name=name,
            title=title,
            email=email,
            phone=contact.phone,
        )
        if email:
            current = by_email.get(email)
            if current is None:
                by_email[email] = cleaned
            elif _richer(cleaned, current):
                by_email[email] = _merge_contact(current, cleaned)
            else:
                by_email[email] = _merge_contact(cleaned, current)
        else:
            rest.append(cleaned)
    emailed_phones = {_phone_digits(item.phone) for item in by_email.values()}
    unique_rest = [
        item
        for item in rest
        if not _phone_digits(item.phone) or _phone_digits(item.phone) not in emailed_phones
    ]
    return list(by_email.values()) + unique_rest


def _phone_digits(phone: str | None) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else ""


def _richer(candidate: ExtractedContact, current: ExtractedContact) -> bool:
    def filled(contact: ExtractedContact) -> int:
        return sum(bool(value) for value in (contact.name, contact.title, contact.phone))

    return filled(candidate) >= filled(current)


def _merge_contact(left: ExtractedContact, right: ExtractedContact) -> ExtractedContact:
    return ExtractedContact(
        name=right.name or left.name,
        title=right.title or left.title,
        email=right.email or left.email,
        phone=right.phone or left.phone,
    )
