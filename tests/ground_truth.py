"""Compare a crawled site against a hand-built ground-truth file."""

from __future__ import annotations

import json
from pathlib import Path

from app.crawl import CrawlConfig
from app.db import Database

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_expected(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}_expected.json").read_text("utf-8"))


def config_for(expected: dict) -> CrawlConfig:
    return CrawlConfig(**expected["config"])


def fetched_pages(db: Database, site_id: int) -> set[str]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT url FROM pages WHERE site_id = ? AND error IS NULL", (site_id,)
        )
        return {row["url"] for row in rows}


def _norm(value: str | None) -> str:
    return " ".join((value or "").split()).lower()


def _contact_matches(actual: dict, want: dict) -> bool:
    key = "email" if want.get("email") else "name"
    return _norm(actual.get(key)) == _norm(want[key])


def diff_contacts(site: dict, expected: dict) -> list[str]:
    problems: list[str] = []
    contacts = site["contacts"]
    for want in expected["required"]:
        found = [c for c in contacts if _contact_matches(c, want)]
        if not found:
            problems.append(f"missing contact {want}")
            continue
        if not any(
            all(_norm(c.get(k)) == _norm(v) for k, v in want.items()) for c in found
        ):
            problems.append(f"contact fields differ for {want}: got {found}")
    for contact in contacts:
        if not contact.get("email") and not contact.get("phone"):
            problems.append(f"contact without email or phone: {contact}")
        if contact.get("name") in expected["forbidden_names"]:
            problems.append(f"role label recorded as a person: {contact}")
        if contact.get("email") in expected["forbidden_emails"]:
            problems.append(f"off-topic contact recorded: {contact}")
    distinct = {(c.get("name"), c.get("email"), c.get("phone")) for c in contacts}
    if len(distinct) > expected["max_total"]:
        problems.append(f"{len(distinct)} distinct contacts, expected <= {expected['max_total']}")
    return problems


def diff_documents(site: dict, expected: dict) -> list[str]:
    problems: list[str] = []
    by_url = {d["url"]: d for d in site["documents"]}
    for want in expected["required"]:
        actual = by_url.get(want["url"])
        if actual is None:
            problems.append(f"missing document {want['url']}")
            continue
        for key, value in want.items():
            if key != "url" and _norm(actual.get(key)) != _norm(value):
                problems.append(f"{want['url']}: {key}={actual.get(key)!r}, expected {value!r}")
    confirmed = sum(d["verdict"] == "confirmed" for d in site["documents"])
    if confirmed < expected["confirmed_min"]:
        problems.append(f"{confirmed} confirmed documents, expected >= {expected['confirmed_min']}")
    for url in by_url:
        if any(bad in url for bad in expected["forbidden_substrings"]):
            problems.append(f"forbidden document recorded: {url}")
    return problems


def diff_links(site: dict, expected: dict) -> list[str]:
    problems: list[str] = []
    by_url = {link["url"]: link for link in site["links"]}
    for want in expected["required"]:
        actual = by_url.get(want["url"])
        if actual is None:
            problems.append(f"missing link {want['url']}")
        elif actual["link_type"] not in want["types"]:
            problems.append(f"{want['url']}: type {actual['link_type']}, expected {want['types']}")
    for url in by_url:
        if any(bad in url for bad in expected["forbidden_substrings"]):
            problems.append(f"forbidden link recorded: {url}")
    return problems


def diff_site(site: dict, pages: set[str], expected: dict) -> list[str]:
    problems = [
        f"page not fetched: {url}"
        for url in expected["pages_fetched"]
        if url not in pages
    ]
    problems += diff_links(site, expected["links"])
    problems += diff_contacts(site, expected["contacts"])
    problems += diff_documents(site, expected["documents"])
    return problems
